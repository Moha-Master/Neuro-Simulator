"""stream 服务端：直播循环控制 + 观众消息队列 + /ui 画面宿主。

端点：
- GET  /health / GET /stream/status / GET /stream/sessions   状态与场次史
- POST /stream/start|pause|resume|stop                        直播循环控制
- GET  /queue/messages、POST /queue/message                   当前场次队列（尾读/入列）
- GET  /queues/{queue_id}/messages                            历史场次队列（按 queue_id 读）
- POST /scene                                                 手动切场景（→ 广播 scene_set）
- GET  /ui/events（SSE）：snapshot + 循环/控制事件只读镜像
- POST /ui/report：UI 上行回报（intro_done → 开播闸门）
- /ui：直播画面静态托管（Vite 构建产物）

直播循环语义见 loop.py；跨模块访问（neuro URL）约定见 settings.py。
"""

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..broadcaster import EventBroadcaster
from ..config import get_config, module_url
from . import media
from .loop import StreamConflict, StreamLoop
from .neuro_client import NeuroClient
from .storage import Storage


class MessagePayload(BaseModel):
    username: str = "viewer"
    content: str


class ScenePayload(BaseModel):
    id: str
    params: dict[str, Any] | None = None


class ReportPayload(BaseModel):
    event: str


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        cfg = get_config()
        storage = Storage(cfg.WORKDIR / "stream" / "data.db")
        await storage.init()
        bus = EventBroadcaster()
        loop = StreamLoop(storage, bus)
        app.state.storage = storage
        app.state.bus = bus
        app.state.loop = loop
        # 进程重启后的遗留场次兜底：进行中记录标记为 offline
        stale = await storage.get_active_session()
        if stale:
            await storage.update_session(stale["id"], state="offline", ended=True)
            print(
                f"[stream] 清理遗留场次（进程重启所致）: {stale['session_id']}",
                flush=True,
            )
        print(f"[stream] ready: ui_dist={media.ui_dist_dir()}", flush=True)
        yield
        await loop.shutdown()
        await storage.close()

    app = FastAPI(title="stream", lifespan=lifespan)

    def _loop() -> StreamLoop:
        return app.state.loop

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
        }

    @app.get("/stream/status")
    async def stream_status():
        storage: Storage = app.state.storage
        lp = _loop()
        active = await storage.get_active_session()
        recent = (await storage.list_sessions(limit=1)) or None
        depth = None
        if lp.session:
            depth = await storage.queue_depth_after(lp.session["queue_row"], lp.session["cursor"])
        return {
            "state": lp.state,
            "session": lp.session and {k: lp.session[k] for k in (
                "session_id", "queue_id", "channel", "conversation_id", "cursor"
            )},
            "queue_depth_unconsumed": depth,
            "persisted": active or (recent[0] if recent else None),
        }

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
    async def stream_start():
        session = await _guard(_loop().start())
        return {"status": "starting", "session": session["session_id"], "queue": session["queue_id"]}

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
        storage: Storage = app.state.storage
        msg_id = await storage.append_message(lp.session["queue_row"], username, content)
        lp.on_new_message()  # 静默等待模式下的唤醒触发器
        return {"status": "ok", "id": msg_id}

    @app.get("/queues/{queue_id}/messages")
    async def queue_messages_by_id(queue_id: str, after_id: int = 0, limit: int = 200):
        storage: Storage = app.state.storage
        q = await storage.get_queue(queue_id)
        if not q:
            raise HTTPException(status_code=404, detail="queue not found")
        return {"queue_id": queue_id, "messages": await storage.list_messages(q["id"], after_id, max(1, min(limit, 500)))}

    # ---------- 场景控制（手动/脚本；直播循环本身不感知场景内容） ----------

    @app.post("/scene")
    async def scene_set(payload: ScenePayload):
        _loop().on_scene(payload.id)
        return {"status": "ok", "scene": payload.id}

    # ---------- /ui 画面事件通道 ----------

    @app.get("/ui/events")
    async def ui_events():
        """画面只读事件流：连接即推 snapshot（state/scene/session），随后实时广播。"""
        bus: EventBroadcaster = app.state.bus
        lp = _loop()
        q = bus.subscribe()

        async def gen():
            try:
                snapshot = {
                    "state": lp.state,
                    "scene": lp.last_scene,
                    "session": lp.session and {k: lp.session[k] for k in (
                        "session_id", "queue_id", "channel", "conversation_id"
                    )},
                }
                # 中途连接的续播依据：intro 已过时长 / 当前轮 speech 时间线
                ie = lp.intro_elapsed()
                if ie is not None:
                    snapshot["intro_elapsed"] = round(ie, 2)
                turn = lp.turn_snapshot()
                if turn["speech"]:
                    snapshot["turn"] = turn
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

    @app.post("/ui/report")
    async def ui_report(payload: ReportPayload):
        _loop().on_report(payload.event)
        return {"status": "ok"}

    # ---------- 静态托管（放在最后，避免遮挡 API 路由） ----------

    ui_dist = media.ui_dist_dir()
    if ui_dist is not None:
        app.mount("/ui", StaticFiles(directory=ui_dist, html=True), name="ui")
    else:

        @app.get("/ui", include_in_schema=False)
        @app.get("/ui/", include_in_schema=False)
        async def _no_ui():
            return JSONResponse(
                status_code=404,
                content={"detail": "ui dist 未找到：请在 server/neuro_simulator/stream/ui/ 下运行 npm run build"},
            )

    return app


app = create_app()
