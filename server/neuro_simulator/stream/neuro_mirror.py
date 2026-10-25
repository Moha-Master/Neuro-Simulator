"""stream 侧的画面事件聚合器。

浏览器对同一 origin 的常驻 SSE 有 6 条上限。neuro 窗口拆成内容/状态两条流后，
同时打开 `/stream-view` 与 `/neuro-view` 会占满连接池。本模块让 **stream 成为
画面事件的唯一聚合者**：

- 后端直连 neuro 的 `/chat/events`（内容平面）与 `/state/events`（状态平面），
  不占浏览器连接池；
- 每条事件原样通过 stream 自己的 `/ui/events` 转发，事件名为 `neuro`：
  `{"type":"neuro","plane":"content"|"state","event":<原始事件名>,"data":<原始 payload>}`；
- 缓存最近一轮 turn 与状态基线，供 `/ui/events` 首帧 snapshot 重放；
- 顺带接管原 `StreamLoop._watch_neuro_scene()`：把 neuro 场景的
  `highlight_messages` 同步到 `highlight_paused`，并维护 `neuro_link`。

`/neuro-view`（dashboard 预览）仍由窗口页自己直连两条流；只有被 stream 合成器
iframe 捕获时，窗口页才改吃这里转发的 feed。
"""

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, Any, Optional

from . import settings as stream_settings
from .neuro_client import NeuroClient

if TYPE_CHECKING:  # pragma: no cover - 仅类型检查，避免运行时循环导入
    from .loop import StreamLoop

logger = logging.getLogger(__name__)

# 断线重连间隔：不终结直播，只更新 neuro_link（与旧 _watch_neuro_scene 一致）
NEURO_MIRROR_RETRY_S = 3.0

# 订阅参数（与 neuro 窗口页一致；stream 自己不看 reasoning/content 正文）
CONTENT_DROP = ["reasoning", "content"]
STATE_WANT = ["scene", "caption", "layers", "manifest"]

_TERMINAL_EVENTS = frozenset(("done", "cancelled", "error"))


