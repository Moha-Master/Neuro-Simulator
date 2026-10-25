"""stream SQLite 迁移：旧的 priority_type / priority_cursor 列就地改名，数据保留。"""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from neuro_simulator.stream.storage import Storage

_LEGACY_SCHEMA = """
CREATE TABLE queues (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    queue_id   TEXT NOT NULL UNIQUE,
    title      TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE queue_messages (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    queue_row     INTEGER NOT NULL REFERENCES queues(id) ON DELETE CASCADE,
    username      TEXT NOT NULL,
    content       TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    priority_type TEXT,
    value         INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL UNIQUE,
    queue_row       INTEGER NOT NULL REFERENCES queues(id),
    channel         TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    state           TEXT NOT NULL DEFAULT 'online',
    started_at      TEXT NOT NULL,
    ended_at        TEXT,
    cursor_msg      INTEGER NOT NULL DEFAULT 0,
    priority_cursor INTEGER NOT NULL DEFAULT 0,
    rounds          INTEGER NOT NULL DEFAULT 0,
    title           TEXT,
    category        TEXT,
    tags            TEXT
);
"""


class TestStreamStorageMigration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "legacy.db"
        conn = sqlite3.connect(self.path)
        try:
            conn.executescript(_LEGACY_SCHEMA)
            conn.execute(
                "INSERT INTO queues (queue_id, title, created_at) VALUES ('q1', 'old', '2026-01-01 00:00:00')"
            )
            conn.execute(
                "INSERT INTO queue_messages (queue_row, username, content, created_at, priority_type, value) "
                "VALUES (1, 'fan', 'paid hello', '2026-01-01 00:00:01', 'bits', 99)"
            )
            conn.execute(
                "INSERT INTO queue_messages (queue_row, username, content, created_at) "
                "VALUES (1, 'viewer', 'normal hello', '2026-01-01 00:00:02')"
            )
            conn.execute(
                "INSERT INTO sessions (session_id, queue_row, channel, conversation_id, started_at, priority_cursor) "
                "VALUES ('s1', 1, 'stream', 'conv', '2026-01-01 00:00:00', 1)"
            )
            conn.commit()
        finally:
            conn.close()

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_old_columns_are_renamed_and_data_is_preserved(self):
        storage = Storage(self.path)
        await storage.init()
        try:
            cur = await storage.db.execute("PRAGMA table_info(queue_messages)")
            message_cols = {row[1] for row in await cur.fetchall()}
            cur = await storage.db.execute("PRAGMA table_info(sessions)")
            session_cols = {row[1] for row in await cur.fetchall()}

            self.assertIn("highlight_type", message_cols)
            self.assertNotIn("priority_type", message_cols)
            self.assertIn("highlight_cursor", session_cols)
            self.assertNotIn("priority_cursor", session_cols)

            messages = await storage.list_messages(1)
            self.assertEqual(messages[0]["highlight_type"], "bits")
            self.assertEqual(messages[0]["value"], 99)
            self.assertIsNone(messages[1]["highlight_type"])

            normal = await storage.list_normal_messages(1)
            self.assertEqual([m["content"] for m in normal], ["normal hello"])
            highlighted = await storage.list_highlight_messages(1)
            self.assertEqual([m["content"] for m in highlighted], ["paid hello"])
            self.assertEqual(await storage.highlight_depth_after(1, 0), 1)

            session = await storage.get_session("s1")
            self.assertEqual(session["highlight_cursor"], 1)
        finally:
            await storage.close()

    async def test_migration_is_idempotent(self):
        storage = Storage(self.path)
        await storage.init()
        await storage.close()
        storage = Storage(self.path)
        await storage.init()
        try:
            cur = await storage.db.execute("PRAGMA table_info(queue_messages)")
            cols = [row[1] for row in await cur.fetchall()]
            self.assertEqual(cols.count("highlight_type"), 1)
            self.assertNotIn("priority_type", cols)
        finally:
            await storage.close()


if __name__ == "__main__":
    unittest.main()
