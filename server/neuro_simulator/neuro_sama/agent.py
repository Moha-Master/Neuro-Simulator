"""Agent 主循环：流式 LLM 调用 -> 工具执行 -> 结果回填 -> 再次调用，直到模型不再调用工具。

会话历史持久化在 Storage（按 channel + conversation_id 隔离），
system prompt 每轮动态编译：固定人设 + 记忆区（永不缓存）。

事件为 dict：{"type": ..., ...}，由 server 层转成 SSE 下发。事件类型：
reasoning / content / speech / tool_start / tool_result / usage / done / cancelled / error

speech：句级 TTS 产物 {index, text, audio, mime, duration}。content 流按句切分
后逐句并发合成（顺序保持：头堵尾流），合成永不抛异常（失败降级虚拟 TTS 估算）。
"""

import asyncio
import contextlib
import json
import re
import time
from collections import deque
from itertools import count
from typing import AsyncIterator

from openai import AsyncOpenAI, AsyncStream

from ..config import get_config
from . import tts
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


_HAS_WORD = re.compile(r"\w", re.UNICODE)


class _SentenceSplitter:
    """把流式文本切分为完整句子（供句级 TTS）。

    规则：
    - 。！？与换行：立即切分（中英文通用）；
    - .!?：默认切分，但 "3.14" 这类小数点不切——结尾的 '.' 且前为数字时
      挂起等下一 chunk 判定；
    - 句首尾空白剥离；无文字内容（纯标点）的"句子"不产出。
    step 边界由调用方 flush，句子不跨 step。
    """

    def __init__(self) -> None:
        self._buf = ""
        self._scanned = 0

    @staticmethod
    def _keep(sentence: str) -> bool:
        # 纯标点/符号句不送 TTS（如连续句点被切出的碎片）
        return bool(_HAS_WORD.search(sentence))

    def feed(self, text: str) -> list[str]:
        self._buf += text
        out: list[str] = []
        buf = self._buf
        i = self._scanned
        n = len(buf)
        while i < n:
            ch = buf[i]
            if ch in "。！？\n":
                cut = i + 1
            elif ch in ".!?":
                before_digit = i > 0 and buf[i - 1].isdigit()
                after_digit = i + 1 < n and buf[i + 1].isdigit()
                if ch == "." and before_digit and not after_digit and i + 1 >= n:
                    break  # 句尾小数点候选：等下一个 chunk 再判定
                if ch == "." and before_digit and after_digit:
                    i += 1
                    continue
                cut = i + 1
            else:
                i += 1
                continue
            sentence = buf[:cut].strip()
            if sentence and self._keep(sentence):
                out.append(sentence)
            buf = buf[cut:]
            i = 0
            n = len(buf)
        self._buf = buf
        self._scanned = i
        return out

    def flush(self) -> list[str]:
        tail = self._buf.strip()
        self._buf = ""
        self._scanned = 0
        return [tail] if tail and self._keep(tail) else []


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
        client: AsyncOpenAI | None,
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
        svc = cfg.neuro_llm()
        if svc is None or not svc.base_url or not svc.api_key or not svc.model:
            # 引用缺失/悬空/不完整：明确报错而非静默用坏客户端发请求
            yield {
                "type": "error",
                "message": (
                    f"LLM 服务不可用：neuro_sama.model 引用的服务 {cfg.NEURO_MODEL_REF!r} "
                    f"未在 server.llm_services 中完整定义"
                ),
            }
            return
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

        # --- 句级 TTS 管线 ---
        # speech_q: FIFO (句子, 合成 task)。content 流每切出一句即发起合成（并发），
        # 事件按句序取出（头堵尾流）：合成快于生成时零延迟；慢于生成时 step 边界
        # 阻塞等待（保证 speech 事件不跨 step 乱序、done 前全部产出）。
        tts_on = tts.tts_enabled()
        speech_q: deque[tuple[str, asyncio.Task]] = deque()
        speech_no = count()

        def _enqueue_speech(sentence: str) -> None:
            speech_q.append((sentence, asyncio.create_task(tts.synthesize(sentence))))

        async def _drain_ready() -> AsyncIterator[dict]:
            """非阻塞：只取头部已完成的合成结果（保持句序）。"""
            while speech_q and speech_q[0][1].done():
                sentence, task = speech_q.popleft()
                yield {
                    "type": "speech",
                    "index": next(speech_no),
                    "text": sentence,
                    **task.result(),
                }

        async def _drain_all() -> AsyncIterator[dict]:
            """阻塞：等全部排队合成完成并按序产出（step 边界/结束前）。"""
            while speech_q:
                sentence, task = speech_q.popleft()
                result = await task
                yield {
                    "type": "speech",
                    "index": next(speech_no),
                    "text": sentence,
                    **result,
                }

        splitter = _SentenceSplitter()

        # 注：早退路径（cancelled/error/客户端断开）遗留的合成任务由其内部超时
        # 上限自然终结，结果被丢弃——不阻塞生成主循环，也无需外层 finally 清理。
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
                        model=svc.model,
                        messages=llm_messages,
                        tools=self.registry.openai_schema() or None,
                        stream=True,
                        stream_options={"include_usage": True},
                        extra_body=svc.extra_body,
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
                        if tts_on:
                            for sentence in splitter.feed(delta.content):
                                _enqueue_speech(sentence)
                            async for ev in _drain_ready():
                                yield ev
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
                fallback = "Someone tells Vedal there is a problem with my AI."
                # 即使 LLM 失败，也以 Agent 身份留下这一句并存库
                await self.storage.append_message(conv_id, {"role": "assistant", "content": fallback})
                yield {"type": "content", "delta": fallback}
                if tts_on:
                    _enqueue_speech(fallback)
                    async for ev in _drain_all():
                        yield ev

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
                # 终结本步：flush 残句并等待全部合成完成（done 事件前 speech 必须收齐）
                if tts_on:
                    for sentence in splitter.flush():
                        _enqueue_speech(sentence)
                    async for ev in _drain_all():
                        yield ev
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

            # 进入工具步：本步的 speech 全部收尾，保证事件按 step 顺序产出
            if tts_on:
                for sentence in splitter.flush():
                    _enqueue_speech(sentence)
                async for ev in _drain_all():
                    yield ev

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
            fallback = "Someone tells Vedal there is a problem with my AI."
            await self.storage.append_message(conv_id, {"role": "assistant", "content": fallback})
            yield {"type": "content", "delta": fallback}
            if tts_on:
                _enqueue_speech(fallback)
                async for ev in _drain_all():
                    yield ev
            yield {
                "type": "error",
                "message": f"max steps ({cfg.MAX_STEPS}) exceeded, aborted",
            }
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
