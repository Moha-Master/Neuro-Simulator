"""Chatbot 调度测试：消息解析、Neuro 状态判定、固定节拍并发调度与在途上限。"""

import asyncio
import inspect
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from neuro_simulator.stream.chatbot import (
    _CHAT_TOOL,
    _HISTORY_BATCHES,
    ChatbotManager,
)
from neuro_simulator.stream.persona import ChatbotPersonaStore, build_template_store
from neuro_simulator.stream.settings import ChatbotSettings, ChessSettings, StreamSettings
from neuro_simulator.stream.storage import Storage


def _tool_message(lines: list[str], content: str = ""):
    """造一个 OpenAI tool call 响应（arguments 是 JSON 字符串）。"""
    call = SimpleNamespace(
        id="call_test",
        function=SimpleNamespace(
            name=_CHAT_TOOL,
            arguments=json.dumps({"messages": lines}),
        ),
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=[call]))]
    )


def _settings(**chatbot_overrides) -> StreamSettings:
    base = dict(
        enabled=True,
        model_ref="fake",
        api_base_url="http://fake.test/v1",
        api_key="key",
        model="fake-model",
        extra_body={},
        timeout=5.0,
        interval_s=0.1,
        count=2,
        max_concurrent=3,
        interval_jitter=0.0,
    )
    base.update(chatbot_overrides)
    cb = ChatbotSettings(**base)
    return StreamSettings(
        neuro_url="http://fake.test:8000",
        channel="stream",
        scene_context="ctx",
        messages_per_round=10,
        speech_cooldown_ms=0,
        game_tool_wait_speech=True,
        empty_queue_behavior="placeholder",
        placeholder_message="idle",
        default_title="t",
        default_category="c",
        default_tags=[],
        chatbot=cb,
        chess=ChessSettings(
            enabled=False,
            token="",
            base_url="https://example.invalid",
            ai_level=1,
            clock_limit=60,
            clock_increment=0,
            color="white",
        ),
    )


class _FakeLLM:
    """按固定延迟返回 N 条弹幕的假客户端，同时记录并发峰值与调用次数。

    走 `send_chat_message`：每批内容都带 call 序号，因此"同一批被重复提交"这类
    缺陷会被去重逻辑暴露出来。
    """

    def __init__(self, latency: float, lines: int = 2) -> None:
        self.latency = latency
        self.lines = lines
        self.calls = 0
        self.active = 0
        self.max_active = 0
        self.last_messages: list[dict] = []
        self.last_kwargs: dict = {}
        self.tool_choices: list[object] = []

    async def create(self, **kwargs):
        self.last_messages = kwargs.get("messages") or []
        self.last_kwargs = kwargs
        self.tool_choices.append(kwargs.get("tool_choice"))
        self.calls += 1
        # 序号必须在 await **之前**取：并发完成时如果之后再读 self.calls，
        # 多个调用会拿到同一个序号 → 生成的文本相同 → 被产品的去重逻辑丢弃，
        # 测试就会误判成"消息没入列"。
        n = self.calls
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(self.latency)
        finally:
            self.active -= 1
        lines = [f"hello from call {n} ({i})" for i in range(self.lines)]
        return _tool_message(lines)

    def as_client(self):
        return SimpleNamespace(chat=SimpleNamespace(completions=self))


class _FakeToolError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


class _ScriptedLLM:
    """按脚本逐次返回响应 / 抛异常（tool_choice 降级、坏 JSON 等用）。"""

    def __init__(self, script) -> None:
        self.script = list(script)
        self.calls = 0
        self.last_messages: list[dict] = []
        self.last_kwargs: dict = {}
        self.tool_choices: list[object] = []

    async def create(self, **kwargs):
        self.calls += 1
        self.last_messages = kwargs.get("messages") or []
        self.last_kwargs = kwargs
        self.tool_choices.append(kwargs.get("tool_choice"))
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def as_client(self):
        return SimpleNamespace(chat=SimpleNamespace(completions=self))


