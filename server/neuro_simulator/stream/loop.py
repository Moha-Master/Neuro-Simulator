"""StreamLoop：直播循环状态机（stream 模块的心脏）。

状态：offline → starting（固定时长的 intro 场景）→ live ⇄ paused → offline

每轮：先查优先轨（有则只消费最早 1 条），否则从普通轨随机抽取 ≤N 条未消费消息
（按时间正序呈现）→ 拼装场景化 user message 调 neuro /chat → 消费响应
（speech 转发给 /ui 画面 + 记录预测播放时间线）→ 等“最后一句播完 +
speech_cooldown_ms”→ 下一轮。
优先消息在注入时与普通消息完全同格式（对模型只是降噪）；入列即广播
chat_message / priority_message 给画面。空队列行为可配（占位消息 / 静默等待，
静默模式下由入列事件即时唤醒，无需轮询）。

对外广播（EventBroadcaster → /ui/events）：session_begin / session_end / state /
scene_set / element_state（游戏等元素的全量状态）/ speech / turn_begin /
turn_end / chat_message / priority_message / tool_start / tool_result
（neuro 事件原样镜像；工具语义由画面端按 name 处理）。
循环是单 task，控制方法（pause/resume/stop）都只动 Event/标志位，跨 task 安全。
开播 intro 是普通场景：时长来自 scenes.json，后端到点切 room，不等待任何前端。
"""

import asyncio
import contextlib
import logging
import random
import time
import uuid
from typing import Any, Callable, Optional

