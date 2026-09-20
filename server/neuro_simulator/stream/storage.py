"""stream 模块 SQLite 持久化（aiosqlite，模式同 neuro_sama/storage.py）。

表结构：
- queues：观众消息队列（每场直播一个，queue_id 为对外 uuid）
- queue_messages：队列消息（只加不减；自增 id 即注入水位线 cursor 的刻度）
- sessions：直播场次（state: online / online_pause / offline；ended_at 为空 = 进行中）

并发：单 event loop 单连接 + WAL + busy_timeout。
"""

import time
import uuid
from pathlib import Path
from typing import Any, Optional

import aiosqlite

_SCHEMA = """
CREATE TABLE IF NOT EXISTS queues (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    queue_id   TEXT NOT NULL UNIQUE,
    title      TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS queue_messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    queue_row  INTEGER NOT NULL REFERENCES queues(id) ON DELETE CASCADE,
    username   TEXT NOT NULL,
    content    TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_queue_messages_queue ON queue_messages (queue_row, id);

CREATE TABLE IF NOT EXISTS sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL UNIQUE,
    queue_row       INTEGER NOT NULL REFERENCES queues(id),
    channel         TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    state           TEXT NOT NULL DEFAULT 'online',
    started_at      TEXT NOT NULL,
    ended_at        TEXT,
    cursor_msg      INTEGER NOT NULL DEFAULT 0,
    rounds          INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_sessions_started ON sessions (started_at DESC);
"""


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def new_id() -> str:
    return uuid.uuid4().hex


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

    # ---------- 队列 ----------

    async def create_queue(self, title: str) -> tuple[int, str]:
        """新建队列，返回 (行 id, 对外 queue_id)。"""
        queue_id = new_id()
        cur = await self.db.execute(
            "INSERT INTO queues (queue_id, title, created_at) VALUES (?, ?, ?) RETURNING id",
            (queue_id, title, _now()),
        )
        row = await cur.fetchone()
        await self.db.commit()
        return int(row[0]), queue_id

    async def append_message(self, queue_row: int, username: str, content: str) -> int:
        cur = await self.db.execute(
            "INSERT INTO queue_messages (queue_row, username, content, created_at) VALUES (?, ?, ?, ?)",
            (queue_row, username, content, _now()),
        )
        await self.db.commit()
        return int(cur.lastrowid)

    async def list_messages(
        self, queue_row: int, after_id: int = 0, limit: int = 200
    ) -> list[dict[str, Any]]:
        """尾读：id > after_id 的消息正序（dashboard 增量轮询 / 注入候选）。"""
        cur = await self.db.execute(
            "SELECT id, username, content, created_at FROM queue_messages "
            "WHERE queue_row=? AND id>? ORDER BY id LIMIT ?",
            (queue_row, after_id, limit),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def get_queue(self, queue_id: str) -> Optional[dict[str, Any]]:
        cur = await self.db.execute(
            "SELECT id, queue_id, title, created_at FROM queues WHERE queue_id=?", (queue_id,)
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def queue_depth_after(self, queue_row: int, cursor_msg: int) -> int:
        cur = await self.db.execute(
            "SELECT COUNT(*) FROM queue_messages WHERE queue_row=? AND id>?",
            (queue_row, cursor_msg),
        )
        row = await cur.fetchone()
        return int(row[0])

    # ---------- 场次 ----------

    async def create_session(
        self, queue_row: int, channel: str, conversation_id: str
    ) -> tuple[int, str]:
        session_id = new_id()
        cur = await self.db.execute(
            "INSERT INTO sessions (session_id, queue_row, channel, conversation_id, state, started_at) "
            "VALUES (?, ?, ?, ?, 'online', ?) RETURNING id",
            (session_id, queue_row, channel, conversation_id, _now()),
        )
        row = await cur.fetchone()
        await self.db.commit()
        return int(row[0]), session_id

    async def update_session(
        self,
        session_row: int,
        *,
        state: Optional[str] = None,
        ended: bool = False,
        cursor_msg: Optional[int] = None,
        inc_rounds: bool = False,
    ) -> None:
        sets: list[str] = []
        params: list[Any] = []
        if state is not None:
            sets.append("state=?")
            params.append(state)
        if ended:
            sets.append("ended_at=?")
            params.append(_now())
        if cursor_msg is not None:
            sets.append("cursor_msg=?")
            params.append(cursor_msg)
        if inc_rounds:
            sets.append("rounds=rounds+1")
        if sets:
            params.append(session_row)
            # sets 为内部白名单拼装，不接受外部输入
            await self.db.execute(f"UPDATE sessions SET {', '.join(sets)} WHERE id=?", params)
            await self.db.commit()

    async def get_active_session(self) -> Optional[dict[str, Any]]:
        cur = await self.db.execute(
            "SELECT s.id, s.session_id, s.queue_row, q.queue_id, s.channel, s.conversation_id, "
            "s.state, s.started_at, s.cursor_msg, s.rounds "
            "FROM sessions s JOIN queues q ON q.id=s.queue_row "
            "WHERE s.ended_at IS NULL ORDER BY s.id DESC LIMIT 1"
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def get_session(self, session_id: str) -> Optional[dict[str, Any]]:
        cur = await self.db.execute(
            "SELECT s.id, s.session_id, s.queue_row, q.queue_id, s.channel, s.conversation_id, "
            "s.state, s.started_at, s.ended_at, s.rounds "
            "FROM sessions s JOIN queues q ON q.id=s.queue_row "
            "WHERE s.session_id=?",
            (session_id,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def delete_session_data(self, session_id: str) -> Optional[tuple[str, str]]:
        """删除场次及其关联队列。返回 (channel, conversation_id) 以便后续删除 Neuro 对话。"""
        sess = await self.get_session(session_id)
        if not sess:
            return None
        # sessions 表没设 ON DELETE CASCADE，手动按序删除
        await self.db.execute("DELETE FROM sessions WHERE id=?", (sess["id"],))
        await self.db.execute("DELETE FROM queues WHERE id=?", (sess["queue_row"],))
        await self.db.commit()
        return sess["channel"], sess["conversation_id"]

    async def list_sessions(self, limit: int = 20) -> list[dict[str, Any]]:
        cur = await self.db.execute(
            "SELECT s.session_id, q.queue_id, s.channel, s.conversation_id, "
            "s.state, s.started_at, s.ended_at, s.rounds "
            "FROM sessions s JOIN queues q ON q.id=s.queue_row "
            "ORDER BY s.id DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in await cur.fetchall()]
