"""FastAPI 服务：POST /chat（SSE/批量）+ 内容/状态两条总线 + 窗口端点 + /manage。

两条总线的分工（详见 AGENTS）：
- `GET /chat/events`：**内容平面**（start/reasoning/content/highlight/speech/tool_*/usage/
  done/cancelled/error）。生成在后台任务里（`turns.TurnRunner`），连接只是观察者，
  断开不终止生成；首帧 snapshot 供中途连接续播。
- `GET /state/events`：**状态平面**（`state_kit.StateBoard` 的离散变化 + 首帧完整
  信封）。只推"发生了的变化"，不推 `elapsed`/`remaining_s` 这类时间流逝。

多渠道多会话：请求携带 channel + conversation_id（缺省时服务端生成并随 start 事件回传），
历史与记忆持久化在 <workdir>/neuro-sama/data.db（见 storage.py）。

本模块 = Agent + 独立的 Neuro 窗口：
- Agent 只处理输入并流出响应（含句级 TTS 的 speech 事件），不感知直播；
- /ui 前端负责背景 / 开场视频 / 立绘 / 字幕，可脱离直播单独运行；
- 直播循环、观众消息队列、场景与最终画面合成属于 stream 模块。

/manage 管理接口（由 vedal 模块代理给 dashboard 使用；配置管理在 vedal 统一处理）：
- POST /manage/reload：配置热重载（由 vedal 在配置保存/重载指令时调用）
- GET /manage/tools、PUT /manage/tools/{name}/enabled、POST /manage/tools/{name}/run
- /manage/mcp-servers CRUD + enabled（改动即时重建 registry/MCPManager）
- GET/DELETE /manage/sessions...  会话管理
- GET /manage/chats...、PUT/DELETE .../messages/{id}  对话记录管理
"""

import asyncio
import json
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from .. import log_kit, scene_kit, state_kit
from ..broadcaster import EventBroadcaster
from ..config import get_config
from . import tts, ui_media
from . import scenes as ui_scenes
from .agent import NeuroAgent
from .games import StreamGameProvider
from .mcp_client import MCPManager
from .memory import MemoryError, MemoryStore
from .persona import PLACEHOLDERS, PersonaError, PersonaStore
from .storage import Storage, seed_mcp_servers
from .turns import ActiveTurn, TurnConflict, TurnRunner
from .tools import ToolRegistry, build_builtin_tools
from .ui_scene import SceneLoopError, make_runner
from .ui_state import UiState, strip_speech_audio

_DIM = "\033[2m"
_CYAN = "\033[36m"
_MAGENTA = "\033[35m"
_YELLOW = "\033[33m"
_ORANGE = "\033[38;5;208m"
_RED = "\033[31m"
_RESET = "\033[0m"

# 内容总线只承载"发生过的事实"；场景/设置/清单属于状态平面（/state/events），
# 不在这一层。默认 = 全集，want/drop 只在序列化层裁剪。
CONTENT_EVENT_TYPES = (
    "start",
    "reasoning",
    "content",
    "highlight",
    "speech",
    "tool_start",
    "tool_result",
    "usage",
    "done",
    "cancelled",
    "error",
)

_TERMINAL_EVENTS = frozenset(("done", "cancelled", "error"))

# reload 取消在途生成后的有界等待：生成正常会在几百 ms 内以 cancelled 结束；
# 卡住的（LLM 不吐字节且不响应 cancel）不能把配置保存也拖死。
RELOAD_WAIT_S = 5.0

# play_head 定时器的余量：`turn_projection().speaking` 的阈值是 0.05s，
# 正好在 play_head 时刻唤醒会读到"还剩 0.00x 秒 → 还在说话"，白白丢一次翻转。
_PLAY_HEAD_EPS_S = 0.08


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _split(raw: str | None) -> list | None:
    """query 里的 `a,b` → 列表；空/缺省 → None。"""
    if not raw:
        return None
    items = [p.strip() for p in raw.split(",") if p.strip()]
    return items or None


def _content_names(want: list | None, drop: list | None) -> frozenset:
    """内容总线的类型过滤：先取 want、再减 drop，`*` = 全集。

    过滤只在序列化层做（总线上的事件永远是全量），所以同一条总线可以同时
    服务 audio=1 的 OBS 和 drop=reasoning,content 的窗口页。
    """
    names = set(CONTENT_EVENT_TYPES)
    if want is not None and "*" not in want:
        names &= set(want)
    if drop is not None:
        if "*" in drop:
            names.clear()
        else:
            names -= set(drop)
    return frozenset(names)


def _section_names(board: state_kit.StateBoard, want: list | None, drop: list | None) -> frozenset:
    """状态总线的分区过滤（与 `/status` 的 want/drop 同一语义）。"""
    names = set(board.section_names())
    if want is not None and "*" not in want:
        names &= set(want)
    if drop is not None:
        if "*" in drop:
            names.clear()
        else:
            names -= set(drop)
    return frozenset(names)


def _content_snapshot(ui_state: UiState, channel: str | None, turn_id: int | None) -> dict:
    """内容订阅的首帧：只给这一轮"已经说过什么/正在说什么"。

    `settings`/`scene` 属于状态平面，这里不再出现——窗口页另外挂
    `/state/events` 拿它们。
    """
    return {
        "type": "snapshot",
        "channel": channel,
        "turn": ui_state.content_snapshot(channel, turn_id),
    }


async def _content_events(
    ui_state: UiState,
    bus: EventBroadcaster,
    queue: asyncio.Queue,
    *,
    channel: str | None,
    turn_id: int | None,
    audio: bool,
    want: list | None,
    drop: list | None,
) -> AsyncIterator[str]:
    """`GET /chat/events` 的响应体（订阅已由调用方完成）。"""
    names = _content_names(want, drop)
    try:
        yield _sse("snapshot", _content_snapshot(ui_state, channel, turn_id))
        while True:
            try:
                ev = await asyncio.wait_for(queue.get(), timeout=15.0)
            except asyncio.TimeoutError:
                yield ": ping\n\n"
                continue
            ev_channel = ev.get("channel")
            if channel and ev_channel and ev_channel != channel:
                continue
            if turn_id is not None and ev.get("turn_id") != turn_id:
                continue
            if ev.get("type") not in names:
                continue
            # audio=0 只裁这一条连接的副本；总线上的事件与其它订阅者不受影响
            out = ev if audio else strip_speech_audio(ev)
            yield _sse(out.get("type", "message"), out)
    finally:
        bus.unsubscribe(queue)


async def _state_events(
    board: state_kit.StateBoard,
    bus: EventBroadcaster,
    queue: asyncio.Queue,
    *,
    want: list | None,
    drop: list | None,
) -> AsyncIterator[str]:
    """`GET /state/events` 的响应体（订阅已由调用方完成）。"""
    names = _section_names(board, want, drop)
    try:
        # 首帧是**完整** envelope：订阅者拿它做基线，后续再按 want/drop 收增量
        yield _sse("snapshot", board.read())
        while True:
            try:
                ev = await asyncio.wait_for(queue.get(), timeout=15.0)
            except asyncio.TimeoutError:
                yield ": ping\n\n"
                continue
            payload = _filter_sections(ev, names)
            # 这一帧变化的分区全被这个订阅者 drop 掉了 → 不发空帧（rev 跳号由
            # 客户端回读 /status 对账；它本来就不关心这些分区）
            if payload["sections"]:
                yield _sse("state", payload)
    finally:
        bus.unsubscribe(queue)


