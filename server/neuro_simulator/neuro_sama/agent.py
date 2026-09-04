"""Agent 主循环：流式 LLM 调用 -> 工具执行 -> 结果回填 -> 再次调用，直到模型不再调用工具。

会话历史持久化在 Storage（按 channel + conversation_id 隔离），
system prompt 每轮动态编译：固定人设 + 记忆区（永不缓存）。

事件为 dict：{"type": ..., ...}，由 server 层转成 SSE 下发。事件类型：
reasoning / content / tool_start / tool_result / usage / done / error
"""

import asyncio
import contextlib
import json
import time
from typing import AsyncIterator

from openai import AsyncOpenAI
from openai import AsyncStream

from ..config import get_config
from .memory import MemoryStore
from .storage import Storage
from .tools import ToolRegistry


def build_system_prompt(memory: MemoryStore) -> str:
    """system prompt 每轮动态编译：固定人设 + 记忆区（Letta 式，永不缓存）。"""
    parts = [get_config().SYSTEM_PROMPT_BASE]
    memory_section = memory.render()
    if memory_section:
        parts.append(memory_section)
    return "\n\n".join(parts)


async def _stream_with_cancel(stream: AsyncStream, cancel_event: asyncio.Event | None) -> AsyncIterator:
    """逐块迭代 LLM 流；cancel_event 置位时立即打断下一 chunk 的等待并关闭连接。

    直接 `async for chunk in stream` 无法响应中断：LLM 思考阶段没有 chunk，
    __anext__ 的 await 不会返回（且 httpx 的 close() 不能打断挂起的 socket 读）。
    这里让"下一 chunk"任务与 cancel 信号竞速；cancel 胜出时取消挂起任务
    （asyncio 取消可立即中断 socket 读），随即关闭 LLM 连接终止请求。
    """
    if cancel_event is None:
        async for chunk in stream:
            yield chunk
        return

    aiter = stream.__aiter__()
    cancel_waiter = asyncio.ensure_future(cancel_event.wait())
    chunk_task: asyncio.Future | None = None
    try:
        while True:
            chunk_task = asyncio.ensure_future(aiter.__anext__())
            try:
                await asyncio.wait({chunk_task, cancel_waiter}, return_when=asyncio.FIRST_COMPLETED)
                if cancel_waiter.done():
                    return
                try:
                    yield chunk_task.result()
                except StopAsyncIteration:
                    return
            finally:
                if not chunk_task.done():
                    chunk_task.cancel()
                    with contextlib.suppress(BaseException):
                        await chunk_task
    finally:
        if chunk_task is not None and not chunk_task.done():
            chunk_task.cancel()
        if not cancel_waiter.done():
            cancel_waiter.cancel()
        with contextlib.suppress(BaseException):
            await stream.close()


class _Cancelled(Exception):
    """LLM 请求在返回响应头之前被用户中断。"""


async def _create_with_cancel(create_coro, cancel_event: asyncio.Event | None):
    """等待 LLM 请求返回（含响应头）；cancel 置位时立即取消请求并抛 _Cancelled。

    LLM 网关在开始响应前可能长时间不返回响应头（排队/慢思考），
    这段等待同样必须响应中断。
    """
    if cancel_event is None:
        return await create_coro
    create_task = asyncio.ensure_future(create_coro)
    cancel_waiter = asyncio.ensure_future(cancel_event.wait())
    try:
        await asyncio.wait({create_task, cancel_waiter}, return_when=asyncio.FIRST_COMPLETED)
        if cancel_waiter.done() and not create_task.done():
            create_task.cancel()
            with contextlib.suppress(BaseException):
                await create_task
            raise _Cancelled()
        return create_task.result()
    finally:
        if not cancel_waiter.done():
            cancel_waiter.cancel()


