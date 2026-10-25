"""stream 模块 Chatbot：在直播期间模拟 Twitch 弹幕发送者。

持有独立循环，不受直播主循环阻塞；在 Session 启动时即刻开启，在 Session 结束时关闭。
维护内存中的临时对话历史（不存数据库）。

Neuro 的三态叙述（刚开播 / 说了什么 / 疑似睡着）**不在代码里**：chatbot 只缓存
"本轮正文 + 最近一次 neuro 状态投影"，措辞与沉默阈值全部由消息模板（Jinja）决定。

并发模型（固定节拍调度器）：
- 调度循环只负责"到点发起"：每 interval_s（± jitter）spawn 一个生成任务后立刻继续排程，
  因此弹幕产出节奏取决于设定间隔，而不是 LLM 响应时长
- 同时在途任务数上限 max_concurrent；到上限的 tick 直接跳过（网关卡死时不堆积）
- 每批弹幕在各自完成时立即入库并广播，画面上天然是错开到达的
- LLM 客户端跨任务复用，服务引用变更时才重建

人设与消息内容全部来自数据库（可在 dashboard 编辑）：
- ChatbotPersonaStore：system prompt + 观众视角 core memory
- TemplateStore('chatbot_user')：每轮注入的 user message 模板
"""

import asyncio
import json
import logging
import random
import re
import time
import uuid
from collections import deque
from typing import Awaitable, Callable, Optional

from openai import AsyncOpenAI

from ..templates import TemplateRenderError
from . import settings
from .usernames import random_username

logger = logging.getLogger("stream.chatbot")

# 表情代码大小写敏感（`neuroICANT` 不是 `neuroicant`），但小模型经常写成全小写。
# 这里对已知代码做一次大小写归一；与 defaults/chatbot_prompt.md 的词表保持一致
# （不在表里的词原样保留，prompt 改了也不会出错）。
_EMOTE_CANON = {
    name.lower(): name
    for name in (
        "LUL", "KEKW", "LULE", "LOL", "Pog", "Sadge", "monkaS", "Clueless", "ICANT",
        "EZ", "COPIUM", "catJAM", "Clap", "AYAYA", "NODDERS", "mhm", "o7", "Kappa",
        "monkaW", "Aware", "Prayge", "GIGACHAD", "LETSGO", "PepeLaugh", "HYPERCLAP",
        "vedalSad", "vedalBread", "vedalGymbag", "vedalNURU", "vedalEvilGiggle",
        "vedalCry", "vedalJam", "vedalOkayge", "vedalSip", "vedalSpin", "vedalVibe",
        "vedalRap", "vedalStare", "vedalSnooze", "vedalBedge", "vedalClown",
        "vedalDepress", "vedalDespair", "vedalHmm", "vedalYes", "vedalGasp",
        "vedalRage", "vedalTroll", "vedalSmile", "vedalCool", "vedalCute", "vedalEvil",
        "neurom", "neuroClueless", "neuroICANT", "neuroCry", "neuroHeart", "neuroJam",
        "neuroLUL", "neuroSad", "neuroScared", "neuroSip", "neuroSpin", "neuroStare",
        "neuroYay", "evilSoCute", "evilLUL", "evilGiggle", "evilICANT", "evilCry",
        "evilSad", "evilW", "evilYes", "evilZ", "evilJam", "evilStare", "evilSip",
        "evilAYAYA", "evilBedge", "evilScared", "evilHug",
    )
}
_EMOTE_WORD = re.compile(r"[A-Za-z][A-Za-z0-9]*")

_HISTORY_BATCHES = 6
# chatbot 模板能看到多少条"观众最近消息"（含自己发的行；人设 prompt 要求不复述）
_RECENT_CHAT = 12
# 同场次内已入列消息的签名窗口：模型爱复述自己上一批的"金句"，
# 这里做一层廉价去重，避免刷屏式重复（并发下也兜住同一批被重复提交的极端情况）。
_DEDUP_WINDOW = 300

# 工具调用是唯一的发言通道：普通 assistant 正文一律丢弃。
_CHAT_TOOL = "send_chat_message"

# tool_choice 兼容档位：先点名具体函数，网关不认再退 required / auto。
# 档位在进程内缓存（不随场次重置），避免每个 session 都重新撞一次 400。
_TOOL_CHOICES: tuple[object, ...] = (
    {"type": "function", "function": {"name": _CHAT_TOOL}},
    "required",
    "auto",
)

