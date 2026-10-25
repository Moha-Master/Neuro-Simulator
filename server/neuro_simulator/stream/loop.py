"""StreamLoop：直播循环状态机（stream 模块的心脏）。

状态：offline → starting（neuro 窗口播放开场）→ live ⇄ paused → offline

每轮：先查高亮轨（有则只消费最早 1 条），否则从普通轨随机抽取 ≤N 条未消费消息
（按时间正序呈现）→ 拼装场景化 user message 调 neuro /chat（批量模式）→
读 `GET /status` 的 `sections.turn.current.ends_at` 拿本轮语音预测播完的绝对时刻 →
等“播完 + speech_cooldown_ms” → 下一轮。**stream 不自己累加字幕时间线**：neuro 的
`UiState` 是唯一权威。
高亮消息在注入时与普通消息完全同格式（对模型只是降噪）；入列即广播
chat_message / highlight_message 给画面。真正的浮层属于 neuro 场景：选中高亮消息后
随 `POST /chat` 把元数据发给 neuro，由 neuro 窗口弹窗。空队列行为可配（占位消息 /
静默等待，静默模式下由入列事件即时唤醒，无需轮询）。

高亮队列暂停开关（`highlight_paused`）：暂停时不消费高亮轨（游标不推进），普通消息
照常循环；来源可为 `scene:stream` / `scene:neuro` / `manual`，最后写入者生效。

对外广播（EventBroadcaster → /ui/events）：session_begin / session_end / state /
scene_set / element_state（游戏等元素的全量状态）/ chat_message / highlight_message /
stream_metadata / settings。**speech/tool/turn 事件不再走 stream 画面**——neuro
窗口（合成器里的 iframe）直接订阅 neuro 自己的 `/chat/events`（内容平面）与
`/state/events`（场景/字幕设置）。

开播：先探测 neuro 健康，再 POST neuro /ui/intro 触发开场（拿她切到的**场景 id**，
不拿时长）；合成器落到 room_intro（只有 neuro 全屏，不叠 chat），随后
**订阅** `GET neuro /state/events?want=scene` 等 `sections.scene.current.id` 离开
开场场景——**她离开那个场景 = 开场放完** → 切 room 并推进 starting→live。
订阅在 `INTRO_FALLBACK_S` 内没有任何消息（或建立失败）时退回 0.5s 轮询
`GET neuro /status`（幂等对账），两条路径共用同一判据与同一硬上限。stream 侧
不存在任何秒数倒计时：换素材、调 after 余量、neuro 改了场景 id 都不需要同步改这里。
到硬上限（START_READY_TIMEOUT_S）仍未放行则中止本次开播（落库 ended +
session_end{reason:"intro_timeout"} + status.last_error），不硬转 live。
neuro 不可达时禁止开播；中途 neuro 掉线由合成器显示 no_source 背景
（画面侧健康轮询）。
"""

import asyncio
import contextlib
import inspect
import logging
import random
import time
import uuid
from datetime import datetime
from typing import Any, Awaitable, Callable, Optional, Union

from .. import scene_kit, state_kit
from ..broadcaster import EventBroadcaster
from ..scene_kit import SceneRunner
from . import settings as stream_settings
from .scenes import StreamScenePolicy, highlight_messages_enabled
from .chatbot import ChatbotManager
from .games import GameHub
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


# 开播时"开场是否放完"的判定参数。
#
# 主路径是**订阅** neuro 的状态总线（她离开开场场景是一次离散状态变化，正适合
# 推送）；订阅建立失败或 `INTRO_FALLBACK_S` 内没有任何消息（长开场本来就没有
# 中间变化）就退回 0.5s 轮询 `/status`——幂等对账是订阅的天然短板，两条路共用
# 同一判据。下面的硬上限只是纯兜底：她永远停在开场场景（state_script 数据坏、
# 进程假死）才会走到这里，到点**中止本次开播**——而不是硬转 live（那等于开播一个
# 画面卡在开场的直播）。硬上限与 neuro 的场景时长无关，所以 stream 侧不存在任何秒数。
START_READY_TIMEOUT_S = 120.0
READY_POLL_INTERVAL_S = 0.5
# 订阅建立后多久没有消息就认为"推不动了"（长开场期间状态总线本来就不发帧：
# 它只推离散变化，不推 elapsed 这种时间流逝）
INTRO_FALLBACK_S = 3.0

# 单轮/等语音的硬上限（模块常量，不进 config）：改成"问 neuro 状态"之后，
# neuro 重启、丢状态、返回一个荒唐的 ends_at 都可能让循环永久挂起，必须靠超时兜底。
# 超时按失败轮处理（退避 3s 继续），不终结场次。
ROUND_DEADLINE_S = 300.0
SPEECH_POLL_INTERVAL_S = 0.2
# 消息模板直接用最近一次 /status 的投影；超过这个秒数才重新拉。取数发生在轮次
# 逻辑里（provider 绝不能发 HTTP），供同轮模板与 chatbot 共用。
NEURO_STATUS_STALE_S = 5.0

# 开场期间合成器落在 room_intro（neuro 全屏、不显示 chat）；
# 她离开开场场景后切到 room，并同时推进 live。
SCENE_ROOM_INTRO = "room_intro"
SCENE_ROOM = "room"

# neuro 场景订阅断线后的重连间隔（不终结直播，只更新 neuro_link）
NEURO_SCENE_RETRY_S = 3.0


