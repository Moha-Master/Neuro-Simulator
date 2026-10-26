"""MCP 客户端接入（mcp Python SDK v2 标准用法，已实测）：

- stdio：stdio_client(StdioServerParameters) -> (read, write)
- streamable HTTP：streamable_http_client(url) -> (read, write)；
  带 headers 时 SDK 不接受该参数——自建 ``create_mcp_http_client(headers=...)``
  并**先进栈**（SDK 不关闭传入的 client），再把 client 交给 transport。
- legacy SSE：sse_client(url, headers=...) -> (read, write)，headers 原生支持，
  client 由 SDK 自管。
- 三者之上统一挂 ClientSession，initialize -> list_tools -> call_tool
- 发现的 MCP 工具物化成与内置工具同构的 Tool，注册进同一张 ToolRegistry，
  agent loop 对本地/远程工具无差别调度（Gemini CLI / AstrBot 同款模式）。
- 注意 v2 字段为 snake_case：Tool.input_schema、CallToolResult.is_error。
"""

import asyncio
import re
from contextlib import AsyncExitStack
from urllib.parse import urlparse

from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
# SDK 私有路径，但 mcp 自身公开使用它；升级 mcp 后若移动，test_tool_admin
# 会在导入阶段第一时间报错（可接受的取舍）。不要顶层 import httpx2。
from mcp.shared._httpx_utils import create_mcp_http_client

from .tools import Tool, ToolRegistry

# OpenAI 对 function name 的限制：^[a-zA-Z0-9_-]{1,64}$
_NAME_RE = re.compile(r"[^a-zA-Z0-9_-]+")


def _sanitize(name: str) -> str:
    return _NAME_RE.sub("_", name)[:64] or "tool"


async def _check_http_endpoint(url: str, timeout: float = 3.0) -> None:
    """连接前先做一次 TCP 探活。

    mcp SDK 的 streamable_http_client 在 TCP 失败时会把异常包进 anyio task
    group，并在不同 task 里做 async generator 收尾（Python 3.14 上会报
    "exit cancel scope in a different task"）。先探活可以让不可达的 server
    以普通 OSError 快速失败，状态里留下可读的 error。
    """
    parsed = urlparse(url)
    host = parsed.hostname
    if not host:
        raise ValueError(f"invalid MCP url: {url!r}")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:
        pass


