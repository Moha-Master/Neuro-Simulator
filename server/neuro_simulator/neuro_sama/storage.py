"""SQLite 持久化层（aiosqlite）：多渠道多对话独立隔离，设计参考 AstrBot。

表结构：
- conversations：(channel, conversation_id) 为唯一对话标识，天然按渠道+会话隔离
- messages：OpenAI 格式消息逐行存储（content 列为完整消息 dict 的 JSON）
- memories：记忆条目，scope 目前为 'global'（预留 per-conversation）

并发：单 event loop 单连接 + WAL + busy_timeout（AstrBot 同款，无需应用级锁）。
"""

import json
import time
from pathlib import Path
from typing import Any, Optional

import aiosqlite

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    channel         TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    title           TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    UNIQUE (channel, conversation_id)
);
CREATE INDEX IF NOT EXISTS ix_conversations_channel_updated
    ON conversations (channel, updated_at DESC);

CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL,
    content         TEXT NOT NULL,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_messages_conv ON messages (conversation_id, id);

CREATE TABLE IF NOT EXISTS memories (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    scope      TEXT NOT NULL DEFAULT 'global',
    content    TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_memories_scope ON memories (scope);
"""


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


class Storage:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._db: Optional[aiosqlite.Connection] = None

    async def init(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA busy_timeout=5000")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._db.executescript(_SCHEMA)
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Storage not initialized: call init() first")
        return self._db

    # ---------- conversations ----------

    async def get_or_create_conversation(
        self, channel: str, conversation_id: str, title: Optional[str] = None
    ) -> int:
        """按 (channel, conversation_id) 取对话行 id，不存在则创建。"""
        now = _now()
        cur = await self.db.execute(
            "INSERT INTO conversations (channel, conversation_id, title, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(channel, conversation_id) DO UPDATE SET updated_at=excluded.updated_at "
            "RETURNING id",
            (channel, conversation_id, title, now, now),
        )
        row = await cur.fetchone()
        await self.db.commit()
        return int(row[0])

    # ---------- messages ----------

    async def append_message(self, conv_id: int, message: dict) -> None:
        """追加一条消息（完整 OpenAI 消息 dict 序列化进 content 列）。"""
        await self.db.execute(
            "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (conv_id, message.get("role", ""), json.dumps(message, ensure_ascii=False), _now()),
        )
        await self.db.execute(
            "UPDATE conversations SET updated_at=? WHERE id=?", (_now(), conv_id)
        )
        await self.db.commit()

    async def get_messages(self, conv_id: int, limit: Optional[int] = None) -> list[dict]:
        """取最近 limit 条消息（时间正序，每条含行 id）；limit=None 取全部。"""
        query = "SELECT id, content FROM messages WHERE conversation_id=? ORDER BY id DESC"
        params: list[Any] = [conv_id]
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        cur = await self.db.execute(query, params)
        rows = await cur.fetchall()
        return [{"id": r[0], **json.loads(r[1])} for r in reversed(rows)]

    # ---------- 管理：会话 ----------

    async def list_conversations(self) -> list[dict]:
        """全部会话（含拆分计数），按最近活跃倒序。

        - message_count：对话消息数 = user 行 + 有非空正文的 assistant 行
          （纯 tool_calls 无正文的 assistant 行不计入，它们体现在 tool_calls）
        - tool_calls：发起工具调用的 assistant 消息数（"calls"）
        - tool_uses：实际执行的工具调用数 = tool 结果行数（"used"）
        """
        cur = await self.db.execute(
            "SELECT c.channel, c.conversation_id, c.title, c.created_at, c.updated_at, "
            "(SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id "
            "  AND (m.role = 'user' OR m.role = 'assistant') "
            "  AND json_extract(m.content, '$.content') IS NOT NULL "
            "  AND json_extract(m.content, '$.content') != '') AS message_count, "
            "(SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id "
            "  AND m.role = 'assistant' AND m.content LIKE '%\"tool_calls\"%') AS tool_calls, "
            "(SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id "
            "  AND m.role = 'tool') AS tool_uses "
            "FROM conversations c ORDER BY c.updated_at DESC, c.id DESC"
        )
        return [
            {
                "channel": r[0],
                "conversation_id": r[1],
                "title": r[2],
                "created_at": r[3],
                "updated_at": r[4],
                "message_count": r[5],
                "tool_calls": r[6],
                "tool_uses": r[7],
            }
            for r in await cur.fetchall()
        ]

    async def find_conversation_id(self, channel: str, conversation_id: str) -> Optional[int]:
        cur = await self.db.execute(
            "SELECT id FROM conversations WHERE channel=? AND conversation_id=?",
            (channel, conversation_id),
        )
        row = await cur.fetchone()
        return int(row[0]) if row else None

    async def delete_conversation(self, channel: str, conversation_id: str) -> bool:
        """删除会话（级联删除其全部消息）。返回是否真的删掉了什么。"""
        cur = await self.db.execute(
            "DELETE FROM conversations WHERE channel=? AND conversation_id=?",
            (channel, conversation_id),
        )
        await self.db.commit()
        return cur.rowcount > 0

    async def update_conversation_title(self, channel: str, conversation_id: str, title: str) -> bool:
        """重命名会话。返回是否真的改到了什么。"""
        cur = await self.db.execute(
            "UPDATE conversations SET title=? WHERE channel=? AND conversation_id=?",
            (title, channel, conversation_id),
        )
        await self.db.commit()
        return cur.rowcount > 0

    # ---------- 管理：消息 ----------

    async def update_message(self, message_id: int, message: dict) -> bool:
        """整体替换一条消息（完整 OpenAI 消息 dict）。"""
        cur = await self.db.execute(
            "UPDATE messages SET role=?, content=? WHERE id=?",
            (message.get("role", ""), json.dumps(message, ensure_ascii=False), message_id),
        )
        await self.db.commit()
        return cur.rowcount > 0

    async def delete_message(self, message_id: int) -> bool:
        cur = await self.db.execute("DELETE FROM messages WHERE id=?", (message_id,))
        await self.db.commit()
        return cur.rowcount > 0

    # ---------- memories ----------

    async def add_memory(self, content: str, scope: str = "global") -> int:
        """新增一条记忆，返回新行 id。"""
        cur = await self.db.execute(
            "INSERT INTO memories (scope, content, created_at) VALUES (?, ?, ?)",
            (scope, content, _now()),
        )
        await self.db.commit()
        return int(cur.lastrowid)

    async def update_memory(self, memory_id: int, content: str) -> bool:
        """整体替换一条记忆的内容（保留 created_at）。返回是否真的改到了。"""
        cur = await self.db.execute(
            "UPDATE memories SET content=? WHERE id=?",
            (content, memory_id),
        )
        await self.db.commit()
        return cur.rowcount > 0

    async def delete_memory(self, memory_id: int) -> bool:
        cur = await self.db.execute("DELETE FROM memories WHERE id=?", (memory_id,))
        await self.db.commit()
        return cur.rowcount > 0

    async def get_memories(self, scope: str = "global") -> list[dict]:
        """按写入顺序返回 [{id, content, ts}]。"""
        cur = await self.db.execute(
            "SELECT id, content, created_at FROM memories WHERE scope=? ORDER BY id", (scope,)
        )
        return [{"id": r[0], "content": r[1], "ts": r[2][:16]} for r in await cur.fetchall()]

    async def count_memories(self, scope: str = "global") -> int:
        cur = await self.db.execute("SELECT COUNT(*) FROM memories WHERE scope=?", (scope,))
        row = await cur.fetchone()
        return int(row[0])