def _filter_sections(envelope: dict, names: frozenset) -> dict:
    sections = {
        key: value
        for key, value in (envelope.get("sections") or {}).items()
        if key in names
    }
    return {**envelope, "sections": sections}


def build_state_bus(board: state_kit.StateBoard) -> EventBroadcaster:
    """把 board 的变更接到一条广播总线（启动时调用一次）。

    `note_change` 是同步的，这里同步算好"变了的分区"再 publish；provider 都
    同步无 I/O，所以这一步不会阻塞生成。
    """

    bus = EventBroadcaster()

    def publish(rev: int, names: tuple) -> None:
        fields = list(names) or None  # 没报名字 = 全部分区（宁多勿缺）
        snapshot = board.read(want=fields)
        bus.publish({"rev": rev, "ts": snapshot["ts"], "sections": snapshot["sections"]})

    board.set_listener(publish)
    return bus


class TurnSignal:
    """`turn.speaking` 的 true→false：按 `play_head` 到点喊一次状态变化。

    这不是新事件类型（`turn_drained` 不存在）：它只是状态投影里 `speaking`
    从 true 变 false 的那一次 `note_change("turn")`。新句到达就重排，轮次结束
    就取消；否则状态总线会停在一个过期的 speaking=true 上。
    """

    def __init__(self, board: state_kit.StateBoard, ui_state: UiState) -> None:
        self._board = board
        self._ui_state = ui_state
        # 每个 channel 一个句柄：web 与 stream 可能同时有一轮在跑，
        # 全局单句柄会让后到的句子取消掉另一个 channel 的翻转定时器
        self._handles: dict[str, asyncio.TimerHandle] = {}

    def cancel(self, channel: str) -> None:
        handle = self._handles.pop(channel, None)
        if handle is not None:
            handle.cancel()

    def cancel_all(self) -> None:
        for channel in list(self._handles):
            self.cancel(channel)

    def reschedule(self, channel: str) -> None:
        self.cancel(channel)
        head = self._ui_state.turn_play_head(channel)
        if head is None:
            return
        delay = head - time.monotonic() + _PLAY_HEAD_EPS_S
        if delay <= 0:
            # 已经播完（极短句/测试里的假时钟）：立刻翻一次
            self._board.note_change("turn")
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # 没有循环（脚本/单测直接调 observe）时不排
            return
        # call_at 用 monotonic 基准（与 UiState.play_head 同一时钟）
        self._handles[channel] = loop.call_at(head + _PLAY_HEAD_EPS_S, self._fire, channel)

    def _fire(self, channel: str) -> None:
        self._handles.pop(channel, None)
        self._board.note_change("turn")


def make_observer(board: state_kit.StateBoard, ui_state: UiState, signal: TurnSignal):
    """agent 产出侧记账 + 派生状态变化的通知（启动时建一个）。

    记账仍然只有 `UiState` 一处；这里只在"状态投影真的会变"的事件上喊
    `note_change`——content 逐字 delta 不进状态投影，喊它只会把总线变成洪水。
    """

    def observe(ev: dict, channel: str, turn_id: int) -> None:
        ui_state.observe(ev, channel, turn_id)
        typ = ev.get("type")
        if typ == "speech":
            board.note_change("turn")
            signal.reschedule(channel)
        elif typ in _TERMINAL_EVENTS:
            board.note_change("turn")
            # 生成结束 ≠ 语音播完（批量/短生成很常见）：这里不能 cancel，
            # 否则 speaking 会永远停在 true。reschedule 会在 play_head 到点时
            # 再推一次 true→false（已经播完就立刻推，多一帧无害）。
            signal.reschedule(channel)

    return observe


def _batch_payload(turn: ActiveTurn, audio: bool) -> dict:
    """批量响应的交付副本：结果存全量（含 audio），这里按调用方裁。"""
    payload = turn.result or {
        "turn_id": turn.turn_id,
        "channel": turn.channel,
        "conversation_id": turn.conversation_id,
        "terminal": "error",
        "message": "generation result missing",
        "text": "",
        "segments": [],
    }
    if audio:
        return payload
    return {
        **payload,
        "segments": [
            {key: value for key, value in seg.items() if key != "audio"}
            for seg in payload.get("segments") or []
        ],
    }


async def _chat_sse(
    turn: ActiveTurn,
    bus: EventBroadcaster,
    queue: asyncio.Queue,
    *,
    audio: bool,
) -> AsyncIterator[str]:
    """`POST /chat {stream:true}` 的响应体：只转发**这一轮**的内容事件。

    订阅由 handler 在 spawn 之前完成（否则 start 会漏），这里只负责消费与裁剪。
    """
    try:
        while True:
            try:
                ev = await asyncio.wait_for(queue.get(), timeout=15.0)
            except asyncio.TimeoutError:
                # 生成可能已经结束但终结帧丢了（队列满）；别把连接永远挂着
                if turn.done_event.is_set():
                    return
                continue
            if ev.get("turn_id") != turn.turn_id:
                continue
            out = ev if audio else strip_speech_audio(ev)
            yield _sse(out.get("type", "message"), out)
            if out.get("type") in _TERMINAL_EVENTS:
                return
    finally:
        bus.unsubscribe(queue)




def _terminal(event: str, data: dict) -> None:
    """把 agent 事件镜像到服务端终端（测试阶段观察用）。"""
    if event == "reasoning":
        print(f"{_MAGENTA}{data['delta']}{_RESET}", end="", flush=True)
    elif event == "content":
        print(data["delta"], end="", flush=True)
    elif event == "tool_start":
        args = json.dumps(data.get("arguments"), ensure_ascii=False)
        print(f"\n{_YELLOW}[tool] {data['name']}({args}){_RESET}", flush=True)
    elif event == "tool_result":
        mark = "ok" if data["ok"] else "ERROR"
        result = data["result"] if len(data["result"]) <= 300 else data["result"][:300] + "…"
        print(f"{_ORANGE}    {mark}: {result} ({data['duration_ms']}ms){_RESET}", flush=True)
    elif event == "speech":
        audio = "🔊" if data.get("audio") else "🔇"
        print(
            f"\n{_DIM}[speech#{data['index']} {audio} {data['duration']:.2f}s] {data['text'][:60]}{_RESET}",
            flush=True,
        )
    elif event == "usage":
        print(
            f"{_DIM}[step {data['step']} tokens in={data['input_tokens']} out={data['output_tokens']}]{_RESET}",
            flush=True,
        )
    elif event == "done":
        print(flush=True)
        mem = data.get("_memory")
        if mem:
            print(
                f"{_DIM}    memory: {mem['count']} entries ({mem['used']}/{mem['char_limit']} chars){_RESET}",
                flush=True,
            )
    elif event == "error":
        print(flush=True)
        print(f"{_RED}[error] {data['message']}{_RESET}", flush=True)


def _core_block_dict(block) -> dict:
    return {
        "id": block.id,
        "title": block.title,
        "content": block.content,
        "created_at": block.created_at,
        "updated_at": block.updated_at,
    }


def _persona_core_stats(persona: PersonaStore) -> dict:
    return {
        "core": {
            "blocks": persona.list_core(),
            "count": len(persona.blocks),
            "used": persona.core_used,
            "is_default": persona.is_default_core,
        }
    }


# ---------- /status 的分区 provider（同步、无 I/O，组合 UiState 与场景清单） ----------


