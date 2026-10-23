"""`/chat` 内容平面：产出侧记账、批量响应（terminal/segments）与 audio 裁剪。

这里刻意不走 HTTP：`TurnRunner` 是后台生成单元，因此测试能同时证明
"没人读 `/chat` 时 UiState 仍然完整"（E-1 的回归点）与"交付路径只做序列化"。
"""

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from neuro_simulator.broadcaster import EventBroadcaster
from neuro_simulator.neuro_sama import tts
from neuro_simulator.neuro_sama.agent import NeuroAgent
from neuro_simulator.neuro_sama.server import _batch_payload
from neuro_simulator.neuro_sama.storage import Storage
from neuro_simulator.neuro_sama.turns import TurnRunner
from neuro_simulator.neuro_sama.ui_state import UiState, strip_speech_audio


class _ScriptedAgent(NeuroAgent):
    """直接吐预置事件的 agent（跳过 LLM，只验证产出侧记账）。"""

    def __init__(self, events: list[dict]) -> None:
        super().__init__(None, None, None, None, None)
        self._events = events

    async def _run(self, channel, conversation_id, user_text, cancel_event=None):
        for ev in self._events:
            await asyncio.sleep(0)
            yield ev


class _FakeStream:
    """最小 AsyncStream 替身：先吐 chunks，之后可抛错或正常结束。"""

    def __init__(self, chunks: list, error: Exception | None = None) -> None:
        self._chunks = list(chunks)
        self._error = error

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._chunks:
            return self._chunks.pop(0)
        if self._error is not None:
            raise self._error
        raise StopAsyncIteration

    async def close(self) -> None:
        return None


def _chunk(text: str = "half") -> SimpleNamespace:
    delta = SimpleNamespace(content=text, reasoning_content=None, tool_calls=[])
    return SimpleNamespace(usage=None, choices=[SimpleNamespace(delta=delta, finish_reason=None)])


class _FakeCompletions:
    def __init__(self, stream: _FakeStream) -> None:
        self._stream = stream

    async def create(self, **kwargs):
        return self._stream


class TestProductionSideAccounting(unittest.IsolatedAsyncioTestCase):
    async def test_state_is_complete_without_any_http_reader(self):
        st = UiState()
        agent = _ScriptedAgent(
            [
                {"type": "content", "step": 1, "delta": "Before the tool."},
                {"type": "speech", "step": 1, "index": 0, "text": "Before the tool.",
                 "duration": 1.0, "audio": "YQ==", "mime": "audio/mpeg"},
                {"type": "content", "step": 2, "delta": "After the tool."},
                {"type": "speech", "step": 2, "index": 1, "text": "After the tool.",
                 "duration": 1.0, "audio": "Yg==", "mime": "audio/mpeg"},
                {"type": "done", "content": "After the tool."},
            ]
        )
        agent.event_sink = st.observe
        tid = st.new_turn("stream")

        # 只消费 agent（没有任何 /chat HTTP 连接）
        async for _ in agent.run("stream", "conv", "hi", turn_id=tid):
            pass

        turn = st.turn_projection("stream")
        self.assertFalse(turn["generating"])
        self.assertEqual(turn["in_flight"], 0)
        self.assertEqual(st.turn_text("stream"), "Before the tool. After the tool.")
        self.assertEqual([s["text"] for s in st.turn_segments("stream")],
                         ["Before the tool.", "After the tool."])


