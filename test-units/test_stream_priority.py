"""双轨队列（普通 / 优先）行为测试：优先轨先进先出、一次一条、优先不污染普通轨。"""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from neuro_simulator.broadcaster import EventBroadcaster
from neuro_simulator.stream.loop import StreamLoop
from neuro_simulator.stream.storage import Storage


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


if __name__ == "__main__":
    unittest.main()
