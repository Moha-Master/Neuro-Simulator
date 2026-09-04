"""MCP 客户端接入（mcp Python SDK v2 标准用法，已实测）：

- stdio：stdio_client(StdioServerParameters) -> (read, write)
- streamable HTTP：streamable_http_client(url) -> (read, write)
- 两者之上统一挂 ClientSession，initialize -> list_tools -> call_tool
- 发现的 MCP 工具物化成与内置工具同构的 Tool，注册进同一张 ToolRegistry，
  agent loop 对本地/远程工具无差别调度（Gemini CLI / AstrBot 同款模式）。
- 注意 v2 字段为 snake_case：Tool.input_schema、CallToolResult.is_error。
"""

import re
from contextlib import AsyncExitStack

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from .tools import Tool, ToolRegistry

# OpenAI 对 function name 的限制：^[a-zA-Z0-9_-]{1,64}$
_NAME_RE = re.compile(r"[^a-zA-Z0-9_-]+")


def _sanitize(name: str) -> str:
    return _NAME_RE.sub("_", name)[:64] or "tool"


class MCPManager:
    def __init__(self, servers: list[dict]) -> None:
        self._servers = servers
        self._stack = AsyncExitStack()
        self._sessions: dict[str, ClientSession] = {}

    @property
    def servers(self) -> list[dict]:
        return self._servers

    async def start(self, registry: ToolRegistry) -> list[str]:
        """连接所有 server 并注册工具；单个失败不阻塞其余。返回日志行。"""
        logs: list[str] = []
        for cfg in self._servers:
            server_name = cfg.get("name", "mcp")
            try:
                if "command" in cfg:
                    read, write = await self._stack.enter_async_context(
                        stdio_client(
                            StdioServerParameters(
                                command=cfg["command"],
                                args=cfg.get("args", []),
                                env=cfg.get("env"),
                            )
                        )
                    )
                elif "url" in cfg:
                    read, write = await self._stack.enter_async_context(
                        streamable_http_client(cfg["url"])
                    )
                else:
                    logs.append(f"[mcp] {server_name}: config has neither 'command' nor 'url', skipped")
                    continue

                session = await self._stack.enter_async_context(ClientSession(read, write))
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
                logs.append(f"[mcp] {server_name}: connected, {len(tools)} tool(s) registered")
            except Exception as e:
                logs.append(f"[mcp] {server_name}: connection failed: {type(e).__name__}: {e}")
        return logs

    async def close(self) -> None:
        await self._stack.aclose()