class TestAudioPayload(unittest.IsolatedAsyncioTestCase):
    def _state_with_azure_speech(self) -> tuple[UiState, int]:
        st = UiState()
        tid = st.new_turn("stream")
        st.observe(
            {"type": "speech", "step": 0, "index": 0, "text": "hello",
             "duration": 2.31, "audio": "YQ==", "mime": "audio/mpeg"},
            "stream", tid,
        )
        return st, tid

    def test_audio_true_includes_audio_and_state_keeps_it(self):
        st, tid = self._state_with_azure_speech()
        seg = st.batch("stream", "conv", tid, "done", audio=True)["segments"][0]
        self.assertEqual(seg["audio"], "YQ==")
        self.assertEqual(seg["duration"], 2.31)
        # 内容总线的 snapshot（/chat/events 断点续播）仍必须有音频
        self.assertIn("audio", str(st.snapshot("stream")["turn"]))

    def test_audio_false_removes_the_key_but_not_the_stored_audio(self):
        st, tid = self._state_with_azure_speech()
        seg = st.batch("stream", "conv", tid, "done", audio=False)["segments"][0]
        self.assertNotIn("audio", seg, "audio=false 时键不出现（不是空串）")
        self.assertEqual(seg["duration"], 2.31, "duration 来自合成，不受 audio 开关影响")
        self.assertIn("YQ==", str(st.snapshot("stream")["turn"]))

    def test_streaming_speech_event_strips_audio_only_for_the_caller(self):
        ev = {"type": "speech", "step": 0, "index": 0, "text": "hi",
              "duration": 1.0, "audio": "YQ==", "mime": "audio/mpeg"}
        out = strip_speech_audio(ev)
        self.assertNotIn("audio", out)
        self.assertEqual(ev["audio"], "YQ==", "不得修改原事件（广播总线还在用）")

    def test_null_tts_has_estimated_duration_and_no_audio_key(self):
        estimated = tts.synthesize_null("hello there")
        self.assertEqual(estimated["audio"], "")
        st = UiState()
        tid = st.new_turn("stream")
        st.observe(
            {"type": "speech", "step": 0, "index": 0, "text": "hello there", **estimated},
            "stream", tid,
        )
        seg = st.batch("stream", "conv", tid, "done", audio=True)["segments"][0]
        self.assertNotIn("audio", seg, "虚拟 TTS 本来无字节")
        self.assertAlmostEqual(seg["duration"], estimated["duration"])

    def test_disabled_tts_degrades_to_one_text_segment_per_step(self):
        st = UiState()
        tid = st.new_turn("stream")
        st.observe({"type": "content", "step": 1, "delta": "Let me check the board!"}, "stream", tid)
        st.observe({"type": "content", "step": 2, "delta": "LOLA is my guess."}, "stream", tid)
        st.observe({"type": "done", "content": "LOLA is my guess."}, "stream", tid)
        payload = st.batch("stream", "conv", tid, "done", audio=True)
        self.assertTrue(payload["text"])
        self.assertEqual(payload["segments"], [
            {"step": 1, "index": 0, "kind": "text",
             "text": "Let me check the board!", "duration": 0},
            {"step": 2, "index": 0, "kind": "text",
             "text": "LOLA is my guess.", "duration": 0},
        ])