from ..broadcaster import EventBroadcaster
from . import media
from . import scenes as scene_catalog
from . import settings as stream_settings
from .chatbot import ChatbotManager
from .games import GameHub
from .neuro_client import NeuroClient, NeuroError
from .persona import stream_info_line
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
    def __init__(self, storage: Storage, bus: EventBroadcaster, persona, templates) -> None:
        self.storage = storage
        self.bus = bus
        self.persona = persona
        self.templates = templates
        self.chatbot = ChatbotManager(storage, persona, templates)
        self.state: str = STATE_OFFLINE
        self.session: Optional[dict[str, Any]] = None
        self.last_scene: Optional[str] = None
        # 直播里可被 Neuro 工具驱动的本地小游戏（同时至多一个活跃游戏）
        self.games = GameHub(publish=self.publish, set_scene=self.on_scene)
        self._task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._wakeup = asyncio.Event()
        # game 动作在 stream 内的实际执行串行化（工具请求可能并发到达）
        self._game_action_lock = asyncio.Lock()
        # intro 场景时间线计时起点（snapshot 供新连接的 UI 续播 intro）
        self._intro_started: Optional[float] = None
        # 当前轮次的 speech 时间线（供 snapshot 断点续播）：
        # 每项 {index, text, duration, start}，start 为预测发声起点（monotonic）
        self._turn_speech: list[dict[str, Any]] = []
        self._turn_play_head = 0.0
        self._in_turn = False
        # 轮次序号：speech / turn_begin / turn_end / snapshot 共用，画面据此区分轮次
        self._turn_id = 0

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
                "step": s.get("step", 0),
                "index": s["index"],
                "text": s["text"],
                "duration": s["duration"],
                "consumed": round(consumed, 2),
            }
            if consumed < s["duration"] - 0.05 and s.get("audio"):
                seg["audio"] = s["audio"]
                seg["mime"] = s.get("mime", "")
            segs.append(seg)
        return {"id": self._turn_id, "active": self._in_turn, "speech": segs}

    async def wait_for_speech_end(self) -> None:
        """等当前预测的语音时间线播完（game 工具执行前的闸门）。

        用服务端预测的 TTS 时间轴，不依赖任何前端/播放器回报；stop 时立即放行。
        """
        while not self._stop_event.is_set():
            remaining = self._turn_play_head - time.monotonic()
            if remaining <= 0:
                return
            await asyncio.sleep(min(remaining, 0.2))

    async def run_game_action(self, action: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        """串行执行 game 动作；默认先等语音播完，配置可关闭等待。"""
        async with self._game_action_lock:
            try:
                wait_speech = stream_settings.load().game_tool_wait_speech
            except RuntimeError:
                wait_speech = True
            if wait_speech:
                await self.wait_for_speech_end()
            return action()

    def ui_settings(self) -> dict[str, Any]:
        """画面端运行参数（snapshot 下发；配置热重载时广播 settings 事件）。"""
        try:
            s = stream_settings.load()
        except RuntimeError:
            return {"speech_cooldown_ms": 1000}
        return {"speech_cooldown_ms": s.speech_cooldown_ms}

    # ---------- 广播 ----------

    def publish(self, ev: dict) -> None:
        self.bus.publish(ev)

    def _end_turn(self, reason: str) -> None:
        """轮次结束：时间线保留（供中途连接的 UI 续显/淡出），仅标记不在轮中。"""
        if not self._in_turn:
            return  # 本轮尚未开始/已结束：不要给上一轮补发迟到的 turn_end
        self._in_turn = False
        self.publish({"type": "turn_end", "turn_id": self._turn_id, "reason": reason})

    def _publish_state(self) -> None:
        self.publish({"type": "state", "state": self.state, "session_state": _SESSION_STATE[self.state]})

    # ---------- 控制入口 ----------

    async def start(
        self,
        title: Optional[str] = None,
        category: Optional[str] = None,
        tags: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        if self.state != STATE_OFFLINE:
            raise StreamConflict(f"直播已在进行中（state={self.state}），先停止再开始")
        try:
            s = stream_settings.load()
        except RuntimeError as e:
            raise StreamConflict(str(e)) from e

        queue_row, queue_id = await self.storage.create_queue(f"stream {time.strftime('%Y-%m-%d %H:%M')}")
        conversation_id = uuid.uuid4().hex
        # 未显式指定时取 config 的默认值（dashboard 开播面板里也可以直接改）
        meta_title = (s.default_title if title is None else title).strip()
        meta_category = (s.default_category if category is None else category).strip()
        meta_tags = (
            list(s.default_tags)
            if tags is None
            else stream_settings.normalize_tags(tags)
        )
        session_row, session_id = await self.storage.create_session(
            queue_row,
            s.channel,
            conversation_id,
            title=meta_title,
            category=meta_category,
            tags=meta_tags,
        )
        self.session = {
            "session_row": session_row,
            "session_id": session_id,
            "queue_row": queue_row,
            "queue_id": queue_id,
            "channel": s.channel,
            "conversation_id": conversation_id,
            "cursor": 0,
            "priority_cursor": 0,
            "rounds": 0,
            "title": meta_title,
            "category": meta_category,
            "tags": meta_tags,
        }
        self.state = STATE_STARTING
        self._stop_event.clear()
        self._wakeup.clear()
        self.last_scene = "intro"
        self._intro_started = time.monotonic()
        self._turn_play_head = 0.0
        self.publish({"type": "session_begin", "session_id": session_id, "channel": s.channel,
                      "conversation_id": conversation_id,
                      "title": meta_title, "category": meta_category, "tags": meta_tags})
        self._publish_state()
        # intro 是 scenes.json 里的普通场景：广播 scene_set 让画面按数据跑时间线。
        self.on_scene("intro")

        # 启动 chatbot 循环（本场标题/分区作为其消息模板的变量来源）
        self.chatbot.start_session(
            queue_row,
            self.on_new_message,
            self._announce_chatbot_message,
            metadata_provider=self.session_metadata,
        )

        self._task = asyncio.create_task(self._main(), name="stream-loop")
        logger.info("stream started: session=%s conv=%s", session_id, conversation_id[:8])
        return self.session

    # ---------- 本场信息（标题 / 分区） ----------

    def session_metadata(self) -> tuple[str, str]:
        """(title, category)。无场次时回落到 config 默认值（供预览与开播面板用）。"""
        if self.session:
            return (self.session.get("title") or "", self.session.get("category") or "")
        try:
            s = stream_settings.load()
        except RuntimeError:
            return ("", "")
        return (s.default_title, s.default_category)

    def session_tags(self) -> list[str]:
        """本场标签；无场次时回落到 config 默认值。"""
        if self.session:
            return list(self.session.get("tags") or [])
        try:
            s = stream_settings.load()
        except RuntimeError:
            return []
        return list(s.default_tags)

    async def set_metadata(
        self,
        *,
        title: Optional[str] = None,
        category: Optional[str] = None,
        tags: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """直播中修改本场标题/分区/标签：落库 + 广播，下一轮消息即生效。"""
        if not self.session:
            raise StreamConflict("没有进行中的直播，无法修改本场信息")
        new_title = self.session.get("title", "") if title is None else title.strip()
        new_category = (
            self.session.get("category", "") if category is None else category.strip()
        )
        new_tags = (
            list(self.session.get("tags") or [])
            if tags is None
            else stream_settings.normalize_tags(tags)
        )
        await self.storage.update_session(
            self.session["session_row"],
            title=new_title,
            category=new_category,
            tags=new_tags,
        )
        self.session["title"] = new_title
        self.session["category"] = new_category
        self.session["tags"] = new_tags
        meta = {"title": new_title, "category": new_category, "tags": new_tags}
        self.publish({"type": "stream_metadata", **meta})
        logger.info(
            "stream metadata updated: %r [%s] tags=%s",
            new_title,
            new_category,
            new_tags,
        )
        return meta

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
        """进程退出钩子：结束当前场次（尽力而为）并关闭 chatbot 的 LLM 客户端。"""
        if self.state != STATE_OFFLINE:
            self._stop_event.set()
            self._wakeup.set()
        if self._task is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(asyncio.shield(self._task), timeout=5)
        with contextlib.suppress(Exception):
            await self.chatbot.aclose()

    def on_scene(self, scene_id: str) -> None:
        self.last_scene = scene_id
        self.publish({"type": "scene_set", "id": scene_id})

    def on_new_message(self) -> None:
        """入列唤醒：静默等待模式下立刻推进下一轮。"""
        if self.state == STATE_LIVE:
            self._wakeup.set()

    # ---------- 入列（普通 / 优先共用广播与唤醒） ----------

    def _announce_message(
        self,
        msg_id: int,
        username: str,
        content: str,
        *,
        priority_type: Optional[str] = None,
        value: int = 0,
    ) -> None:
        """把新入列消息镜像给 /ui（聊天层 / 优先弹窗）。"""
        ev: dict[str, Any] = {
            "type": "priority_message" if priority_type else "chat_message",
            "id": msg_id,
            "username": username,
            "content": content,
        }
        if priority_type:
            ev["priority_type"] = priority_type
            ev["value"] = value
        self.publish(ev)

    def _announce_chatbot_message(self, msg_id: int, username: str, content: str) -> None:
        """Chatbot 直接写库后的广播回调（不含唤醒，批量入列由 chat 循环统一触发）。"""
        self._announce_message(msg_id, username, content)

    async def append_message(
        self,
        username: str,
        content: str,
        *,
        priority_type: Optional[str] = None,
        value: int = 0,
    ) -> int:
        """API 入列：写库 → 广播 → 唤醒（静默等待模式）。"""
        if self.session is None:
            raise StreamConflict("当前没有进行中的直播场次，消息无处入列")
        msg_id = await self.storage.append_message(
            self.session["queue_row"],
            username,
            content,
            priority_type=priority_type,
            value=value,
        )
        self._announce_message(
            msg_id, username, content, priority_type=priority_type, value=value
        )
        self.on_new_message()
        return msg_id

    # ---------- 主循环 ----------

    async def _main(self) -> None:
        session = self.session
        try:
            # intro 是固定时长的普通场景：时长首选 scenes.json 的 timeline.duration，
            # 缺失时按视频+入场动画兜底。后端按自己的时钟推进，不等待任何前端；
            # stop 事件可即时打断等待。
            try:
                duration = scene_catalog.duration("intro") or media.intro_duration_seconds()
            except RuntimeError:
                # 场景数据损坏也只能影响画面构建，不能把直播循环拖住/弄崩。
                logger.warning("intro scene duration unavailable; using media fallback", exc_info=True)
                duration = media.intro_duration_seconds()
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=duration)
                return
            except asyncio.TimeoutError:
                pass
            if self._stop_event.is_set():
                return
            self.state = STATE_LIVE
            # 只有没被显式改过（intro 期间手动切场景或开了游戏）才落到 room。
            # 广播 scene_set 是“后端推进画面”的唯一手段：任何前端卡住/迟到，
            # 都会被这一帧纠正；没有前端也不影响循环。
            if self.last_scene in (None, "intro"):
                self.on_scene("room")
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
                # 语音播完 + speech_cooldown 的等待已在 _run_round 内完成
        except Exception:
            logger.exception("stream loop crashed, ending session")
        finally:
            await self._finalize()

    async def _finalize(self) -> None:
        self.chatbot.stop_session()
        self.games.clear()
        # 若停止时仍有在途轮次，先按正常轮次结束广播 turn_end（画面据此进入淡出）
        self._end_turn("stopped")
        was = self.state
        self.state = STATE_OFFLINE
        self._intro_started = None
        self._turn_speech = []
        self._turn_play_head = 0.0
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

    async def _run_round(self, s: stream_settings.StreamSettings, session: dict) -> str | None:
        """跑一轮：抽取→拼装→生成消费→等语音播完 + cooldown。

        返回 "wait"（静默模式无消息）或 None（本轮已完成，可继续下一轮）。
        语音播完的判定使用服务端预测时间轴 `_turn_play_head`：最后一句的
        `start + duration`，与画面顺序播放模型一致；**不是**“生成结束后再等
        整轮语音总时长”（那会把生成期间已经播掉的时间重复计算）。
        """
        picked = await self._pick(s, session)
        if picked is None:
            return "wait"

        last_id = None
        title, category = self.session_metadata()
        # 消息模板（可在 dashboard 编辑）的公共变量；{{round}} 用"本轮序号"
        tpl_vars = {
            # 场景自带 prompt_context 就用它（如 play_wordle 会说明"正在玩 Wordle"），
            # 否则回落到 config 的全局 scene_context。棋盘/比分这类细节不走这里——
            # 它们只从工具返回里给（将来由通用 stream_status 变量统一承载）。
            "scene_context": scene_catalog.prompt_context(self.last_scene) or s.scene_context,
            "datetime": _now_human(),
            "stream_info": stream_info_line(title, category),
            "title": title,
            "category": category,
            "scene": self.last_scene or "",
            "round": str(session.get("rounds", 0) + 1),
        }
        if picked["kind"] == "placeholder":
            # 空队列：{{messages}} 换成占位指令（不推进 cursor）。
            # 不再单独维护"空队列模板"——少一条模板、少一个变量，行为也更直观。
            messages_block = picked["body"]
        else:
            msgs = picked["messages"]
            # 优先消息与普通消息对 neuro 完全同格式：不带 type/value，不加标注
            messages_block = "\n".join(f"- {m['username']}: {m['content']}" for m in msgs)
            last_id = msgs[-1]["id"]
            if picked["kind"] == "priority":
                session["priority_cursor"] = last_id
            else:
                session["cursor"] = last_id
        text = self.templates.render("neuro_user", {**tpl_vars, "messages": messages_block})
        round_failed = False
        had_speech = False
        turn_text = ""
        self._turn_id += 1
        turn_id = self._turn_id
        self._turn_speech = []
        self._turn_play_head = time.monotonic()
        self._in_turn = True
        self.publish({"type": "turn_begin", "turn_id": turn_id})
        client = NeuroClient(s.neuro_url)
        try:
            async for ev in client.chat(session["channel"], session["conversation_id"], text):
                if self._stop_event.is_set():
                    break
                et = ev.get("type")
                if et == "speech":
                    duration = float(ev.get("duration") or 0)
                    stext = str(ev.get("text") or "").strip()
                    step = ev.get("step", 0)
                    if stext:
                        turn_text = f"{turn_text} {stext}".strip()
                    # 同一轮内的不同 LLM step 之间也留出 cooldown：上游可能在上一步
                    # 语音还没播完时就把工具执行完、进入下一步并吐出 speech。字幕端
                    # 按 step 边界清空旧文本，如果事件立即下发，旧字幕会在最后一个
                    # 字符刚出现时被下一步首句覆盖。这里按服务端预测时间线先压住
                    # 新 step 的首句，等上一步播完 + cooldown 再转发；这样音频与字幕
                    # 的节拍一致，snapshot 的预测时间线也不会前后矛盾。
                    if self._turn_speech:
                        prev = self._turn_speech[-1]
                        if step != prev.get("step", 0):
                            wait_until = (
                                prev["start"]
                                + prev["duration"]
                                + s.speech_cooldown_ms / 1000.0
                            )
                            wait_s = wait_until - time.monotonic()
                            if wait_s > 0:
                                await self._sleep_ms(wait_s * 1000)
                                if self._stop_event.is_set():
                                    break
                    # 发声时刻 = max(事件到达, 前句播完)：与 UI 顺序播放模型一致
                    now = time.monotonic()
                    start = max(now, self._turn_play_head)
                    self._turn_play_head = start + duration
                    had_speech = True
                    self._turn_speech.append(
                        {
                            "step": step,
                            "index": ev.get("index", 0),
                            "text": stext,
                            "duration": duration,
                            "start": start,
                            "audio": str(ev.get("audio") or ""),
                            "mime": str(ev.get("mime") or ""),
                        }
                    )
                    # 睡眠判定从“这句预测播完”开始计时：字幕还在播时 chatbot 不会
                    # 把 Neuro 判成空闲；长工具轮里也不会等整轮结束才更新。
                    self.chatbot.note_neuro_speech(turn_text, self._turn_play_head)
                    self.publish({
                        "type": "speech",
                        "turn_id": turn_id,
                        "step": step,
                        "index": ev.get("index", 0),
                        "text": ev.get("text", ""),
                        "audio": ev.get("audio", ""),
                        "mime": ev.get("mime", ""),
                        "duration": ev.get("duration", 0),
                    })
                elif et in ("tool_start", "tool_result"):
                    # 与普通 Chat 一致：工具事件原样镜像（type 已在事件里），
                    # 是否消费由画面端按 name 自行判断（如 model_spin/model_zoom）。
                    self.publish(ev)
                elif et in ("done", "cancelled", "error"):
                    round_failed = et in ("cancelled", "error")
                    self._end_turn(et)
                    break
        except NeuroError as e:
            logger.error("neuro 轮次失败: %s", e)
            self._end_turn("error")
            round_failed = True

        if last_id is not None:
            logger.info(
                "round picked %s message id=%s",
                "priority" if picked["kind"] == "priority" else "normal",
                last_id,
            )
        session["rounds"] = session.get("rounds", 0) + 1
        await self.storage.update_session(
            session["session_row"],
            cursor_msg=session["cursor"],
            priority_cursor=session["priority_cursor"],
            inc_rounds=True,
        )

        # 下一轮请求 = 语音播完 + cooldown。没有语音时从本轮结束开始计 cooldown。
        speech_finished = self._turn_play_head if had_speech else time.monotonic()
        wait_ms = (speech_finished + s.speech_cooldown_ms / 1000.0 - time.monotonic()) * 1000
        await self._sleep_ms(wait_ms)
        if round_failed:
            # 失败轮次加长退避，防毒上游时打爆网关
            await self._sleep_ms(3000)
        return None

    async def _pick(self, s, session: dict) -> Optional[dict]:
        """双轨抽取：优先轨最早一条优先（一次只消费 1 条）；
        优先轨为空才回落到普通轨随机抽样。返回 None 表示静默等待。"""
        priority = await self.storage.list_priority_messages(
            session["queue_row"], session["priority_cursor"], limit=1
        )
        if priority:
            return {"kind": "priority", "messages": priority}

        candidates = await self.storage.list_normal_messages(
            session["queue_row"], session["cursor"], limit=500
        )
        if not candidates:
            if s.empty_queue_behavior == "silent":
                return None
            return {"kind": "placeholder", "body": s.placeholder_message}
        if len(candidates) > s.messages_per_round:
            picked = random.sample(candidates, s.messages_per_round)
            picked.sort(key=lambda m: m["id"])
        else:
            picked = candidates
        return {"kind": "normal", "messages": picked}

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
