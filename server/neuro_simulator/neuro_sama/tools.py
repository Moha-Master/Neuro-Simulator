"""工具系统：内置工具与 MCP 工具统一进同一张注册表，错误作为结果回填给模型。"""

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Awaitable, Callable

from ..config import get_config
from .memory import MemoryStore

_WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict                       # JSON Schema（OpenAI function calling 直接可用）
    handler: Callable[..., Awaitable[str]] # **args -> str
    origin: str = "builtin"                # "builtin" | "mcp"


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def add(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool name: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def openai_schema(self) -> list[dict]:
        """转成 OpenAI chat.completions 的 tools 参数（按名排序以利用 prompt cache）。"""
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in sorted(self._tools.values(), key=lambda x: x.name)
        ]

    async def dispatch(self, name: str, args: dict) -> tuple[bool, str]:
        """执行工具。返回 (ok, 结果文本)；任何异常都转成错误文本回填，不打断 agent loop。"""
        tool = self._tools.get(name)
        if tool is None:
            return False, f"Error: unknown tool {name!r}."
        timeout = get_config().TOOL_TIMEOUT
        try:
            async with asyncio.timeout(timeout):
                result = await tool.handler(**args)
            return True, str(result)
        except TimeoutError:
            return False, f"Error: tool {name!r} timed out after {timeout}s."
        except TypeError as e:
            return False, f"Error: invalid arguments for {name!r}: {e}"
        except Exception as e:
            return False, f"Error: tool {name!r} raised {type(e).__name__}: {e}"


def _format_now() -> str:
    now = datetime.now().astimezone()
    offset = now.strftime("%z")
    tz = f"{offset[:3]}:{offset[3:]}" if offset else "UTC"
    return f"{now:%Y-%m-%d %H:%M:%S} {_WEEKDAYS[now.weekday()]} (UTC{tz})"


def build_builtin_tools(memory: MemoryStore) -> list[Tool]:
    async def time_get_current() -> str:
        return _format_now()

    async def memory_add(content: str) -> str:
        return await memory.add(content)

    return [
        Tool(
            "time_get_current",
            "Get the current detailed date, weekday and time. "
            "Call this whenever you need to know what time or day it is now.",
            {"type": "object", "properties": {}, "required": []},
            time_get_current,
        ),
        Tool(
            "memory_add",
            "Remember a persistent piece of information (the user's name or preferred form of address, "
            "preferences, important facts, events that happened, etc.). "
            "Only record durable information; do not record small talk or guesses; do not record the same thing twice.",
            {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "One-line memory in a declarative sentence.",
                    },
                },
                "required": ["content"],
            },
            memory_add,
        ),
    ]
