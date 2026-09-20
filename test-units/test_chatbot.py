import asyncio
import time
from pathlib import Path
import tempfile
import unittest

from neuro_simulator.stream.storage import Storage
from neuro_simulator.stream.chatbot import ChatbotManager
from neuro_simulator.broadcaster import EventBroadcaster
from neuro_simulator.stream.loop import StreamLoop


class TestChatbot(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "test.db"
        self.storage = Storage(self.db_path)
        await self.storage.init()

    async def asyncTearDown(self):
        await self.storage.close()
        self.tmpdir.cleanup()

    def test_parse_output(self):
        cb = ChatbotManager(self.storage)
        raw = (
            "viewer_a: Pog hello Neuro!\n"
            "- user_b: So funny LUL\n"
            "Just another comment\n"
            "4. mod_guy: calm down chat\n"
        )
        parsed = cb._parse_output(raw, count=3)
        self.assertEqual(len(parsed), 3)
        self.assertEqual(parsed[0], ("viewer_a", "Pog hello Neuro!"))
        self.assertEqual(parsed[1], ("user_b", "So funny LUL"))
        self.assertTrue(len(parsed[2][0]) > 0)
        self.assertEqual(parsed[2][1], "Just another comment")

    def test_neuro_status_transitions(self):
        cb = ChatbotManager(self.storage)
        # 1. 尚未发言
        self.assertEqual(
            cb._get_neuro_status(sleep_timeout_s=5.0),
            "Neuro just online now! Stream is starting soon..."
        )

        # 2. 刚说完话 (耗时 2s)
        cb.update_neuro_speech("Hello everyone, I am streaming!", duration=2.0)
        self.assertEqual(
            cb._get_neuro_status(sleep_timeout_s=5.0),
            "Neuro says: Hello everyone, I am streaming!"
        )

        # 3. 模拟超时 Sleeping (把 tts_finish_time 拨回过去)
        cb._tts_finish_time = time.monotonic() - 10.0
        self.assertEqual(
            cb._get_neuro_status(sleep_timeout_s=5.0),
            "Neuro seems fall asleep on stream..."
        )

    async def test_session_lifecycle(self):
        cb = ChatbotManager(self.storage)
        queue_row, _ = await self.storage.create_queue("test queue")
        woken = False
        def on_msg():
            nonlocal woken
            woken = True

        cb.start_session(queue_row, on_msg)
        self.assertIsNotNone(cb._loop_task)
        cb.stop_session()
        self.assertIsNone(cb._loop_task)


if __name__ == "__main__":
    unittest.main()