class _AgentHarness(unittest.IsolatedAsyncioTestCase):
    """带真实 Storage / 假 LLM 的 agent，覆盖批量模式的终结三态。"""

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = Storage(Path(self.tmp.name) / "test.db")
        await self.storage.init()
        self.ui_state = UiState()
        cfg = SimpleNamespace(
            MAX_STEPS=3,
            MAX_CONTEXT_MESSAGES=10,
            NEURO_MODEL_REF="fake",
            neuro_llm=lambda: SimpleNamespace(
                base_url="http://fake", api_key="k", model="m", extra_body={}, timeout=1.0
            ),
        )
        registry = SimpleNamespace(openai_schema=lambda active: [])
        memory = SimpleNamespace(render=lambda: "", count=0, used=0, char_limit=1000)
        persona = SimpleNamespace(render_system=lambda rendered: "system")
        self.stream = _FakeStream([_chunk()])
        client = SimpleNamespace(
            chat=SimpleNamespace(completions=_FakeCompletions(self.stream))
        )
        self.agent = NeuroAgent(
            client, registry, self.storage, memory, persona, event_sink=self.ui_state.observe
        )
        self.runner = TurnRunner(
            self.agent, self.ui_state, memory, EventBroadcaster()
        )
        self._patchers = [
            patch("neuro_simulator.neuro_sama.agent.get_config", return_value=cfg),
            patch("neuro_simulator.neuro_sama.agent.tts.tts_enabled", return_value=False),
        ]
        for p in self._patchers:
            p.start()

    async def asyncTearDown(self):
        for p in self._patchers:
            p.stop()
        await self.storage.close()
        self.tmp.cleanup()

    async def _run(self, cancel_event=None, cancel_on_content=False) -> dict:
        turn = self.runner.register("stream", "conv")
        if cancel_event is not None:
            turn.cancel_event = cancel_event
        if cancel_on_content:
            observe = self.ui_state.observe

            def sink(ev, channel, turn_id):
                observe(ev, channel, turn_id)
                # 模拟 `/manage/chats/.../stop`：正文刚产出就置位取消
                if ev.get("type") == "content":
                    turn.cancel_event.set()

            self.agent.event_sink = sink
        self.runner.spawn(turn, "hi")
        await asyncio.wait_for(turn.done_event.wait(), 5.0)
        self._tid = turn.turn_id
        return _batch_payload(turn, audio=False)

    async def test_terminal_done(self):
        payload = await self._run()
        self.assertEqual(payload["terminal"], "done")
        self.assertNotIn("message", payload)
        self.assertNotIn("ends_at", payload)
        self.assertNotIn("speaking", payload)
        self.assertNotIn("remaining_s", payload)
        self.assertEqual(payload["text"], "half")

    async def test_terminal_error_carries_reason(self):
        # 第一 chunk 之后抛错：有正文、没有 finish_reason → 半截落库 + error
        self.stream._chunks = [_chunk("partial")]
        self.stream._error = RuntimeError("gateway exploded")
        payload = await self._run()
        self.assertEqual(payload["terminal"], "error")
        self.assertIn("gateway exploded", payload["message"])
        self.assertEqual(payload["text"], "partial")

    async def test_audio_false_still_synthesizes(self):
        """audio 只裁交付载荷：合成照做，duration 来自合成。"""
        calls: list[str] = []

        async def fake_synthesize(text: str) -> dict:
            calls.append(text)
            return {"audio": "YQ==", "mime": "audio/mpeg", "duration": 1.5}

        with patch("neuro_simulator.neuro_sama.agent.tts.tts_enabled", return_value=True):  # noqa: SIM117
            with patch("neuro_simulator.neuro_sama.agent.tts.synthesize", side_effect=fake_synthesize):
                payload = await self._run()  # _run 固定 audio=False

        self.assertEqual(calls, ["half"], "audio=false 不影响是否合成")
        seg = payload["segments"][0]
        self.assertEqual(seg["duration"], 1.5)
        self.assertNotIn("audio", seg)

    async def test_terminal_cancelled_persists_partial_and_returns_text(self):
        payload = await self._run(cancel_on_content=True)
        self.assertEqual(payload["terminal"], "cancelled")
        self.assertEqual(payload["text"], "half")

        conv_id = await self.storage.find_conversation_id("stream", "conv")
        messages = await self.storage.get_messages(conv_id)
        assistant = [m for m in messages if m["role"] == "assistant"]
        self.assertEqual(assistant[-1]["content"], "half", "半截内容必须落库")
        self.assertFalse(self.ui_state.turn_projection("stream")["generating"])

    async def test_cancelled_before_start(self):
        # 请求刚发出就被 /stop 打断：agent 以 cancelled 终结
        cancel_event = asyncio.Event()
        cancel_event.set()
        payload = await self._run(cancel_event=cancel_event)
        self.assertEqual(payload["terminal"], "cancelled")


class TestRunnerTerminalFallback(unittest.IsolatedAsyncioTestCase):
    async def test_missing_terminal_maps_to_error(self):
        class _OrphanAgent(NeuroAgent):
            def __init__(self):
                super().__init__(None, None, None, None, None)

            async def _run(self, channel, conversation_id, user_text, cancel_event=None):
                yield {"type": "content", "step": 1, "delta": "orphan"}

        ui = UiState()
        runner = TurnRunner(_OrphanAgent(), ui, None, EventBroadcaster())
        turn = runner.register("stream", "conv")
        runner.spawn(turn, "hi")
        await asyncio.wait_for(turn.done_event.wait(), 2.0)
        self.assertEqual(turn.result["terminal"], "error")
        self.assertTrue(turn.result["message"])


if __name__ == "__main__":
    unittest.main()
