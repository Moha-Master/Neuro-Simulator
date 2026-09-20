"""StreamLoop：直播循环状态机（stream 模块的心脏）。

状态：offline → starting（开播 intro，等 UI 回报或超时）→ live ⇄ paused → offline

每轮：从绑定队列随机抽取 ≤N 条未消费消息（按时间正序呈现）→ 拼装场景化
user message 调 neuro /chat → 消费响应（speech 转发给 /ui 画面 + 累计语音时长）
→ 等"发声总时长 + round_gap"→ 下一轮。空队列行为可配（占位消息 / 静默等待，
静默模式下由入列事件即时唤醒，无需轮询）。

对外广播（EventBroadcaster → /ui/events）：session_begin / session_end / state /
intro / scene_set / speech / turn_begin / turn_end。循环是单 task，控制方法
（pause/resume/stop/report）都只动 Event/标志位，跨 task 安全。
"""

import asyncio
import contextlib
import logging
import random
import time
import uuid
from typing import Any, Optional

from ..broadcaster import EventBroadcaster
from . import media
from . import settings as stream_settings
from .chatbot import ChatbotManager
from .neuro_client import NeuroClient, NeuroError
from .storage import Storage

logger = logging.getLogger(__name__)

STATE_OFFLINE = "offline"
STATE_STARTING = "starting"
STATE_LIVE = "live"
STATE_PAUSED = "paused"

# 循环状态 → 场次持久化状态（config/用户口径：online/online_pause/offline）
_SESSION_STATE = {
    STATE_OFFLINE: "offline",
    STATE_STARTING: "online",
    STATE_LIVE: "online",
    STATE_PAUSED: "online_pause",
}


class StreamConflict(Exception):
    """状态不允许该操作（→ 409）。"""


def _now_human() -> str:
    return time.strftime("%a %Y-%m-%d %H:%M:%S")