class NeuroMirror:
    """订阅 neuro 的两条总线，转发并缓存画面事件。

    `snapshot()` 只读缓存（同步、无 I/O），符合 `state_kit` 的 provider 纪律；
    网络 I/O 全部发生在两个常驻任务里。
    """

    def __init__(self, loop: "StreamLoop") -> None:
        self.loop = loop
        self._turn: Optional[dict] = None
        self._state: dict[str, Any] = {}
        self._tasks: list[asyncio.Task] = []

    # ---------- 生命周期 ----------

    def start(self) -> None:
        """启动两个常驻订阅任务（重复调用是幂等的）。"""
        self._tasks = [t for t in self._tasks if not t.done()]
        if self._tasks:
            return
        self._tasks = [
            asyncio.create_task(self._content_loop(), name="stream-neuro-mirror-content"),
            asyncio.create_task(self._state_loop(), name="stream-neuro-mirror-state"),
        ]

    async def stop(self) -> None:
        """取消并等待两个订阅任务；重复调用安全。"""
        tasks, self._tasks = self._tasks, []
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    # ---------- 首帧重放 ----------

    def snapshot(self) -> dict:
        """最近一轮 turn + 状态基线（只读缓存，绝不发请求）。"""
        return {"turn": self._turn, "state": dict(self._state)}

    # ---------- 订阅循环 ----------

    async def _content_loop(self) -> None:
        """常驻订阅内容总线：断线 3s 后重连，不终结直播。"""
        while True:
            target = self._target()
            if target is None:
                await self._retry_sleep()
                continue
            url, channel = target
            events = NeuroClient(url).chat_events(
                channel=channel, audio=True, drop=CONTENT_DROP
            )
            try:
                async for ev in events:
                    self.loop.note_neuro_link(url, True)
                    self._apply_content(ev)
                    self._publish("content", ev)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # 连接中断/非 200/解析失败都走重连
                self.loop.note_neuro_link(url, False, f"{type(e).__name__}: {e}")
                logger.warning(
                    "neuro 内容订阅失败（%.0fs 后重连）: %s", NEURO_MIRROR_RETRY_S, e
                )
            finally:
                aclose = getattr(events, "aclose", None)
                if aclose is not None:
                    with contextlib.suppress(Exception):
                        await aclose()
            await self._retry_sleep()

    async def _state_loop(self) -> None:
        """常驻订阅状态总线：断线 3s 后重连，不终结直播。"""
        while True:
            target = self._target()
            if target is None:
                await self._retry_sleep()
                continue
            url, _channel = target
            events = NeuroClient(url).state_events(want=STATE_WANT)
            try:
                async for ev in events:
                    self.loop.note_neuro_link(url, True)
                    self._apply_state(ev)
                    self._publish("state", ev)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.loop.note_neuro_link(url, False, f"{type(e).__name__}: {e}")
                logger.warning(
                    "neuro 状态订阅失败（%.0fs 后重连）: %s", NEURO_MIRROR_RETRY_S, e
                )
            finally:
                aclose = getattr(events, "aclose", None)
                if aclose is not None:
                    with contextlib.suppress(Exception):
                        await aclose()
            await self._retry_sleep()

    # ---------- 内部 ----------

    @staticmethod
    def _target() -> Optional[tuple[str, str]]:
        """当前 neuro 地址与 channel；配置不可用时返回 None（稍后重试）。"""
        try:
            s = stream_settings.load()
        except RuntimeError:
            return None
        url = str(getattr(s, "neuro_url", "") or "").rstrip("/")
        if not url:
            return None
        return url, str(getattr(s, "channel", "stream") or "stream")

    async def _retry_sleep(self) -> None:
        await asyncio.sleep(NEURO_MIRROR_RETRY_S)

    def _publish(self, plane: str, ev: dict) -> None:
        """原样转发一帧（`data` 逐字段不变，外层 type 固定为 neuro）。"""
        self.loop.publish(
            {
                "type": "neuro",
                "plane": plane,
                "event": str(ev.get("type") or "message"),
                "data": ev,
            }
        )

    def _apply_content(self, ev: dict) -> None:
        """维护内容平面基线：首帧 snapshot / start / speech / 终态。"""
        kind = str(ev.get("type") or "")
        if kind == "snapshot":
            turn = ev.get("turn")
            if not isinstance(turn, dict):
                self._turn = None
                return
            # mirror 不知道前端播放进度：重放时所有句子都从 0 开始（不复制神经侧
            # 的 consumed；否则中途重连的 iframe 会以为半句已经播过）
            speech = turn.get("speech")
            if isinstance(speech, list):
                turn = {
                    **turn,
                    "speech": [
                        {**seg, "consumed": 0}
                        for seg in speech
                        if isinstance(seg, dict)
                    ],
                }
            self._turn = turn
            return
        if kind == "start":
            self._turn = {
                "id": ev.get("turn_id"),
                "active": True,
                "speech": [],
            }
            return
        if kind == "speech":
            if self._turn is None:
                self._turn = {
                    "id": ev.get("turn_id"),
                    "active": True,
                    "speech": [],
                }
            seg: dict[str, Any] = {
                key: ev[key]
                for key in ("step", "index", "text", "duration", "audio", "mime")
                if key in ev
            }
            # mirror 不知道前端播放进度；中途重载会重播当前轮已发出的句子
            seg["consumed"] = 0
            self._turn["speech"].append(seg)
            return
        if kind in _TERMINAL_EVENTS and self._turn is not None:
            self._turn["active"] = False

    def _apply_state(self, ev: dict) -> None:
        """维护状态基线（`sections.<name>.current`），并同步高亮开关。"""
        sections = ev.get("sections")
        if not isinstance(sections, dict):
            return
        for name, section in sections.items():
            # 首帧 snapshot 是完整信封（不受 want 影响）：只缓存画面关心的分区
            if name not in STATE_WANT or not isinstance(section, dict):
                continue
            self._state[name] = section.get("current")
        if "scene" not in sections:
            return
        section = sections.get("scene")
        current = section.get("current") if isinstance(section, dict) else None
        # current=null 视为暂停（保守）；与旧 _watch_neuro_scene 行为一致
        if not isinstance(current, dict):
            self.loop._set_highlight_paused(True, source="scene:neuro")
            return
        flag = current.get("highlight_messages")
        enabled = flag if isinstance(flag, bool) else True
        self.loop._set_highlight_paused(not enabled, source="scene:neuro")
