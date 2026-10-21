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
    """只回放固定事件序列，用于验证 stream 对 tool_start 的转发/过滤。"""

    def __init__(self, url: str) -> None:
        self.url = url

    async def chat(self, channel: str, conversation_id: str, text: str):
        yield {
            "type": "tool_start",
            "step": 1,
            "tool_call_id": "c1",
            "name": "model_spin",
            "arguments": {},
        }
        yield {
            "type": "tool_start",
            "step": 1,
            "tool_call_id": "c2",
            "name": "memory_add",
            "arguments": {"content": "not for the UI"},
        }
        yield {
            "type": "tool_result",
            "step": 1,
            "tool_call_id": "c1",
            "name": "model_spin",
            "ok": True,
            "result": "Spin command sent.",
            "duration_ms": 0,
        }
        yield {"type": "speech", "step": 1, "index": 0, "text": "ta-da", "duration": 0.01}
        yield {"type": "done", "content": "ta-da"}


class _FakeSlowNeuroClient:
    """先说一句，再“生成”一段时间才 done：用于验证轮间等待以语音播完为准。"""

    def __init__(self, url: str) -> None:
        self.url = url

    async def chat(self, channel: str, conversation_id: str, text: str):
        yield {"type": "speech", "index": 0, "text": "short line", "duration": 0.01}
        await asyncio.sleep(0.05)  # 模拟工具执行 / 下一步生成：此时语音早已播完
        yield {"type": "done", "content": "short line"}


class _FakeTwoStepNeuroClient:
    """第一步的语音还没播完，第二步就立即产出：验证 step 间也留 cooldown。"""

    def __init__(self, url: str) -> None:
        self.url = url

    async def chat(self, channel: str, conversation_id: str, text: str):
        yield {"type": "speech", "step": 1, "index": 0, "text": "first", "duration": 0.1}
        await asyncio.sleep(0.01)
        yield {"type": "speech", "step": 2, "index": 1, "text": "second", "duration": 0.02}
        yield {"type": "done", "content": "first second"}


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
        """空队列不再走独立模板：{{messages}} 直接换成 placeholder_message。"""
        self.settings.empty_queue_behavior = "placeholder"
        self.settings.placeholder_message = "Nobody is talking. Start a topic yourself."
        self.session["rounds"] = 0
        captured: dict[str, str] = {}

        class _CapturingClient(_FakeNeuroClient):
            async def chat(self, channel: str, conversation_id: str, text: str):
                captured["text"] = text
                async for ev in super().chat(channel, conversation_id, text):
                    yield ev

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

    async def test_mirrors_all_tool_events_without_filtering(self):
        await self.storage.append_message(self.queue_row, "viewer", "do a trick")
        self.settings.scene_context = "ctx"
        self.settings.neuro_url = "http://fake"
        # settings 以 SimpleNamespace 充当 StreamSettings（_run_round 只读这几个字段）
        q = self.bus.subscribe()
        with patch("neuro_simulator.stream.loop.NeuroClient", _FakeNeuroClient):
            await self.loop._run_round(self.settings, self.session)

        events = []
        while not q.empty():
            events.append(q.get_nowait())
        # stream 不再解释工具语义：所有 tool_start/tool_result 原样镜像，
        # 由画面端按 name 自行决定是否消费（当前只处理 model_*）。
        tool_starts = [e for e in events if e["type"] == "tool_start"]
        self.assertEqual([e["name"] for e in tool_starts], ["model_spin", "memory_add"])
        self.assertTrue(
            any(
                e["type"] == "tool_result" and e["name"] == "model_spin"
                for e in events
            )
        )
        # 不再产生专用 tool_call 事件
        self.assertFalse(any(e["type"] == "tool_call" for e in events))

    async def test_step_change_waits_for_prev_speech_and_cooldown(self):
        """同一轮内跨 step：第二步 speech 要等第一步播完 + cooldown 才转发。"""
        await self.storage.append_message(self.queue_row, "viewer", "do a trick")
        self.settings.speech_cooldown_ms = 80
        events: list[tuple[float, dict]] = []
        sleeps: list[float] = []
        original_publish = self.loop.publish

        def capture(ev: dict) -> None:
            events.append((time.monotonic(), ev))
            original_publish(ev)

        async def record_sleep(ms: float) -> None:
            sleeps.append(float(ms))
            await asyncio.sleep(float(ms) / 1000.0)

        with patch("neuro_simulator.stream.loop.NeuroClient", _FakeTwoStepNeuroClient):  # noqa: SIM117
            with patch.object(self.loop, "publish", side_effect=capture):
                with patch.object(self.loop, "_sleep_ms", new=record_sleep):
                    await self.loop._run_round(self.settings, self.session)

        speech = [(t, e) for t, e in events if e["type"] == "speech"]
        self.assertEqual([e["step"] for _, e in speech], [1, 2])
        gap = speech[1][0] - speech[0][0]
        # 第一步 duration 0.1s + cooldown 0.08s；允许调度误差
        self.assertGreaterEqual(gap, 0.15)
        self.assertTrue(any(ms > 50 for ms in sleeps))
        self.assertEqual(
            [e["step"] for _, e in speech],
            [s.get("step") for s in self.loop._turn_speech],
        )

    async def test_pacing_uses_last_speech_end_not_generation_end(self):
        """语音在 done 之前早已播完时，等待量应为 0（旧实现会再等整整一句时长）。"""
        await self.storage.append_message(self.queue_row, "viewer", "say something")
        self.settings.speech_cooldown_ms = 0
        sleeps: list[float] = []

        async def _record_sleep(ms: float) -> None:
            sleeps.append(float(ms))
            await asyncio.sleep(0)

        with patch("neuro_simulator.stream.loop.NeuroClient", _FakeSlowNeuroClient):
            with patch.object(self.loop, "_sleep_ms", new=_record_sleep):
                await self.loop._run_round(self.settings, self.session)

        # 语音 0.01s，done 前又过了 0.05s：播完 + cooldown 已过，不应再有轮间等待
        self.assertEqual(len(sleeps), 1)
        self.assertLess(sleeps[0], 1.0)

    async def test_turn_id_is_stable_within_round(self):
        """speech / turn_begin / turn_end 共用 turn_id；下一轮递增（画面跨轮去重依据）。"""
        await self.storage.append_message(self.queue_row, "viewer", "do a trick")
        q = self.bus.subscribe()
        with patch("neuro_simulator.stream.loop.NeuroClient", _FakeNeuroClient):
            await self.loop._run_round(self.settings, self.session)

        events = []
        while not q.empty():
            events.append(q.get_nowait())
        begin = next(e for e in events if e["type"] == "turn_begin")
        end = next(e for e in events if e["type"] == "turn_end")
        speech = next(e for e in events if e["type"] == "speech")
        self.assertEqual(begin["turn_id"], end["turn_id"])
        self.assertEqual(begin["turn_id"], speech["turn_id"])
        self.assertEqual(speech["step"], 1)
        snap = self.loop.turn_snapshot()
        self.assertEqual(snap["id"], begin["turn_id"])
        self.assertEqual(snap["speech"][0]["step"], 1)


if __name__ == "__main__":
    unittest.main()