def _scene_current(ui_state: UiState) -> dict | None:
    """当前场景 + 清单里的 label/desc/icon（场景清单是唯一权威）。"""
    snap = ui_state.scene_snapshot()
    sid = str(snap.get("id") or "")
    if not sid:
        return None
    try:
        label = ui_scenes.label(sid)
        desc = ui_scenes.desc(sid)
        scene = ui_scenes.find(sid)
        icon = scene.get("icon") if scene else None
        # 只读清单里的脚本声明，不解析时长（后者可能触发视频轨道探测 = I/O）
        script = (scene or {}).get("state_script") or {}
        auto_returns = bool(script.get("steps"))
        flag = (scene or {}).get("highlight_messages")
        highlight_messages = flag if isinstance(flag, bool) else True
    except ui_scenes.SceneError:
        # 场景数据坏了不拖垮状态投影：用 id 兜底，其余字段留空
        label, desc, icon, auto_returns = sid, "", None, False
        highlight_messages = True
    return {
        "id": sid,
        "label": label,
        "desc": desc,
        "icon": icon,
        "duration": snap.get("duration"),
        "elapsed": snap.get("elapsed"),
        "auto_returns": auto_returns,
        "highlight_messages": highlight_messages,
    }


def _layers_current(ui_state: UiState) -> list:
    """当前场景的元素层（visible 来自清单）。

    服务端没有运行时的"父页面隐藏集合"权威（隐藏由画面渲染器按自身页面决定），
    所以这里不臆造 `hidden_by_parent`——只报清单里的可见性。
    """
    sid = str(ui_state.scene_snapshot().get("id") or "")
    if not sid:
        return []
    try:
        for scene in ui_scenes.scenes():
            if scene.get("id") == sid:
                return [
                    {
                        "id": str(e.get("id")),
                        "name": e.get("name") or str(e.get("id")),
                        "visible": e.get("visible", True),
                    }
                    for e in scene.get("elements") or []
                ]
    except ui_scenes.SceneError:
        return []
    return []


def _model_current() -> dict:
    """与 /health 同一个判断（`cfg.neuro_llm() is not None`），不复制规则。"""
    try:
        service_ok = get_config().neuro_llm() is not None
    except RuntimeError:
        service_ok = False
    return {"service_ok": service_ok}


def _tts_current() -> dict:
    """复用 `tts.tts_state()`（/health 用的同一个函数）。"""
    state = tts.tts_state()
    return {
        "effective_provider": state.get("effective_provider"),
        "degraded": bool(state.get("degraded")),
    }


def _neuro_scene_graph() -> dict:
    """states 从场景清单现生成；transitions 是手写小表。"""
    try:
        states = [
            {"id": s["id"], "label": s.get("label") or s["id"], "desc": s.get("desc") or ""}
            for s in ui_scenes.scenes()
        ]
    except ui_scenes.SceneError:
        states = []
    return {
        "states": states,
        "transitions": [
            {"from": "intro", "to": "normal", "by": "intro state_script reaches its end"},
            {"from": "normal", "to": "intro", "by": "POST /ui/intro"},
        ],
    }


def build_status_board(ui_state: UiState, ctx: dict) -> state_kit.StateBoard:
    """装配 neuro 的分区（启动时调用一次；测试可直接调）。"""
    board = state_kit.StateBoard("neuro_sama")
    board.register("scene", lambda: _scene_current(ui_state), graph=_neuro_scene_graph())
    board.register("turn", lambda: ui_state.turn_projection(ctx["channel"]))
    board.register("layers", lambda: _layers_current(ui_state))
    board.register("model", _model_current)
    board.register("tts", _tts_current)
    # 两个字幕参数：复用 `UiState.settings()`（配置的唯一读取点），不另存
    board.register("caption", lambda: ui_state.settings()["caption"])
    # 清单元信息：复用 `SceneStore.source()`（与 GET /ui/manifest/source 同一函数）
    board.register("manifest", lambda: ui_scenes.manifest_source())
    return board


class HighlightPayload(BaseModel):
    """随 `POST /chat` 一起发来的高亮消息元数据（仅用于窗口浮层，不进 prompt）。"""

    id: int | None = None
    username: str = "viewer"
    content: str = Field(min_length=1)
    highlight_type: Literal["bits", "points"]
    value: int = Field(default=0, ge=0)


class ChatRequest(BaseModel):
    message: str
    channel: str = "web"
    conversation_id: str | None = None  # None 时服务端生成并随 start 事件回传
    # 只影响返回给调用方的载荷，绝不影响是否合成（duration 来自合成）
    audio: bool = True
    # true=SSE（默认，dashboard/test_chat 的现有行为）；false=生成结束后返回批量 JSON
    stream: bool = True
    # 高亮消息（stream 注入时带上）：空 = 普通轮，不产生 highlight 内容事件
    highlights: list[HighlightPayload] = Field(default_factory=list)


class MessagePayload(BaseModel):
    message: dict  # 完整 OpenAI 消息 dict（必须含 role）


class RenamePayload(BaseModel):
    title: str


class MemoryPayload(BaseModel):
    content: str


class PromptPayload(BaseModel):
    content: str


class ManifestPayload(BaseModel):
    """场景清单（整份 JSON 文本；服务端校验后存进模块自己的 DB）。"""

    content: str


class PreviewPayload(BaseModel):
    content: str | None = None  # 可选：预览未保存的草稿


class CoreMemoryPayload(BaseModel):
    title: str
    content: str


class ScenePayload(BaseModel):
    id: str  # 目标场景 id（来自 neuro_scenes.json）


class ToolEnabledPayload(BaseModel):
    enabled: bool


class ToolRunPayload(BaseModel):
    # 刻意用 Any 而不是 dict：非对象要按任务契约回 400（Pydantic 默认是 422）
    arguments: Any = None


class MCPServerPayload(BaseModel):
    name: str
    transport: Literal["stdio", "http", "sse"] | None = None
    command: str | None = None
    args: list[str] | None = None
    env: dict[str, str] | None = None
    url: str | None = None
    # 通用认证头（如 Authorization: Bearer ... / 自定义 API key）；仅 http/sse。
    # 明文存储/回显，与 env、server.llm_services.api_key 同先例（编辑要回填）。
    headers: dict[str, str] | None = None
    enabled: bool = True


def _transport_of(server: dict) -> str:
    transport = server.get("transport")
    if transport in ("stdio", "http", "sse"):
        return str(transport)
    return "stdio" if server.get("command") else "http"


def _mcp_servers_from_status(
    servers: list[dict], statuses: list[dict]
) -> list[dict]:
    """DB 文档 + 最近一次连接结果合并成管理端点形状。

    DB 里有、但当前没连上的 server（禁用/失败/被跳过）也必须出现，
    connected=False；反过来连接结果不会凭空造出 DB 里没有的条目。
    """
    by_name = {s.get("name"): s for s in statuses}
    out: list[dict] = []
    for server in servers:
        name = str(server.get("name") or "")
        status = by_name.get(name) or {}
        transport = _transport_of(server)
        row: dict[str, Any] = {
            "name": name,
            "transport": transport,
            "enabled": bool(server.get("enabled", True)),
            "connected": bool(status.get("connected", False)),
            "tool_count": int(status.get("tool_count") or 0),
            "error": status.get("error"),
        }
        if transport == "stdio":
            row["command"] = server.get("command") or ""
            row["args"] = list(server.get("args") or [])
            if server.get("env"):
                row["env"] = dict(server["env"])
        else:
            row["url"] = server.get("url") or ""
            if server.get("headers"):
                row["headers"] = dict(server["headers"])
        out.append(row)
    return out


