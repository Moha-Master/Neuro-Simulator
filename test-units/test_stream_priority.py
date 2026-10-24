"""双轨队列（普通 / 优先）行为测试：优先轨先进先出、一次一条、优先不污染普通轨。"""

import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from neuro_simulator.broadcaster import EventBroadcaster
from neuro_simulator.stream.loop import StreamLoop
from neuro_simulator.stream.persona import ChatbotPersonaStore, build_template_store
from neuro_simulator.stream.storage import Storage


class _FakeNeuroClient:
    """批量模式替身：生成结果与状态都来自 neuro（stream 不再自己算时间轴）。"""

    def __init__(self, url: str, *, ends_in_s: float = 0.0, text: str = "ta-da") -> None:
        self.url = url
        self.base_url = url
        self.ends_in_s = ends_in_s
        self.text = text
        self.chat_calls = 0
        self.audio_flags: list[bool] = []
        self.status_calls = 0
        self.captured: dict[str, str] = {}

    async def chat_batch(
        self, message: str, channel: str, conversation_id: str, audio: bool = False
    ) -> dict:
        self.chat_calls += 1
        self.audio_flags.append(audio)
        self.captured["text"] = message
        return {
            "turn_id": 7,
            "terminal": "done",
            "text": self.text,
            "segments": [],
        }

    async def status(self, channel=None) -> dict:
        self.status_calls += 1
        return {
            "sections": {
                "turn": {
                    "current": {
                        "turn_id": 7,
                        "speaking": self.ends_in_s > 0,
                        "ends_at": time.time() + self.ends_in_s,
                    }
                }
            }
        }


