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
import logging
import random
import re
import time
from collections import deque
from typing import Callable, Optional

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

_HISTORY_LIMIT = 20
# chatbot 模板能看到多少条"观众最近消息"（含自己发的行；人设 prompt 要求不复述）
_RECENT_CHAT = 12
# 同场次内已入列消息的签名窗口：模型爱复述自己上一批的"金句"，
# 这里做一层廉价去重，避免刷屏式重复（并发下也兜住同一批被重复提交的极端情况）。
_DEDUP_WINDOW = 300

MetadataProvider = Callable[[], "tuple[str, str]"]


class ChatbotManager:
    def __init__(self, storage, persona, templates) -> None:
        self.storage = storage
        self.persona = persona
        self.templates = templates
        self._scheduler: Optional[asyncio.Task] = None
        self._tasks: set[asyncio.Task] = set()
        self._queue_row: Optional[int] = None
        self._on_new_message: Optional[Callable[[], None]] = None
        self._on_message: Optional[Callable[[int, str, str], None]] = None
        self._metadata: MetadataProvider = lambda: ("", "")
        # 内容平面缓存：本轮正文 + 最近一次 neuro 状态（剥掉 envelope/graph 的 dict）。
        # 它们只喂给模板，不在 chatbot 侧做任何"睡没睡着"的判定。
        self._neuro_said: str = ""
        self._neuro_status: dict = {}
        # loop 注入：messages 文本 → 完整 Jinja context（含 stream_status）
        self._context_provider: Optional[Callable[[str], dict]] = None
        self._history: list[dict] = []
        self._history_lock = asyncio.Lock()
        self._seen: set[str] = set()
        self._seen_order: deque[str] = deque(maxlen=_DEDUP_WINDOW)
        self._warned_ref: str | None = None
        self._skipped = False
        self._client: Optional[AsyncOpenAI] = None
        self._client_key: tuple | None = None

    # ---------- 生命周期 ----------

    def start_session(
        self,
        queue_row: int,
        on_new_message_cb: Callable[[], None],
        on_message_cb: Optional[Callable[[int, str, str], None]] = None,
        metadata_provider: Optional[MetadataProvider] = None,
        context_provider: Optional[Callable[[str], dict]] = None,
    ) -> None:
        self.stop_session()
        self._queue_row = queue_row
        self._on_new_message = on_new_message_cb
        self._on_message = on_message_cb
        self._metadata = metadata_provider or (lambda: ("", ""))
        self._context_provider = context_provider
        self._neuro_said = ""
        self._neuro_status = {}
        self._history = []
        self._seen = set()
        self._seen_order = deque(maxlen=_DEDUP_WINDOW)
        self._skipped = False
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
        self._on_new_message = None
        self._on_message = None
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
        system_prompt = self.persona.render_prompt(cb.count)
        async with self._history_lock:
            history = list(self._history)

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
            )
            raw_output = resp.choices[0].message.content or ""
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"Chatbot LLM call failed: {type(e).__name__}: {e}")
            return

        if not raw_output.strip():
            return

        bodies = self._parse_output(raw_output, cb.count)
        queue_row = self._queue_row
        if not bodies or queue_row is None:
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
        # 历史只记实际发出去的行：模型爱复述自己上一批的"金句"，
        # 把重复内容回灌只会让它越抄越起劲。
        async with self._history_lock:
            self._history.append({"role": "user", "content": user_content})
            self._history.append(
                {
                    "role": "assistant",
                    "content": "\n".join(f"{u}: {m}" for u, m in fresh) or "(nothing posted)",
                }
            )
            if len(self._history) > _HISTORY_LIMIT:
                self._history = self._history[-_HISTORY_LIMIT:]

        if not fresh:
            return
        for uname, msg in fresh:
            msg_id = await self.storage.append_message(queue_row, uname, msg)
            if self._on_message:
                self._on_message(msg_id, uname, msg)
        if self._on_new_message:
            self._on_new_message()

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