class MCPManager:
    """一组 MCP 连接的生命周期由**自己的 owner task**持有。

    mcp SDK 的 stdio/streamable-http 客户端建立在 anyio task group 上：进入与
    退出必须在同一个 task。管理端点会在请求 task 里重建 registry，因此把连接
    放进独立 owner task，start() 只等它就绪、close() 只通知它退出。
    """

    def __init__(self, servers: list[dict]) -> None:
        self._servers = servers
        self._sessions: dict[str, ClientSession] = {}
        # 每个 server 的连接结果（管理端点直接读它，不臆造状态）：
        # {name, connected, tool_count, error}
        self.server_status: list[dict] = []
        self._ready = asyncio.Event()
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._logs: list[str] = []

    @property
    def servers(self) -> list[dict]:
        return self._servers

    async def start(self, registry: ToolRegistry) -> list[str]:
        """启动 owner task 并等连接完成；单个失败不阻塞其余。返回日志行。"""
        if self._task is None:
            self._task = asyncio.create_task(
                self._run(registry), name="mcp-manager"
            )
        await self._ready.wait()
        return self._logs

    async def _run(self, registry: ToolRegistry) -> None:
        stack = AsyncExitStack()
        try:
            await self._connect_all(stack, registry)
        finally:
            # 连接结果已经写进 server_status；调用方可以继续（失败也不阻塞）
            self._ready.set()
        try:
            # 保持连接存活，直到 close() 发信号；所有上下文在**本 task** 退出
            await self._stop.wait()
        finally:
            try:
                await stack.aclose()
            except asyncio.CancelledError:
                task = asyncio.current_task()
                if task is not None and task.cancelling():
                    raise
                print("[mcp] close cancelled by a failed server context", flush=True)
            except Exception as e:
                # 连接失败留下的 task group 会在 aclose 时汇总异常；关闭是收尾
                # 动作，不应让它把管理端点的重建流程打断。
                print(f"[mcp] close failed: {type(e).__name__}: {e}", flush=True)

    async def _connect_all(self, stack: AsyncExitStack, registry: ToolRegistry) -> None:
        logs: list[str] = []
        self.server_status = []
        for cfg in self._servers:
            server_name = cfg.get("name", "mcp")
            status = {"name": server_name, "connected": False, "tool_count": 0, "error": None}
            self.server_status.append(status)
            try:
                if cfg.get("command"):
                    read, write = await stack.enter_async_context(
                        stdio_client(
                            StdioServerParameters(
                                command=cfg["command"],
                                args=cfg.get("args", []),
                                env=cfg.get("env"),
                            )
                        )
                    )
                elif cfg.get("url"):
                    await _check_http_endpoint(cfg["url"])
                    # headers 是通用认证通道（Authorization: Bearer ... 或自定义
                    # API key 头）；SSE 的 timeout 类参数刻意不进配置，用 SDK 默认。
                    headers = cfg.get("headers") or None
                    if cfg.get("transport") == "sse":
                        read, write = await stack.enter_async_context(
                            sse_client(cfg["url"], headers=headers)
                        )
                    elif headers:
                        # SDK 对传入的 client 不负责任何关闭——必须自己进栈，
                        # 且在 transport 之前（后进先出：transport 先关、client 后关）。
                        client = create_mcp_http_client(headers=headers)
                        await stack.enter_async_context(client)
                        read, write = await stack.enter_async_context(
                            streamable_http_client(cfg["url"], http_client=client)
                        )
                    else:
                        # 无 headers 的 streamable HTTP 走原路径，行为零变化。
                        read, write = await stack.enter_async_context(
                            streamable_http_client(cfg["url"])
                        )
                else:
                    status["error"] = "config has neither 'command' nor 'url'"
                    logs.append(f"[mcp] {server_name}: config has neither 'command' nor 'url', skipped")
                    continue

                session = await stack.enter_async_context(ClientSession(read, write))
                await session.initialize()
                self._sessions[server_name] = session

                tools = (await session.list_tools()).tools
                for t in tools:
                    base = _sanitize(t.name)
                    exposed = base if registry.get(base) is None else _sanitize(f"{server_name}__{t.name}")

                    async def call(session=session, original=t.name, **kwargs) -> str:
                        r = await session.call_tool(original, kwargs)
                        text = "".join(
                            block.text for block in r.content if getattr(block, "text", None) is not None
                        )
                        if r.is_error:
                            return f"Error: {text or 'MCP tool reported an error'}"
                        return text

                    registry.add(
                        Tool(
                            exposed,
                            (t.description or "").strip() or f"MCP tool {t.name}",
                            t.input_schema or {"type": "object", "properties": {}},
                            call,
                            origin="mcp",
                        )
                    )
                status["connected"] = True
                status["tool_count"] = len(tools)
                logs.append(f"[mcp] {server_name}: connected, {len(tools)} tool(s) registered")
            except asyncio.CancelledError as e:
                # anyio 的 streamable HTTP 客户端在连接失败时会以 cancel scope
                # 的形式向外抛 CancelledError（真正的取消也会走到这里）。
                # 只有外层任务确实被 cancel 时才继续向上传播；否则记为连接失败，
                # 单点失败不阻塞其余 server。
                task = asyncio.current_task()
                if task is not None and task.cancelling():
                    raise
                status["error"] = f"{type(e).__name__}: {e}"[:200]
                logs.append(f"[mcp] {server_name}: connection failed: {type(e).__name__}: {e}")
            except Exception as e:
                status["error"] = f"{type(e).__name__}: {e}"[:200]
                logs.append(f"[mcp] {server_name}: connection failed: {type(e).__name__}: {e}")
        self._logs = logs

    async def close(self) -> None:
        """通知 owner task 退出并等它关闭全部连接（幂等）。"""
        if self._task is None:
            return
        self._stop.set()
        try:
            await self._task
        except asyncio.CancelledError:
            task = asyncio.current_task()
            if task is not None and task.cancelling():
                raise
        finally:
            self._task = None