async def _tools_payload(app: FastAPI) -> dict:
    """`GET /manage/tools` 的全量载荷（所有变更端点也返回它，前端直接替换）。"""
    registry: ToolRegistry = app.state.agent.registry
    disabled = registry.disabled
    tools = []
    for name in registry.all_names():
        tool = registry.get(name)
        if tool is None:  # 理论上不会发生；防御性跳过
            continue
        tools.append(
            {
                "name": tool.name,
                "origin": tool.origin,
                "game": tool.game,
                "phase": tool.phase,
                "description": tool.description,
                "parameters": tool.parameters,
                "enabled": name not in disabled,
            }
        )
    storage: Storage = app.state.storage
    servers = await storage.get_mcp_servers() or []
    mcp: MCPManager = app.state.mcp
    return {
        "tools": tools,
        "mcp_servers": _mcp_servers_from_status(servers, mcp.server_status),
        "tool_timeout_s": get_config().TOOL_TIMEOUT,
    }


async def _build_registry(memory: MemoryStore, storage: Storage) -> ToolRegistry:
    """内置工具 + DB 里的禁用集（MCP 工具由 MCPManager.start 追加）。"""
    registry = ToolRegistry()
    for tool in build_builtin_tools(memory):
        registry.add(tool)
    settings = await storage.get_tool_settings()
    registry.set_disabled({name for name, enabled in settings.items() if not enabled})
    return registry


async def _enabled_mcp_servers(storage: Storage) -> list[dict]:
    servers = await storage.get_mcp_servers() or []
    return [s for s in servers if s.get("enabled", True)]


async def _rebuild_mcp(app: FastAPI, memory: MemoryStore) -> None:
    """按 DB 重建 registry + MCPManager（MCP 增删改/启停后调用）。

    先建好新对象再替换、最后关旧的：直播中的一轮即使正持有旧 registry 也能
    把当前 step 跑完；下一 step / 下一轮才会看到新工具集。

    串行锁：两个管理端点几乎同时重建时会交错（各自 start 一套连接、旧 manager
    的 close 只看得到已被覆盖的引用），结果是一套没人 close 的 MCPManager
    泄漏 owner task。单事件循环 + asyncio.Lock 即足够。
    """
    lock: asyncio.Lock | None = getattr(app.state, "mcp_rebuild_lock", None)
    if lock is None:
        lock = asyncio.Lock()
        app.state.mcp_rebuild_lock = lock
    async with lock:
        storage: Storage = app.state.storage
        registry = await _build_registry(memory, storage)
        mcp = MCPManager(await _enabled_mcp_servers(storage))
        for line in await mcp.start(registry):
            print(line, flush=True)
        app.state.agent.registry = registry
        old_mcp: MCPManager = app.state.mcp
        app.state.mcp = mcp
        await old_mcp.close()


def _validate_mcp_server(
    payload: MCPServerPayload, *, path_name: str | None, existing: list[dict]
) -> dict:
    """校验并规范化一条 MCP server 配置；不合法抛 HTTPException(400/409)。"""
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="MCP server name 不能为空")
    if path_name is not None and name != path_name:
        raise HTTPException(
            status_code=409,
            detail="不支持改名：请新建一条再删除旧的",
        )

    transport = payload.transport
    if transport is None:
        transport = "stdio" if payload.command else ("http" if payload.url else "")
    if transport not in ("stdio", "http", "sse"):
        raise HTTPException(
            status_code=400,
            detail="必须提供 command（stdio）或 url（http/sse）之一",
        )

    # 空 dict 与 None 等价，不算"给了 headers"
    if transport == "stdio" and payload.headers:
        raise HTTPException(status_code=400, detail="stdio 不支持 headers")

    headers: dict[str, str] = {}
    for key, value in (payload.headers or {}).items():
        k = key.strip()
        if not k:
            raise HTTPException(status_code=400, detail="headers 的键不能为空")
        if ":" in k:
            raise HTTPException(status_code=400, detail="headers 的键不能包含 ':'")
        # 值只 strip 两侧空白；冒号保留（Bearer x:y 合法）
        headers[k] = value.strip()

    old = next((s for s in existing if s.get("name") == name), None)
    if path_name is None and old is not None:
        raise HTTPException(status_code=409, detail=f"MCP server {name!r} 已存在")

    doc: dict[str, Any] = {"name": name, "transport": transport, "enabled": bool(payload.enabled)}
    if transport == "stdio":
        command = (payload.command or "").strip()
        if not command:
            raise HTTPException(status_code=400, detail="stdio server 必须提供 command")
        args = payload.args or []
        if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
            raise HTTPException(status_code=400, detail="args 必须是字符串数组")
        env = payload.env or {}
        if not isinstance(env, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in env.items()
        ):
            raise HTTPException(status_code=400, detail="env 必须是字符串到字符串的对象")
        doc["command"] = command
        doc["args"] = args
        doc["env"] = env
    else:
        url = (payload.url or "").strip()
        if not url:
            raise HTTPException(status_code=400, detail=f"{transport} server 必须提供 url")
        doc["url"] = url
        if headers:
            doc["headers"] = headers
    return doc


async def _save_mcp_servers(app: FastAPI, servers: list[dict]) -> None:
    await app.state.storage.upsert_mcp_servers(servers)
    await _rebuild_mcp(app, app.state.memory)


