"""vedal 服务端：托管 dashboard + /manage 管理接口 + /health。

与 dashboard 的通信协议（同域 HTTP，dashboard 由本模块静态托管）：
- GET  /health                    健康检查（dashboard 只检查 vedal；vedal 顺带探测各模块）
- GET  /manage/config             取 config.yaml 各 schema 的 YAML 原文
- PUT  /manage/config             保存各 schema 原文 -> 校验 -> 写盘 -> 按变动 schema 热重载对应模块
- POST /manage/config/reload-all  向所有模块和 vedal 自身执行 reload
- GET  /manage/modules            各模块状态（url/可达性/PID/是否由 vedal 托管）
- POST /manage/module_run/<module_name>    以子进程方式启动模块（python -m neuro_simulator.<module>.main --dir <workdir>）
- POST /manage/module_stop/<module_name>   停止模块（托管进程直接发信号；外部实例经 <workdir>/<module>.pid 定位）
- POST /manage/module_restart/<module_name> 重启
- /manage/<module>/<path...>      代理到目标模块的 /<path...>（完整路径转发；
                                    模块名 = config.yaml 中的 schema 名；
                                    模块 URL = external_url 或 http://{host}:{port}；
                                    响应为 text/event-stream 时逐块流式透传，
                                    其余照旧缓冲后整体返回）

各模块是独立进程（各自一个 uvicorn + 独立可执行入口）；模块启动时写
<workdir>/<module>.pid（见 pidfile.py），因此 vedal 能管理任何入口启动的
实例（命令行手动、子进程托管、甚至 vedal 重启前遗留的）。子进程日志写
<workdir>/logs/<module>.log。vedal 自身不接受被自身管理（run/stop/restart 返回 400）。
"""

import asyncio
import importlib.util
import os
import signal
import subprocess
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import httpx
import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.staticfiles import StaticFiles

from .. import pidfile
from ..config import SERVER_SCHEMA, CONFIG_FILENAME, get_config, parse_module_fields

# ---------- 模块子进程托管 ----------


@dataclass
class _ManagedModule:
    proc: subprocess.Popen
    log_path: Path


_managed: dict[str, _ManagedModule] = {}


def _module_entry_available(name: str) -> bool:
    """模块是否具备约定入口 neuro_simulator.<name>.main。"""
    try:
        return importlib.util.find_spec(f"neuro_simulator.{name}.main") is not None
    except (ImportError, ValueError):
        return False


async def _is_reachable(url: Optional[str]) -> bool:
    if not url:
        return False
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            r = await client.get(f"{url}/health")
            return r.status_code == 200
    except httpx.HTTPError:
        return False


def _log_tail(path: Path, lines: int = 5) -> str:
    try:
        content = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return " | ".join(content[-lines:])[:500]
    except OSError:
        return ""


def _live_pid(module_name: str) -> Optional[int]:
    """模块当前存活 PID：优先 PID 文件（覆盖任何入口启动的实例）。"""
    return pidfile.read_pid_file(get_config().WORKDIR, module_name)


def _signal(pid: int, sig: int) -> None:
    try:
        os.kill(pid, sig)
    except ProcessLookupError:
        pass


async def _stop_module(module_name: str) -> dict[str, Any]:
    """停止模块：托管进程走 Popen；其它实例经 PID 文件定位后发信号。"""
    cfg = get_config()
    entry = _managed.get(module_name)

    # 1) 由 vedal 子进程托管：直接 terminate/kill
    if entry is not None and entry.proc.poll() is None:
        proc = entry.proc
        _managed.pop(module_name, None)
        proc.terminate()
        try:
            await asyncio.wait_for(asyncio.to_thread(proc.wait), timeout=8)
        except asyncio.TimeoutError:
            proc.kill()
            await asyncio.to_thread(proc.wait)
        pidfile.remove_pid_file(cfg.WORKDIR, module_name)
        print(f"[vedal] module {module_name} stopped (pid={proc.pid}, managed)", flush=True)
        return {"status": "stopped", "module": module_name, "pid": proc.pid}
    if entry is not None:
        _managed.pop(module_name, None)  # 注册表里的进程已死，清理

    # 2) 任何入口启动的实例：经 PID 文件定位
    pid = _live_pid(module_name)
    if pid is not None:
        _signal(pid, signal.SIGTERM)
        for _ in range(16):
            if not pidfile.pid_alive(pid):
                break
            await asyncio.sleep(0.5)
        if pidfile.pid_alive(pid):
            _signal(pid, signal.SIGKILL)
            await asyncio.sleep(0.5)
        pidfile.remove_pid_file(cfg.WORKDIR, module_name)
        print(f"[vedal] module {module_name} stopped (pid={pid}, via pidfile)", flush=True)
        return {"status": "stopped", "module": module_name, "pid": pid}

    # 3) 既无 PID 文件又不可达：未运行；可达但无 PID：拒绝
    if await _is_reachable(module_url(module_name)):
        raise HTTPException(
            status_code=409,
            detail=f"module {module_name} 正在运行但没有 {module_name}.pid 文件，无法定位 PID，请手动停止",
        )
    return {"status": "not_running"}