class StreamConflict(Exception):
    """状态不允许该操作（→ 409）。"""


def _now_human() -> str:
    return time.strftime("%a %Y-%m-%d %H:%M:%S")


def _neuro_turn(status: Any) -> dict:
    """/status 新形状里的 turn 分区（sections.turn.current）；读不到就是空 dict。"""
    if not isinstance(status, dict):
        return {}
    sections = status.get("sections")
    if not isinstance(sections, dict):
        return {}
    turn = sections.get("turn")
    if not isinstance(turn, dict):
        return {}
    current = turn.get("current")
    return current if isinstance(current, dict) else {}


# 状态总线的帧可能只含变化的分区：没有 scene 分区 ≠ 场景为空
_NO_SCENE = object()


def _scene_from_state_event(event: Any) -> Any:
    """从 `/state/events` 的一帧里取当前场景 id。

    返回 `_NO_SCENE` 表示"这帧没带 scene 分区"（例如 turn 变化），调用方应忽略；
    返回 `""` 表示 scene 分区存在但 current 为 null（neuro 刚重启/场景数据缺失，
    与轮询路径的"空场景算放行"同一语义）。
    """
    if not isinstance(event, dict):
        return _NO_SCENE
    sections = event.get("sections")
    if not isinstance(sections, dict) or "scene" not in sections:
        return _NO_SCENE
    section = sections.get("scene")
    current = section.get("current") if isinstance(section, dict) else None
    return str((current or {}).get("id") or "")


