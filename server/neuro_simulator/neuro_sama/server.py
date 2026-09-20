"""FastAPI 服务：POST /chat（SSE 流式）+ GET /health + /manage 管理接口。

多渠道多会话：请求携带 channel + conversation_id（缺省时服务端生成并随 start 事件回传），
历史与记忆持久化在 <workdir>/neuro-sama/data.db（见 storage.py）。

本模块是纯 Agent 服务：只处理输入并流出响应（含句级 TTS 的 speech 事件），
不感知直播——直播循环、观众消息队列、场景与 /ui 画面宿主属于 stream 模块，
由 stream 作为 /chat 的调用方消费响应流。

/manage 管理接口（由 vedal 模块代理给 dashboard 使用；配置管理在 vedal 统一处理）：
- POST /manage/reload：配置热重载（由 vedal 在配置保存/重载指令时调用）
- GET/DELETE /manage/sessions...  会话管理
- GET /manage/chats...、PUT/DELETE .../messages/{id}  对话记录管理
"""

import asyncio
import json
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from openai import AsyncOpenAI
from pydantic import BaseModel

from ..broadcaster import EventBroadcaster
from ..config import get_config
from . import tts
from .agent import NeuroAgent
from .mcp_client import MCPManager
from .memory import MemoryError, MemoryStore
from .storage import Storage
from .tools import ToolRegistry, build_builtin_tools

_DIM = "\033[2m"
_CYAN = "\033[36m"
_MAGENTA = "\033[35m"
_YELLOW = "\033[33m"
_ORANGE = "\033[38;5;208m"
_RED = "\033[31m"
_RESET = "\033[0m"


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
        f"tools={agent.registry.names()}",
        flush=True,
    )


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        cfg = get_config()

        storage = Storage(cfg.DB_PATH)
        await storage.init()
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
        agent = NeuroAgent(client, registry, storage, memory)
        app.state.agent = agent
        app.state.memory = memory
        app.state.storage = storage
        app.state.mcp = mcp
        app.state.active_streams: dict[str, asyncio.Event] = {}
        app.state.broadcaster = EventBroadcaster()
        print(
            f"[neuro-sama] ready: model_ref={cfg.NEURO_MODEL_REF!r} "
            f"model={svc.model if svc else None} db={cfg.DB_PATH} tools={registry.names()}",
            flush=True,
        )
        yield
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
            "tools": app.state.agent.registry.names(),
            "memory_entries": app.state.memory.count,
            "tts": tts.tts_state(),
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

        async def gen():
            try:
                svc = cfg.neuro_llm()
                start_ev = {
                    "channel": channel,
                    "conversation_id": conversation_id,
                    "model": svc.model if svc else None,
                }
                yield _sse("start", start_ev)
                app.state.broadcaster.publish({"type": "start", "stream_key": stream_key, **start_ev})
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
                    app.state.broadcaster.publish({"stream_key": stream_key, "channel": channel, "conversation_id": conversation_id, **ev})
                    yield _sse(ev["type"], ev)
            finally:
                app.state.active_streams.pop(stream_key, None)

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ---------- /manage：热重载 + 会话/对话记录管理（配置管理由 vedal 统一处理） ----------

    @app.get("/manage/events")
    async def manage_events():
        """SSE 事件广播：供 Dashboard Chat 页面实时拉取在途会话的生成状态。"""
        async def event_generator():
            active = list(app.state.active_streams.keys())
            yield _sse("snapshot", {"active": active})
            async for ev in app.state.broadcaster.subscribe():
                yield _sse(ev.get("type", "message"), ev)

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

    return app


app = create_app()
