"""Chat 消息行时间戳测试：管理接口依赖 get_messages 返回 DB created_at。"""

import tempfile
import unittest
from pathlib import Path

from neuro_simulator.neuro_sama.storage import Storage


class TestMessageTimestamps(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = Storage(Path(self.tmp.name) / "test.db")
        await self.storage.init()

    async def asyncTearDown(self):
        await self.storage.close()
        self.tmp.cleanup()

    async def test_get_messages_includes_created_at(self):
        conv_id = await self.storage.get_or_create_conversation("web", "conv-time-test")
        await self.storage.append_message(conv_id, {"role": "user", "content": "hello"})
        await self.storage.append_message(conv_id, {"role": "assistant", "content": "hi"})

        messages = await self.storage.get_messages(conv_id)
        self.assertEqual(len(messages), 2)
        for m in messages:
            self.assertIn("created_at", m)
            self.assertRegex(m["created_at"], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
        self.assertEqual(messages[0]["content"], "hello")
        self.assertEqual(messages[1]["content"], "hi")


if __name__ == "__main__":
    unittest.main()
