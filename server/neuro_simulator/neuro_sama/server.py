"""FastAPI 服务：POST /chat（SSE 流式）+ GET /health + /manage 管理接口 + /ui 窗口。

多渠道多会话：请求携带 channel + conversation_id（缺省时服务端生成并随 start 事件回传），
历史与记忆持久化在 <workdir>/neuro-sama/data.db（见 storage.py）。

本模块 = Agent + 独立的 Neuro 窗口：
- Agent 只处理输入并流出响应（含句级 TTS 的 speech 事件），不感知直播；
- /ui 前端负责背景 / 开场视频 / 立绘 / 字幕，可脱离直播单独运行；
- 直播循环、观众消息队列、场景与最终画面合成属于 stream 模块。

/manage 管理接口（由 vedal 模块代理给 dashboard 使用；配置管理在 vedal 统一处理）：
- POST /manage/reload：配置热重载（由 vedal 在配置保存/重载指令时调用）
- GET/DELETE /manage/sessions...  会话管理
- GET /manage/chats...、PUT/DELETE .../messages/{id}  对话记录管理
"""

import asyncio
import json
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from openai import AsyncOpenAI
from pydantic import BaseModel

from ..broadcaster import EventBroadcaster
from ..config import get_config
from . import tts, ui_media
from . import scenes as ui_scenes
from .agent import NeuroAgent
from .games import StreamGameProvider
from .mcp_client import MCPManager
from .memory import MemoryError, MemoryStore
from .persona import PLACEHOLDERS, PersonaError, PersonaStore
from .storage import Storage
from .tools import ToolRegistry, build_builtin_tools
from .ui_scene import SceneLoopError, make_runner
from .ui_state import UiState

_DIM = "\033[2m"
_CYAN = "\033[36m"
_MAGENTA = "\033[35m"
_YELLOW = "\033[33m"
_ORANGE = "\033[38;5;208m"
_RED = "\033[31m"
_RESET = "\033[0m"

# /ui/events 只镜像 Neuro 窗口需要的生成事件；content/reasoning/usage/tool_result
# 等纯文本/统计帧不进窗口（省带宽，也避免 EventSource 解析无用数据）。
# scene_set 是窗口的权威场景状态（后端脚本或手动切换产生），不是生成事件。
_UI_EVENT_TYPES = {
    "start",
    "speech",
    "done",
    "cancelled",
    "error",
    "tool_start",
    "scene_set",
    "settings",
}


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


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


class ChatRequest(BaseModel):
    message: str
    channel: str = "web"
    conversation_id: str | None = None  # None 时服务端生成并随 start 事件回传


class MessagePayload(BaseModel):
    message: dict  # 完整 OpenAI 消息 dict（必须含 role）


class RenamePayload(BaseModel):
    title: str


class MemoryPayload(BaseModel):
    content: str


class PromptPayload(BaseModel):
    content: str


class PreviewPayload(BaseModel):
    content: str | None = None  # 可选：预览未保存的草稿


class CoreMemoryPayload(BaseModel):
    title: str
    content: str


class ScenePayload(BaseModel):
    id: str  # 目标场景 id（来自 neuro_scenes.json）


