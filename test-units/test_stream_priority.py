"""双轨队列（普通 / 优先）行为测试：优先轨先进先出、一次一条、优先不污染普通轨。"""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from neuro_simulator.broadcaster import EventBroadcaster
from neuro_simulator.stream.loop import StreamLoop
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
        yield {"type": "speech", "index": 0, "text": "ta-da", "duration": 0.01}
        yield {"type": "done", "content": "ta-da"}


class TestPriorityQueue(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = Storage(Path(self.tmp.name) / "test.db")
        await self.storage.init()
        self.bus = EventBroadcaster()
        self.loop = StreamLoop(self.storage, self.bus)
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
            empty_queue_behavior="silent",
            placeholder_message="placeholder",
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


if __name__ == "__main__":
    unittest.main()