async def _run_module(module_name: str) -> dict[str, Any]:
    """以子进程方式启动模块并等待健康检查通过。"""
    cfg = get_config()
    existing = _managed.get(module_name)
    if existing is not None and existing.proc.poll() is None:
        raise HTTPException(status_code=409, detail=f"module {module_name} 已在运行 (pid={existing.proc.pid})")
    if existing is not None:
        _managed.pop(module_name, None)

    pid = _live_pid(module_name)
    if pid is not None:
        raise HTTPException(status_code=409, detail=f"module {module_name} 已在运行 (pid={pid})")
    if await _is_reachable(module_url(module_name)):
        raise HTTPException(
            status_code=409,
            detail=f"module {module_name} 已在运行但没有 {module_name}.pid 文件，无法启动新实例",
        )

    log_path = cfg.WORKDIR / "logs" / f"{module_name}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_f = open(log_path, "ab")
    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", f"neuro_simulator.{module_name}.main", "--dir", str(cfg.WORKDIR)],
            stdout=log_f,
            stderr=subprocess.STDOUT,
        )
    except Exception:
        log_f.close()
        raise
    _managed[module_name] = _ManagedModule(proc=proc, log_path=log_path)
    print(f"[vedal] module {module_name} started (pid={proc.pid}, log={log_path})", flush=True)

    # 等待启动：最多 10s，进程退出即报失败（附日志尾部），健康检查通过即成功
    for _ in range(20):
        await asyncio.sleep(0.5)
        if proc.poll() is not None:
            log_f.close()
            _managed.pop(module_name, None)
            raise HTTPException(
                status_code=500,
                detail=f"module {module_name} 启动后退出 (code={proc.returncode}): {_log_tail(log_path)}",
            )
        if await _is_reachable(module_url(module_name)):
            log_f.close()
            return {"status": "started", "module": module_name, "pid": proc.pid}
    log_f.close()
    return {
        "status": "started_unverified",
        "module": module_name,
        "pid": proc.pid,
        "detail": "进程存活但 10s 内健康检查未通过，请检查 <workdir>/logs/ 下日志",
    }


def _module_names() -> list[str]:
    """config.yaml 中的模块名列表（除全局 server schema 外的一切顶层 schema）。"""
    return [name for name in get_config().data if name != SERVER_SCHEMA]


def module_url(name: str) -> Optional[str]:
    """模块访问 URL：external_url 优先，否则 http://{host}:{port}；未配置返回 None。"""
    ns = get_config().module(name)
    ext = str(ns.get("external_url") or "").strip()
    if ext:
        return ext.rstrip("/")
    host, port = ns.get("host"), ns.get("port")
    if host and port is not None:
        return f"http://{host}:{port}"
    return None


