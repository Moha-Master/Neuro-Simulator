"""vedal 服务端：托管 dashboard + /manage 管理接口 + /health。

与 dashboard 的通信协议（同域 HTTP，dashboard 由本模块静态托管）：
- GET  /health                    健康检查（dashboard 只检查 vedal；vedal 顺带探测各模块）
- GET  /manage/state              各模块状态机投影的全景聚合（并发扇出，原样透传）
- GET  /manage/config             取 config.json 结构化内容 + 服务反查表
- PUT  /manage/config             提交结构化 config -> 悬空引用清理 -> 校验 -> 写盘 -> 按变动 schema 热重载
- POST /manage/config/reload-all  向所有模块和 vedal 自身执行 reload
- GET  /manage/modules            各模块状态（url/可达性/PID/是否由 vedal 托管）
- POST /manage/module_run/<module_name>    以子进程方式启动模块（python -m neuro_simulator.<module>.main --dir <workdir>）
- POST /manage/module_stop/<module_name>   停止模块（托管进程直接发信号；外部实例经 <workdir>/<module>.pid 定位）
- POST /manage/module_restart/<module_name> 重启
- GET  /manage/vedal/logs[/events]     vedal 自己的内存日志（活体；形状与其它模块经代理的一致）
- GET  /manage/logs/<module>           读某模块磁盘日志的尾部（死档；进程不在了也能读）
- /manage/<module>/<path...>      代理到目标模块的 /<path...>（完整路径转发；
                                    模块名 = config.json 中的 schema 名；
                                    模块 URL = external_url 或 http://{host}:{port}；
                                    响应为 text/event-stream 时逐块流式透传，
                                    其余照旧缓冲后整体返回）

各模块是独立进程（各自一个 uvicorn + 独立可执行入口）；模块启动时写
<workdir>/<module>.pid（见 pidfile.py），因此 vedal 能管理任何入口启动的
实例（命令行手动、子进程托管、甚至 vedal 重启前遗留的）。vedal 自身不接受被
自身管理（run/stop/restart 返回 400）。

**日志由进程自己写**（见 log_kit.py）：每个入口在 main() 里把 stdout/stderr 套一层
tee 落到 <workdir>/logs/<模块名>.log，所以手动跑的实例与 vedal 托管的实例都有日志，
vedal 不再重定向子进程输出（否则每行双写）。代价是：进程在装 tee 之前（import 期）
就崩的话，日志文件是空的——那种情况的 traceback 只在 vedal 的终端里。
"""

import asyncio
import importlib.util
import json
import os
import signal
import subprocess
import sys
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.staticfiles import StaticFiles

from .. import log_kit, pidfile
from ..config import (
    CONFIG_FILENAME,
    SERVER_SCHEMA,
    get_config,
    service_usage,
    sweep_dangling_refs,
    validate_config,
)
from ..config import module_url as _module_url

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


def _log_tail(module_name: str, pid: int) -> str:
    """某次运行（按 pid）在磁盘上留下的输出末尾，用于启动失败的 detail。

    磁盘文件是跨运行追加的（进程死后它是唯一证据，绝不能启动时截断），所以必须按
    **运行分隔行**取本次那一段：否则进程秒崩、一行没写时，detail 会端出上一个运行的
    尾巴冒充本次崩溃原因——这是旧实现真实踩过的坑（旧代码用追加模式读整个文件）。
    """
    return log_kit.read_run_tail(get_config().WORKDIR, module_name, pid)



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

    log_path = log_kit.log_path(cfg.WORKDIR, module_name)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    # **不再重定向子进程 stdout**：日志由进程自己 tee（log_kit），vedal 再指一次就是
    # 每行双写。子进程继承 vedal 的 stdout，操作者终端照样看得见它的输出。
    proc = subprocess.Popen(
        [sys.executable, "-m", f"neuro_simulator.{module_name}.main", "--dir", str(cfg.WORKDIR)],
    )
    _managed[module_name] = _ManagedModule(proc=proc, log_path=log_path)
    print(f"[vedal] module {module_name} started (pid={proc.pid}, log={log_path})", flush=True)

    # 等待启动：最多 10s，进程退出即报失败（附日志尾部），健康检查通过即成功
    for _ in range(20):
        await asyncio.sleep(0.5)
        if proc.poll() is not None:
            _managed.pop(module_name, None)
            raise HTTPException(
                status_code=500,
                detail=f"module {module_name} 启动后退出 (code={proc.returncode}): {_log_tail(module_name, proc.pid)}",
            )
        if await _is_reachable(module_url(module_name)):
            return {"status": "started", "module": module_name, "pid": proc.pid}
    return {
        "status": "started_unverified",
        "module": module_name,
        "pid": proc.pid,
        "detail": f"进程存活但 10s 内健康检查未通过，见 dashboard 的 Logs 页或 {log_path}",
    }