class StreamLoop:
    def __init__(self, storage: Storage, bus: EventBroadcaster) -> None:
        self.storage = storage
        self.bus = bus
        self.chatbot = ChatbotManager(storage)
        self.state: str = STATE_OFFLINE
        self.session: Optional[dict[str, Any]] = None
        self.last_scene: Optional[str] = None
        self._task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._intro_event = asyncio.Event()
        self._wakeup = asyncio.Event()
        # 开播闸门计时起点（snapshot 供新连接的 UI 续播 intro 视频）
        self._intro_started: Optional[float] = None
        # 当前轮次的 speech 时间线（供 snapshot 断点续播）：
        # 每项 {index, text, duration, start}，start 为预测发声起点（monotonic）
        self._turn_speech: list[dict[str, Any]] = []
        self._turn_play_head = 0.0
        self._in_turn = False

    # ---------- 供 snapshot 的中途连接状态 ----------

    def intro_elapsed(self) -> Optional[float]:
        """starting 阶段已过秒数（UI 续播开场视频用）。"""
        if self.state == STATE_STARTING and self._intro_started is not None:
            return time.monotonic() - self._intro_started
        return None

    def turn_snapshot(self) -> dict[str, Any]:
        """当前/最近一轮的 speech 列表，附 consumed（已发声秒数，含轮间保留期）。
        仅对"正在发声"的那一句附带音频（供新连接的 UI 从断点 seek 续播）；
        完成句只回文本（静态显示），未来句由实时事件携带音频到达。"""
        now = time.monotonic()
        segs = []
        for s in self._turn_speech:
            consumed = min(max(now - s["start"], 0.0), s["duration"])
            seg: dict[str, Any] = {
                "index": s["index"],
                "text": s["text"],
                "duration": s["duration"],
                "consumed": round(consumed, 2),
            }
            if consumed < s["duration"] - 0.05 and s.get("audio"):
                seg["audio"] = s["audio"]
                seg["mime"] = s.get("mime", "")
            segs.append(seg)
        return {"active": self._in_turn, "speech": segs}

    # ---------- 广播 ----------

    def publish(self, ev: dict) -> None:
        self.bus.publish(ev)

    def _end_turn(self, reason: str) -> None:
        """轮次结束：时间线保留（供中途连接的 UI 续显/淡出），仅标记不在轮中。"""
        self._in_turn = False
        self.publish({"type": "turn_end", "reason": reason})

    def _publish_state(self) -> None:
        self.publish({"type": "state", "state": self.state, "session_state": _SESSION_STATE[self.state]})

    # ---------- 控制入口 ----------

    async def start(self) -> dict[str, Any]:
        if self.state != STATE_OFFLINE:
            raise StreamConflict(f"直播已在进行中（state={self.state}），先停止再开始")
        try:
            s = stream_settings.load()
        except RuntimeError as e:
            raise StreamConflict(str(e)) from e

        queue_row, queue_id = await self.storage.create_queue(f"stream {time.strftime('%Y-%m-%d %H:%M')}")
        conversation_id = uuid.uuid4().hex
        session_row, session_id = await self.storage.create_session(
            queue_row, s.channel, conversation_id
        )
        self.session = {
            "session_row": session_row,
            "session_id": session_id,
            "queue_row": queue_row,
            "queue_id": queue_id,
            "channel": s.channel,
            "conversation_id": conversation_id,
            "cursor": 0,
        }
        self.state = STATE_STARTING
        self._stop_event.clear()
        self._intro_event.clear()
        self._wakeup.clear()
        self.last_scene = "intro"
        self._intro_started = time.monotonic()
        self.publish({"type": "session_begin", "session_id": session_id, "channel": s.channel,
                      "conversation_id": conversation_id})
        self._publish_state()
        self.publish({"type": "intro"})
        
        # 启动 chatbot 循环
        self.chatbot.start_session(queue_row, self.on_new_message)
        
        self._task = asyncio.create_task(self._main(), name="stream-loop")
        logger.info("stream started: session=%s conv=%s", session_id, conversation_id[:8])
        return self.session

    async def pause(self) -> None:
        if self.state != STATE_LIVE:
            raise StreamConflict(f"只能暂停进行中的直播（state={self.state}）")
        self.state = STATE_PAUSED
        await self.storage.update_session(self.session["session_row"], state=_SESSION_STATE[self.state])
        self._publish_state()

    async def resume(self) -> None:
        if self.state != STATE_PAUSED:
            raise StreamConflict(f"只能恢复已暂停的直播（state={self.state}）")
        self.state = STATE_LIVE
        await self.storage.update_session(self.session["session_row"], state=_SESSION_STATE[self.state])
        self._wakeup.set()  # 若循环正停在轮间/静默等待，立刻继续
        self._publish_state()

    async def stop(self) -> dict[str, Any]:
        if self.state == STATE_OFFLINE:
            return {"status": "not_running"}
        self._stop_event.set()
        self._wakeup.set()
        self._intro_event.set()
        if self.session and self.state in (STATE_LIVE, STATE_PAUSED):
            # 中断 neuro 侧在途生成（若正在一轮中）
            s = self.session
            with contextlib.suppress(Exception):
                await NeuroClient(stream_settings.load().neuro_url).stop(s["channel"], s["conversation_id"])
        if self._task is not None:
            with contextlib.suppress(asyncio.TimeoutError, Exception):
                await asyncio.wait_for(asyncio.shield(self._task), timeout=10)
        return {"status": "stopped"}

    async def shutdown(self) -> None:
        """进程退出钩子：结束当前场次（尽力而为）。"""
        if self.state != STATE_OFFLINE:
            self._stop_event.set()
            self._wakeup.set()
            self._intro_event.set()
        if self._task is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(asyncio.shield(self._task), timeout=5)

    def on_scene(self, scene_id: str) -> None:
        self.last_scene = scene_id
        self.publish({"type": "scene_set", "id": scene_id})

    def on_report(self, event: str) -> None:
        """/ui 上行回报。intro_done：intro 视频 + 立绘入场播完，允许开始循环。"""
        if event == "intro_done":
            if self.state == STATE_STARTING:
                self._intro_event.set()
        else:
            logger.debug("未知 report: %s", event)

    def on_new_message(self) -> None:
        """入列唤醒：静默等待模式下立刻推进下一轮。"""
        if self.state == STATE_LIVE:
            self._wakeup.set()

    # ---------- 主循环 ----------

    async def _main(self) -> None:
        session = self.session
        try:
            # intro 闸门：等画面回报，或按"视频时长+入场动画+余量"精确兜底
            # （无人接入画面/回报丢失时不至于永久卡在 starting）
            timeout = media.intro_gate_seconds()
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._intro_event.wait(), timeout)
            if self._stop_event.is_set():
                return
            self.state = STATE_LIVE
            self.last_scene = "room"
            self._publish_state()

            while not self._stop_event.is_set():
                if self.state == STATE_PAUSED:
                    await self._wait_or(self._wakeup)
                    self._wakeup.clear()
                    continue
                s = stream_settings.load()
                try:
                    outcome = await self._run_round(s, session)
                except Exception:
                    # 单轮异常不终结整场直播：退避后继续
                    logger.exception("round failed, backing off 3s")
                    self._end_turn("error")
                    await self._sleep_ms(3000)
                    continue
                if outcome == "wait":  # 静默等待新消息
                    await self._wait_or(self._wakeup)
                    self._wakeup.clear()
                    continue
                # 轮间等待（发声时长 + gap 已在 _run_round 内消耗的部分会被扣掉？不——
                # _run_round 只负责生成与转发，等待放这里统一做）
                await self._sleep_ms(s.round_gap_ms + outcome)
        except Exception:
            logger.exception("stream loop crashed, ending session")
        finally:
            await self._finalize()

    async def _finalize(self) -> None:
        self.chatbot.stop_session()
        was = self.state
        self.state = STATE_OFFLINE
        self._intro_started = None
        self._turn_speech = []
        self._in_turn = False
        if self.session is not None:
            await self.storage.update_session(
                self.session["session_row"], state="offline", ended=True
            )
            self.publish({"type": "session_end", "session_id": self.session["session_id"]})
            logger.info("stream session ended (from %s)", was)
        self.session = None
        self.last_scene = None
        self._publish_state()

    async def _run_round(self, s: stream_settings.StreamSettings, session: dict) -> int | str:
        """跑一轮：抽取→拼装→生成消费→（发声时长等待）。返回需追加等待的毫秒数，
        或 "wait"（静默模式无消息）。"""
        picked = await self._pick(s, session)
        if picked is None:
            return "wait"

        if isinstance(picked, str):
            body = picked  # 占位互动（不推进 cursor）
            last_id = None
        else:
            lines = "\n".join(f"- {m['username']}: {m['content']}" for m in picked)
            body = f"Recent viewer messages:\n{lines}"
            last_id = picked[-1]["id"]

        text = f"{s.scene_context}\nCurrent date and time: {_now_human()}\n\n{body}"
        speech_ms = 0.0
        round_failed = False
        turn_texts: list[str] = []
        self._turn_speech = []
        self._turn_play_head = time.monotonic()
        self._in_turn = True
        self.publish({"type": "turn_begin"})
        client = NeuroClient(s.neuro_url)
        try:
            async for ev in client.chat(session["channel"], session["conversation_id"], text):
                if self._stop_event.is_set():
                    break
                et = ev.get("type")
                if et == "speech":
                    duration = float(ev.get("duration") or 0)
                    speech_ms += duration * 1000
                    stext = str(ev.get("text") or "").strip()
                    if stext:
                        turn_texts.append(stext)
                    # 发声时刻 = max(事件到达, 前句播完)：与 UI 顺序播放模型一致
                    now = time.monotonic()
                    start = max(now, self._turn_play_head)
                    self._turn_play_head = start + duration
                    self._turn_speech.append(
                        {
                            "index": ev.get("index", 0),
                            "text": stext,
                            "duration": duration,
                            "start": start,
                            "audio": str(ev.get("audio") or ""),
                            "mime": str(ev.get("mime") or ""),
                        }
                    )
                    self.publish({
                        "type": "speech",
                        "index": ev.get("index", 0),
                        "text": ev.get("text", ""),
                        "audio": ev.get("audio", ""),
                        "mime": ev.get("mime", ""),
                        "duration": ev.get("duration", 0),
                    })
                elif et in ("done", "cancelled", "error"):
                    round_failed = et in ("cancelled", "error")
                    self._end_turn(et)
                    break
        except NeuroError as e:
            logger.error("neuro 轮次失败: %s", e)
            self._end_turn("error")
            round_failed = True

        if turn_texts:
            self.chatbot.update_neuro_speech(" ".join(turn_texts), speech_ms / 1000.0)

        if last_id is not None:
            session["cursor"] = last_id
        await self.storage.update_session(
            session["session_row"], cursor_msg=session["cursor"], inc_rounds=True
        )
        if round_failed:
            # 失败轮次加长退避，防毒上游时打爆网关
            await self._sleep_ms(3000)
            return 0
        return int(speech_ms)

    async def _pick(self, s, session: dict):
        """返回：抽中的消息 list（正序）| 占位字符串 | None(静默等待)。"""
        candidates = await self.storage.list_messages(session["queue_row"], session["cursor"], limit=500)
        if not candidates:
            if s.empty_queue_behavior == "silent":
                return None
            return s.placeholder_message
        if len(candidates) > s.messages_per_round:
            picked = random.sample(candidates, s.messages_per_round)
            picked.sort(key=lambda m: m["id"])
        else:
            picked = candidates
        return picked

    # ---------- 等待工具（全部可被 stop/wakeup 打断） ----------

    async def _wait_or(self, event: asyncio.Event) -> None:
        """等 event 或 stop_event。"""
        stop_task = asyncio.ensure_future(self._stop_event.wait())
        ev_task = asyncio.ensure_future(event.wait())
        try:
            await asyncio.wait({stop_task, ev_task}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            stop_task.cancel()
            ev_task.cancel()
            with contextlib.suppress(BaseException):
                await asyncio.gather(stop_task, ev_task, return_exceptions=True)

    async def _sleep_ms(self, ms: float) -> None:
        if ms <= 0:
            return
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._stop_event.wait(), ms / 1000)