def _scene_current_from_state_event(event: Any) -> Any:
    """从 `/state/events` 的一帧里取 `sections.scene.current` 对象。

    返回 `_NO_SCENE` 表示这帧没带 scene 分区（调用方应忽略）；返回 `None` 表示
    scene 分区存在但 current 为 null（neuro 刚重启/场景数据缺失）。
    """
    if not isinstance(event, dict):
        return _NO_SCENE
    sections = event.get("sections")
    if not isinstance(sections, dict) or "scene" not in sections:
        return _NO_SCENE
    section = sections.get("scene")
    current = section.get("current") if isinstance(section, dict) else None
    return current if isinstance(current, dict) else None



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
        # 合成器场景的权威状态：进入时刻与时长供 scene_set / snapshot 断点恢复
        self._scene_started_at: float = 0.0
        self._scene_duration: float = 0.0
        # 场景切换走公共机制（scene_kit）：写状态 → 广播 scene_set → 排程后端脚本
        self._scenes = SceneRunner(self, self.publish, StreamScenePolicy())
        # 直播里可被 Neuro 工具驱动的本地小游戏（同时至多一个活跃游戏）
        self.games = GameHub(publish=self.publish, set_scene=self.on_scene)
        self._task: Optional[asyncio.Task] = None
        # neuro 场景订阅任务（live 期间常驻；把 neuro 场景开关同步到 highlight_paused）
        self._scene_watch_task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._wakeup = asyncio.Event()
        # game 动作在 stream 内的实际执行串行化（工具请求可能并发到达）
        self._game_action_lock = asyncio.Lock()
        # 开场闸门（starting 阶段）：等 neuro 离开她自己的开场场景，**不倒数任何秒数**
        self._intro_gate: Optional[dict[str, Any]] = None
        # 最近一次"异常终止开播"的原因（供 dashboard 显示；下次成功开播时清空）
        self.last_error: Optional[dict[str, Any]] = None

        # /stream/status 的 provider 必须同步无 I/O：有 I/O 的值由 loop（归属者）
        # 在事情发生时顺手记下，provider 只读这些缓存。
        self.queue_depth = 0
        self.highlight_depth = 0
        self.last_message_id = 0
        self.persisted: Optional[dict[str, Any]] = None
        # 高亮消息队列暂停开关：True = 暂停（不消费高亮轨、游标不推进）
        self.highlight_paused = False
        self.highlight_source: Optional[str] = None
        # stream 对 neuro 的最近观测（health / intro / status / 语音闸门都更新它）
        self.neuro_link: dict[str, Any] = {
            "reachable": False,
            "observed_at": None,
            "url": "",
            "error": None,
        }
        # 最近一次 GET neuro /status 的原始信封与剥好的投影；模板与 chatbot 共用。
        # 取数只能发生在轮次逻辑里，provider 只读这份缓存（state_kit 纪律）。
        self._neuro_status_envelope: Optional[dict] = None
        self._neuro_status_plain: dict = {}
        self._neuro_status_at: float = 0.0
        # 启动时由 server 绑定：状态变化时 bump rev（未绑定时 no-op，便于单测）
        self.state_board: Optional[Any] = None

    def _touch(self, *names: str) -> None:
        """状态分区变了：通知 board 递增 rev（粗粒度，见 state_kit）。"""
        board = self.state_board
        if board is not None:
            board.note_change(*names)

    def note_neuro_link(self, url: str, reachable: bool, error: Optional[str] = None) -> None:
        """记录一次对 neuro 的真实观测（绝不在这里发请求）。"""
        self.neuro_link = {
            "reachable": bool(reachable),
            "observed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "url": url or "",
            "error": error,
        }
        self._touch("neuro_link")

    async def _fetch_neuro_status(
        self, client: NeuroClient, channel: str, *, force: bool = False
    ) -> Optional[dict]:
        """取 neuro `/status`（带缓存）：force=False 时 5s 内直接复用。

        失败时：force=True（语音时间轴必须读新的）返回 None；否则返回上一次的
        信封（模板宁可看旧状态，也不要因为一次抖动退化成空）。
        """
        if (
            not force
            and self._neuro_status_envelope is not None
            and time.monotonic() - self._neuro_status_at < NEURO_STATUS_STALE_S
        ):
            return self._neuro_status_envelope
        try:
            st = await client.status(channel)
        except Exception as e:  # 网络/解析/客户端都降级，不能拖垮整轮
            self.note_neuro_link(
                getattr(client, "base_url", ""), False, f"{type(e).__name__}: {e}"
            )
            logger.warning("neuro /status 读取失败: %s", e)
            return None if force else self._neuro_status_envelope
        self.note_neuro_link(getattr(client, "base_url", ""), True)
        self._neuro_status_envelope = st
        self._neuro_status_plain = state_kit.current_sections(st)
        self._neuro_status_at = time.monotonic()
        return st

    def stream_status_dict(self) -> dict:
        """stream 自己的投影，剥成 `{分区: current}`（模板 context 的 stream_status）。"""
        board = self.state_board
        return state_kit.current_sections(board.read()) if board is not None else {}

    def template_context(self, messages: str) -> dict:
        """消息模板的完整 Jinja context：只放纯 dict/list/标量。

        `neuro_said`/`neuro_status` 由 chatbot 持有（本轮正文 + 最近一次投影），
        `stream_status` 是 stream 自己的状态机现读，`chat_messages` 由调用方给。
        """
        return {
            "stream_status": self.stream_status_dict(),
            # 优先用 loop 刚拉到的投影（chatbot 缓存是上一轮结束时的同一份）
            "neuro_status": self._neuro_status_plain or self.chatbot.neuro_status,
            "neuro_said": self.chatbot.neuro_said,
            "chat_messages": messages,
            "datetime": _now_human(),
        }

    # ---------- 供 snapshot 的中途连接状态 ----------

    def ui_settings(self) -> dict[str, Any]:
        """画面端运行参数（snapshot 下发；配置热重载时广播 settings 事件）。

        neuro UI 的地址同时给出绝对 URL（直连 stream 时用）与 channel；
        合成器在 vedal 代理下会优先使用同源 /manage/neuro_sama/... 路径。
        """
        try:
            s = stream_settings.load()
        except RuntimeError:
            return {"neuro_ui_url": "", "neuro_health_url": "", "neuro_channel": "stream"}
        base = (s.neuro_url or "").rstrip("/")
        return {
            "neuro_ui_url": f"{base}/ui/" if base else "",
            "neuro_health_url": f"{base}/health" if base else "",
            "neuro_channel": s.channel,
        }

    async def wait_for_speech_end(self) -> None:
        """等当前预测的语音时间线播完（game 工具执行前的闸门）。

        这里等的是**轮次中间**的时刻（生成还在进行、批量响应还没回来），所以不能
        依赖 `/chat` 的返回值——直接问 neuro `/status`。每次循环重读以适配工具
        执行期间新出现的句子；stop 立即放行，读不到状态/ends_at 远得离谱时由
        `ROUND_DEADLINE_S` 兜底，不能把工具调用卡死。
        """
        try:
            s = stream_settings.load()
        except RuntimeError:
            return
        neuro_url = getattr(s, "neuro_url", "")
        channel = getattr(s, "channel", "stream")
        if not neuro_url:
            return
        client = NeuroClient(neuro_url)
        deadline = time.monotonic() + ROUND_DEADLINE_S
        while not self._stop_event.is_set():
            if time.monotonic() >= deadline:
                logger.error("game 语音闸门超过 %.0fs deadline，放行", ROUND_DEADLINE_S)
                return
            try:
                st = await client.status(channel)
                self.note_neuro_link(neuro_url, True)
            except NeuroError as e:
                # neuro 重启中：先短睡重试，deadline 兜底
                self.note_neuro_link(neuro_url, False, f"{type(e).__name__}: {e}")
                logger.warning("game 语音闸门读 status 失败: %s", e)
                await self._sleep_ms(SPEECH_POLL_INTERVAL_S * 1000)
                continue
            ends_at = _neuro_turn(st).get("ends_at")
            if ends_at is None:
                return
            try:
                remaining = float(ends_at) - time.time()
            except (TypeError, ValueError):
                return
            if remaining <= 0:
                return
            if remaining > deadline - time.monotonic():
                logger.error("game 语音闸门 ends_at 超出 deadline，放行")
                return
            await self._sleep_ms(min(remaining, SPEECH_POLL_INTERVAL_S) * 1000)

    async def run_game_action(
        self, action: Callable[[], Union[dict[str, Any], Awaitable[dict[str, Any]]]]
    ) -> dict[str, Any]:
        """串行执行 game 动作；默认先等语音播完，配置可关闭等待。"""
        async with self._game_action_lock:
            try:
                wait_speech = stream_settings.load().game_tool_wait_speech
            except RuntimeError:
                wait_speech = True
            if wait_speech:
                await self.wait_for_speech_end()
            result = action()
            if inspect.isawaitable(result):
                result = await result
            return result

    # ---------- 广播 ----------

    def publish(self, ev: dict) -> None:
        self.bus.publish(ev)

    def _publish_state(self) -> None:
        self._touch("loop")
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

        # 开播守卫：neuro 不可达 / 开场无法启动时禁止开播。
        # 先触发开场再建场次：失败不会留下半场 session；健康检查与 intro 调用
        # 都在任何广播之前，dashboard 可直接看到明确的 409 detail。
        client = NeuroClient(s.neuro_url)
        try:
            await client.health()
        except NeuroError as e:
            self.note_neuro_link(s.neuro_url, False, f"{type(e).__name__}: {e}")
            raise StreamConflict(f"neuro 模块不可达，无法开播：{e}") from e
        self.note_neuro_link(s.neuro_url, True)
        try:
            intro_scene = await client.intro()  # 要等她离开的场景 id，不是时长
        except NeuroError as e:
            self.note_neuro_link(s.neuro_url, False, f"{type(e).__name__}: {e}")
            raise StreamConflict(f"neuro 开场启动失败，无法开播：{e}") from e

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
            "highlight_cursor": 0,
            "rounds": 0,
            "title": meta_title,
            "category": meta_category,
            "tags": meta_tags,
        }
        # 供 /stream/status 的 loop.started_at / loop.persisted（无 I/O 投影）；
        # 新队列从空开始，深度与最近消息 id 都归零。
        self.persisted = await self.storage.get_session(session_id)
        if self.persisted:
            self.session["started_at"] = self.persisted.get("started_at")
        self.queue_depth = 0
        self.highlight_depth = 0
        self.last_message_id = 0
        # 新场次从"未暂停、无来源"开始；第一次 on_scene 会立刻按场景写入
        self.highlight_paused = False
        self.highlight_source = None
        self._touch("loop", "chat")
        self.state = STATE_STARTING
        self._stop_event.clear()
        self._wakeup.clear()
        self.last_scene = SCENE_ROOM_INTRO
        # 上一次开播的异常原因不跨场次留痕（dashboard 关心的是"这一次怎么样了"）
        self.last_error = None
        self._intro_gate = {
            "waiting_for": intro_scene,
            "neuro_scene": intro_scene,
            "polls": 0,
            "started_at": time.monotonic(),
            "timeout_s": START_READY_TIMEOUT_S,
        }
        self.publish({"type": "session_begin", "session_id": session_id, "channel": s.channel,
                      "conversation_id": conversation_id,
                      "title": meta_title, "category": meta_category, "tags": meta_tags})
        self._publish_state()
        # 合成器切到开场画面：开场视频此刻已经在 neuro iframe 里播放（POST /ui/intro 已触发），
        # 而 chat 不该盖在开场动画上（真实开场没有弹幕）。
        self.on_scene(SCENE_ROOM_INTRO)

        # 启动 chatbot 循环（本场标题/分区作为其消息模板的变量来源）
        self.chatbot.start_session(
            queue_row,
            self.on_new_message,
            self._announce_chatbot_message,
            metadata_provider=self.session_metadata,
            context_provider=self.template_context,
        )

        self._task = asyncio.create_task(self._main(), name="stream-loop")
        logger.info("stream started: session=%s conv=%s", session_id, conversation_id[:8])
        return self.session

    def intro_status(self) -> Optional[dict[str, Any]]:
        """开场闸门快照（供 /stream/status）：只在 starting 阶段有值。

        dashboard 靠它解释"为什么还在开播中"：卡在开场时能看到 neuro 当前到底停在
        哪个场景、已经轮询了多少次、距离硬上限还有多久。
        """
        gate = self._intro_gate
        if not gate or self.state != STATE_STARTING:
            return None
        out = {k: v for k, v in gate.items() if k != "started_at"}
        out["waited_s"] = round(max(0.0, time.monotonic() - float(gate.get("started_at", 0.0))), 1)
        out["remaining_s"] = round(
            max(0.0, float(gate.get("timeout_s", 0.0)) - (time.monotonic() - float(gate.get("started_at", 0.0)))),
            1,
        )
        return out

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
        if self.persisted is not None:
            self.persisted.update(
                {"title": new_title, "category": new_category, "tags": new_tags}
            )
        self._touch("metadata", "loop")
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
        await self._stop_scene_watch()
        if self._task is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(asyncio.shield(self._task), timeout=5)
        with contextlib.suppress(Exception):
            await self.chatbot.aclose()

    # ---------- 场景（SceneRunner 要的 SceneState 实现） ----------

    def set_scene(self, scene_id: str, duration: float) -> dict[str, Any]:
        """只写权威状态（广播由 SceneRunner 负责）；对外入口是 `on_scene`。"""
        self.last_scene = scene_id
        self._scene_duration = max(0.0, float(duration))
        self._scene_started_at = time.monotonic()
        self._touch("scene")
        return self.scene_snapshot()

    def scene_id(self) -> Optional[str]:
        return self.last_scene

    def scene_snapshot(self) -> dict[str, Any]:
        """给 /ui/events 的 snapshot：中途连接的画面按 elapsed 恢复场景脚本。"""
        if not self.last_scene:
            return {}
        return {
            "id": self.last_scene,
            "duration": round(self._scene_duration, 3),
            "elapsed": round(max(0.0, time.monotonic() - self._scene_started_at), 3),
        }

    def on_scene(self, scene_id: str) -> None:
        """切合成器场景：交给 SceneRunner（写状态 + 广播 + 排程该场景的后端脚本）。

        未知 id 只降级为"广播一个裸 id"：游戏场景 id 来自 GameHub、手动端点已自行
        校验，这里抛异常会把直播循环带崩；但一定要留日志，它说明场景数据不一致。
        切换成功后按该 stream 场景的 `highlight_messages` 更新高亮队列开关。
        """
        try:
            self._scenes.switch(scene_id)
        except scene_kit.SceneError as e:
            logger.warning("场景不在清单里，仅广播: %s (%s)", scene_id, e)
            self.set_scene(scene_id, 0.0)
            self.publish({"type": "scene_set", **self.scene_snapshot()})
        enabled = highlight_messages_enabled(scene_id)
        self._set_highlight_paused(not enabled, source="scene:stream")

    # ---------- 高亮队列暂停开关 ----------

    def _set_highlight_paused(self, paused: bool, source: Optional[str]) -> None:
        """写入开关（最后写入者生效）；状态页/模板现读 loop 的字段。"""
        paused = bool(paused)
        if paused == self.highlight_paused and source == self.highlight_source:
            return
        self.highlight_paused = paused
        self.highlight_source = source
        self._touch("loop")

    def set_highlight_paused(self, paused: bool, source: str = "manual") -> None:
        """人工/程序写入开关（人工 PUT /stream/highlight 走这里）。"""
        if self.session is None:
            raise StreamConflict("没有进行中的直播场次，无法切换高亮队列")
        self._set_highlight_paused(paused, source=source)

    async def _watch_neuro_scene(self) -> None:
        """订阅 neuro 状态总线，把 neuro 场景的 `highlight_messages` 同步到开关。

        - 首帧 snapshot 也应用；`current == null` 视为暂停（保守）；
        - 断线 / 非 200 / 解析失败：记录 `neuro_link`，3 秒后重连，**不终结直播**；
        - 从开播起运行（starting + live），`_finalize` 会取消它。
        """
        try:
            s = stream_settings.load()
        except RuntimeError:
            return
        url = getattr(s, "neuro_url", "")
        if not url:
            return
        while not self._stop_event.is_set():
            client = NeuroClient(url)
            subscribe = getattr(client, "state_events", None)
            if subscribe is None:  # 旧替身/极旧客户端：没有这条通道就安静退出
                return
            events = subscribe(want=("scene",))
            try:
                async for ev in events:
                    if self._stop_event.is_set():
                        return
                    current = _scene_current_from_state_event(ev)
                    if current is _NO_SCENE:
                        continue
                    self.note_neuro_link(url, True)
                    if current is None:
                        self._set_highlight_paused(True, source="scene:neuro")
                        continue
                    flag = current.get("highlight_messages")
                    enabled = flag if isinstance(flag, bool) else True
                    self._set_highlight_paused(not enabled, source="scene:neuro")
            except asyncio.CancelledError:
                raise
            except Exception as e:  # 连接中断/非 200/解析失败都走重连
                self.note_neuro_link(url, False, f"{type(e).__name__}: {e}")
                logger.warning("neuro 场景订阅失败（%.0fs 后重连）: %s", NEURO_SCENE_RETRY_S, e)
            finally:
                aclose = getattr(events, "aclose", None)
                if aclose is not None:
                    with contextlib.suppress(Exception):
                        await aclose()
            if self._stop_event.is_set():
                return
            await self._sleep_ms(NEURO_SCENE_RETRY_S * 1000)

    async def _stop_scene_watch(self) -> None:
        task = self._scene_watch_task
        self._scene_watch_task = None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    def on_new_message(self) -> None:
        """入列唤醒：静默等待模式下立刻推进下一轮。"""
        if self.state == STATE_LIVE:
            self._wakeup.set()

    # ---------- 入列（普通 / 高亮共用广播与唤醒） ----------

    def _announce_message(
        self,
        msg_id: int,
        username: str,
        content: str,
        *,
        highlight_type: Optional[str] = None,
        value: int = 0,
    ) -> None:
        """把新入列消息镜像给 /ui（聊天层；高亮消息额外带类型与金额）。"""
        ev: dict[str, Any] = {
            "type": "highlight_message" if highlight_type else "chat_message",
            "id": msg_id,
            "username": username,
            "content": content,
        }
        if highlight_type:
            ev["highlight_type"] = highlight_type
            ev["value"] = value
        self.publish(ev)

    def _announce_chatbot_message(self, msg_id: int, username: str, content: str) -> None:
        """Chatbot 直接写库后的广播回调（不含唤醒，批量入列由 chat 循环统一触发）。"""
        # chatbot 绕过 append_message 直接写库，深度缓存要在同一条真实路径上补记
        self._note_appended(None, msg_id)
        self._announce_message(msg_id, username, content)

    def _note_appended(self, highlight_type: Optional[str], msg_id: int) -> None:
        """入列后的深度缓存（未消费数 + 最近消息 id）。"""
        if highlight_type:
            self.highlight_depth += 1
        else:
            self.queue_depth += 1
        self.last_message_id = max(self.last_message_id, int(msg_id))
        self._touch("chat")

    async def append_message(
        self,
        username: str,
        content: str,
        *,
        highlight_type: Optional[str] = None,
        value: int = 0,
    ) -> int:
        """API 入列：写库 → 广播 → 唤醒（静默等待模式）。"""
        if self.session is None:
            raise StreamConflict("当前没有进行中的直播场次，消息无处入列")
        msg_id = await self.storage.append_message(
            self.session["queue_row"],
            username,
            content,
            highlight_type=highlight_type,
            value=value,
        )
        self._announce_message(
            msg_id, username, content, highlight_type=highlight_type, value=value
        )
        self._note_appended(highlight_type, msg_id)
        self.on_new_message()
        return msg_id

    # ---------- 主循环 ----------

    async def _await_intro_ready(self) -> bool:
        """等 neuro 离开开场场景（= 开场放完）。返回 True 才允许进 live。

        判据是 neuro 的**权威场景状态**，不是本地倒计时：她换素材、调 `after` 余量、
        开场期间被人工切走场景，这里都自动跟上，stream 侧不存在任何秒数。

        主路径：订阅 `/state/events?want=scene`（状态变化 → 立刻放行）。
        兜底：订阅建立失败 / `INTRO_FALLBACK_S` 内没有消息 → 0.5s 轮询 `/status`
        （判据完全相同）。两条路径共用 `START_READY_TIMEOUT_S` 硬上限。

        三条退出路径：
          - 离开开场场景，或读不到场景（neuro 刚重启 / 场景数据缺失）→ True
          - stop_event → False（人工下播，静默）
          - 硬上限 → False + last_error（中止本次开播）
        读状态失败不单独终止：neuro 重启几秒是常见情况，硬上限兜住最坏情形。
        """
        gate = self._intro_gate or {}
        waiting_for = str(gate.get("waiting_for") or "")
        s = stream_settings.load()
        client = NeuroClient(s.neuro_url)
        deadline = time.monotonic() + START_READY_TIMEOUT_S
        gate.setdefault("ready_via", None)

        if self._stop_event.is_set():
            return False
        if not waiting_for:
            # 没有"要等离开的场景"：与轮询路径一致，第一次判定就放行
            gate["ready_via"] = "none"
            return True

        via_subscription = await self._await_intro_subscription(
            client, s, gate, waiting_for, deadline
        )
        if via_subscription is not None:
            return via_subscription
        return await self._poll_intro_ready(client, s, gate, waiting_for, deadline)

    async def _await_intro_subscription(
        self, client, settings, gate, waiting_for: str, deadline: float
    ) -> Optional[bool]:
        """订阅状态总线等场景变化；`None` = 该走轮询兜底。"""
        subscribe = getattr(client, "state_events", None)
        if subscribe is None:  # 旧替身/极旧客户端：直接兜底
            return None
        events = subscribe(want=("scene",))
        try:
            while True:
                if self._stop_event.is_set():
                    return False
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return self._intro_timeout(gate, waiting_for, None)
                kind, payload = await self._next_state_event(
                    events, min(INTRO_FALLBACK_S, remaining)
                )
                if kind == "stop":
                    return False
                if kind != "event":
                    # 超时（这段时间没有变化）/连接失败/流结束：幂等对账兜底；
                    # 但硬上限优先——别为了兜底多等出一个 fallback 窗口。
                    if time.monotonic() >= deadline:
                        return self._intro_timeout(gate, waiting_for, None)
                    return None
                gate["updates"] = int(gate.get("updates", 0)) + 1
                current = _scene_from_state_event(payload)
                if current is _NO_SCENE:
                    continue
                self.note_neuro_link(settings.neuro_url, True)
                gate["neuro_scene"] = current or None
                if current != waiting_for:
                    gate["ready_via"] = "sse"
                    return True
                if time.monotonic() >= deadline:
                    return self._intro_timeout(gate, waiting_for, None)
        finally:
            aclose = getattr(events, "aclose", None)
            if aclose is not None:
                with contextlib.suppress(Exception):
                    await aclose()

    async def _next_state_event(self, events, timeout: float):
        """等下一条状态帧，同时被 stop_event 即时打断（不能睡满 fallback 窗口）。"""
        next_task = asyncio.ensure_future(events.__anext__())
        stop_task = asyncio.ensure_future(self._stop_event.wait())
        try:
            done, _ = await asyncio.wait(
                {next_task, stop_task},
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                return "timeout", None
            if stop_task in done:
                return "stop", None
            try:
                return "event", next_task.result()
            except StopAsyncIteration:
                return "end", None
            except Exception:  # 连接中断/解析失败都走轮询兜底
                return "error", None
        finally:
            for task in (next_task, stop_task):
                if not task.done():
                    task.cancel()
            # 让取消真正传播进 async generator；否则后面的 aclose() 会撞上
            # "generator is already running"，SSE 连接跟着泄漏
            with contextlib.suppress(BaseException):
                await next_task

    async def _poll_intro_ready(
        self, client, settings, gate, waiting_for: str, deadline: float
    ) -> bool:
        """轮询兜底（订阅不可用/长时间没有推送时）：判据与订阅路径完全相同。"""
        status_error: Optional[str] = gate.get("status_error")
        while True:
            if self._stop_event.is_set():
                return False
            gate["polls"] = int(gate.get("polls", 0)) + 1
            try:
                st = await client.status(settings.channel)
                self.note_neuro_link(settings.neuro_url, True)
                scene = ((st.get("sections") or {}).get("scene") or {}).get("current")
                current = str((scene or {}).get("id") or "")
                gate["neuro_scene"] = current or None
                status_error = None
                gate.pop("status_error", None)
                if not waiting_for or current != waiting_for:
                    # 空场景也算"没停在开场"：不能因为读不到场景就一直等
                    gate["ready_via"] = "poll"
                    return True
            except (NeuroError, RuntimeError) as e:
                status_error = f"{type(e).__name__}: {e}"
                self.note_neuro_link(settings.neuro_url, False, status_error)
                gate["status_error"] = status_error

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return self._intro_timeout(gate, waiting_for, status_error)
            # 短睡且可被 stop 即时打断（睡满整个上限会让下播等十几秒）
            await self._sleep_ms(min(READY_POLL_INTERVAL_S, remaining) * 1000.0)

    def _intro_timeout(
        self, gate: dict, waiting_for: str, status_error: Optional[str]
    ) -> bool:
        self.last_error = {
            "code": "intro_timeout",
            "message": (
                f"neuro 停在开场场景超过 {START_READY_TIMEOUT_S:g}s，已中止本次开播"
            ),
            "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "detail": {
                "waiting_for": waiting_for,
                "neuro_scene": gate.get("neuro_scene"),
                "polls": gate.get("polls", 0),
                "updates": gate.get("updates", 0),
                "ready_via": gate.get("ready_via"),
                "status_error": status_error,
            },
        }
        logger.error(
            "开播中止：%s（neuro 场景=%s，轮询 %s 次，订阅帧 %s 个）",
            self.last_error["message"],
            gate.get("neuro_scene"),
            gate.get("polls", 0),
            gate.get("updates", 0),
        )
        return False

    async def _main(self) -> None:
        session = self.session
        try:
            # 从开播起就订阅 neuro 场景：intro 的 highlight_messages:false 也要生效，
            # 不能等 live 才开始（首帧 snapshot 会立刻落到 intro）。
            self._scene_watch_task = asyncio.create_task(
                self._watch_neuro_scene(), name="stream-neuro-scene"
            )
            if not await self._await_intro_ready():
                return  # 人工下播（静默）或开场超时（已记 last_error），都不进循环
            self.state = STATE_LIVE
            # 开场画面 → 常态画面：切 room 与推进 live 是同一个动作。广播 scene_set
            # 是后端推进画面的唯一手段（迟到/卡住的前端都会被这一帧纠正，
            # 没有前端也不影响循环）。
            self.on_scene(SCENE_ROOM)
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
            await self._finalize(self.last_error["code"] if self.last_error else None)

    async def _finalize(self, reason: Optional[str] = None) -> None:
        self.chatbot.stop_session()
        await self.games.aclear()
        await self._stop_scene_watch()
        was = self.state
        self.state = STATE_OFFLINE
        self._intro_gate = None
        if self.session is not None:
            await self.storage.update_session(
                self.session["session_row"], state="offline", ended=True
            )
            ev: dict[str, Any] = {"type": "session_end", "session_id": self.session["session_id"]}
            if reason:
                ev["reason"] = reason
            self.publish(ev)
            logger.info(
                "stream session ended (from %s%s)", was, f", reason={reason}" if reason else ""
            )
        self.session = None
        self._scenes.cancel()  # 作废在跑的场景后端脚本
        self.last_scene = None
        self._scene_duration = 0.0
        self.queue_depth = 0
        self.highlight_depth = 0
        self.highlight_paused = False
        self.highlight_source = None
        if self.persisted is not None:
            # 下播后 dashboard 仍要看到刚刚结束的那一场（state/ended_at 由 loop 自己维护）
            self.persisted.update(
                {"state": "offline", "ended_at": time.strftime("%Y-%m-%d %H:%M:%S")}
            )
        self._touch("loop", "chat", "scene")
        self._publish_state()

    async def _speech_ends_at(
        self, client: NeuroClient, channel: str, turn_id: Any
    ) -> Optional[float]:
        """读这一轮语音预测播完的 unix 时刻（来自 neuro 的权威时间轴）。

        必须在批量响应之后**立刻**读：`UiState.new_turn()` 会覆盖同 channel 的旧
        记录，晚读会拿到下一轮的时间轴。`turn_id` 不匹配时不猜，按无语音处理。
        """
        try:
            st = await self._fetch_neuro_status(client, channel, force=True)
        except Exception as e:  # 兜底：取数失败也不能把整轮打成异常
            logger.warning("neuro /status 读取异常，本轮按无语音处理: %s", e)
            return None
        if st is None:
            # 生成已经成功，状态读不到只意味着本轮无法等语音：退回 cooldown
            logger.warning("neuro /status 读取失败，本轮按无语音处理")
            return None
        speech = _neuro_turn(st)
        if not speech:
            return None
        reported_turn = speech.get("turn_id")
        if turn_id is not None and reported_turn is not None and reported_turn != turn_id:
            logger.warning(
                "neuro /status 已被别的轮次覆盖（期望 turn=%s，读到 %s），本轮不等语音",
                turn_id,
                reported_turn,
            )
            return None
        ends_at = speech.get("ends_at")
        try:
            return float(ends_at) if ends_at is not None else None
        except (TypeError, ValueError):
            return None

    async def _run_round(self, s: stream_settings.StreamSettings, session: dict) -> str | None:
        """跑一轮：抽取→拼装→生成消费→等语音播完 + cooldown。

        返回 "wait"（静默模式无消息）或 None（本轮已完成，可继续下一轮）。
        **节奏完全来自 neuro**：`text`/`terminal` 取批量响应，`ends_at` 取
        `GET /status`；stream 不再自己累加 step gap（那会和 `UiState` 的时间轴
        重复计算，且两边必须手工对齐）。
        """
        picked = await self._pick(s, session)
        if picked is None:
            return "wait"

        last_id = None
        highlights: Optional[list[dict]] = None
        client = NeuroClient(s.neuro_url)
        # 同轮模板用最近一次 neuro 投影；缓存超过 5s 才补拉一次（provider 不发 HTTP）
        await self._fetch_neuro_status(client, session["channel"])
        if picked["kind"] == "placeholder":
            # 空队列：{{chat_messages}} 换成占位指令（不推进 cursor）。
            # 不再单独维护"空队列模板"——少一条模板、少一个变量，行为也更直观。
            messages_block = picked["body"]
        else:
            msgs = picked["messages"]
            # 高亮消息与普通消息对 neuro 完全同格式：不带 type/value，不加标注；
            # 元数据只随 POST /chat 一起发（由 neuro 窗口弹浮层）。
            messages_block = "\n".join(f"- {m['username']}: {m['content']}" for m in msgs)
            last_id = msgs[-1]["id"]
            if picked["kind"] == "highlight":
                highlights = [
                    {
                        "id": m["id"],
                        "username": m["username"],
                        "content": m["content"],
                        "highlight_type": m["highlight_type"],
                        "value": m["value"],
                    }
                    for m in msgs
                ]
        # **先渲染、后推进水位线**：模板坏了就不该把弹幕吃掉。事实留在队列里，
        # 下一轮重试同一批；配合 `chatbot.template_error` 与日志，表现是
        # "她停摆且看得见为什么"，而不是"队列被清空、她一直沉默"。
        text = self.templates.render("neuro_user", self.template_context(messages_block))
        if last_id is not None:
            if picked["kind"] == "highlight":
                session["highlight_cursor"] = last_id
            else:
                session["cursor"] = last_id
        round_failed = False
        turn_text = ""
        ends_at: Optional[float] = None
        try:
            r = await asyncio.wait_for(
                client.chat_batch(
                    text,
                    session["channel"],
                    session["conversation_id"],
                    audio=False,
                    highlights=highlights,
                ),
                timeout=ROUND_DEADLINE_S,
            )
            terminal = str(r.get("terminal") or "")
            round_failed = terminal in ("cancelled", "error")
            turn_text = str(r.get("text") or "")
            if terminal not in ("done", "cancelled", "error"):
                logger.error("neuro 批量响应缺少合法 terminal: %r", terminal)
                round_failed = True
            elif terminal == "error":
                logger.error("neuro 轮次 error: %s", r.get("message"))
            # 必须立刻读：同 channel 的下一条记录会覆盖 UiState 里的这一轮
            ends_at = await self._speech_ends_at(client, session["channel"], r.get("turn_id"))
        except asyncio.TimeoutError:
            logger.error("neuro 轮次超过 %.0fs deadline，按失败处理", ROUND_DEADLINE_S)
            round_failed = True
        except NeuroError as e:
            logger.error("neuro 轮次失败: %s", e)
            round_failed = True

        # 本轮正文（内容平面）+ 最近一次状态投影（`_speech_ends_at` 刚刷新过）
        # 一起交给 chatbot 缓存，下一轮模板 / chatbot 都从这里读。
        self.chatbot.note_neuro_turn(turn_text, self._neuro_status_plain)

        if last_id is not None:
            logger.info(
                "round picked %s message id=%s",
                "highlight" if picked["kind"] == "highlight" else "normal",
                last_id,
            )
        session["rounds"] = session.get("rounds", 0) + 1
        await self.storage.update_session(
            session["session_row"],
            cursor_msg=session["cursor"],
            highlight_cursor=session["highlight_cursor"],
            inc_rounds=True,
        )
        # 游标已推进：未消费深度是"归属者现算后缓存"，/status 只读缓存不查库。
        self.queue_depth = await self.storage.queue_depth_after(
            session["queue_row"], session["cursor"]
        )
        self.highlight_depth = await self.storage.highlight_depth_after(
            session["queue_row"], session["highlight_cursor"]
        )
        if self.persisted is not None:
            self.persisted.update(
                {
                    "rounds": session["rounds"],
                    "cursor_msg": session["cursor"],
                    "highlight_cursor": session["highlight_cursor"],
                }
            )
        self._touch("loop", "chat")

        # 下一轮请求 = 语音播完 + cooldown。没有语音（ends_at 为 None）时从本轮
        # 结束开始计 cooldown——等价于旧实现的 had_speech=False 分支。
        if ends_at is not None:
            delay = ends_at - time.time()
            if delay > ROUND_DEADLINE_S:
                # 重启/时钟错乱给出的荒唐未来时刻：不能真的睡过去
                logger.error("neuro ends_at 距离现在 %.0fs（> deadline），按失败处理", delay)
                round_failed = True
                delay = 0.0
            await self._sleep_ms(max(0.0, delay) * 1000)
        await self._sleep_ms(s.speech_cooldown_ms)
        if round_failed:
            # 失败轮次加长退避，防毒上游时打爆网关
            await self._sleep_ms(3000)
        return None

    async def _pick(self, s, session: dict) -> Optional[dict]:
        """双轨抽取：高亮轨最早一条优先（一次只消费 1 条）；
        高亮轨为空才回落到普通轨随机抽样。暂停时不查高亮轨（游标不推进）。
        返回 None 表示静默等待。"""
        if not self.highlight_paused:
            highlight = await self.storage.list_highlight_messages(
                session["queue_row"], session["highlight_cursor"], limit=1
            )
            if highlight:
                return {"kind": "highlight", "messages": highlight}

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
