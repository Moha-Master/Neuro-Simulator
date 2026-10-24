"""stream 服务端：直播循环控制 + 观众消息队列 + /ui 画面宿主。

端点：
- GET  /health / GET /stream/status / GET /stream/sessions   状态与场次史
- POST /stream/start|pause|resume|stop                        直播循环控制
- GET  /queue/messages、POST /queue/message                   当前场次队列（尾读/入列；
                                                               可选 type/value 进优先轨）
- GET  /queues/{queue_id}/messages                            历史场次队列（按 queue_id 读）
- POST /scene                                                 手动切场景（→ 广播 scene_set）
- GET  /stream/scenes                                         场景清单（读 ui 构建的 scenes.json）
- POST /game/{game}/action · GET /game/state                   本地小游戏（Wordle…）：动作 + 全量状态
- PUT  /stream/metadata                                       改本场标题/分区/标签（→ 广播 stream_metadata）
- /manage/chatbot/persona*：chatbot 的 prompt 模板 + core memory（人工编辑，存 stream DB）
- /manage/templates*：stream 拼给 neuro / chatbot 的 user message 模板（人工编辑 + 预览）
- GET  /ui/events（SSE）：snapshot（含最近聊天 + 元素状态） + 循环/控制/聊天事件只读镜像
- /ui：直播画面静态托管（Vite 构建产物）

直播循环语义见 loop.py；跨模块访问（neuro URL）约定见 settings.py。
"""

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .. import log_kit, scene_kit
from ..broadcaster import EventBroadcaster
from ..config import get_config, module_url
from ..persona import PersonaError
from ..scene_kit import SceneError
from ..templates import TemplateError
from . import scenes as scene_catalog
from . import settings as stream_settings
from . import status as stream_status_projection
from .audience import AudienceTracker
from .games import GameError
from .loop import StreamConflict, StreamLoop
from .neuro_client import NeuroClient
from .persona import (
    CHATBOT_PLACEHOLDERS,
    ChatbotPersonaStore,
    build_template_store,
)
from .storage import Storage


class MessagePayload(BaseModel):
    username: str = "viewer"
    content: str
    # 优先消息（可选）：type ∈ {"bits","points"}；value 为货币量（bits/points 数）。
    # 不传 type = 普通观众消息。
    type: str | None = None
    value: int = 0


class StartPayload(BaseModel):
    """开播参数（全部可选）：缺省用 config 的 stream 默认值。"""

    title: str | None = None
    category: str | None = None
    tags: list[str] | None = None


class MetadataPayload(BaseModel):
    """本场信息（直播中可改）。None 表示该字段不动，空串/空数组表示清空。"""

    title: str | None = None
    category: str | None = None
    tags: list[str] | None = None


class PromptPayload(BaseModel):
    content: str


class ManifestPayload(BaseModel):
    """场景清单（整份 JSON 文本；服务端校验后存进模块自己的 DB）。"""

    content: str


class ChatbotPreviewPayload(BaseModel):
    content: str | None = None  # 可选：预览未保存的草稿


class CoreMemoryPayload(BaseModel):
    title: str
    content: str


class TemplatePayload(BaseModel):
    content: str


class TemplatePreviewPayload(BaseModel):
    content: str | None = None  # 可选：预览未保存的草稿
    messages: int = 3           # 预览样例：取队列里最近 N 条观众消息


class ScenePayload(BaseModel):
    id: str
    params: dict[str, Any] | None = None


class GameActionPayload(BaseModel):
    """游戏动作：action = start / type / delete / submit / restart / move / state / resign / exit。"""

    action: str
    args: dict[str, Any] | None = None


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _prompt_info(persona) -> dict:
    return {
        "content": persona.prompt,
        "updated_at": persona.prompt_updated_at,
        "is_default": persona.is_default_prompt,
    }


def _core_block_dict(block) -> dict:
    return {
        "id": block.id,
        "title": block.title,
        "content": block.content,
        "created_at": block.created_at,
        "updated_at": block.updated_at,
    }


def _core_stats(persona) -> dict:
    return {
        "core": {
            "blocks": persona.list_core(),
            "count": len(persona.blocks),
            "used": persona.core_used,
            "is_default": persona.is_default_core,
        }
    }