_CHAT_CONTRACT = (
    "\n\n# Chat output contract\n"
    "Your normal reply text is never shown to viewers: only tool calls are visible. "
    f"You MUST call the `{_CHAT_TOOL}` tool to speak; each item in `messages` is one "
    "complete chat line from one viewer. Do not add usernames, numbering, markdown, "
    "or explanations. If any format instruction above conflicts with this, the tool "
    "call wins."
)

MetadataProvider = Callable[[], "tuple[str, str]"]
AppendMessages = Callable[[list[tuple[str, str]]], Awaitable[list[int]]]


def _tool_schema(count: int) -> dict:
    """按本轮条数生成工具 schema（maxItems 跟 config 走）。"""
    return {
        "type": "function",
        "function": {
            "name": _CHAT_TOOL,
            "description": (
                "Send one or more messages to the live Twitch chat as viewer(s). "
                "This is the only way to speak; your normal reply text is discarded. "
                "Each item is one complete chat line from one viewer."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "messages": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "maxItems": max(1, int(count)),
                        "description": (
                            "Chat lines, one per viewer. Plain text, no usernames, "
                            "no numbering."
                        ),
                    }
                },
                "required": ["messages"],
            },
        },
    }


class ChatbotManager:
    def __init__(self, storage, persona, templates) -> None:
        self.storage = storage
        self.persona = persona
        self.templates = templates
        self._scheduler: Optional[asyncio.Task] = None
        self._tasks: set[asyncio.Task] = set()
        self._queue_row: Optional[int] = None
        self._append_messages: Optional[AppendMessages] = None
        self._metadata: MetadataProvider = lambda: ("", "")
        # 内容平面缓存：本轮正文 + 最近一次 neuro 状态（剥掉 envelope/graph 的 dict）。
        # 它们只喂给模板，不在 chatbot 侧做任何"睡没睡着"的判定。
        self._neuro_said: str = ""
        self._neuro_status: dict = {}
        # loop 注入：messages 文本 → 完整 Jinja context（含 stream_status）
        self._context_provider: Optional[Callable[[str], dict]] = None
        # 按批保存：每批 = assistant(tool_calls) + tool 结果。半截 tool 对会让
        # 部分网关直接 400，所以裁剪只能整批丢。
        self._history: list[list[dict]] = []
        self._history_lock = asyncio.Lock()
        self._seen: set[str] = set()
        self._seen_order: deque[str] = deque(maxlen=_DEDUP_WINDOW)
        self._warned_ref: str | None = None
        self._skipped = False
        self._client: Optional[AsyncOpenAI] = None
        self._client_key: tuple | None = None
        # 工具档位与丢弃计数都是进程级可观测事实；start_session 只重置后者。
        self._tool_choice_index: int = 0
        self.discarded: int = 0
        self.last_discard: Optional[dict] = None

    # ---------- 生命周期 ----------

    def start_session(
        self,
        queue_row: int,
        append_messages_cb: AppendMessages,
        metadata_provider: Optional[MetadataProvider] = None,
        context_provider: Optional[Callable[[str], dict]] = None,
    ) -> None:
        self.stop_session()
        self._queue_row = queue_row
        self._append_messages = append_messages_cb
        self._metadata = metadata_provider or (lambda: ("", ""))
        self._context_provider = context_provider
        self._neuro_said = ""
        self._neuro_status = {}
        self._history = []
        self._seen = set()
        self._seen_order = deque(maxlen=_DEDUP_WINDOW)
        self._skipped = False
        self.discarded = 0
        self.last_discard = None
        # 注意：不重置 _tool_choice_index。网关兼容性是进程级事实，降级后
        # 后续场次直接沿用，不必每场重新撞一次 400。
        self._scheduler = asyncio.create_task(self._scheduler_loop())
        print(f"[stream.chatbot] Session {queue_row} chatbot loop started", flush=True)

    def stop_session(self) -> None:
        if self._scheduler and not self._scheduler.done():
            self._scheduler.cancel()
        self._scheduler = None
        for task in self._tasks:
            if not task.done():
                task.cancel()
        self._tasks.clear()
        self._queue_row = None
        self._append_messages = None
        self._context_provider = None

    async def aclose(self) -> None:
        """关闭复用的 LLM 客户端（进程退出时调用）。"""
        self.stop_session()
        client, self._client, self._client_key = self._client, None, None
        if client is not None:
            await client.close()

    def note_neuro_turn(self, text: str, status: dict) -> None:
        """缓存本轮正文与最近一次 neuro 状态（纯 dict，供 Jinja context）。

        status 是已剥掉 envelope/graph 的 `{section: current}`；这里不做任何
        阈值判定——"多久算睡着"属于模板措辞，不是这个模块的策略。
        """
        if text and text.strip():
            self._neuro_said = text.strip()
        if isinstance(status, dict):
            self._neuro_status = status

    @property
    def neuro_said(self) -> str:
        return self._neuro_said

    @property
    def neuro_status(self) -> dict:
        return self._neuro_status

    @property
    def in_flight(self) -> int:
        """当前在途的生成任务数（供 /health 与 dashboard 观察并发调度）。"""
        return sum(1 for t in self._tasks if not t.done())

    def _context(self, messages_block: str) -> dict:
        """模板 context：优先用 loop 注入的（带 stream_status），否则给最小集合。"""
        if self._context_provider is not None:
            return self._context_provider(messages_block)
        title, category = self._metadata()
        return {
            "stream_status": {"metadata": {"title": title, "category": category}},
            "neuro_status": self._neuro_status,
            "neuro_said": self._neuro_said,
            "chat_messages": messages_block,
            "datetime": time.strftime("%Y-%m-%d %H:%M:%S (%A)"),
        }

    # ---------- 客户端 ----------

    async def _client_for(self, cb: settings.ChatbotSettings) -> AsyncOpenAI:
        key = (cb.api_base_url, cb.api_key, cb.timeout)
        if self._client is None or self._client_key != key:
            stale, self._client = self._client, None
            if stale is not None:
                await stale.close()
            self._client = AsyncOpenAI(
                base_url=cb.api_base_url, api_key=cb.api_key, timeout=cb.timeout
            )
            self._client_key = key
        return self._client

    # ---------- 调度 ----------

    async def _scheduler_loop(self) -> None:
        while True:
            try:
                cb = settings.load().chatbot
                if not cb.enabled:
                    await asyncio.sleep(5.0)
                    continue
                if not cb.api_base_url or not cb.api_key or not cb.model:
                    ref = cb.model_ref or "(未配置)"
                    if ref != self._warned_ref:
                        self._warned_ref = ref
                        logger.warning(
                            f"Chatbot 模型服务不可用：stream.chatbot.model={ref!r} "
                            f"未在 server.llm_services 中完整定义，暂停生成弹幕"
                        )
                    await asyncio.sleep(cb.interval_s or 10.0)
                    continue
                self._warned_ref = None

                live = {t for t in self._tasks if not t.done()}
                self._tasks = live
                if len(live) >= cb.max_concurrent:
                    # 在途已满：跳过本 tick（只提示一次，恢复前不刷屏）
                    if not self._skipped:
                        self._skipped = True
                        logger.warning(
                            f"chatbot 在途生成已满（{len(live)}/{cb.max_concurrent}），跳过本轮"
                        )
                    await asyncio.sleep(min(cb.interval_s, 1.0))
                    continue
                self._skipped = False

                task = asyncio.create_task(self._generate_once(cb), name="chatbot-round")
                task.add_done_callback(self._tasks.discard)
                self._tasks.add(task)

                delay = cb.interval_s
                if cb.interval_jitter:
                    delay *= 1 + random.uniform(-cb.interval_jitter, cb.interval_jitter)
                # 下限只为避免 0 间隔空转，不能盖住配置的小间隔
                await asyncio.sleep(max(0.02, delay))
            except asyncio.CancelledError:
                break
            except Exception as e:  # 调度循环本身不能死
                logger.warning(f"Chatbot scheduler error: {type(e).__name__}: {e}")
                await asyncio.sleep(5.0)

    # ---------- 单轮生成 ----------

    async def _generate_once(self, cb: settings.ChatbotSettings) -> None:
        # `chat_messages` 照常放进 context（作者想在模板里引用随时可以），但
        # **默认模板不引用它**：给小模型看最近弹幕、再配一句 "do not copy or
        # repeat"，实测反而更促复述。见 AGENTS 的模板作者政策。
        queue_row = self._queue_row
        recent = (
            await self.storage.recent_messages(queue_row, _RECENT_CHAT)
            if queue_row is not None
            else []
        )
        # 空就是空，不替作者编一句"还没有消息"：chatbot 的措辞归模板作者，
        # 程序只负责把事实（这里就是"没内容"）递过去。
        messages_block = "\n".join(f"- {m['username']}: {m['content']}" for m in recent)
        try:
            user_content = self.templates.render("chatbot_user", self._context(messages_block))
        except TemplateRenderError:
            # 实况渲染失败（只可能来自绕过保存直接写库）：本轮作废。
            # `templates.render` 已经把错误记进 chatbot.template_error，这里不重复记，
            # 也不让异常冒出任务（否则 asyncio 会甩"Task exception was never retrieved"）。
            return
        system_prompt = self.persona.render_prompt(cb.count) + _CHAT_CONTRACT
        async with self._history_lock:
            history = [msg for batch in self._history for msg in batch]

        message = await self._request_completion(
            cb, system_prompt, history, user_content
        )
        if message is None:
            return

        raw_output, content_preview = self._read_tool_calls(message)
        if raw_output is None:
            return
        bodies = self._parse_output(raw_output, cb.count)
        if not bodies:
            self._discard("empty", content_preview)
            return

        queue_row = self._queue_row
        if queue_row is None or self._append_messages is None:
            return
        fresh: list[tuple[str, str]] = []
        for body in bodies:
            key = self._signature(body)
            if key in self._seen:
                continue  # 本场已经发过一模一样的行，跳过
            self._remember(key)
            # 用户名由本地生成器负责，模型只写内容。
            # 用 (纳秒时间戳, 内容) 播种：每条消息独立随机，不做撞名规避
            # （真实 chat 里同一个人连发多条很正常）。
            fresh.append((random_username(f"{time.time_ns()}:{body}"), body))
        if not fresh:
            # 全部重复是正常现象（模型复述金句），不是失败：不记 discard、不写历史。
            return

        try:
            await self._append_messages(fresh)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # 入列是 loop 的职责；它失败只说明本轮没发出去，不能让任务异常冒出去
            logger.warning(f"Chatbot append failed: {type(e).__name__}: {e}")
            return

        self._record_history(fresh)

    async def _request_completion(
        self,
        cb: settings.ChatbotSettings,
        system_prompt: str,
        history: list[dict],
        user_content: str,
    ):
        """发一次 chat.completions；tool_choice 不兼容时就地降档重试。

        返回 `message`；请求失败/全部档位都不可用时返回 None（discard 已记）。
        """
        tool = _tool_schema(cb.count)
        while True:
            choice = _TOOL_CHOICES[self._tool_choice_index]
            try:
                client = await self._client_for(cb)
                resp = await client.chat.completions.create(
                    model=cb.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        *history,
                        {"role": "user", "content": user_content},
                    ],
                    max_tokens=300,
                    temperature=0.8,
                    extra_body=cb.extra_body or None,
                    tools=[tool],
                    tool_choice=choice,
                )
                return resp.choices[0].message
            except asyncio.CancelledError:
                raise
            except Exception as e:
                if not self._is_tool_error(e):
                    self._discard("api_error", f"{type(e).__name__}: {e}")
                    return None
                if self._tool_choice_index + 1 >= len(_TOOL_CHOICES):
                    self._discard("tools_unsupported", f"{type(e).__name__}: {e}")
                    return None
                self._tool_choice_index += 1
                logger.warning(
                    "Chatbot tool_choice=%r 被网关拒绝（%s: %s），降级为 %r",
                    choice,
                    type(e).__name__,
                    e,
                    _TOOL_CHOICES[self._tool_choice_index],
                )

    @staticmethod
    def _is_tool_error(exc: Exception) -> bool:
        # 不能按状态码判定：400/422 里还混着 context length、auth、消息格式等
        # 与工具无关的失败，误降档会重复请求并可能错记 tools_unsupported。
        text = f"{exc} {getattr(exc, 'body', '')}".lower()
        return any(k in text for k in ("tool_choice", "tools", "function"))

    def _read_tool_calls(self, message) -> tuple[Optional[str], str]:
        """从 assistant message 取工具参数。

        返回 `(raw_text, content_preview)`；`raw_text is None` 表示本轮已记 discard。
        普通正文只作为 `last_discard.preview` 的来源，**永远不入队**。
        """
        content = getattr(message, "content", "") or ""
        calls = getattr(message, "tool_calls", None) or []
        chunks: list[str] = []
        matched = False
        for call in calls:
            function = getattr(call, "function", None)
            if function is None and isinstance(call, dict):
                function = call.get("function")
            if function is None:
                continue
            name = getattr(function, "name", None)
            if name is None and isinstance(function, dict):
                name = function.get("name")
            if name != _CHAT_TOOL:
                continue
            matched = True
            arguments = getattr(function, "arguments", None)
            if arguments is None and isinstance(function, dict):
                arguments = function.get("arguments")
            try:
                payload = json.loads(arguments or "")
            except (TypeError, ValueError):
                self._discard("bad_arguments", content or str(arguments))
                return None, content
            if not isinstance(payload, dict) or "messages" not in payload:
                self._discard("bad_arguments", content or str(arguments))
                return None, content
            value = payload["messages"]
            if isinstance(value, str):
                chunks.append(value)
            elif isinstance(value, list):
                chunks.extend(item for item in value if isinstance(item, str))
            else:
                self._discard("bad_arguments", content or str(arguments))
                return None, content

        if not matched:
            self._discard("no_tool_call", content)
            return None, content
        return "\n".join(chunks), content

    def _record_history(self, fresh: list[tuple[str, str]]) -> None:
        """只记录实际入列的整批（assistant tool_calls + tool 结果）。"""
        texts = [body for _, body in fresh]
        call_id = f"call_{uuid.uuid4().hex[:12]}"
        batch = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {
                            "name": _CHAT_TOOL,
                            "arguments": json.dumps({"messages": texts}, ensure_ascii=False),
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": call_id,
                "content": f"Posted {len(texts)} messages.",
            },
        ]
        self._history.append(batch)
        if len(self._history) > _HISTORY_BATCHES:
            self._history = self._history[-_HISTORY_BATCHES:]

    def _discard(self, reason: str, preview: object) -> None:
        """记录一次静默丢弃（不入队、不写历史）。"""
        self.discarded += 1
        one_line = " ".join(str(preview or "").split())
        self.last_discard = {
            "at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "reason": reason,
            "preview": one_line[:120],
        }
        log = logger.info if reason == "no_tool_call" else logger.warning
        log("Chatbot discarded (%s): %s", reason, self.last_discard["preview"])

    def _remember(self, key: str) -> None:
        """记录已入列签名；窗口满时按 FIFO 淘汰最旧的。"""
        if len(self._seen_order) == self._seen_order.maxlen and self._seen_order:
            self._seen.discard(self._seen_order[0])
        self._seen_order.append(key)
        self._seen.add(key)

    @staticmethod
    def _signature(text: str) -> str:
        """内容去重签名：大小写与空白归一（用户名每轮都会重新生成，不参与比较）。"""
        return " ".join(text.casefold().split())

    _THINK_TAG = re.compile(r"</?think>", re.IGNORECASE)
    # 只剥 markdown 列表前缀（"- " / "* " / "• " / "4. "），保留内容里的数字
    _LIST_PREFIX = re.compile(r"^\s*(?:[-*\u2022]+\s*|\d+[.)]\s+)")
    # 模型偶尔无视"不写用户名"的指令：剥掉 `name: ` 前缀（冒号后至少要有一个空格，
    # 所以 "ratio:yes" 这类内容不会被误伤）
    _NAME_PREFIX = re.compile(r"^[A-Za-z][A-Za-z0-9_]{1,24}:\s+")

    @staticmethod
    def _normalize_emotes(line: str) -> str:
        """把已知表情代码改回正确大小写（`neuroicant` → `neuroICANT`）。"""
        return _EMOTE_WORD.sub(
            lambda m: _EMOTE_CANON.get(m.group(0).lower(), m.group(0)), line
        )

    def _parse_output(self, raw: str, count: int) -> list[str]:
        """把模型输出解析成纯消息行（用户名由本地生成器负责）。"""
        # 部分模型（qwen3 系）即便关了 thinking 也会漏出 think 标签，
        # 或把思考内容混进正文；先剥掉标签，再丢弃空白行。
        raw = self._THINK_TAG.sub("", raw)
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        result: list[str] = []
        for line in lines:
            clean = self._LIST_PREFIX.sub("", line).strip()
            clean = self._NAME_PREFIX.sub("", clean).strip()
            clean = self._normalize_emotes(clean)
            if clean:
                result.append(clean)
        return result[:count]