async def _hot_reload(app: FastAPI) -> None:
    """配置热重载：重读 config.yaml，更新 LLM 客户端 / 记忆上限 / MCP。

    在途流式生成先优雅终止：置位全部活跃 cancel_event，各流落库半截内容
    并以 cancelled 事件结束——避免 LLM 客户端重建时旧客户端被 close
    导致在途请求裸 error。
    """
    for ev in list(app.state.active_streams.values()):
        ev.set()
    cfg = get_config()
    cfg.reload()
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

    # 4) MCP：配置变化时重建（内置工具随 registry 一起重建）
    if cfg.MCP_SERVERS != app.state.mcp.servers:
        new_registry = ToolRegistry()
        for tool in build_builtin_tools(memory):
            new_registry.add(tool)
        new_mcp = MCPManager(cfg.MCP_SERVERS)
        for line in await new_mcp.start(new_registry):
            print(line, flush=True)
        agent.registry = new_registry
        await app.state.mcp.close()
        app.state.mcp = new_mcp

    print(
        f"[neuro-sama] hot reload: model={(svc.model if svc else cfg.NEURO_MODEL_REF) or cfg.NEURO_MODEL_REF or None!r} host={cfg.HOST}:{cfg.PORT} "
        f"tools={agent.registry.all_names()}",
        flush=True,
    )
    app.state.broadcaster.publish({"type": "settings", **app.state.ui_state.settings()})
    app.state.ui_broadcaster.publish({"type": "settings", **app.state.ui_state.settings()})


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        cfg = get_config()

        storage = Storage(cfg.DB_PATH)
        await storage.init()
        persona = PersonaStore(storage)
        await persona.load()
        memory = MemoryStore(storage, cfg.MEMORY_CHAR_LIMIT)
        await memory.load()

        registry = ToolRegistry()
        for tool in build_builtin_tools(memory):
            registry.add(tool)

        mcp = MCPManager(cfg.MCP_SERVERS)
        for line in await mcp.start(registry):
            print(line, flush=True)

        svc = cfg.neuro_llm()
        client = (
            AsyncOpenAI(base_url=svc.base_url, api_key=svc.api_key, timeout=svc.timeout)
            if svc
            else None
        )
        agent = NeuroAgent(client, registry, storage, memory, persona, StreamGameProvider())
        app.state.agent = agent
        app.state.memory = memory
        app.state.persona = persona
        app.state.storage = storage
        app.state.mcp = mcp
        app.state.active_streams: dict[str, asyncio.Event] = {}
        app.state.broadcaster = EventBroadcaster()
        app.state.ui_broadcaster = EventBroadcaster()
        app.state.ui_state = UiState()
        app.state.ui_scene = make_runner(
            app.state.ui_state, app.state.ui_broadcaster.publish
        )
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
        await app.state.ui_scene.stop()
        if client is not None:
            await client.close()
        await mcp.close()
        await storage.close()

    app = FastAPI(title="neuro-sama", lifespan=lifespan)

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

    # ---------- 状态投影（其他模块消费） ----------

    @app.get("/status")
    async def status(request: Request):
        """跨模块的状态投影：当前场景 + 语音节奏 + 在途轮次，**不含音频**。

        与 `/ui/state` 的分工：那边是**画面快照**（逐句 consumed + 正在发声那句的
        base64 音频，给渲染器断点续播）；这边是**状态投影**，可以被别的模块每半秒
        拉一次而不拖几 MB。stream 的直播循环用它判"开场是否放完"与下一轮节奏，
        chatbot 用它判她是不是睡着了。
        """
        cfg = get_config()
        channel = (request.query_params.get("channel") or "").strip() or None
        return {
            "module": "neuro_sama",
            "model_service_ok": cfg.neuro_llm() is not None,
            **app.state.ui_state.status(channel),
        }

    # ---------- 对话 ----------

    @app.post("/chat")
    async def chat(req: ChatRequest):
        message = req.message.strip()
        if not message:
            raise HTTPException(status_code=400, detail="message must not be empty")
        agent: NeuroAgent = app.state.agent
        memory: MemoryStore = app.state.memory
        cfg = get_config()
        conversation_id = req.conversation_id or uuid.uuid4().hex
        channel = req.channel

        print(f"\n{_CYAN}you ({channel}:{conversation_id[:8]}) » {message}{_RESET}", flush=True)

        stream_key = f"{channel}:{conversation_id}"
        if stream_key in app.state.active_streams:
            # 同会话并发流会导致历史消息交错落库、/stop 误投，直接拒绝（检查与
            # 登记之间无 await，单事件循环内原子）；新会话（服务端生成 id）不受限
            raise HTTPException(status_code=409, detail="this conversation already has a generation in progress")
        cancel_event = asyncio.Event()
        app.state.active_streams[stream_key] = cancel_event
        turn_id = app.state.ui_state.new_turn(channel)

        async def gen():
            try:
                svc = cfg.neuro_llm()
                start_ev = {
                    "channel": channel,
                    "conversation_id": conversation_id,
                    "model": svc.model if svc else None,
                    "turn_id": turn_id,
                }
                yield _sse("start", start_ev)
                start_evt = {"type": "start", "stream_key": stream_key, **start_ev}
                app.state.broadcaster.publish(start_evt)
                app.state.ui_broadcaster.publish(start_evt)
                async for ev in agent.run(channel, conversation_id, message, cancel_event=cancel_event):
                    _terminal(ev["type"], ev)
                    if ev["type"] == "done":
                        ev = {
                            **ev,
                            "_memory": {
                                "count": memory.count,
                                "used": memory.used,
                                "char_limit": memory.char_limit,
                            },
                        }
                    app.state.ui_state.observe(ev, channel, turn_id)
                    ev = {**ev, "turn_id": turn_id}
                    mirrored = {"stream_key": stream_key, "channel": channel, "conversation_id": conversation_id, **ev}
                    app.state.broadcaster.publish(mirrored)
                    if ev["type"] in _UI_EVENT_TYPES:
                        app.state.ui_broadcaster.publish(mirrored)
                    yield _sse(ev["type"], ev)
            finally:
                app.state.active_streams.pop(stream_key, None)
                app.state.ui_state.abort(channel, turn_id)

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ---------- /manage：热重载 + 会话/对话记录管理（配置管理由 vedal 统一处理） ----------

    @app.get("/manage/events")
    async def manage_events():
        """SSE 事件广播：供 Dashboard Chat 页面实时拉取在途会话的生成状态。"""
        bus: EventBroadcaster = app.state.broadcaster
        q = bus.subscribe()

        async def event_generator():
            try:
                active = list(app.state.active_streams.keys())
                yield _sse("snapshot", {"active": active})
                while True:
                    try:
                        ev = await asyncio.wait_for(q.get(), timeout=15.0)
                    except asyncio.TimeoutError:
                        yield ": ping\n\n"
                        continue
                    yield _sse(ev.get("type", "message"), ev)
            finally:
                bus.unsubscribe(q)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/manage/reload")
    async def manage_reload():
        """配置热重载（由 vedal 在配置保存/重载指令时调用）。"""
        await _hot_reload(app)
        return {"status": "ok", "reloaded": True}

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
        """中断指定会话的流式生成。"""
        stream_key = f"{channel}:{conversation_id}"
        cancel_event = app.state.active_streams.get(stream_key)
        if cancel_event is None:
            return {"status": "not_running"}
        cancel_event.set()
        print(f"[neuro-sama] stop requested: {channel}:{conversation_id}", flush=True)
        return {"status": "ok", "stopped": True}

    # ---------- /ui：Neuro 窗口（背景 / 开场 / 立绘 / 字幕） ----------

    @app.get("/ui/events")
    async def ui_events(request: Request):
        """Neuro 窗口只读事件流：snapshot + 实时 speech/tool/intro。

        `?channel=stream` 时只推该 channel 的生成事件（合成器用）；独立打开
        Neuro View 时不带 channel，显示全部。intro/settings 是全局事件，始终下发。
        """
        bus: EventBroadcaster = app.state.ui_broadcaster
        ui_state: UiState = app.state.ui_state
        channel = (request.query_params.get("channel") or "").strip() or None
        q = bus.subscribe()

        async def gen():
            try:
                yield _sse("snapshot", ui_state.snapshot(channel))
                while True:
                    try:
                        ev = await asyncio.wait_for(q.get(), timeout=15.0)
                    except asyncio.TimeoutError:
                        yield ": ping\n\n"
                        continue
                    ev_channel = ev.get("channel")
                    if channel and ev_channel and ev_channel != channel:
                        continue
                    if ev.get("type") not in _UI_EVENT_TYPES:
                        continue
                    yield _sse(ev.get("type", "message"), ev)
            finally:
                bus.unsubscribe(q)

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/ui/state")
    async def ui_state_view(request: Request):
        """当前窗口状态（调试/合成器恢复用）。"""
        channel = (request.query_params.get("channel") or "").strip() or None
        return app.state.ui_state.snapshot(channel)

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

    # 静态托管放在最后，避免遮挡 /ui/events、/ui/intro 等 API 路由。
    ui_dist = ui_media.ui_dist_dir()
    if ui_dist is not None:
        app.mount("/ui", StaticFiles(directory=ui_dist, html=True), name="ui")
    else:

        @app.get("/ui", include_in_schema=False)
        @app.get("/ui/", include_in_schema=False)
        async def _no_ui():
            return JSONResponse(
                status_code=404,
                content={"detail": "neuro ui dist 未找到：请在 server/neuro_simulator/neuro_sama/ui/ 下运行 npm run build"},
            )

    return app


app = create_app()