async def _template_vars(app: FastAPI, lp: StreamLoop, sample_messages: int) -> dict:
    """构造一次模板渲染的 Jinja context：优先用当前场次真实上下文，无场次时用样例值。"""
    s = stream_settings.load()
    context = lp.template_context("")
    if lp.session:
        msgs = await app.state.storage.recent_messages(
            lp.session["queue_row"], max(1, min(sample_messages, 20))
        )
        # 与真实循环一致：没有消息时 {{chat_messages}} 就是 placeholder_message
        context["messages"] = (
            "\n".join(f"- {m['username']}: {m['content']}" for m in msgs)
            or s.placeholder_message
        )
    else:
        context["messages"] = (
            "- pogger_99: hello neuro\n- kappa_fan: 9+10?"
        )
    return context


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        cfg = get_config()
        storage = Storage(cfg.WORKDIR / "stream" / "data.db")
        await storage.init()
        persona = ChatbotPersonaStore(storage)
        await persona.load()
        templates = build_template_store(storage)
        await templates.load()
        # 场景清单：DB 里的那份是权威（包内 defaults/scenes.json 只做首次播种）
        scene_store = scene_kit.SceneStore(
            storage, scene_catalog._defaults_path(), scene_catalog._validate
        )
        await scene_store.load()
        scene_catalog.set_store(scene_store)
        bus = EventBroadcaster()
        loop = StreamLoop(storage, bus, persona, templates)
        # 模板渲染结果变化时 bump chatbot 分区（template_error 在 State 页可见）
        templates.on_render_result = lambda: loop._touch("chatbot")
        app.state.storage = storage
        app.state.bus = bus
        app.state.loop = loop
        app.state.persona = persona
        app.state.templates = templates
        # 进程重启后的遗留场次兜底：进行中记录标记为 offline
        stale = await storage.get_active_session()
        if stale:
            await storage.update_session(stale["id"], state="offline", ended=True)
            print(
                f"[stream] 清理遗留场次（进程重启所致）: {stale['session_id']}",
                flush=True,
            )
        # 状态机投影：provider 只读 loop / 场景清单 / 缓存，绝不 await。
        # 观众数是频道级事实（未开播也要能取），后台任务自己刷新缓存。
        app.state.audience = AudienceTracker()
        board = stream_status_projection.build_board(loop, app.state.audience)
        loop.state_board = board
        app.state.status_board = board
        recent = await storage.list_sessions(limit=1)
        loop.persisted = recent[0] if recent else None
        app.state.audience.start()
        print("[stream] ready", flush=True)
        yield
        await app.state.audience.shutdown()
        await loop.shutdown()
        await storage.close()

    app = FastAPI(title="stream", lifespan=lifespan)

    def _loop() -> StreamLoop:
        return app.state.loop

    # ---------- 进程日志（内存 = 活体；磁盘历史见 vedal） ----------

    def _own_log() -> log_kit.ProcessLog:
        log = log_kit.current("stream")
        if log is None:
            raise HTTPException(status_code=503, detail="日志未启用：本进程不是经 main() 启动的")
        return log

    @app.get("/logs")
    async def logs(max_bytes: int = log_kit.TAIL_MAX_BYTES):
        """本进程此刻的输出尾部，读**内存环形缓冲**（只含本次运行）。

        进程死掉之后再来看日志，走 vedal 的磁盘端点（`GET /manage/logs/stream`）。
        """
        log = _own_log()
        return log.snapshot(log_kit.clamp_bytes(log, max_bytes))

    @app.get("/logs/events")
    async def logs_events(since: int = -1, max_bytes: int = log_kit.TAIL_MAX_BYTES):
        """SSE 跟随日志：首帧 `snapshot`,之后 `event: log` 增量。

        `?since=<seq>` 用于断线续播；那段已被环形缓冲挤掉时先收 `gap` 再收完整
        `snapshot`（整屏重来），不会静默丢帧。
        """
        log = _own_log()
        return StreamingResponse(
            log_kit.sse_stream(
                log, since=since, max_bytes=log_kit.clamp_bytes(log, max_bytes)
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ---------- 健康与状态 ----------

    @app.get("/health")
    async def health():
        lp = _loop()
        return {
            "status": "ok",
            "module": "stream",
            "state": lp.state,
            "session_id": lp.session["session_id"] if lp.session else None,
            "conversation_id": lp.session["conversation_id"] if lp.session else None,
            "ui_subscribers": app.state.bus.subscriber_count,
            "chatbot": {
                **app.state.persona.stats(),
                "in_flight": lp.chatbot.in_flight,
            },
        }

    @app.get("/stream/status")
    async def stream_status(want: str | None = None, drop: str | None = None):
        """状态机投影：`{module, rev, ts, sections}`（**没有任何旧扁平键**）。

        每个分区统一是 `{current, graph?}`；provider 同步、无 I/O，所以这里既
        不查库也不发 HTTP（队列深度/场次/观众数/neuro 观测都由归属者缓存）。
        `want`/`drop` 是分区级过滤（逗号分隔，`*`=全部），只影响输出。
        """
        return app.state.status_board.read(
            want=want.split(",") if want else None,
            drop=drop.split(",") if drop else None,
        )

    @app.get("/stream/sessions")
    async def stream_sessions(limit: int = 20):
        storage: Storage = app.state.storage
        return {"sessions": await storage.list_sessions(limit=max(1, min(limit, 100)))}

    @app.post("/manage/reload")
    async def manage_reload():
        """重读 config.json（由 vedal 在配置保存/重载指令时调用）。

        本模块业务参数经 settings.load() 现取现算，刷新内存 Config 后
        下一轮循环即用新值；host/port 变更仍需重启进程。
        """
        get_config().reload()
        # 通知已连接画面：字幕停留/轮间冷却参数可能变了（无需刷新页面）
        lp = _loop()
        lp.publish({"type": "settings", **lp.ui_settings()})
        return {"status": "ok", "reloaded": True}

    @app.delete("/stream/sessions/{session_id}")
    async def delete_stream_session(session_id: str):
        lp = _loop()
        storage: Storage = app.state.storage

        # 若删除的是当前正进行的直播，先停止直播
        if lp.session and lp.session.get("session_id") == session_id:
            await lp.stop()

        res = await storage.delete_session_data(session_id)
        if not res:
            raise HTTPException(status_code=404, detail="session not found")

        channel, conversation_id = res

        # 尝试清理 Neuro 模块中的对应会话记录（Neuro 不可达也不抛异常，提示已处理）
        cfg = get_config()
        neuro_client = NeuroClient(module_url(cfg, "neuro_sama"))
        neuro_deleted = await neuro_client.delete_session(channel, conversation_id)

        return {
            "status": "ok",
            "session_id": session_id,
            "neuro_deleted": neuro_deleted,
        }

    # ---------- 直播循环控制 ----------

    async def _guard(coro) -> dict:
        try:
            return await coro
        except StreamConflict as e:
            raise HTTPException(status_code=409, detail=str(e))

    @app.post("/stream/start")
    async def stream_start(payload: StartPayload = StartPayload()):
        title = None if payload.title is None else str(payload.title)
        category = None if payload.category is None else str(payload.category)
        try:
            tags = None if payload.tags is None else stream_settings.normalize_tags(payload.tags)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        session = await _guard(_loop().start(title, category, tags))
        return {
            "status": "starting",
            "session": session["session_id"],
            "queue": session["queue_id"],
            "title": session.get("title", ""),
            "category": session.get("category", ""),
            "tags": session.get("tags", []),
        }

    @app.put("/stream/metadata")
    async def stream_metadata(payload: MetadataPayload):
        """直播中修改本场标题/分区/标签（→ 广播 stream_metadata，下一轮消息即生效）。"""
        try:
            tags = None if payload.tags is None else stream_settings.normalize_tags(payload.tags)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        meta = await _guard(
            _loop().set_metadata(
                title=payload.title,
                category=payload.category,
                tags=tags,
            )
        )
        return {"status": "ok", **meta}

    @app.post("/stream/pause")
    async def stream_pause():
        await _guard(_loop().pause())
        return {"status": "paused"}

    @app.post("/stream/resume")
    async def stream_resume():
        await _guard(_loop().resume())
        return {"status": "live"}

    @app.post("/stream/stop")
    async def stream_stop():
        result = await _guard(_loop().stop())
        return {"status": result.get("status", "stopped")}

    # ---------- 观众消息队列 ----------

    @app.get("/queue/messages")
    async def queue_messages(after_id: int = 0, limit: int = 200):
        lp = _loop()
        if not lp.session:
            raise HTTPException(status_code=409, detail="当前没有进行中的直播场次（队列未绑定）")
        storage: Storage = app.state.storage
        return {
            "queue_id": lp.session["queue_id"],
            "messages": await storage.list_messages(lp.session["queue_row"], after_id, max(1, min(limit, 500))),
        }

    @app.post("/queue/message")
    async def queue_append(payload: MessagePayload):
        lp = _loop()
        if not lp.session:
            raise HTTPException(status_code=409, detail="当前没有进行中的直播场次，消息无处入列")
        username = payload.username.strip() or "viewer"
        content = payload.content.strip()
        if not content:
            raise HTTPException(status_code=400, detail="content must not be empty")
        priority_type = (payload.type or "").strip().lower() or None
        if priority_type is not None and priority_type not in ("bits", "points"):
            raise HTTPException(
                status_code=400, detail='type 只能是 "bits" 或 "points"（留空 = 普通消息）'
            )
        value = int(payload.value)
        if value < 0:
            raise HTTPException(status_code=400, detail="value must be >= 0")
        try:
            msg_id = await lp.append_message(
                username, content, priority_type=priority_type, value=value
            )
        except StreamConflict as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        return {
            "status": "ok",
            "id": msg_id,
            "priority": priority_type is not None,
            "type": priority_type,
            "value": value,
        }

    @app.get("/queues/{queue_id}/messages")
    async def queue_messages_by_id(queue_id: str, after_id: int = 0, limit: int = 200):
        storage: Storage = app.state.storage
        q = await storage.get_queue(queue_id)
        if not q:
            raise HTTPException(status_code=404, detail="queue not found")
        return {"queue_id": queue_id, "messages": await storage.list_messages(q["id"], after_id, max(1, min(limit, 500)))}

    # ---------- 场景控制与场景清单（手动/脚本；循环本身只记 last_scene） ----------

    @app.post("/scene")
    async def scene_set(payload: ScenePayload):
        # hidden 内部场景（如 starting_soon）只能由程序化路径触发，不允许手动选中——
        # 手动切进去没有"到点回 room"的驱动者，画面会卡住。
        # 注：dashboard 已不再提供手动切场景按钮（避免与循环/游戏的自动切换打架），
        # 端点保留给调试与未来的固定时长场景。
        if not scene_catalog.exists(payload.id) or scene_catalog.hidden(payload.id):
            known = ", ".join(s["id"] for s in scene_catalog.overview()) or "（ui 未构建）"
            raise HTTPException(
                status_code=400,
                detail=f"未知或不可手动选择的场景 {payload.id!r}；可用：{known}",
            )
        _loop().on_scene(payload.id)
        return {"status": "ok", "scene": payload.id}

    @app.get("/stream/scenes")
    async def stream_scenes():
        """场景清单（元素概览）。单一来源 = ui 的 scenes.json，dashboard 场景按钮据此渲染。"""
        return {"scenes": scene_catalog.overview()}

    # ---------- 本地小游戏（Wordle…）：Neuro 的工具打到这里，画面收 element_state ----------

    @app.get("/game/state")
    async def game_state():
        game = _loop().games.active
        return {"active": game.id if game else None, "state": game.state() if game else None}

    @app.post("/game/{game_id}/action")
    async def game_action(game_id: str, payload: GameActionPayload):
        """执行一个游戏动作。返回 result_text（给 LLM 读）+ state（画面用的全量快照）。"""
        lp = _loop()
        if payload.action == "start" and not lp.session:
            raise HTTPException(status_code=409, detail="No stream is running; games only exist on air.")
        hub = lp.games

        async def execute() -> dict[str, Any]:
            if payload.action == "start":
                return await hub.start(game_id)
            return await hub.act(game_id, payload.action, payload.args or {})

        try:
            # 所有 game 动作在 stream 内串行执行；默认先等当前语音播完再动棋盘。
            return await lp.run_game_action(execute)
        except GameError as e:
            # 面向模型的"这步不能做"：给明确文本，不要抛成工具异常
            raise HTTPException(status_code=400, detail=str(e)) from e

    # ---------- /manage：chatbot 人设（prompt 模板 + core memory） ----------

    @app.get("/manage/chatbot/persona")
    async def manage_chatbot_persona():
        persona: ChatbotPersonaStore = app.state.persona
        cb = stream_settings.load().chatbot
        return {
            "prompt": {
                "content": persona.prompt,
                "updated_at": persona.prompt_updated_at,
                "is_default": persona.is_default_prompt,
            },
            **_core_stats(persona),
            "placeholders": CHATBOT_PLACEHOLDERS,
            "count": cb.count,
        }

    @app.put("/manage/chatbot/persona/prompt")
    async def manage_chatbot_save_prompt(payload: PromptPayload):
        persona: ChatbotPersonaStore = app.state.persona
        try:
            await persona.save_prompt(payload.content)
        except PersonaError as e:
            raise HTTPException(status_code=e.status, detail=e.message) from e
        print(f"[stream] chatbot prompt saved ({len(persona.prompt)} chars)", flush=True)
        return {"status": "ok", "prompt": _prompt_info(persona)}

    @app.post("/manage/chatbot/persona/prompt/reset")
    async def manage_chatbot_reset_prompt():
        persona: ChatbotPersonaStore = app.state.persona
        await persona.reset_prompt()
        print("[stream] chatbot prompt reset to default", flush=True)
        return {"status": "ok", "prompt": _prompt_info(persona)}

    @app.post("/manage/chatbot/persona/reset")
    async def manage_chatbot_reset_persona():
        """chatbot prompt + core memory 一起恢复内置默认。"""
        persona: ChatbotPersonaStore = app.state.persona
        await persona.reset_all()
        print("[stream] chatbot persona reset to default", flush=True)
        return {"status": "ok", "prompt": _prompt_info(persona), **_core_stats(persona)}

    @app.post("/manage/chatbot/persona/preview")
    async def manage_chatbot_preview(payload: ChatbotPreviewPayload):
        """用当前 count 编译 chatbot 的 system prompt（可传未保存草稿）。"""
        persona: ChatbotPersonaStore = app.state.persona
        cb = stream_settings.load().chatbot
        return persona.preview_prompt(cb.count, payload.content)

    @app.post("/manage/chatbot/persona/core-memories")
    async def manage_chatbot_add_core(payload: CoreMemoryPayload):
        persona: ChatbotPersonaStore = app.state.persona
        try:
            block = await persona.add_core(payload.title, payload.content)
        except PersonaError as e:
            raise HTTPException(status_code=e.status, detail=e.message) from e
        print(f"[stream] chatbot core memory added: {block.title}", flush=True)
        return {"status": "ok", "block": _core_block_dict(block), **_core_stats(persona)}

    @app.put("/manage/chatbot/persona/core-memories/{block_id}")
    async def manage_chatbot_update_core(block_id: int, payload: CoreMemoryPayload):
        persona: ChatbotPersonaStore = app.state.persona
        try:
            block = await persona.update_core(block_id, payload.title, payload.content)
        except PersonaError as e:
            raise HTTPException(status_code=e.status, detail=e.message) from e
        print(f"[stream] chatbot core memory updated: {block.title}", flush=True)
        return {"status": "ok", "block": _core_block_dict(block), **_core_stats(persona)}

    @app.delete("/manage/chatbot/persona/core-memories/{block_id}")
    async def manage_chatbot_delete_core(block_id: int):
        persona: ChatbotPersonaStore = app.state.persona
        try:
            await persona.delete_core(block_id)
        except PersonaError as e:
            raise HTTPException(status_code=e.status, detail=e.message) from e
        print(f"[stream] chatbot core memory deleted: {block_id}", flush=True)
        return {"status": "ok", "deleted": True, **_core_stats(persona)}

    @app.post("/manage/chatbot/persona/core-memories/reset")
    async def manage_chatbot_reset_core():
        persona: ChatbotPersonaStore = app.state.persona
        await persona.reset_core()
        print(f"[stream] chatbot core memory reset ({len(persona.blocks)} blocks)", flush=True)
        return {"status": "ok", **_core_stats(persona)}

    # ---------- /manage：消息模板（stream 拼给 neuro / chatbot 的 user message） ----------

    @app.get("/manage/templates")
    async def manage_list_templates():
        templates = app.state.templates
        lp = _loop()
        title, category = lp.session_metadata()
        return {
            "templates": templates.list(),
            "context": {
                "title": title,
                "category": category,
                "live": lp.session is not None,
                "scene": lp.last_scene,
                "rounds": (lp.session or {}).get("rounds", 0),
            },
        }

    @app.put("/manage/templates/{name}")
    async def manage_save_template(name: str, payload: TemplatePayload):
        templates = app.state.templates
        try:
            validation = await templates.save(name, payload.content)
        except TemplateError as e:
            raise HTTPException(status_code=e.status, detail=e.message) from e
        print(f"[stream] template saved: {name}", flush=True)
        return {"status": "ok", "templates": templates.list(), "validation": validation}

    @app.post("/manage/templates/{name}/reset")
    async def manage_reset_template(name: str):
        templates = app.state.templates
        try:
            validation = await templates.reset(name)
        except TemplateError as e:
            raise HTTPException(status_code=e.status, detail=e.message) from e
        print(f"[stream] template reset: {name}", flush=True)
        return {"status": "ok", "templates": templates.list(), "validation": validation}

    @app.post("/manage/templates/{name}/preview")
    async def manage_preview_template(name: str, payload: TemplatePreviewPayload):
        """用当前场次上下文（可含未保存草稿）渲染一次，看到实际会发出去的内容。"""
        templates = app.state.templates
        lp = _loop()
        vars = await _template_vars(app, lp, payload.messages)
        if payload.content is not None:
            try:
                templates.spec(name)  # 未知模板 → 404
            except TemplateError as e:
                raise HTTPException(status_code=e.status, detail=e.message) from e
        try:
            return templates.preview(name, vars, payload.content)
        except TemplateError as e:
            raise HTTPException(status_code=e.status, detail=e.message) from e

    # ---------- /ui 画面事件通道 ----------

    @app.get("/ui/manifest")
    async def ui_manifest():
        """完整场景清单（画面渲染器用：元素/box/css/script/neuro 块都在里面）。

        数据存在**模块自己的 DB** 里（包内 `defaults/scenes.json` 只是首次播种的
        模板），人工可以在 dashboard 里编辑（`PUT /ui/manifest`）。
        """
        return scene_catalog.manifest()

    @app.get("/ui/manifest/source")
    async def ui_manifest_source():
        """清单的元信息：是不是默认值 / 什么时候改的 / 多少字符。"""
        return scene_catalog.manifest_source()

    @app.put("/ui/manifest")
    async def ui_manifest_save(payload: ManifestPayload):
        """保存场景清单（校验 → 落库 → 刷新缓存 → 通知画面重载）。"""
        try:
            await scene_catalog.save_manifest(payload.content)
        except SceneError as e:
            raise HTTPException(status_code=400, detail=str(e))
        # 画面页启动时拉一次清单：改了要让它重新拉（重载页面最省事，也最不容易出偏）
        app.state.bus.publish({"type": "manifest_changed"})
        return {"status": "ok", "source": scene_catalog.manifest_source()}

    @app.post("/ui/manifest/reset")
    async def ui_manifest_reset():
        """恢复包内默认清单。"""
        try:
            await scene_catalog.reset_manifest()
        except SceneError as e:
            raise HTTPException(status_code=400, detail=str(e))
        app.state.bus.publish({"type": "manifest_changed"})
        return {"status": "ok", "source": scene_catalog.manifest_source()}

    @app.get("/ui/events")
    async def ui_events():
        """画面只读事件流：连接即推 snapshot（state/scene/session），随后实时广播。"""
        bus: EventBroadcaster = app.state.bus
        lp = _loop()
        q = bus.subscribe()

        async def gen():
            try:
                session_info = None
                if lp.session:
                    session_info = {
                        k: lp.session[k] for k in (
                            "session_id", "queue_id", "channel", "conversation_id"
                        )
                    }
                title, category = lp.session_metadata()
                snapshot = {
                    "state": lp.state,
                    # 场景是 {id, duration, elapsed}（与 neuro 窗口同一套契约）；
                    # 中途连接的画面按 elapsed 恢复场景脚本。无场景时为 null。
                    "scene": lp.scene_snapshot() or None,
                    "session": session_info,
                    "settings": lp.ui_settings(),
                    "metadata": {
                        "title": title,
                        "category": category,
                        "tags": lp.session_tags(),
                    },
                }
                # 元素层状态（游戏棋盘等）：中途连接的画面直接按全量快照渲染当前局面
                elements = lp.games.element_states()
                if elements:
                    snapshot["elements"] = elements
                # 聊天层恢复：最近 N 条（含优先消息，带 type/value 供样式区分）
                if lp.session:
                    storage: Storage = app.state.storage
                    snapshot["chat"] = await storage.recent_messages(
                        lp.session["queue_row"], 30
                    )
                yield _sse("snapshot", snapshot)
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
            gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app


app = create_app()