def _module_names() -> list[str]:
    """config.json 中的模块名列表（除全局 server schema 外的一切顶层 schema）。"""
    return [name for name in get_config().data if name != SERVER_SCHEMA]


def module_url(name: str) -> Optional[str]:
    """模块访问 URL（实现在共享 config.module_url：external_url 优先，其次 host/port）。"""
    return _module_url(get_config(), name)


# 磁盘日志一次最多给多少：这是死档阅读窗口，比内存端点的夹取更宽（文件可以整体比
# 内存上限大得多），但仍要有界——不能把一个可能上百 MB 的历史甩给浏览器。
DISK_READ_MAX_BYTES = 2 * 1024 * 1024

# 状态机投影的读超时：模块的 provider 是同步无 I/O 的，2s 足够；这是聚合层的
# 观测纪律，不进 config（改它没有业务含义，只会让 dashboard 更慢）。
STATE_TIMEOUT_S = 2.0

# 各模块状态投影的路径由模块自己定（neuro 是 /status，stream 是 /stream/status），
# 这里只做"按约定拼 URL"的小表；未来模块默认按 /status 处理。
_MODULE_STATUS_PATHS = {
    "neuro_sama": "/status",
    "stream": "/stream/status",
}


def _local_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


async def _fetch_module_state(
    name: str,
    *,
    want: Optional[str],
    drop: Optional[str],
) -> dict[str, Any]:
    """读取一个模块的 `/status` 投影，**原样**放进聚合结果。

    规则：只加观测字段（url/reachable/observed_at/elapsed_ms/error），不改名、
    不摊平、不合并模块返回的任何内容；任何失败都必须降级成本模块
    `reachable=false + error`，绝不让整次 `/manage/state` 变成 500。
    """
    url = module_url(name)
    observed_at = _local_iso()
    started = time.monotonic()
    entry: dict[str, Any] = {
        "url": url,
        "reachable": False,
        "observed_at": observed_at,
        "elapsed_ms": 0,
        "status": None,
        "error": None,
    }
    if not url:
        entry["error"] = "no url configured"
        return entry

    params: dict[str, str] = {}
    if want is not None:
        params["want"] = want
    if drop is not None:
        params["drop"] = drop

    path = _MODULE_STATUS_PATHS.get(name, "/status")
    try:
        async with httpx.AsyncClient(timeout=STATE_TIMEOUT_S) as client:
            response = await client.get(f"{url}{path}", params=params or None)
        if response.status_code != 200:
            raise RuntimeError(f"HTTP {response.status_code}: {response.text[:200]}")
        entry["status"] = response.json()
        entry["reachable"] = True
    except Exception as e:  # 连接/超时/非 200/坏 JSON 一律降级该模块
        entry["error"] = f"{type(e).__name__}: {e}"
    finally:
        # 观测完成时刻：freshness 描述的是"这份快照多久前拿到"，不是发出请求的时刻
        entry["observed_at"] = _local_iso()
        entry["elapsed_ms"] = max(0, int((time.monotonic() - started) * 1000))
    return entry


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

    # ---------- /manage/state：各模块状态机投影的全景聚合 ----------
    # 必须注册在通用代理 /manage/{module}/{path} 之前；根路径 `/state` 属于 dashboard
    # 的 State 页（spa 回退），API 挤在根上会把那个页面遮蔽掉（2026-10-24 实机踩过）。

    @app.get("/manage/state")
    async def state(
        want: Optional[str] = None,
        drop: Optional[str] = None,
        modules: Optional[str] = None,
    ):
        """并发读取各模块 `/status`，原样聚合；**不做二次计算/缓存/摊平**。

        - `want`/`drop` 原样透传给每个模块（分区级过滤由模块解释）；
        - `modules=stream,neuro_sama` 只聚合指定模块（默认 config 里的全部模块）；
        - vedal 自身没有状态机投影，不进 `modules`；
        - `/manage/modules` 的生命周期信息（managed/pid）是另一条路径的语义，不混进来；
        - 任何模块失败只影响它自己：`reachable=false + error`，HTTP 永远 200。
        """
        names = [name for name in _module_names() if name != "vedal"]
        if modules:
            requested = {part.strip() for part in modules.split(",") if part.strip()}
            names = [name for name in names if name in requested]

        results = await asyncio.gather(
            *(_fetch_module_state(name, want=want, drop=drop) for name in names),
            return_exceptions=True,
        )

        payload: dict[str, Any] = {}
        for name, result in zip(names, results):
            if isinstance(result, BaseException):
                # _fetch 自己已经兜底过；这里是防御性分支（例如取消/意外异常）
                payload[name] = {
                    "url": module_url(name),
                    "reachable": False,
                    "observed_at": _local_iso(),
                    "elapsed_ms": 0,
                    "status": None,
                    "error": f"{type(result).__name__}: {result}",
                }
            else:
                payload[name] = result
        return {"vedal": {"module": "vedal", "ts": _local_iso()}, "modules": payload}

    # ---------- /manage：全局配置管理 ----------

    @app.get("/manage/config")
    async def manage_get_config():
        """结构化配置 + 服务反查表（每个服务 id 被哪些配置字段引用）。"""
        cfg = get_config()
        return {"config": cfg.data, "usage": service_usage(cfg.data)}

    @app.put("/manage/config")
    async def manage_save_config(request: Request):
        """保存结构化配置：悬空引用自动清空 -> 校验 -> 写盘 -> 按变动 schema 热重载。

        删除服务导致的引用悬空在此处兜底清理（dashboard 已在删除时先行确认并
        清空，这里防手动编辑/并发覆盖）；返回 cleared_references 供前端提示。
        """
        payload = await request.json()
        new_config = payload.get("config")
        if not isinstance(new_config, dict):
            raise HTTPException(status_code=400, detail="body 必须是 {\"config\": {...}}")

        cleared = sweep_dangling_refs(new_config)
        try:
            validate_config(new_config)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        cfg = get_config()
        old_schemas = cfg.data
        changed = [
            name
            for name in list(old_schemas) + [n for n in new_config if n not in old_schemas]
            if old_schemas.get(name) != new_config.get(name)
        ]
        if changed:
            path = cfg.WORKDIR / CONFIG_FILENAME
            path.write_text(
                json.dumps(new_config, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            # 文件已是新真源：无条件刷新 vedal 自身内存配置（此前仅改模块 schema 时
            # vedal 不刷新，导致代理 module_url 打旧地址、GET /manage/config 返回旧内容）
            cfg.reload()

        # server 是全局共用 schema（服务注册表在此）：变动时影响所有引用它的模块
        targets: set[str] = set()
        for name in changed:
            if name == SERVER_SCHEMA:
                # 仅重载当前可达的模块（未运行的模块跳过，避免 ConnectError）
                for m in _module_names():
                    url = module_url(m)
                    if url and await _is_reachable(url):
                        targets.add(m)
                targets.add("vedal")
            else:
                targets.add(name)
        if "vedal" in changed:
            targets.add("vedal")

        reloads = await _reload_modules(targets) if changed else {}
        print(f"[vedal] config saved: changed={changed} cleared={cleared} reloads={reloads}", flush=True)
        return {
            "status": "ok",
            "changed_schemas": changed,
            "reloads": reloads,
            "cleared_references": cleared,
        }

    @app.post("/manage/config/reload-all")
    async def manage_reload_all():
        """向所有**可达**的模块和 vedal 自身执行 reload。"""
        targets = {"vedal"}
        for m in _module_names():
            url = module_url(m)
            if url and await _is_reachable(url):
                targets.add(m)
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

    # ---------- 进程日志 ----------
    #
    # 两条路径、两个语义（与"读状态"那套 /status vs /state/events 的分工同构）：
    # - `/manage/vedal/logs[/events]`   vedal **自己**的内存（活体；路径形状与其它模块
    #   经代理的 `/manage/<module>/logs` 一致，前端一套 URL 模式吃三个模块）
    # - `/manage/logs/<module>`         磁盘文件（vedal 读，进程死了仍可读）
    # 模块活着时前端走内存；探测到不可达就回落到磁盘端点，并明确告诉用户
    # "这不是实时输出，是那个进程最后留下的记录"。

    @app.get("/manage/vedal/logs")
    async def own_logs(max_bytes: int = log_kit.TAIL_MAX_BYTES):
        """vedal 自己的输出尾部（内存环形缓冲，只含本次运行）。"""
        log = log_kit.current("vedal")
        if log is None:
            raise HTTPException(status_code=503, detail="日志未启用：vedal 不是经 main() 启动的")
        return log.snapshot(log_kit.clamp_bytes(log, max_bytes))

    @app.get("/manage/vedal/logs/events")
    async def own_logs_events(since: int = -1, max_bytes: int = log_kit.TAIL_MAX_BYTES):
        """vedal 自己日志的 SSE 跟随。"""
        log = log_kit.current("vedal")
        if log is None:
            raise HTTPException(status_code=503, detail="日志未启用：vedal 不是经 main() 启动的")
        return StreamingResponse(
            log_kit.sse_stream(
                log, since=since, max_bytes=log_kit.clamp_bytes(log, max_bytes)
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/manage/logs/{module_name}")
    async def module_logs_from_disk(module_name: str, max_bytes: int = log_kit.TAIL_MAX_BYTES):
        """某模块磁盘日志的尾部（进程不在了才需要它，故它读文件而不是转发请求）。

        只读**当前**那个文件（`.log`）；更早的内容在 `.log.1`/`.log.2`，响应里的
        `disk.files` 会列出它们，前端据此说明"总共还有多少可翻"。
        """
        cfg = get_config()
        if module_name not in cfg.data:
            raise HTTPException(status_code=404, detail=f"unknown module: {module_name}")
        info = log_kit.read_disk_tail(
            cfg.WORKDIR, module_name, max_bytes=max(1, min(int(max_bytes), DISK_READ_MAX_BYTES))
        )
        # 只报"有没有活着的进程"（读 PID 文件），不报 URL/可达性：那个的权威是
        # /manage/modules，前端已经有它，这里抄第二份就会有两个真相。
        info["pid"] = _live_pid(module_name)
        info["running"] = info["pid"] is not None
        return info

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
        # 媒体元素（开场视频）会发 Range 请求做 seek/断点加载；代理必须原样带上，
        # 并保留上游的 206 / Content-Range 等响应头，否则浏览器只能拿到一个
        # 被缓冲的 200 全量响应，视频会从头加载、拖动/中途接续失效。
        forward_headers = {"Content-Type": request.headers.get("content-type", "application/json")}
        for name in ("range", "if-range", "if-none-match", "if-modified-since"):
            value = request.headers.get(name)
            if value:
                forward_headers[name] = value
        # read 超时置 None：SSE 长连接期间可能较长时间没有新数据
        client = httpx.AsyncClient(timeout=httpx.Timeout(60.0, read=None))
        try:
            req = client.build_request(
                request.method,
                f"{url}/{path}",
                params=dict(request.query_params),
                content=body,
                headers=forward_headers,
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
                except httpx.HTTPError:
                    # 上游（neuro/stream）中途掉线：客户端 EventSource 会自动重连，
                    # 这里静默结束，避免 uvicorn 把断管当成未处理异常刷 traceback。
                    return
                finally:
                    await r.aclose()
                    await client.aclose()

            return StreamingResponse(
                pipe(),
                status_code=r.status_code,
                media_type=ctype,
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        response_headers = {
            name: r.headers[name]
            for name in (
                "content-range",
                "accept-ranges",
                "content-length",
                "etag",
                "last-modified",
                "cache-control",
            )
            if name in r.headers
        }
        # 视频/音频/图片按块透传：媒体元素能边下边播，Range seek 也无需等整个
        # 文件缓冲完。其它响应保持原来的整体缓冲语义。
        if ctype.startswith(("video/", "audio/", "image/")):
            async def pipe_media():
                try:
                    async for chunk in r.aiter_bytes():
                        yield chunk
                finally:
                    await r.aclose()
                    await client.aclose()

            return StreamingResponse(
                pipe_media(),
                status_code=r.status_code,
                media_type=ctype,
                headers=response_headers,
            )

        try:
            content = await r.aread()
        except httpx.HTTPError as e:
            await r.aclose()
            await client.aclose()
            raise HTTPException(status_code=502, detail=f"module {module} 请求失败: {type(e).__name__}: {e}")
        await r.aclose()
        await client.aclose()
        return Response(
            content=content,
            status_code=r.status_code,
            media_type=ctype,
            headers=response_headers,
        )

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
