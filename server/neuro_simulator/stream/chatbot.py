"""stream 模块 Chatbot：在直播期间模拟 Twitch 弹幕发送者。

持有独立循环，不受直播主循环阻塞；在 Session 启动时即刻开启，在 Session 结束时关闭。
维护内存中的临时对话历史（不存数据库），自动判定 Neuro 的当前状态（Online / Says / Sleeping）。
"""

import asyncio
import logging
import random
import time
from typing import Callable, Optional

from openai import AsyncOpenAI

from . import settings
from .storage import Storage

logger = logging.getLogger("stream.chatbot")

_RANDOM_USERNAMES = [
    "pogger_99",
    "kappa_fan",
    "neuro_simp",
    "el_clasico",
    "random_viewer",
    "vtuber_enjoyer",
    "chat_guy",
    "giga_chad",
    "clueless_user",
    "lmao_person",
    "fannn_01",
    "stream_watcher",
    "chat_mod_wannabe",
]


class ChatbotManager:
    def __init__(self, storage: Storage) -> None:
        self.storage = storage
        self._loop_task: Optional[asyncio.Task] = None
        self._queue_row: Optional[int] = None
        self._on_new_message: Optional[Callable[[], None]] = None
        self._last_neuro_said: Optional[str] = None
        self._tts_finish_time: float = 0.0
        self._history: list[dict] = []
        self._warned_ref: str | None = None

    def start_session(self, queue_row: int, on_new_message_cb: Callable[[], None]) -> None:
        self.stop_session()
        self._queue_row = queue_row
        self._on_new_message = on_new_message_cb
        self._last_neuro_said = None
        self._tts_finish_time = 0.0
        self._history = []
        self._loop_task = asyncio.create_task(self._run_loop())
        print(f"[stream.chatbot] Session {queue_row} chatbot loop started", flush=True)

    def stop_session(self) -> None:
        if self._loop_task and not self._loop_task.done():
            self._loop_task.cancel()
        self._loop_task = None
        self._queue_row = None
        self._on_new_message = None

    def update_neuro_speech(self, text: str, duration: float) -> None:
        if text and text.strip():
            self._last_neuro_said = text.strip()
            self._tts_finish_time = time.monotonic() + max(0.0, duration)

    def _get_neuro_status(self, sleep_timeout_s: float) -> str:
        if self._last_neuro_said is None:
            return "Neuro just online now! Stream is starting soon..."
        now = time.monotonic()
        if now <= self._tts_finish_time + sleep_timeout_s:
            return f"Neuro says: {self._last_neuro_said}"
        return "Neuro seems fall asleep on stream..."

    async def _run_loop(self) -> None:
        while True:
            try:
                st = settings.load()
                cb_st = st.chatbot
                if not cb_st.enabled:
                    await asyncio.sleep(5.0)
                    continue
                if not cb_st.api_base_url or not cb_st.api_key or not cb_st.model:
                    ref = cb_st.model_ref or "(未配置)"
                    if ref != self._warned_ref:
                        self._warned_ref = ref
                        logger.warning(
                            f"Chatbot 模型服务不可用：stream.chatbot.model={ref!r} "
                            f"未在 server.llm_services 中完整定义，暂停生成弹幕"
                        )
                    await asyncio.sleep(cb_st.interval_s or 10.0)
                    continue
                self._warned_ref = None

                status_str = self._get_neuro_status(cb_st.neuro_sleep_timeout_s)
                now_str = time.strftime("%Y-%m-%d %H:%M:%S (%A)")
                user_content = f"Current date and time: {now_str}\n\n{status_str}"

                try:
                    sys_prompt = cb_st.system_prompt.format(count=cb_st.count)
                except (KeyError, ValueError):
                    sys_prompt = cb_st.system_prompt

                messages = [
                    {"role": "system", "content": sys_prompt},
                    *self._history,
                    {"role": "user", "content": user_content},
                ]

                client = AsyncOpenAI(
                    base_url=cb_st.api_base_url, api_key=cb_st.api_key, timeout=cb_st.timeout
                )
                try:
                    resp = await client.chat.completions.create(
                        model=cb_st.model,
                        messages=messages,
                        max_tokens=300,
                        temperature=0.8,
                        extra_body=cb_st.extra_body or None,
                    )
                    raw_output = resp.choices[0].message.content or ""
                finally:
                    await client.close()

                if raw_output.strip():
                    self._history.append({"role": "user", "content": user_content})
                    self._history.append({"role": "assistant", "content": raw_output})
                    if len(self._history) > 20:
                        self._history = self._history[-20:]

                    parsed = self._parse_output(raw_output, cb_st.count)
                    if parsed and self._queue_row is not None:
                        for uname, msg in parsed:
                            await self.storage.append_message(self._queue_row, uname, msg)
                        if self._on_new_message:
                            self._on_new_message()

                await asyncio.sleep(cb_st.interval_s)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Chatbot LLM call failed: {type(e).__name__}: {e}")
                await asyncio.sleep(10.0)

    def _parse_output(self, raw: str, count: int) -> list[tuple[str, str]]:
        lines = [l.strip() for l in raw.splitlines() if l.strip()]
        result: list[tuple[str, str]] = []
        for line in lines:
            clean = line.lstrip("-*•1234567890. ").strip()
            if not clean:
                continue
            if ":" in clean:
                parts = clean.split(":", 1)
                uname = parts[0].strip()
                msg = parts[1].strip()
                if uname and msg:
                    result.append((uname, msg))
                    continue
            uname = random.choice(_RANDOM_USERNAMES)
            result.append((uname, clean))
        return result[:count]