class TestChatbot(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.storage = Storage(Path(self.tmpdir.name) / "test.db")
        await self.storage.init()
        self.persona = ChatbotPersonaStore(self.storage)
        await self.persona.load()
        self.templates = build_template_store(self.storage)
        await self.templates.load()

    async def asyncTearDown(self):
        await self.storage.close()
        self.tmpdir.cleanup()

    def _manager(self, queue_row: int, fake: _FakeLLM, settings: StreamSettings):
        mgr = ChatbotManager(self.storage, self.persona, self.templates)

        async def _client_for(cb):
            return fake.as_client()

        async def _append(fresh):
            ids = []
            for username, content in fresh:
                ids.append(await self.storage.append_message(queue_row, username, content))
            return ids

        mgr._client_for = _client_for
        mgr.start_session(
            queue_row, _append, metadata_provider=lambda: ("T", "C")
        )
        return mgr

    def _bare_manager(self, fake: _FakeLLM | None = None) -> ChatbotManager:
        mgr = ChatbotManager(self.storage, self.persona, self.templates)
        if fake is not None:
            async def _client_for(cb):
                return fake.as_client()

            mgr._client_for = _client_for
        return mgr

    async def _run_once(
        self, fake: _FakeLLM, settings: StreamSettings, queue_row: int
    ) -> ChatbotManager:
        """直接跑一轮生成（不启动调度器，避免测试与定时器赛跑）。"""
        mgr = self._bare_manager(fake)
        mgr._queue_row = queue_row
        mgr._metadata = lambda: ("T", "C")

        async def _append(fresh):
            ids = []
            for username, content in fresh:
                ids.append(await self.storage.append_message(queue_row, username, content))
            return ids

        mgr._append_messages = _append
        await mgr._generate_once(settings.chatbot)
        return mgr

    async def _drain(self, mgr: ChatbotManager, fake: _FakeLLM, seconds: float):
        """跑一段时间（按调度间隔轮询，避免依赖精确计时）。"""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        await mgr.aclose()
        return fake

    async def _drain_until(self, mgr: ChatbotManager, fake: _FakeLLM, predicate, timeout=8.0):
        """跑到条件成立为止（而不是固定时间窗）。

        固定窗口 + 断言"至少 N 次"在机器繁忙时会假失败（调度被推迟 → 次数不够）；
        条件等待只在条件成立时立刻返回，机器慢也只是多等一会儿。
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = predicate()
            if inspect.isawaitable(result):
                result = await result
            if result:
                break
            await asyncio.sleep(0.02)
        await mgr.aclose()
        return fake

    def _mgr(self) -> ChatbotManager:
        return ChatbotManager(self.storage, self.persona, self.templates)

    # ---------- 解析 ----------

    async def test_parse_output(self):
        """模型只写内容；列表编号被剥离，用户名前缀也会被剥掉。"""
        mgr = self._mgr()
        raw = (
            "Pog hello Neuro!\n"
            "- So funny LUL\n"
            "4. calm down chat\n"
            "\n"
        )
        self.assertEqual(
            mgr._parse_output(raw, count=3),
            ["Pog hello Neuro!", "So funny LUL", "calm down chat"],
        )

    async def test_parse_strips_model_supplied_username(self):
        """模型偶尔还会输出 `name: text`：剥掉前缀，用户名交给生成器。"""
        mgr = self._mgr()
        self.assertEqual(
            mgr._parse_output("lulz420: 9+10=21 bro", count=5), ["9+10=21 bro"]
        )
        # 冒号后没有空格 → 视为消息内容本身（如 "ratio:yes"）
        self.assertEqual(mgr._parse_output("ratio:yes", count=5), ["ratio:yes"])

    async def test_emote_casing_normalized(self):
        """表情代码大小写归一；未知代码原样保留。"""
        mgr = self._mgr()
        self.assertEqual(
            mgr._parse_output("neuroicant vedaljam lul", count=5),
            ["neuroICANT vedalJam LUL"],
        )
        self.assertEqual(mgr._parse_output("PogU stays", count=5), ["PogU stays"])

    async def test_parse_strips_leaked_think_tags(self):
        """qwen 系即便关了 thinking 也可能漏出标签，不能进弹幕。"""
        mgr = self._mgr()
        tag = "<" + "/think" + ">"
        open_tag = "<" + "think" + ">"
        parsed = mgr._parse_output(f"filtered. again?{tag}", count=5)
        self.assertEqual(parsed[0], "filtered. again?")
        parsed2 = mgr._parse_output(f"{open_tag}hmm{tag} hi", count=5)
        self.assertTrue(all("think" not in c for c in parsed2))

    async def test_usernames_are_generated_and_diverse(self):
        """用户名由本地生成器负责：批量生成不应高度撞名。"""
        from neuro_simulator.stream.usernames import random_username

        names = {random_username() for _ in range(500)}
        self.assertGreater(len(names), 400)

    async def test_username_seeded_by_timestamp_and_content(self):
        """(时间戳, 内容) 做种子：可复现、且每条消息互相独立。"""
        from neuro_simulator.stream.usernames import random_username

        a = random_username("1750000000000000000:hello")
        self.assertEqual(a, random_username("1750000000000000000:hello"))
        self.assertNotEqual(a, random_username("1750000000000000001:hello"))
        self.assertNotEqual(a, random_username("1750000000000000000:hello!"))

    # ---------- 状态 ----------

    async def test_note_neuro_turn_caches_said_and_status(self):
        """chatbot 只缓存事实（正文 + 剥好的投影），不再自己判定三态。"""
        mgr = self._mgr()
        self.assertEqual(mgr.neuro_said, "")
        self.assertEqual(mgr.neuro_status, {})
        mgr.note_neuro_turn(
            "Hello everyone, I am streaming!",
            {"turn": {"silent_s": 12.5, "idle": True}},
        )
        self.assertEqual(mgr.neuro_said, "Hello everyone, I am streaming!")
        self.assertEqual(mgr.neuro_status["turn"]["silent_s"], 12.5)
        # 空正文不覆盖上一句（纯工具轮没有台词）
        mgr.note_neuro_turn("  ", {"turn": {"silent_s": 50}})
        self.assertEqual(mgr.neuro_said, "Hello everyone, I am streaming!")

    async def test_session_lifecycle(self):
        queue_row, _ = await self.storage.create_queue("q")
        fake = _FakeLLM(latency=0.01)
        mgr = self._manager(queue_row, fake, _settings())
        self.assertIsNotNone(mgr._scheduler)
        mgr.stop_session()
        self.assertIsNone(mgr._scheduler)
        self.assertEqual(mgr.in_flight, 0)
        await mgr.aclose()

    # ---------- 并发调度 ----------

    async def test_slow_llm_does_not_stretch_the_schedule(self):
        """响应比间隔慢得多时，仍按节拍并发发起多轮（串行实现只能出 1 批）。"""
        queue_row, _ = await self.storage.create_queue("q")
        fake = _FakeLLM(latency=0.6, lines=2)
        settings = _settings(interval_s=0.1, max_concurrent=4)
        with patch("neuro_simulator.stream.settings.load", return_value=settings):
            mgr = self._manager(queue_row, fake, settings)

            async def _enough():
                msgs = await self.storage.list_messages(queue_row, limit=5000)
                return fake.calls >= 3 and len(msgs) >= 6

            await self._drain_until(mgr, fake, _enough, timeout=5.0)

        msgs = await self.storage.list_messages(queue_row)
        # 串行时 1.1s / 0.6s ≈ 1 批；并发下至少 3 批（每批 2 条）
        self.assertGreaterEqual(fake.calls, 3, f"并发未生效，只发起了 {fake.calls} 次")
        self.assertGreaterEqual(len(msgs), 6)
        self.assertLessEqual(fake.max_active, 4)

    async def test_no_duplicate_messages_across_concurrent_rounds(self):
        """并发轮次不得互相覆盖/重复入列：每条内容唯一、总数 = 批次 × 条数。"""
        queue_row, _ = await self.storage.create_queue("q")
        fake = _FakeLLM(latency=0.15, lines=3)
        settings = _settings(interval_s=0.05, max_concurrent=4, count=3)
        with patch("neuro_simulator.stream.settings.load", return_value=settings):
            mgr = self._manager(queue_row, fake, settings)

            async def _enough():
                return fake.calls >= 3 and mgr.in_flight == 0

            await self._drain_until(mgr, fake, _enough, timeout=4.0)

        msgs = await self.storage.list_messages(queue_row, limit=5000)
        contents = [m["content"] for m in msgs]
        self.assertGreaterEqual(fake.calls, 3)
        self.assertEqual(
            len(contents), len(set(contents)), "存在重复入列的弹幕（并发覆盖征兆）"
        )
        # 收尾时仍有在途批次被取消（可能停在半批），故只要求内容全都来自
        # 已发起的调用、且没有互相覆盖/重复。
        self.assertLessEqual(len(contents), fake.calls * 3)
        self.assertGreaterEqual(len(contents), (fake.calls - 4) * 3)

    async def test_in_flight_cap_skips_ticks(self):
        """在途达到上限后，后续 tick 跳过而不是无限堆积任务。"""
        queue_row, _ = await self.storage.create_queue("q")
        fake = _FakeLLM(latency=0.8, lines=1)
        settings = _settings(interval_s=0.05, max_concurrent=2)
        with patch("neuro_simulator.stream.settings.load", return_value=settings):
            mgr = self._manager(queue_row, fake, settings)
            # 先等到确实跑过 tick（否则断言只是空转通过），再让它多跑一会儿积累证据
            await self._drain_until(mgr, fake, lambda: fake.calls >= 1, timeout=3.0)
            await self._drain(mgr, fake, 0.8)
            self.assertLessEqual(mgr.in_flight, 2)

        self.assertLessEqual(fake.max_active, 2, "并发数超过 max_concurrent")
        self.assertLess(fake.calls, 20, "tick 未被跳过，任务堆积")

    async def test_duplicate_lines_are_dropped_and_not_refed_to_llm(self):
        """模型复述上一批的金句：入列时丢弃，且不回灌进历史。"""
        queue_row, _ = await self.storage.create_queue("q")
        settings = _settings(interval_s=0.05)
        fake = _FakeLLM(latency=0.01, lines=1)
        fake.create = lambda **kw: self._replay_once(fake, kw)  # type: ignore[assignment]
        with patch("neuro_simulator.stream.settings.load", return_value=settings):
            mgr = self._manager(queue_row, fake, settings)
            # 至少两次调用才能验证"复述没有回灌进历史"（一次调用时历史还是空的）
            await self._drain_until(mgr, fake, lambda: fake.calls >= 2, timeout=3.0)

        msgs = await self.storage.list_messages(queue_row)
        self.assertEqual(len(msgs), 1, f"重复行未被过滤：{[m['content'] for m in msgs]}")
        # 历史里 assistant 的正文是 None：唯一能复述来源是 tool_calls 的参数
        # 与 tool 结果；重复行只应出现一次。
        fragments: list[str] = []
        for message in fake.last_messages:
            if message.get("role") == "tool":
                fragments.append(str(message.get("content") or ""))
            for call in message.get("tool_calls") or []:
                fragments.append(str((call.get("function") or {}).get("arguments") or ""))
        joined = "\n".join(fragments)
        self.assertEqual(joined.count("same old line"), 1, joined)

    async def _replay_once(self, fake: _FakeLLM, kwargs: dict):
        """前两次返回完全相同的一行，之后返回空（避免制造无意义的重复入列）。"""
        fake.last_messages = kwargs.get("messages") or []
        fake.last_kwargs = kwargs
        fake.calls += 1
        if fake.calls <= 2:
            return _tool_message(["same old line"])
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="", tool_calls=None))]
        )

    # ---------- 工具调用 ----------

    async def test_tool_call_enqueues_cleaned_lines_with_generated_usernames(self):
        queue_row, _ = await self.storage.create_queue("q")
        fake = _ScriptedLLM([
            _tool_message(["- hello LUL", "bob: neuroicant says hi", "4. calm down chat"])
        ])
        mgr = await self._run_once(fake, _settings(count=5), queue_row)

        msgs = await self.storage.list_messages(queue_row)
        self.assertEqual(
            [m["content"] for m in msgs],
            ["hello LUL", "neuroICANT says hi", "calm down chat"],
        )
        self.assertTrue(all(m["username"] for m in msgs))
        self.assertEqual(mgr.discarded, 0)
        # 请求里必须带工具与 tool_choice，普通 content 不作为输出
        self.assertEqual(len(fake.last_kwargs["tools"]), 1)
        self.assertEqual(fake.last_kwargs["tools"][0]["function"]["name"], _CHAT_TOOL)
        self.assertIn("tool_choice", fake.last_kwargs)

    async def test_multiple_tool_calls_flatten_newlines_and_truncate(self):
        queue_row, _ = await self.storage.create_queue("q")

        def _two_calls():
            calls = [
                SimpleNamespace(
                    function=SimpleNamespace(
                        name=_CHAT_TOOL,
                        arguments=json.dumps({"messages": ["first\nsecond"]}),
                    )
                ),
                SimpleNamespace(
                    function=SimpleNamespace(
                        name="some_other_tool",
                        arguments=json.dumps({"messages": ["ignored"]}),
                    )
                ),
                SimpleNamespace(
                    function=SimpleNamespace(
                        name=_CHAT_TOOL,
                        arguments=json.dumps({"messages": ["third"]}),
                    )
                ),
            ]
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="", tool_calls=calls))]
            )

        fake = _ScriptedLLM([_two_calls()])
        await self._run_once(fake, _settings(count=2), queue_row)
        msgs = await self.storage.list_messages(queue_row)
        self.assertEqual([m["content"] for m in msgs], ["first", "second"])

    async def test_plain_content_without_tool_call_is_discarded(self):
        queue_row, _ = await self.storage.create_queue("q")
        fake = _ScriptedLLM([
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content="hello chat", tool_calls=None)
                    )
                ]
            )
        ])
        mgr = await self._run_once(fake, _settings(), queue_row)

        self.assertEqual(await self.storage.list_messages(queue_row), [])
        self.assertEqual(mgr.discarded, 1)
        self.assertEqual(mgr.last_discard["reason"], "no_tool_call")
        self.assertIn("hello chat", mgr.last_discard["preview"])

    async def test_bad_json_arguments_are_discarded(self):
        queue_row, _ = await self.storage.create_queue("q")
        call = SimpleNamespace(
            function=SimpleNamespace(name=_CHAT_TOOL, arguments="{not json")
        )
        fake = _ScriptedLLM([
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content="", tool_calls=[call])
                    )
                ]
            )
        ])
        mgr = await self._run_once(fake, _settings(), queue_row)
        self.assertEqual(await self.storage.list_messages(queue_row), [])
        self.assertEqual(mgr.discarded, 1)
        self.assertEqual(mgr.last_discard["reason"], "bad_arguments")

    async def test_all_duplicates_are_not_counted_as_discard(self):
        queue_row, _ = await self.storage.create_queue("q")
        fake = _ScriptedLLM([
            _tool_message(["same old line"]),
            _tool_message(["same old line"]),
        ])
        mgr = self._bare_manager(fake)
        mgr._queue_row = queue_row
        mgr._metadata = lambda: ("T", "C")

        async def _append(fresh):
            ids = []
            for username, content in fresh:
                ids.append(await self.storage.append_message(queue_row, username, content))
            return ids

        mgr._append_messages = _append
        await mgr._generate_once(_settings().chatbot)
        await mgr._generate_once(_settings().chatbot)

        self.assertEqual(len(await self.storage.list_messages(queue_row)), 1)
        self.assertEqual(mgr.discarded, 0)
        self.assertIsNone(mgr.last_discard)

    async def test_tool_choice_degrades_only_on_tool_related_errors(self):
        queue_row, _ = await self.storage.create_queue("q")
        fake = _ScriptedLLM([
            _FakeToolError("tool_choice is not supported", status_code=400),
            _tool_message(["works now"]),
        ])
        mgr = await self._run_once(fake, _settings(), queue_row)

        self.assertEqual(fake.calls, 2)
        self.assertEqual(fake.tool_choices[1], "required")
        self.assertEqual(mgr._tool_choice_index, 1)
        self.assertEqual(len(await self.storage.list_messages(queue_row)), 1)

        # 非工具相关错误不降级，直接 api_error 丢弃
        queue2, _ = await self.storage.create_queue("q2")
        other = _ScriptedLLM([RuntimeError("gateway exploded")])
        mgr2 = await self._run_once(other, _settings(), queue2)
        self.assertEqual(other.calls, 1)
        self.assertEqual(mgr2._tool_choice_index, 0)
        self.assertEqual(mgr2.last_discard["reason"], "api_error")

    async def test_context_length_400_does_not_downgrade(self):
        queue_row, _ = await self.storage.create_queue("q")
        fake = _ScriptedLLM([
            _FakeToolError("context length exceeded", status_code=400),
        ])
        mgr = await self._run_once(fake, _settings(), queue_row)

        self.assertEqual(fake.calls, 1)
        self.assertEqual(mgr._tool_choice_index, 0)
        self.assertEqual(mgr.last_discard["reason"], "api_error")

    async def test_all_tool_choice_levels_failing_marks_tools_unsupported(self):
        queue_row, _ = await self.storage.create_queue("q")
        fake = _ScriptedLLM([
            _FakeToolError("tools are not supported", status_code=400),
            _FakeToolError("tools are not supported", status_code=400),
            _FakeToolError("tools are not supported", status_code=400),
        ])
        mgr = await self._run_once(fake, _settings(), queue_row)
        self.assertEqual(fake.calls, 3)
        self.assertEqual(mgr.last_discard["reason"], "tools_unsupported")
        self.assertEqual(mgr.discarded, 1)

    async def test_system_prompt_contains_tool_contract(self):
        queue_row, _ = await self.storage.create_queue("q")
        fake = _ScriptedLLM([_tool_message(["hi"])])
        await self._run_once(fake, _settings(), queue_row)
        system = fake.last_messages[0]
        self.assertEqual(system["role"], "system")
        self.assertIn("send_chat_message", system["content"])
        self.assertIn("normal reply text is never shown", system["content"])

    async def test_history_is_batched_and_trimmed_by_whole_batches(self):
        queue_row, _ = await self.storage.create_queue("q")
        script = [_tool_message([f"line {i}"]) for i in range(_HISTORY_BATCHES + 1)]
        fake = _ScriptedLLM(script)
        mgr = self._bare_manager(fake)
        mgr._queue_row = queue_row
        mgr._metadata = lambda: ("T", "C")

        async def _append(fresh):
            ids = []
            for username, content in fresh:
                ids.append(await self.storage.append_message(queue_row, username, content))
            return ids

        mgr._append_messages = _append
        for _ in range(_HISTORY_BATCHES + 1):
            await mgr._generate_once(_settings().chatbot)

        self.assertEqual(len(mgr._history), _HISTORY_BATCHES)
        # 最后一次请求里，每条历史批都是完整的一对（assistant tool_calls + tool）
        roles = [m["role"] for m in fake.last_messages]
        self.assertEqual(roles.count("assistant"), _HISTORY_BATCHES)
        self.assertEqual(roles.count("tool"), _HISTORY_BATCHES)
        assistant = next(m for m in fake.last_messages if m["role"] == "assistant")
        self.assertIsNone(assistant["content"])
        self.assertEqual(assistant["tool_calls"][0]["function"]["name"], _CHAT_TOOL)
        self.assertIn("Posted 1 messages.", "".join(
            m["content"] for m in fake.last_messages if m["role"] == "tool"
        ))

    async def test_start_session_resets_discard_but_keeps_tool_choice(self):
        queue_row, _ = await self.storage.create_queue("q")
        mgr = self._bare_manager()
        mgr.discarded = 7
        mgr.last_discard = {"at": "x", "reason": "no_tool_call", "preview": "x"}
        mgr._tool_choice_index = 2

        async def _append(fresh):
            return []

        mgr.start_session(queue_row, _append)
        try:
            self.assertEqual(mgr.discarded, 0)
            self.assertIsNone(mgr.last_discard)
            self.assertEqual(mgr._tool_choice_index, 2, "工具档位是进程级缓存")
        finally:
            mgr.stop_session()

    # ---------- 人设 / 模板接线 ----------

    async def test_chatbot_prompt_and_message_use_persona_and_template(self):
        """生成的 system prompt 来自数据库人设，user message 来自模板（含本场信息）。"""
        queue_row, _ = await self.storage.create_queue("q")
        fake = _FakeLLM(latency=0.01, lines=1)
        settings = _settings(interval_s=0.05)
        with patch("neuro_simulator.stream.settings.load", return_value=settings):
            mgr = self._manager(queue_row, fake, settings)
            await self._drain(mgr, fake, 0.25)

        messages = fake.last_messages
        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("You are the chat of Neuro-sama", messages[0]["content"])
        self.assertIn("### Timeline", messages[0]["content"])  # core memory 已注入
        self.assertIn("1 to 2 chat messages", messages[0]["content"])  # {{count}}
        self.assertIn("send_chat_message", messages[0]["content"])  # 工具契约
        user = messages[-1]["content"]
        self.assertIn("Now streaming: T [C]", user)
        self.assertIn("Neuro just online now", user)


if __name__ == "__main__":
    unittest.main()