async def _hot_reload(app: FastAPI) -> None:
    """配置热重载：重读 config.yaml，更新 LLM 客户端 / 记忆上限 / TTS 锁存。

    在途生成先**优雅终止并有界等待**：置位全部 cancel_event，等任务落库半截内容
    并以 cancelled 结束（最多 RELOAD_WAIT_S）。不这样做，旧 LLM 客户端被 close
    后仍在跑的请求会裸 error；无限等则会把配置保存拖死——超时就记日志继续，
    绝不让 reload 悬挂。MCP/工具清单已迁 DB，不在此处重建（见 /manage/tools*）。
    """
    turns = app.state.turns.cancel_all()
    if turns:
        finished = await app.state.turns.wait_all(turns, RELOAD_WAIT_S)
        if not finished:
            print(
                f"[neuro-sama] WARNING: {RELOAD_WAIT_S:g}s 内仍有在途生成未结束，"
                "继续 reload（它们可能以 error 收尾）",
                flush=True,
            )
    cfg = get_config()
    cfg.reload()
    log_kit.apply_levels(log_kit.resolve_level(cfg.module("neuro_sama")))
    agent: NeuroAgent = app.state.agent
    memory: MemoryStore = app.state.memory

    # 1) LLM 客户端（引用的服务变化时重建；引用悬空时客户端置空，chat 时明确报错）
    old_client = agent.client
    svc = cfg.neuro_llm()
    agent.client = (
        AsyncOpenAI(base_url=svc.base_url, api_key=svc.api_key, timeout=svc.timeout)
        if svc
        else None
    )
    if old_client is not None:
        await old_client.close()

    # 2) 记忆上限
    memory.char_limit = cfg.MEMORY_CHAR_LIMIT

    # 3) TTS：解除 azure 失败锁存，允许配置修复后重新探测
    tts.reset_latch()

    # MCP/工具集不参与 reload：清单已迁 DB，变更走 /manage/tools* 端点即时重建
    # registry + MCPManager（config 里的 mcp_servers 只在首次播种时读取）。

    print(
        f"[neuro-sama] hot reload: model={(svc.model if svc else cfg.NEURO_MODEL_REF) or cfg.NEURO_MODEL_REF or None!r} host={cfg.HOST}:{cfg.PORT} "
        f"tools={agent.registry.all_names()}",
        flush=True,
    )
    app.state.turn_signal.cancel_all()
    # 字幕参数来自 config；生效的 model/tts 也可能变（引用重建、TTS 解锁存）
    app.state.status_board.note_change("caption", "model", "tts")


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        cfg = get_config()

        # 媒体素材：包内那份是模板，复制到工作目录后**只从工作目录读**
        # （用户换开场视频不用动 site-packages；与 config.json 同一套做法）。
        ui_media.deploy_assets(cfg.DATA_DIR)

        storage = Storage(cfg.DB_PATH)
        await storage.init()
        # MCP 清单一次性从 config 播种进 DB；此后 DB 权威（含义见 storage.py）
        await seed_mcp_servers(storage, cfg.MCP_SERVERS)
        persona = PersonaStore(storage)
        await persona.load()
        memory = MemoryStore(storage, cfg.MEMORY_CHAR_LIMIT)
        await memory.load()
        # 场景清单：DB 里的那份是权威（包内 defaults/neuro_scenes.json 只做首次播种）
        scene_store = scene_kit.SceneStore(
            storage, ui_scenes._scenes_path(), ui_scenes._validate
        )
        await scene_store.load()
        ui_scenes.set_store(scene_store)

        registry = await _build_registry(memory, storage)
        mcp = MCPManager(await _enabled_mcp_servers(storage))
        for line in await mcp.start(registry):
            print(line, flush=True)

        svc = cfg.neuro_llm()
        client = (
            AsyncOpenAI(base_url=svc.base_url, api_key=svc.api_key, timeout=svc.timeout)
            if svc
            else None
        )
        # UiState 先于 agent 创建：agent 要在产出事件时直接记账（不依赖 HTTP 消费者）
        ui_state = UiState()
        # /status 的分区提供者：只读 UiState / 场景清单 / 配置，同步且无 I/O。
        # channel 是 query 级别的策略（机制在 state_kit），用 request 作用域变量传递。
        status_ctx = {"channel": None}
        board = build_status_board(ui_state, status_ctx)
        # 两条总线：内容（生成事件，全量含音频）与状态（离散变化，无音频/正文）
        state_bus = build_state_bus(board)
        content_bus = EventBroadcaster()
        turn_signal = TurnSignal(board, ui_state)

        agent = NeuroAgent(
            client,
            registry,
            storage,
            memory,
            persona,
            StreamGameProvider(),
            event_sink=make_observer(board, ui_state, turn_signal),
        )
        app.state.agent = agent
        app.state.memory = memory
        app.state.persona = persona
        app.state.storage = storage
        app.state.mcp = mcp
        app.state.content_bus = content_bus
        app.state.state_bus = state_bus
        app.state.ui_state = ui_state
        app.state.status_board = board
        app.state.status_channel = status_ctx
        app.state.turn_signal = turn_signal

        def on_turn_finish(turn: ActiveTurn) -> None:
            # 任务收尾兜底：即便 agent 没走 error 事件（异常/被取消），状态平面
            # 也要看到 generating=false；语音还没播完则由 reschedule 到点补一次。
            board.note_change("turn")
            turn_signal.reschedule(turn.channel)

        app.state.turns = TurnRunner(
            agent,
            ui_state,
            memory,
            content_bus,
            printer=_terminal,
            on_finish=on_turn_finish,
        )

        def publish_ui(ev: dict) -> None:
            # 场景切换会改变状态平面的 scene/layers 分区（没有别的总线了：
            # scene_set 不再是内容事件）
            if ev.get("type") == "scene_set":
                board.note_change("scene", "layers")

        app.state.ui_scene = make_runner(app.state.ui_state, publish_ui)
        try:
            scene_info = app.state.ui_scene.init_default()
            scene_names = [s["id"] for s in ui_scenes.overview()]
        except ui_scenes.SceneError as e:
            # 场景数据坏了不该拖垮整个模块：Agent/对话照常，窗口端点给出明确原因
            scene_info, scene_names = {}, []
            print(f"[neuro-sama] WARNING: 场景数据不可用: {e}", flush=True)
        print(
            f"[neuro-sama] ready: model_ref={cfg.NEURO_MODEL_REF!r} "
            f"model={svc.model if svc else None} db={cfg.DB_PATH} tools={registry.all_names()} "
            f"scene={scene_info.get('id')} scenes={scene_names}",
            flush=True,
        )
        yield
        turn_signal.cancel_all()
        await app.state.ui_scene.stop()
        if client is not None:
            await client.close()
        await mcp.close()
        await storage.close()

    app = FastAPI(title="neuro-sama", lifespan=lifespan)

    # ---------- 进程日志（内存 = 活体；磁盘历史见 vedal） ----------

    @app.get("/logs")
    async def logs(max_bytes: int = log_kit.TAIL_MAX_BYTES):
        """本进程此刻的输出尾部，读**内存环形缓冲**（只含本次运行）。

        不读磁盘文件：网页能打开的前提就是本进程活着，内存既更实时、又不会掺进上一次
        运行的内容。进程死掉之后再来看日志，走 vedal 的磁盘端点
        （`GET /manage/logs/neuro_sama`）。
        """
        log = log_kit.current("neuro_sama")
        if log is None:
            raise HTTPException(status_code=503, detail="日志未启用：本进程不是经 main() 启动的")
        return log.snapshot(log_kit.clamp_bytes(log, max_bytes))

    @app.get("/logs/events")
    async def logs_events(since: int = -1, max_bytes: int = log_kit.TAIL_MAX_BYTES):
        """SSE 跟随日志：首帧 `snapshot`,之后 `event: log` 增量。

        `?since=<seq>` 用于断线续播；那段已被环形缓冲挤掉时先收 `gap` 再收完整
        `snapshot`（整屏重来），不会静默丢帧——与 `/state/events` 的 `rev` 跳号同一条纪律。
        """
        log = log_kit.current("neuro_sama")
        if log is None:
            raise HTTPException(status_code=503, detail="日志未启用：本进程不是经 main() 启动的")
        return StreamingResponse(
            log_kit.sse_stream(
                log, since=since, max_bytes=log_kit.clamp_bytes(log, max_bytes)
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ---------- 健康检查 ----------

    @app.get("/health")
    async def health():
        cfg = get_config()
        svc = cfg.neuro_llm()
        return {
            "status": "ok",
            "module": "neuro_sama",
            "model": svc.model if svc else None,
            "model_service": cfg.NEURO_MODEL_REF or None,
            "model_service_ok": svc is not None,
            "tools": app.state.agent.registry.all_names(),
            "memory_entries": app.state.memory.count,
            "persona": app.state.persona.stats(),
            "tts": tts.tts_state(),
        }

    # ---------- 状态平面（读取 + 订阅） ----------

    @app.get("/status")
    async def status(request: Request):
        """状态机投影：`{module, rev, ts, sections}`（**不含旧扁平键**）。

        分区：`scene`（当前画面）、`turn`（本轮节奏）、`layers`（元素层）、
        `model`（LLM 服务是否可用）、`tts`（生效的合成器）、`caption`（字幕
        参数）、`manifest`（场景清单元信息）。全部 provider 同步且无 I/O，可被
        别的模块每半秒拉一次；音频/正文只在内容平面。

        `?channel=` 只影响 `turn`（每个 channel 至多一条在途轮次）；`want`/`drop`
        是分区级过滤（逗号分隔，`*`=全部），只影响输出。
        """
        params = request.query_params
        channel = (params.get("channel") or "").strip() or None
        app.state.status_channel["channel"] = channel
        return app.state.status_board.read(
            want=_split(params.get("want")), drop=_split(params.get("drop"))
        )

    @app.get("/state/events")
    async def state_events(request: Request):
        """状态总线：首帧完整 envelope，之后只推**离散变化**的分区。

        `event: state` 的 `sections` 只含这一帧变化的分区（客户端按分区名合并）；
        `rev` 跳号说明中间有帧被丢（总线尽力而为）→ 回读 `GET /status` 对账。
        `elapsed`/`remaining_s` 这类随秒增长的量不推，消费者自己按 `duration`/
        `ends_at` 推演。
        """
        params = request.query_params
        want = _split(params.get("want"))
        drop = _split(params.get("drop"))
        bus: EventBroadcaster = app.state.state_bus
        queue = bus.subscribe()
        return StreamingResponse(
            _state_events(
                app.state.status_board, bus, queue, want=want, drop=drop
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ---------- 内容平面（生成 + 订阅） ----------

    @app.post("/chat")
    async def chat(req: ChatRequest):
        message = req.message.strip()
        if not message:
            raise HTTPException(status_code=400, detail="message must not be empty")
        conversation_id = req.conversation_id or uuid.uuid4().hex
        channel = req.channel
        runner: TurnRunner = app.state.turns

        print(f"\n{_CYAN}you ({channel}:{conversation_id[:8]}) » {message}{_RESET}", flush=True)

        # 同会话并发流会导致历史消息交错落库、/stop 误投，直接拒绝。检查与登记
        # 之间不能有 await（单事件循环内原子）；新会话（服务端生成 id）不受限。
        if runner.find(channel, conversation_id) is not None:
            raise HTTPException(
                status_code=409,
                detail="this conversation already has a generation in progress",
            )
        try:
            turn = runner.register(
                channel,
                conversation_id,
                highlights=[h.model_dump() for h in req.highlights],
            )
        except TurnConflict as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        # 新轮开始：这个 channel 旧的 play_head 定时器作废，状态平面推一次 turn
        app.state.turn_signal.cancel(channel)
        app.state.status_board.note_change("turn")

        if not req.stream:
            # 批量模式：同一个后台任务，调用方只是等结果。请求侧断开/取消不会
            # 传播到任务（任务没有被 await，只被 done_event 等待）。
            runner.spawn(turn, message)
            await turn.done_event.wait()
            return JSONResponse(_batch_payload(turn, req.audio))

        # 先订阅再 spawn：反过来的话 start 事件可能在订阅建立前就发完了
        bus: EventBroadcaster = app.state.content_bus
        queue = bus.subscribe()
        runner.spawn(turn, message)
        return StreamingResponse(
            _chat_sse(turn, bus, queue, audio=req.audio),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/chat/events")
    async def chat_events(request: Request):
        """内容总线：某个 channel / 某一轮的生成事件（**含音频**）。

        事件类型：start / reasoning / content / highlight / speech / tool_start /
        tool_result / usage / done / cancelled / error。`highlight` 只在高亮消息随
        `POST /chat` 注入时出现（`start` 之后、正文之前），带 `messages`；浮层是瞬时
        动画，snapshot 不存它（重连不续播）。

        默认全集；`want`/`drop` 只在序列化层过滤（例如窗口页 drop 掉
        reasoning/content，只要字幕与动作）。`audio=0` 只剥这一条连接的
        `audio` 键——总线上的事件永远是全量，否则 OBS 中途重载会续播不出声。
        """
        params = request.query_params
        channel = (params.get("channel") or "").strip() or None
        raw_turn = (params.get("turn_id") or "").strip()
        try:
            turn_id = int(raw_turn) if raw_turn else None
        except ValueError as e:
            raise HTTPException(status_code=400, detail="turn_id must be an integer") from e
        audio = (params.get("audio") or "1").lower() not in ("0", "false", "no")
        bus: EventBroadcaster = app.state.content_bus
        queue = bus.subscribe()
        return StreamingResponse(
            _content_events(
                app.state.ui_state,
                bus,
                queue,
                channel=channel,
                turn_id=turn_id,
                audio=audio,
                want=_split(params.get("want")),
                drop=_split(params.get("drop")),
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ---------- /manage：热重载 + 会话/对话记录管理（配置管理由 vedal 统一处理） ----------

    @app.post("/manage/reload")
    async def manage_reload():
        """配置热重载（由 vedal 在配置保存/重载指令时调用）。"""
        await _hot_reload(app)
        return {"status": "ok", "reloaded": True}

    # ---------- /manage：工具开关 / 手动运行 / MCP CRUD ----------

    @app.get("/manage/tools")
    async def manage_get_tools():
        """全量工具（含禁用与当前动态不可见的游戏工具）+ MCP server 状态。"""
        return await _tools_payload(app)

    @app.put("/manage/tools/{name}/enabled")
    async def manage_set_tool_enabled(name: str, payload: ToolEnabledPayload):
        """按暴露名开关工具；落库后即时替换 registry 的禁用集（下一步生成生效）。"""
        registry: ToolRegistry = app.state.agent.registry
        if registry.get(name) is None:
            raise HTTPException(status_code=404, detail=f"unknown tool {name!r}")
        storage: Storage = app.state.storage
        await storage.set_tool_enabled(name, payload.enabled)
        settings = await storage.get_tool_settings()
        registry.set_disabled({n for n, enabled in settings.items() if not enabled})
        print(f"[neuro-sama] tool {name}: {'enabled' if payload.enabled else 'disabled'}", flush=True)
        return {"status": "ok", **await _tools_payload(app)}

    @app.post("/manage/tools/{name}/run")
    async def manage_run_tool(name: str, payload: ToolRunPayload):
        """手动运行（调试通道）：不受工具开关限制，参数错误作为结果文本返回。"""
        registry: ToolRegistry = app.state.agent.registry
        if registry.get(name) is None:
            raise HTTPException(status_code=404, detail=f"unknown tool {name!r}")
        args = payload.arguments if payload.arguments is not None else {}
        if not isinstance(args, dict):
            raise HTTPException(status_code=400, detail="arguments 必须是 JSON 对象")
        started = time.perf_counter()
        ok, result = await registry.dispatch(name, args)
        duration_ms = int((time.perf_counter() - started) * 1000)
        print(
            f"[neuro-sama] manual tool {name} -> {'ok' if ok else 'error'} ({duration_ms}ms)",
            flush=True,
        )
        return {"ok": ok, "result": result, "duration_ms": duration_ms}

    @app.post("/manage/mcp-servers")
    async def manage_add_mcp_server(payload: MCPServerPayload):
        storage: Storage = app.state.storage
        servers = await storage.get_mcp_servers() or []
        doc = _validate_mcp_server(payload, path_name=None, existing=servers)
        servers.append(doc)
        await _save_mcp_servers(app, servers)
        print(f"[neuro-sama] MCP server added: {doc['name']}", flush=True)
        return {"status": "ok", **await _tools_payload(app)}

    @app.put("/manage/mcp-servers/{name}")
    async def manage_update_mcp_server(name: str, payload: MCPServerPayload):
        """整条替换（name 必须与路径一致；改名不支持 → 409）。"""
        storage: Storage = app.state.storage
        servers = await storage.get_mcp_servers() or []
        index = next((i for i, s in enumerate(servers) if s.get("name") == name), None)
        if index is None:
            raise HTTPException(status_code=404, detail=f"MCP server {name!r} 不存在")
        doc = _validate_mcp_server(payload, path_name=name, existing=servers)
        servers[index] = doc
        await _save_mcp_servers(app, servers)
        print(f"[neuro-sama] MCP server updated: {name}", flush=True)
        return {"status": "ok", **await _tools_payload(app)}

    @app.delete("/manage/mcp-servers/{name}")
    async def manage_delete_mcp_server(name: str):
        storage: Storage = app.state.storage
        servers = await storage.get_mcp_servers() or []
        kept = [s for s in servers if s.get("name") != name]
        if len(kept) == len(servers):
            raise HTTPException(status_code=404, detail=f"MCP server {name!r} 不存在")
        await _save_mcp_servers(app, kept)
        print(f"[neuro-sama] MCP server deleted: {name}", flush=True)
        return {"status": "ok", **await _tools_payload(app)}

    @app.put("/manage/mcp-servers/{name}/enabled")
    async def manage_set_mcp_enabled(name: str, payload: ToolEnabledPayload):
        storage: Storage = app.state.storage
        servers = await storage.get_mcp_servers() or []
        target = next((s for s in servers if s.get("name") == name), None)
        if target is None:
            raise HTTPException(status_code=404, detail=f"MCP server {name!r} 不存在")
        target["enabled"] = payload.enabled
        await _save_mcp_servers(app, servers)
        print(
            f"[neuro-sama] MCP server {name}: "
            f"{'enabled' if payload.enabled else 'disabled'}",
            flush=True,
        )
        return {"status": "ok", **await _tools_payload(app)}

    # ---------- /manage：会话管理 ----------

    @app.get("/manage/sessions")
    async def manage_list_sessions():
        storage: Storage = app.state.storage
        return {"sessions": await storage.list_conversations()}

    @app.delete("/manage/sessions/{channel}/{conversation_id}")
    async def manage_delete_session(channel: str, conversation_id: str):
        storage: Storage = app.state.storage
        deleted = await storage.delete_conversation(channel, conversation_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="session not found")
        print(f"[neuro-sama] session deleted: {channel}:{conversation_id}", flush=True)
        return {"status": "ok", "deleted": True}

    @app.put("/manage/sessions/{channel}/{conversation_id}")
    async def manage_rename_session(channel: str, conversation_id: str, payload: RenamePayload):
        title = payload.title.strip()
        if not title:
            raise HTTPException(status_code=400, detail="title must not be empty")
        storage: Storage = app.state.storage
        updated = await storage.update_conversation_title(channel, conversation_id, title)
        if not updated:
            raise HTTPException(status_code=404, detail="session not found")
        print(f"[neuro-sama] session renamed: {channel}:{conversation_id} -> {title}", flush=True)
        return {"status": "ok", "title": title}

    # ---------- /manage：persona（prompt 模板 + core memory） ----------

    @app.get("/manage/persona")
    async def manage_get_persona():
        persona: PersonaStore = app.state.persona
        memory: MemoryStore = app.state.memory
        await memory._ensure_loaded()
        return {
            "prompt": {
                "content": persona.prompt,
                "updated_at": persona.prompt_updated_at,
                "is_default": persona.is_default_prompt,
            },
            **_persona_core_stats(persona),
            "placeholders": PLACEHOLDERS,
            "short_term": memory.stats(),
        }

    @app.put("/manage/persona/prompt")
    async def manage_save_prompt(payload: PromptPayload):
        persona: PersonaStore = app.state.persona
        try:
            await persona.save_prompt(payload.content)
        except PersonaError as e:
            raise HTTPException(status_code=e.status, detail=e.message) from e
        print(f"[neuro-sama] system prompt saved ({len(persona.prompt)} chars)", flush=True)
        return {
            "status": "ok",
            "prompt": {
                "content": persona.prompt,
                "updated_at": persona.prompt_updated_at,
                "is_default": persona.is_default_prompt,
            },
        }

    @app.post("/manage/persona/prompt/reset")
    async def manage_reset_prompt():
        persona: PersonaStore = app.state.persona
        await persona.reset_prompt()
        print("[neuro-sama] system prompt reset to default", flush=True)
        return {
            "status": "ok",
            "prompt": {
                "content": persona.prompt,
                "updated_at": persona.prompt_updated_at,
                "is_default": True,
            },
        }

    @app.post("/manage/persona/reset")
    async def manage_reset_persona():
        """prompt 模板 + core memory 一起恢复内置默认（人工内容全部丢弃）。"""
        persona: PersonaStore = app.state.persona
        await persona.reset_all()
        print("[neuro-sama] persona reset to default (prompt + core memory)", flush=True)
        return {
            "status": "ok",
            "prompt": {
                "content": persona.prompt,
                "updated_at": persona.prompt_updated_at,
                "is_default": True,
            },
            **_persona_core_stats(persona),
        }

    @app.post("/manage/persona/preview")
    async def manage_preview_persona(payload: PreviewPayload):
        """返回编译后的完整 system prompt（可传入未保存的 prompt 草稿）。"""
        persona: PersonaStore = app.state.persona
        memory: MemoryStore = app.state.memory
        await memory._ensure_loaded()
        return persona.preview_system(memory.render(), payload.content)

    @app.post("/manage/persona/core-memories")
    async def manage_add_core_memory(payload: CoreMemoryPayload):
        persona: PersonaStore = app.state.persona
        try:
            block = await persona.add_core(payload.title, payload.content)
        except PersonaError as e:
            raise HTTPException(status_code=e.status, detail=e.message) from e
        print(f"[neuro-sama] core memory added: {block.title}", flush=True)
        return {"status": "ok", "block": _core_block_dict(block), **_persona_core_stats(persona)}

    @app.put("/manage/persona/core-memories/{block_id}")
    async def manage_update_core_memory(block_id: int, payload: CoreMemoryPayload):
        persona: PersonaStore = app.state.persona
        try:
            block = await persona.update_core(block_id, payload.title, payload.content)
        except PersonaError as e:
            raise HTTPException(status_code=e.status, detail=e.message) from e
        print(f"[neuro-sama] core memory updated: {block.title}", flush=True)
        return {"status": "ok", "block": _core_block_dict(block), **_persona_core_stats(persona)}

    @app.delete("/manage/persona/core-memories/{block_id}")
    async def manage_delete_core_memory(block_id: int):
        persona: PersonaStore = app.state.persona
        try:
            await persona.delete_core(block_id)
        except PersonaError as e:
            raise HTTPException(status_code=e.status, detail=e.message) from e
        print(f"[neuro-sama] core memory deleted: {block_id}", flush=True)
        return {"status": "ok", "deleted": True, **_persona_core_stats(persona)}

    @app.post("/manage/persona/core-memories/reset")
    async def manage_reset_core_memories():
        """core memory 整体替换为包内默认块（人工内容全部丢弃）。"""
        persona: PersonaStore = app.state.persona
        await persona.reset_core()
        print(f"[neuro-sama] core memory reset to default ({len(persona.blocks)} blocks)", flush=True)
        return {"status": "ok", **_persona_core_stats(persona)}

    # ---------- /manage：记忆管理 ----------

    @app.get("/manage/memories")
    async def manage_list_memories():
        memory: MemoryStore = app.state.memory
        await memory._ensure_loaded()
        return {"memories": memory.list(), **memory.stats()}

    @app.post("/manage/memories")
    async def manage_add_memory(payload: MemoryPayload):
        memory: MemoryStore = app.state.memory
        try:
            entry = await memory.add_entry(payload.content)
        except MemoryError as e:
            raise HTTPException(status_code=e.status, detail=e.message)
        print(f"[neuro-sama] memory added: {entry.text[:40]}", flush=True)
        return {
            "status": "ok",
            "memory": {"id": entry.id, "content": entry.text, "ts": entry.ts},
            **memory.stats(),
        }

    @app.put("/manage/memories/{memory_id}")
    async def manage_update_memory(memory_id: int, payload: MemoryPayload):
        memory: MemoryStore = app.state.memory
        try:
            entry = await memory.update(memory_id, payload.content)
        except MemoryError as e:
            raise HTTPException(status_code=e.status, detail=e.message)
        print(f"[neuro-sama] memory {memory_id} updated: {entry.text[:40]}", flush=True)
        return {
            "status": "ok",
            "memory": {"id": entry.id, "content": entry.text, "ts": entry.ts},
            **memory.stats(),
        }

    @app.delete("/manage/memories/{memory_id}")
    async def manage_delete_memory(memory_id: int):
        memory: MemoryStore = app.state.memory
        try:
            await memory.delete(memory_id)
        except MemoryError as e:
            raise HTTPException(status_code=e.status, detail=e.message)
        print(f"[neuro-sama] memory {memory_id} deleted", flush=True)
        return {"status": "ok", "deleted": True, **memory.stats()}

    # ---------- /manage：对话记录管理 ----------

    @app.get("/manage/chats/{channel}/{conversation_id}")
    async def manage_get_chat(channel: str, conversation_id: str):
        storage: Storage = app.state.storage
        conv_id = await storage.find_conversation_id(channel, conversation_id)
        if conv_id is None:
            raise HTTPException(status_code=404, detail="session not found")
        messages = await storage.get_messages(conv_id)
        return {"channel": channel, "conversation_id": conversation_id, "messages": messages}

    @app.put("/manage/chats/{channel}/{conversation_id}/messages/{message_id}")
    async def manage_edit_message(channel: str, conversation_id: str, message_id: int, payload: MessagePayload):
        storage: Storage = app.state.storage
        conv_id = await storage.find_conversation_id(channel, conversation_id)
        if conv_id is None:
            raise HTTPException(status_code=404, detail="session not found")
        role = payload.message.get("role")
        if role not in ("user", "assistant", "tool"):
            raise HTTPException(status_code=400, detail="role 必须是 user / assistant / tool")
        updated = await storage.update_message(message_id, payload.message)
        if not updated:
            raise HTTPException(status_code=404, detail="message not found")
        return {"status": "ok", "message_id": message_id}

    @app.delete("/manage/chats/{channel}/{conversation_id}/messages/{message_id}")
    async def manage_delete_message(channel: str, conversation_id: str, message_id: int):
        storage: Storage = app.state.storage
        conv_id = await storage.find_conversation_id(channel, conversation_id)
        if conv_id is None:
            raise HTTPException(status_code=404, detail="session not found")
        deleted = await storage.delete_message(message_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="message not found")
        return {"status": "ok", "message_id": message_id}

    @app.post("/manage/chats/{channel}/{conversation_id}/stop")
    async def manage_stop_chat(channel: str, conversation_id: str):
        """中断指定会话的流式生成（生成在后台任务里，这是唯一的用户取消入口）。"""
        turn = app.state.turns.find(channel, conversation_id)
        if turn is None:
            return {"status": "not_running"}
        turn.cancel_event.set()
        print(f"[neuro-sama] stop requested: {channel}:{conversation_id}", flush=True)
        return {"status": "ok", "stopped": True}

    # ---------- /ui：Neuro 窗口（背景 / 开场 / 立绘 / 字幕） ----------

    async def _switch_scene(scene_id: str) -> dict:
        """切场景的唯一入口：手动端点与 stream 开播都走这里（同一套校验与广播）。"""
        try:
            info = app.state.ui_scene.switch(scene_id)
        except ui_scenes.UnknownScene as e:
            raise HTTPException(status_code=404, detail=str(e))
        except ui_scenes.SceneError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except SceneLoopError as e:
            raise HTTPException(status_code=429, detail=str(e))
        print(
            f"[neuro-sama] scene → {info['id']} ({info['duration']:.3f}s)", flush=True
        )
        return {"status": "ok", "scene": info, "duration": info["duration"]}

    @app.get("/ui/manifest")
    async def ui_manifest():
        """完整场景清单（窗口渲染器用：元素/box/content/script/refs 都在里面）。

        数据存在**模块自己的 DB** 里（包内 `defaults/neuro_scenes.json` 只是首次
        播种的模板），人工可以在 dashboard 里编辑（`PUT /ui/manifest`）。
        """
        try:
            return ui_scenes.manifest()
        except ui_scenes.SceneError as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/ui/manifest/source")
    async def ui_manifest_source():
        """清单的元信息：是不是默认值 / 什么时候改的 / 多少字符。"""
        try:
            return ui_scenes.manifest_source()
        except ui_scenes.SceneError as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.put("/ui/manifest")
    async def ui_manifest_save(payload: ManifestPayload):
        """保存场景清单（校验 → 落库 → 刷新缓存 → 通知窗口重载）。"""
        try:
            await ui_scenes.save_manifest(payload.content)
        except ui_scenes.SceneError as e:
            raise HTTPException(status_code=400, detail=str(e))
        app.state.status_board.note_change("manifest", "scene", "layers")
        return {"status": "ok", "source": ui_scenes.manifest_source()}

    @app.post("/ui/manifest/reset")
    async def ui_manifest_reset():
        """恢复包内默认清单。"""
        try:
            await ui_scenes.reset_manifest()
        except ui_scenes.SceneError as e:
            raise HTTPException(status_code=400, detail=str(e))
        app.state.status_board.note_change("manifest", "scene", "layers")
        return {"status": "ok", "source": ui_scenes.manifest_source()}

    @app.get("/ui/scenes")
    async def ui_scenes_list():
        """场景清单 + 当前场景。

        dashboard 的场景按钮与"层显隐"开关都按这份数据渲染——加一个 neuro 场景
        只改 `neuro_scenes.json`，前端不用改代码（层的可寻址性来自元素 id）。
        """
        try:
            return {
                "scenes": ui_scenes.overview(),
                "default": ui_scenes.default_scene_id(),
                "refs": ui_scenes.manifest().get("refs") or {},
                "current": app.state.ui_state.scene_snapshot(),
            }
        except ui_scenes.SceneError as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/ui/scene")
    async def ui_scene_set(payload: ScenePayload):
        """切换窗口场景（外部可操控：dashboard 控制台 / curl / 其它模块）。

        场景是**后端的权威状态**：改一次就广播 `scene_set`，所有渲染器按
        `elapsed` 收敛到同一画面。目标场景若带 state_script（如开场放完自动回
        常态），由后端按自己的时钟执行，不依赖任何前端活着。
        """
        scene_id = (payload.id or "").strip()
        if not scene_id:
            raise HTTPException(status_code=400, detail="缺少场景 id")
        return await _switch_scene(scene_id)

    @app.post("/ui/intro")
    async def ui_intro():
        """开播开场：等价于"切到 intro 场景"，另外把时长返回给 stream 推进 starting→live。

        时长来自场景数据（`{"media": ..., "after": ...}` → 视频轨道探测 + 余量），
        换素材自动跟随；回切常态由 intro 自己的 state_script 负责。
        """
        return await _switch_scene(ui_scenes.INTRO_SCENE_ID)

    @app.get("/ui/media/{name}")
    async def ui_media_file(name: str):
        """模块持有的媒体（开场视频等）。

        **为什么视频不跟前端资产放在一起**：后端要探测它的轨道时长来决定
        `intro` 场景的后端脚本什么时候触发——那件事发生在服务端，文件就必须
        在服务端手里。前端（neuro 窗口页）通过 `/ui/media/{name}` 取它。
        """
        path = ui_media.media_path(name)
        if path is None:
            raise HTTPException(status_code=404, detail=f"没有这个媒体文件: {name}")
        return FileResponse(path, media_type="video/mp4")

    return app


app = create_app()