class SPAStaticFiles(StaticFiles):
    """SPA 回退：无扩展名的未命中路径返回 index.html（vue-router history 模式）。"""

    async def get_response(self, path: str, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as e:
            if e.status_code == 404 and "." not in path.rsplit("/", 1)[-1]:
                return await super().get_response("index.html", scope)
            raise


def _dashboard_dir() -> Optional[Path]:
    # wheel 安装：包内 neuro_simulator/dashboard/（hatchling force-include）
    pkg = Path(__file__).parent.parent / "dashboard"
    if (pkg / "index.html").is_file():
        return pkg
    # editable 安装：仓库根 dashboard/dist
    repo = Path(__file__).parents[3] / "dashboard" / "dist"
    if (repo / "index.html").is_file():
        return repo
    return None


async def _reload_module(name: str) -> dict[str, Any]:
    """向单个模块发送 reload（vedal 自身 = 重读配置）。"""
    if name == "vedal":
        get_config().reload()
        return {"status": "ok"}
    url = module_url(name)
    if not url:
        return {"status": "skipped", "detail": "module has no host/port or external_url"}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(f"{url}/manage/reload")
        if r.status_code == 200:
            return {"status": "ok"}
        return {"status": "error", "detail": f"HTTP {r.status_code}: {r.text[:200]}"}
    except httpx.HTTPError as e:
        return {"status": "error", "detail": f"{type(e).__name__}: {e}"}


async def _reload_modules(names: set[str]) -> dict[str, Any]:
    return {name: await _reload_module(name) for name in sorted(names)}


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        print(f"[vedal] ready: dashboard_dir={_dashboard_dir()} modules={_module_names()}", flush=True)
        yield

    app = FastAPI(title="vedal", lifespan=lifespan)

    # ---------- 健康检查 ----------

    @app.get("/health")
    async def health():
        modules: dict[str, Any] = {}
        for name in _module_names():
            if name == "vedal":
                # 自身不发起 HTTP 探测（会递归），能响应即视为可达
                modules["vedal"] = {"url": module_url("vedal"), "reachable": True}
                continue
            url = module_url(name)
            modules[name] = {"url": url, "reachable": await _is_reachable(url)}
        return {"status": "ok", "module": "vedal", "modules": modules}

    # ---------- /manage：全局配置管理 ----------

    @app.get("/manage/config")
    async def manage_get_config():
        """各 schema 的 YAML 原文（按文件内顺序）。"""
        cfg = get_config()
        return {
            "schemas": {
                name: yaml.safe_dump(schema, allow_unicode=True, sort_keys=False)
                for name, schema in cfg.data.items()
            }
        }

    @app.put("/manage/config")
    async def manage_save_config(request: Request):
        """保存各 schema 原文：校验 -> 写盘 -> 按实际变动的 schema 热重载对应模块（含 vedal 自身）。"""
        payload = await request.json()
        schemas_in: dict[str, str] = payload.get("schemas") or {}
        cfg = get_config()
        old_schemas = cfg.data

        missing = set(old_schemas) - set(schemas_in)
        if missing:
            raise HTTPException(status_code=400, detail=f"缺少 schema: {sorted(missing)}")

        new_schemas: dict[str, Any] = {}
        for name, text in schemas_in.items():
            try:
                parsed = yaml.safe_load(text or "")
            except yaml.YAMLError as e:
                raise HTTPException(status_code=400, detail=f"schema {name}: YAML 解析失败: {e}")
            if parsed is None:
                parsed = {}
            if not isinstance(parsed, dict):
                raise HTTPException(status_code=400, detail=f"schema {name}: 必须是 key: value 映射")
            new_schemas[name] = parsed

        try:
            parse_module_fields(new_schemas)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        changed = [
            name
            for name in list(old_schemas) + [n for n in new_schemas if n not in old_schemas]
            if old_schemas.get(name) != new_schemas.get(name)
        ]
        if changed:
            path = cfg.WORKDIR / CONFIG_FILENAME
            path.write_text(yaml.safe_dump(new_schemas, allow_unicode=True, sort_keys=False), encoding="utf-8")
            # 文件已是新真源：无条件刷新 vedal 自身内存配置（此前仅改模块 schema 时
            # vedal 不刷新，导致代理 module_url 打旧地址、GET /manage/config 返回旧文本）
            cfg.reload()

        # server 是全局共用 schema：变动时影响所有模块；其余 schema 对应各自模块
        targets: set[str] = set()
        for name in changed:
            if name == SERVER_SCHEMA:
                targets.update(_module_names())
                targets.add("vedal")
            else:
                targets.add(name)
        if "vedal" in changed:
            targets.add("vedal")

        reloads = await _reload_modules(targets) if changed else {}
        print(f"[vedal] config saved: changed={changed} reloads={reloads}", flush=True)
        return {"status": "ok", "changed_schemas": changed, "reloads": reloads}

    @app.post("/manage/config/reload-all")
    async def manage_reload_all():
        """向所有模块和 vedal 自身执行 reload。"""
        targets = set(_module_names())
        targets.add("vedal")
        reloads = await _reload_modules(targets)
        print(f"[vedal] reload-all: {reloads}", flush=True)
        return {"status": "ok", "reloads": reloads}

    # ---------- /manage：模块生命周期 + 状态 ----------
    # 注意：必须注册在通用代理 /manage/{module}/{path:path} 之前

    @app.get("/manage/modules")
    async def manage_modules_status():
        modules: dict[str, Any] = {}
        for name in _module_names():
            url = module_url(name)
            reachable = True if name == "vedal" else await _is_reachable(url)
            entry = _managed.get(name)
            managed = entry is not None and entry.proc.poll() is None
            pid = entry.proc.pid if managed else _live_pid(name)
            modules[name] = {
                "url": url,
                "reachable": reachable,
                "managed": managed,
                "pid": pid,
            }
        return {"modules": modules}

    @app.post("/manage/module_run/{module_name}")
    async def manage_module_run(module_name: str):
        """以子进程方式启动模块（入口约定 neuro_simulator.<module>.main）。"""
        if module_name == "vedal":
            raise HTTPException(status_code=400, detail="vedal 是管理进程本身，请用其独立入口启动")
        if module_name == SERVER_SCHEMA or module_name not in get_config().data:
            raise HTTPException(status_code=404, detail=f"unknown module: {module_name}")
        if not _module_entry_available(module_name):
            raise HTTPException(
                status_code=400,
                detail=f"module {module_name} 没有可运行的入口（neuro_simulator.{module_name}.main 不存在）",
            )
        return await _run_module(module_name)

    @app.post("/manage/module_stop/{module_name}")
    async def manage_module_stop(module_name: str):
        """停止模块（托管进程直接发信号；其它实例经 PID 文件定位）。"""
        if module_name == "vedal":
            raise HTTPException(status_code=400, detail="vedal 是管理进程本身，请直接停止其入口进程")
        if module_name == SERVER_SCHEMA or module_name not in get_config().data:
            raise HTTPException(status_code=404, detail=f"unknown module: {module_name}")
        return await _stop_module(module_name)

    @app.post("/manage/module_restart/{module_name}")
    async def manage_module_restart(module_name: str):
        """重启模块：先停止（若由 vedal 托管）再启动。"""
        if module_name == "vedal":
            raise HTTPException(status_code=400, detail="vedal 是管理进程本身，请直接重启其入口进程")
        if module_name == SERVER_SCHEMA or module_name not in get_config().data:
            raise HTTPException(status_code=404, detail=f"unknown module: {module_name}")
        if not _module_entry_available(module_name):
            raise HTTPException(
                status_code=400,
                detail=f"module {module_name} 没有可运行的入口（neuro_simulator.{module_name}.main 不存在）",
            )
        await _stop_module(module_name)  # 运行中则停止（托管或 PID 文件定位）；未运行则无操作
        return await _run_module(module_name)

    # ---------- /manage/<module>/...：模块请求代理（完整路径转发 + SSE 流式透传） ----------

    @app.api_route("/manage/{module}/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
    async def proxy_module(module: str, path: str, request: Request):
        if module == SERVER_SCHEMA:
            raise HTTPException(status_code=400, detail=f"{SERVER_SCHEMA} 是全局 schema，不是可代理的模块")
        if module not in get_config().data:
            raise HTTPException(status_code=404, detail=f"unknown module: {module}")
        url = module_url(module)
        if not url:
            raise HTTPException(status_code=503, detail=f"module {module} 未配置 host/port 或 external_url")

        body = await request.body()
        # read 超时置 None：SSE 长连接期间可能较长时间没有新数据
        client = httpx.AsyncClient(timeout=httpx.Timeout(60.0, read=None))
        try:
            req = client.build_request(
                request.method,
                f"{url}/{path}",
                params=dict(request.query_params),
                content=body,
                headers={"Content-Type": request.headers.get("content-type", "application/json")},
            )
            r = await client.send(req, stream=True)
        except httpx.HTTPError as e:
            await client.aclose()
            raise HTTPException(status_code=502, detail=f"module {module} 请求失败: {type(e).__name__}: {e}")

        ctype = r.headers.get("content-type", "")
        if "text/event-stream" in ctype:
            # 流式透传：client 的生命周期跟随响应流（流结束/客户端断开时清理）
            async def pipe():
                try:
                    async for chunk in r.aiter_bytes():
                        yield chunk
                finally:
                    await r.aclose()
                    await client.aclose()

            return StreamingResponse(
                pipe(),
                status_code=r.status_code,
                media_type=ctype,
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        try:
            content = await r.aread()
        except httpx.HTTPError as e:
            await r.aclose()
            await client.aclose()
            raise HTTPException(status_code=502, detail=f"module {module} 请求失败: {type(e).__name__}: {e}")
        await r.aclose()
        await client.aclose()
        return Response(content=content, status_code=r.status_code, media_type=ctype)

    # ---------- dashboard 静态托管（放在最后，避免遮挡 API 路由） ----------

    dash_dir = _dashboard_dir()
    if dash_dir:
        app.mount("/", SPAStaticFiles(directory=dash_dir, html=True), name="dashboard")
    else:
        @app.get("/")
        async def _no_dashboard():
            return {"detail": "dashboard dist 未找到：请在 dashboard/ 下运行 npm run build"}

    return app


app = create_app()