class TestPriorityQueue(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = Storage(Path(self.tmp.name) / "test.db")
        await self.storage.init()
        self.persona = ChatbotPersonaStore(self.storage)
        await self.persona.load()
        self.templates = build_template_store(self.storage)
        await self.templates.load()
        self.bus = EventBroadcaster()
        self.loop = StreamLoop(self.storage, self.bus, self.persona, self.templates)
        self.queue_row, _ = await self.storage.create_queue("test queue")
        _, session_id = await self.storage.create_session(
            self.queue_row, "stream", "conv-priority-test"
        )
        row = await self.storage.get_session(session_id)
        self.session = {
            "session_row": row["id"],
            "queue_row": self.queue_row,
            "channel": "stream",
            "conversation_id": "conv-priority-test",
            "cursor": 0,
            "priority_cursor": 0,
        }
        self.settings = SimpleNamespace(
            messages_per_round=10,
            speech_cooldown_ms=0,
            empty_queue_behavior="silent",
            placeholder_message="placeholder",
            scene_context="ctx",
            neuro_url="http://fake",
        )

    async def asyncTearDown(self):
        await self.storage.close()
        self.tmp.cleanup()

    async def test_priority_first_then_normal(self):
        n1 = await self.storage.append_message(self.queue_row, "a", "normal 1")
        p1 = await self.storage.append_message(
            self.queue_row, "b", "pay 1", priority_type="bits", value=100
        )
        p2 = await self.storage.append_message(
            self.queue_row, "c", "pay 2", priority_type="points", value=200
        )
        n2 = await self.storage.append_message(self.queue_row, "d", "normal 2")

        picked = await self.loop._pick(self.settings, self.session)
        self.assertEqual(picked["kind"], "priority")
        self.assertEqual([m["id"] for m in picked["messages"]], [p1])
        self.session["priority_cursor"] = p1

        picked = await self.loop._pick(self.settings, self.session)
        self.assertEqual(picked["kind"], "priority")
        self.assertEqual([m["id"] for m in picked["messages"]], [p2])
        self.session["priority_cursor"] = p2

        # 优先轨空后回落普通轨；优先消息不会混入普通候选
        picked = await self.loop._pick(self.settings, self.session)
        self.assertEqual(picked["kind"], "normal")
        self.assertEqual([m["id"] for m in picked["messages"]], [n1, n2])

    async def test_depths_separate(self):
        await self.storage.append_message(self.queue_row, "a", "normal")
        await self.storage.append_message(
            self.queue_row, "b", "paid", priority_type="bits", value=10
        )
        self.assertEqual(await self.storage.queue_depth_after(self.queue_row, 0), 1)
        self.assertEqual(await self.storage.priority_depth_after(self.queue_row, 0), 1)

    async def test_append_broadcasts_priority_event(self):
        self.loop.session = self.session
        q = self.bus.subscribe()
        msg_id = await self.loop.append_message(
            "fan", "hello", priority_type="points", value=250
        )
        ev = q.get_nowait()
        self.assertEqual(ev["type"], "priority_message")
        self.assertEqual(ev["id"], msg_id)
        self.assertEqual(ev["priority_type"], "points")
        self.assertEqual(ev["value"], 250)

    async def test_append_broadcasts_normal_event(self):
        self.loop.session = self.session
        q = self.bus.subscribe()
        msg_id = await self.loop.append_message("viewer", "hi")
        ev = q.get_nowait()
        self.assertEqual(ev["type"], "chat_message")
        self.assertEqual(ev["id"], msg_id)
        self.assertNotIn("priority_type", ev)

    async def test_empty_queue_behavior(self):
        self.assertIsNone(await self.loop._pick(self.settings, self.session))
        self.settings.empty_queue_behavior = "placeholder"
        picked = await self.loop._pick(self.settings, self.session)
        self.assertEqual(picked["kind"], "placeholder")
        self.assertEqual(picked["body"], "placeholder")

    async def test_empty_queue_renders_placeholder_as_messages_block(self):
        """空队列不再走独立模板：{{chat_messages}} 直接换成 placeholder_message。"""
        self.settings.empty_queue_behavior = "placeholder"
        self.settings.placeholder_message = "Nobody is talking. Start a topic yourself."
        self.session["rounds"] = 0
        captured: dict[str, str] = {}

        class _CapturingClient(_FakeNeuroClient):
            async def chat_batch(self, message, channel, conversation_id, audio=False):
                result = await super().chat_batch(message, channel, conversation_id, audio)
                captured["text"] = message
                return result

        with patch("neuro_simulator.stream.loop.NeuroClient", _CapturingClient):
            await self.loop._run_round(self.settings, self.session)

        text = captured["text"]
        self.assertIn("Recent viewer messages:", text)
        self.assertIn("Nobody is talking. Start a topic yourself.", text)
        self.assertNotIn("{{", text)
        # 占位轮不推进 cursor
        self.assertEqual(self.session["cursor"], 0)

    async def test_only_two_message_templates(self):
        names = [t["name"] for t in self.templates.list()]
        self.assertEqual(names, ["neuro_user", "chatbot_user"])

    async def test_does_not_mirror_tool_or_speech_events(self):
        await self.storage.append_message(self.queue_row, "viewer", "do a trick")
        self.settings.scene_context = "ctx"
        self.settings.neuro_url = "http://fake"
        # settings 以 SimpleNamespace 充当 StreamSettings（_run_round 只读这几个字段）
        q = self.bus.subscribe()
        fake = _FakeNeuroClient("http://fake")
        with patch("neuro_simulator.stream.loop.NeuroClient", lambda url: fake):
            await self.loop._run_round(self.settings, self.session)

        events = []
        while not q.empty():
            events.append(q.get_nowait())
        # 字幕/音频/工具动作由 neuro 窗口页自己订阅 /chat/events，stream 不再镜像。
        self.assertFalse(any(e["type"] in ("tool_start", "tool_result") for e in events))
        self.assertFalse(any(e["type"] == "speech" for e in events))
        self.assertFalse(any(e["type"] in ("turn_begin", "turn_end") for e in events))
        # 批量调用不带音频（音频只归 neuro 窗口）
        self.assertEqual(fake.audio_flags, [False])

    async def test_round_wait_comes_from_status_ends_at_not_local_gap(self):
        """等待时长取 neuro `/status` 的 ends_at；本地不再累加 step_gap。"""
        await self.storage.append_message(self.queue_row, "viewer", "do a trick")
        self.settings.speech_cooldown_ms = 0
        # 「陷阱」：如果 stream 还在本地累加 gap，这个值会让等待多出 5 秒
        self.settings.caption_step_gap_ms = 5000
        sleeps: list[float] = []
        fake = _FakeNeuroClient("http://fake", ends_in_s=0.12)

        async def record_sleep(ms: float) -> None:
            sleeps.append(float(ms))
            await asyncio.sleep(0)  # 不真的睡，只记录

        with patch("neuro_simulator.stream.loop.NeuroClient", lambda url: fake):  # noqa: SIM117
            with patch.object(self.loop, "_sleep_ms", new=record_sleep):
                await self.loop._run_round(self.settings, self.session)

        self.assertEqual(fake.chat_calls, 1)
        self.assertGreater(fake.status_calls, 0)
        # 只等 ends_at（约 120ms）+ cooldown 0；绝不该出现 5000ms 级别的本地 gap
        self.assertTrue(any(100 <= ms <= 200 for ms in sleeps), sleeps)
        self.assertFalse(any(ms > 1000 for ms in sleeps), sleeps)

    async def test_pacing_uses_last_speech_end_not_generation_end(self):
        """语音在生成结束前早已播完（ends_at 在过去）时，不应再等一整句。"""
        await self.storage.append_message(self.queue_row, "viewer", "say something")
        self.settings.speech_cooldown_ms = 0
        sleeps: list[float] = []
        fake = _FakeNeuroClient("http://fake", ends_in_s=-5.0)

        async def _record_sleep(ms: float) -> None:
            sleeps.append(float(ms))
            await asyncio.sleep(0)

        with patch("neuro_simulator.stream.loop.NeuroClient", lambda url: fake):
            with patch.object(self.loop, "_sleep_ms", new=_record_sleep):
                await self.loop._run_round(self.settings, self.session)

        self.assertTrue(all(ms == 0 for ms in sleeps), sleeps)

    async def test_no_turn_or_speech_events_are_emitted(self):
        """speech/turn 事件已移出 stream：neuro 窗口页直接订阅 neuro 的 /chat/events。"""
        await self.storage.append_message(self.queue_row, "viewer", "do a trick")
        q = self.bus.subscribe()
        fake = _FakeNeuroClient("http://fake")
        with patch("neuro_simulator.stream.loop.NeuroClient", lambda url: fake):
            await self.loop._run_round(self.settings, self.session)

        events = []
        while not q.empty():
            events.append(q.get_nowait())
        self.assertFalse(any(e["type"] in ("turn_begin", "turn_end", "speech") for e in events))


if __name__ == "__main__":
    unittest.main()