def _prune_messages(messages: list[dict], limit: int) -> None:
    """按“轮”裁剪（一轮 = 一条 user 及其后全部 assistant/tool 消息）。

    绝不在轮中间断开，保证 assistant(tool_calls) 与其配对的 role=tool 消息完整，
    避免孤儿 tool 消息导致后续请求 400。仅作用于内存视图，DB 中旧消息保留存档。
    """
    if len(messages) <= limit:
        return
    for i, m in enumerate(messages):
        if m["role"] == "user" and len(messages) - i <= limit:
            del messages[:i]
            return


class NeuroAgent:
    def __init__(
        self,
        client: AsyncOpenAI,
        registry: ToolRegistry,
        storage: Storage,
        memory: MemoryStore,
    ) -> None:
        self.client = client
        self.registry = registry
        self.storage = storage
        self.memory = memory

    async def run(
        self,
        channel: str,
        conversation_id: str,
        user_text: str,
        cancel_event: asyncio.Event | None = None,
    ) -> AsyncIterator[dict]:
        cfg = get_config()
        conv_id = await self.storage.get_or_create_conversation(channel, conversation_id)

        # 取最近 2 倍窗口的行，再按轮裁剪到窗口大小，保证裁剪后仍凑满一轮
        raw = await self.storage.get_messages(conv_id, limit=cfg.MAX_CONTEXT_MESSAGES * 2)
        # 剥离行 id 与 reasoning：reasoning 仅供 UI 展示，不进 LLM 上下文
        # （OpenAI 消息格式不认 reasoning 字段，带入会导致 400）
        messages = [
            {k: v for k, v in m.items() if k not in ("id", "reasoning")} for m in raw
        ]
        _prune_messages(messages, cfg.MAX_CONTEXT_MESSAGES)

        user_msg = {"role": "user", "content": user_text}
        await self.storage.append_message(conv_id, user_msg)
        messages.append(user_msg)

        total_input = 0
        total_output = 0
        all_tool_calls: list[dict] = []
        final_content = ""
        final_reasoning = ""

        for step in range(1, cfg.MAX_STEPS + 1):
            if cancel_event and cancel_event.is_set():
                yield {"type": "cancelled"}
                return
            llm_messages = [
                {"role": "system", "content": build_system_prompt(self.memory)},
                *messages,
            ]

            content = ""
            reasoning = ""
            pending: dict[int, dict] = {}  # tool_call index -> {id, name, args(拼接中的 str)}
            usage = None

            stream_error: str | None = None
            try:
                stream = await _create_with_cancel(
                    self.client.chat.completions.create(
                        model=cfg.MODEL,
                        messages=llm_messages,
                        tools=self.registry.openai_schema() or None,
                        stream=True,
                        stream_options={"include_usage": True},
                        extra_body=cfg.EXTRA_BODY,
                    ),
                    cancel_event,
                )
                async for chunk in _stream_with_cancel(stream, cancel_event):
                    if getattr(chunk, "usage", None):
                        usage = chunk.usage
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta
                    if delta.content:
                        content += delta.content
                        yield {"type": "content", "delta": delta.content}
                    rc = getattr(delta, "reasoning_content", None)
                    if rc:
                        reasoning += rc
                        yield {"type": "reasoning", "delta": rc}
                    for tc in delta.tool_calls or []:
                        slot = pending.setdefault(tc.index, {"id": "", "name": "", "args": ""})
                        if tc.id:
                            slot["id"] = tc.id
                        if tc.function and tc.function.name:
                            slot["name"] = tc.function.name
                        if tc.function and tc.function.arguments:
                            slot["args"] += tc.function.arguments
            except _Cancelled:
                # 响应头返回前被中断（尚无内容可落库）
                yield {"type": "cancelled"}
                return
            except Exception as e:
                stream_error = f"LLM request failed: {type(e).__name__}: {e}"

            if cancel_event is not None and cancel_event.is_set():
                # 流被中断（_stream_with_cancel 关闭了 LLM 连接）：
                # 半截内容落库，保证 UI 与持久化一致
                if content:
                    partial = {"role": "assistant", "content": content}
                    if reasoning:
                        partial["reasoning"] = reasoning
                    await self.storage.append_message(conv_id, partial)
                yield {"type": "cancelled"}
                return

            if stream_error is not None:
                yield {"type": "error", "message": stream_error}
                return

            if usage:
                total_input += usage.prompt_tokens or 0
                total_output += usage.completion_tokens or 0
                yield {
                    "type": "usage",
                    "step": step,
                    "input_tokens": usage.prompt_tokens or 0,
                    "output_tokens": usage.completion_tokens or 0,
                    "total_tokens": usage.total_tokens or 0,
                }

            calls = [pending[i] for i in sorted(pending) if pending[i]["name"]]
            if not calls:
                final_content = content
                final_reasoning = reasoning
                if content:
                    assistant_msg = {"role": "assistant", "content": content}
                    if reasoning:
                        assistant_msg["reasoning"] = reasoning
                    await self.storage.append_message(conv_id, assistant_msg)
                    # 内存视图不带 reasoning（下一 step 的 LLM 上下文保持干净）
                    messages.append({"role": "assistant", "content": content})
                break

            if cancel_event is not None and cancel_event.is_set():
                # 中断落在流结束与工具执行之间：只落纯文本，
                # 避免留下"有 tool_calls 无 tool 结果"的悬空消息破坏后续 LLM 请求
                if content:
                    partial = {"role": "assistant", "content": content}
                    if reasoning:
                        partial["reasoning"] = reasoning
                    await self.storage.append_message(conv_id, partial)
                yield {"type": "cancelled"}
                return

            # assistant 消息（带 tool_calls）落库；reasoning 一并持久化（调用工具前的思考）
            assistant_msg = {
                "role": "assistant",
                "content": content or None,
                "tool_calls": [
                    {
                        "id": c["id"],
                        "type": "function",
                        "function": {"name": c["name"], "arguments": c["args"]},
                    }
                    for c in calls
                ],
            }
            if reasoning:
                assistant_msg["reasoning"] = reasoning
            await self.storage.append_message(conv_id, assistant_msg)
            messages.append({k: v for k, v in assistant_msg.items() if k != "reasoning"})

            # 逐个执行工具，结果立即落库并回填历史
            # （不在工具之间检查中断：部分工具结果已落库时中断会留下悬空 tool_calls；
            #   本步工具跑完后，下一轮 step 开头的检查会兜住中断）
            for c in calls:
                try:
                    args: dict | str = json.loads(c["args"]) if c["args"].strip() else {}
                except json.JSONDecodeError:
                    args = c["args"]
                yield {
                    "type": "tool_start",
                    "step": step,
                    "tool_call_id": c["id"],
                    "name": c["name"],
                    "arguments": args,
                }

                if isinstance(args, str):
                    ok, result = False, (
                        "Error: tool arguments were not valid JSON. "
                        "Re-issue the call with a valid JSON object."
                    )
                    duration_ms = 0
                else:
                    t0 = time.monotonic()
                    ok, result = await self.registry.dispatch(c["name"], args)
                    duration_ms = int((time.monotonic() - t0) * 1000)

                all_tool_calls.append(
                    {
                        "tool_call_id": c["id"],
                        "name": c["name"],
                        "arguments": args,
                        "ok": ok,
                        "result": result,
                    }
                )
                yield {
                    "type": "tool_result",
                    "step": step,
                    "tool_call_id": c["id"],
                    "name": c["name"],
                    "ok": ok,
                    "result": result,
                    "duration_ms": duration_ms,
                }
                tool_msg = {"role": "tool", "tool_call_id": c["id"], "content": result}
                await self.storage.append_message(conv_id, tool_msg)
                messages.append(tool_msg)
        else:
            yield {"type": "error", "message": f"max steps ({cfg.MAX_STEPS}) exceeded, aborted"}
            return

        yield {
            "type": "done",
            "content": final_content,
            "reasoning": final_reasoning,
            "tool_calls": all_tool_calls,
            "total_usage": {
                "input_tokens": total_input,
                "output_tokens": total_output,
                "total_tokens": total_input + total_output,
            },
        }
