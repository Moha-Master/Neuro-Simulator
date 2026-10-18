"""工具系统：内置工具与 MCP 工具统一进同一张注册表，错误作为结果回填给模型。"""

import asyncio
from dataclasses import dataclass
from typing import Awaitable, Callable

from ..config import get_config
from .memory import MemoryStore


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


def build_builtin_tools(memory: MemoryStore) -> list[Tool]:
    async def memory_add(content: str) -> str:
        return await memory.add(content)

    async def model_spin() -> str:
        return "Spin command sent."

    async def model_zoom() -> str:
        return "Zoom command sent."

    return [
        # 画面动作工具：neuro 只负责发起调用；实际动画由 stream 把 tool_start
        # 中的 model_* 转发给 /ui，前端映射到 avatar.spin / avatar.zoom_in。
        Tool(
            "model_spin",
            "Makes your on-stream avatar spin once counter-clockwise. "
            "Don't get too dizzy when spinning, lol.",
            {"type": "object", "properties": {}, "required": []},
            model_spin,
        ),
        Tool(
            "model_zoom",
            "Makes your on-stream avatar zoom in, as if you got closer to the fans. "
            "The zoom resets after you finish speaking.",
            {"type": "object", "properties": {}, "required": []},
            model_zoom,
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
