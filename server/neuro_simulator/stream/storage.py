"""stream 模块 SQLite 持久化（aiosqlite，模式同 neuro_sama/storage.py）。

表结构：
- queues：观众消息队列（每场直播一个，queue_id 为对外 uuid）
- queue_messages：队列消息（只加不减；自增 id 即注入水位线 cursor 的刻度）
  双轨：priority_type 为空 = 普通消息；非空（bits/points）= 优先消息，
  由 sessions.priority_cursor 独立消费，普通轨永不抽取优先消息。
- sessions：直播场次（state: online / online_pause / offline；ended_at 为空 = 进行中）

并发：单 event loop 单连接 + WAL + busy_timeout。
"""

import json
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
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    queue_row     INTEGER NOT NULL REFERENCES queues(id) ON DELETE CASCADE,
    username      TEXT NOT NULL,
    content       TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    priority_type TEXT,
    value         INTEGER NOT NULL DEFAULT 0
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
    priority_cursor INTEGER NOT NULL DEFAULT 0,
    rounds          INTEGER NOT NULL DEFAULT 0,
    title           TEXT,
    category        TEXT,
    tags            TEXT
);
CREATE INDEX IF NOT EXISTS ix_sessions_started ON sessions (started_at DESC);

-- 可编辑内容层（与 neuro_sama 同构，逻辑复用顶层 neuro_simulator/persona.py）
-- prompts：chatbot 的 system prompt + stream 拼给 neuro/chatbot 的消息模板（按 name 区分）
CREATE TABLE IF NOT EXISTS prompts (
    name       TEXT PRIMARY KEY,
    content    TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- core_memories：人工维护的观众视角事实块（scope 预留多套人设）
CREATE TABLE IF NOT EXISTS core_memories (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    scope      TEXT NOT NULL DEFAULT 'chatbot',
    title      TEXT NOT NULL,
    content    TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (scope, title)
);

-- 一次性播种标记（避免用户删光后重启又恢复默认内容）
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- scenes：场景清单（整份 JSON 文档，单行 name='scenes'）；
-- 包内 defaults/scenes.json 只是播种模板，DB 里的才是权威
CREATE TABLE IF NOT EXISTS scenes (
    name       TEXT PRIMARY KEY,
    content    TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def new_id() -> str:
    return uuid.uuid4().hex


def _decode_tags(value: Any) -> list[str]:
    """sessions.tags 以 JSON 数组存库；读取时还原成 list。"""
    if not value:
        return []
    try:
        data = json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [str(t) for t in data if str(t).strip()]


def _session_row(row: Any) -> dict[str, Any]:
    data = dict(row)
    data["tags"] = _decode_tags(data.get("tags"))
    return data


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
        await self._migrate()
        await self._db.commit()

    async def _migrate(self) -> None:
        """给既有库补列（CREATE TABLE IF NOT EXISTS 不会改已存在的表）。"""
        await self._ensure_column("queue_messages", "priority_type", "priority_type TEXT")
        await self._ensure_column(
            "queue_messages", "value", "value INTEGER NOT NULL DEFAULT 0"
        )
        await self._ensure_column(
            "sessions", "priority_cursor", "priority_cursor INTEGER NOT NULL DEFAULT 0"
        )
        await self._ensure_column("sessions", "title", "title TEXT")
        await self._ensure_column("sessions", "category", "category TEXT")
        await self._ensure_column("sessions", "tags", "tags TEXT")

    async def _ensure_column(self, table: str, column: str, ddl: str) -> None:
        cur = await self.db.execute(f"PRAGMA table_info({table})")
        cols = {row[1] for row in await cur.fetchall()}
        if column not in cols:
            # table/ddl 均为内部常量拼装，不接受外部输入
            await self.db.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")

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

    async def append_message(
        self,
        queue_row: int,
        username: str,
        content: str,
        *,
        priority_type: Optional[str] = None,
        value: int = 0,
    ) -> int:
        """追加消息。priority_type 非空即进入优先轨（bits/points + value）。"""
        cur = await self.db.execute(
            "INSERT INTO queue_messages "
            "(queue_row, username, content, created_at, priority_type, value) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (queue_row, username, content, _now(), priority_type, value),
        )
        await self.db.commit()
        return int(cur.lastrowid)

    async def list_messages(
        self, queue_row: int, after_id: int = 0, limit: int = 200
    ) -> list[dict[str, Any]]:
        """尾读：id > after_id 的消息正序（dashboard 增量轮询 / 注入候选）。"""
        cur = await self.db.execute(
            "SELECT id, username, content, created_at, priority_type, value "
            "FROM queue_messages WHERE queue_row=? AND id>? ORDER BY id LIMIT ?",
            (queue_row, after_id, limit),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def list_normal_messages(
        self, queue_row: int, after_id: int = 0, limit: int = 200
    ) -> list[dict[str, Any]]:
        """普通轨候选（排除优先消息）。"""
        cur = await self.db.execute(
            "SELECT id, username, content, created_at, priority_type, value "
            "FROM queue_messages "
            "WHERE queue_row=? AND id>? AND priority_type IS NULL ORDER BY id LIMIT ?",
            (queue_row, after_id, limit),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def list_priority_messages(
        self, queue_row: int, after_id: int = 0, limit: int = 1
    ) -> list[dict[str, Any]]:
        """优先轨最早未消费消息（id > priority_cursor，正序）。"""
        cur = await self.db.execute(
            "SELECT id, username, content, created_at, priority_type, value "
            "FROM queue_messages "
            "WHERE queue_row=? AND id>? AND priority_type IS NOT NULL ORDER BY id LIMIT ?",
            (queue_row, after_id, limit),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def recent_messages(self, queue_row: int, limit: int = 30) -> list[dict[str, Any]]:
        """最近 N 条（含优先消息），正序返回；供 /ui snapshot 重连恢复聊天层。"""
        cur = await self.db.execute(
            "SELECT id, username, content, created_at, priority_type, value "
            "FROM queue_messages WHERE queue_row=? ORDER BY id DESC LIMIT ?",
            (queue_row, limit),
        )
        rows = [dict(r) for r in await cur.fetchall()]
        rows.reverse()
        return rows

    async def get_queue(self, queue_id: str) -> Optional[dict[str, Any]]:
        cur = await self.db.execute(
            "SELECT id, queue_id, title, created_at FROM queues WHERE queue_id=?", (queue_id,)
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def queue_depth_after(self, queue_row: int, cursor_msg: int) -> int:
        """普通轨未消费深度（优先消息不计入）。"""
        cur = await self.db.execute(
            "SELECT COUNT(*) FROM queue_messages "
            "WHERE queue_row=? AND id>? AND priority_type IS NULL",
            (queue_row, cursor_msg),
        )
        row = await cur.fetchone()
        return int(row[0])

    async def priority_depth_after(self, queue_row: int, priority_cursor: int) -> int:
        cur = await self.db.execute(
            "SELECT COUNT(*) FROM queue_messages "
            "WHERE queue_row=? AND id>? AND priority_type IS NOT NULL",
            (queue_row, priority_cursor),
        )
        row = await cur.fetchone()
        return int(row[0])

    # ---------- 场次 ----------

    async def create_session(
        self,
        queue_row: int,
        channel: str,
        conversation_id: str,
        *,
        title: Optional[str] = None,
        category: Optional[str] = None,
        tags: Optional[list[str]] = None,
    ) -> tuple[int, str]:
        session_id = new_id()
        cur = await self.db.execute(
            "INSERT INTO sessions "
            "(session_id, queue_row, channel, conversation_id, state, started_at, title, category, tags) "
            "VALUES (?, ?, ?, ?, 'online', ?, ?, ?, ?) RETURNING id",
            (
                session_id,
                queue_row,
                channel,
                conversation_id,
                _now(),
                title,
                category,
                json.dumps(tags or [], ensure_ascii=False),
            ),
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
        priority_cursor: Optional[int] = None,
        inc_rounds: bool = False,
        title: Optional[str] = None,
        category: Optional[str] = None,
        tags: Optional[list[str]] = None,
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
        if priority_cursor is not None:
            sets.append("priority_cursor=?")
            params.append(priority_cursor)
        if title is not None:
            sets.append("title=?")
            params.append(title)
        if category is not None:
            sets.append("category=?")
            params.append(category)
        if tags is not None:
            sets.append("tags=?")
            params.append(json.dumps(tags, ensure_ascii=False))
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
            "s.state, s.started_at, s.cursor_msg, s.priority_cursor, s.rounds, "
            "s.title, s.category, s.tags "
            "FROM sessions s JOIN queues q ON q.id=s.queue_row "
            "WHERE s.ended_at IS NULL ORDER BY s.id DESC LIMIT 1"
        )
        row = await cur.fetchone()
        return _session_row(row) if row else None

    async def get_session(self, session_id: str) -> Optional[dict[str, Any]]:
        cur = await self.db.execute(
            "SELECT s.id, s.session_id, s.queue_row, q.queue_id, s.channel, s.conversation_id, "
            "s.state, s.started_at, s.ended_at, s.priority_cursor, s.rounds, s.title, s.category, s.tags "
            "FROM sessions s JOIN queues q ON q.id=s.queue_row "
            "WHERE s.session_id=?",
            (session_id,),
        )
        row = await cur.fetchone()
        return _session_row(row) if row else None

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
            "s.state, s.started_at, s.ended_at, s.rounds, s.title, s.category, s.tags "
            "FROM sessions s JOIN queues q ON q.id=s.queue_row "
            "ORDER BY s.id DESC LIMIT ?",
            (limit,),
        )
        return [_session_row(r) for r in await cur.fetchall()]

    # ---------- persona / 模板（prompts + core_memories + meta） ----------
    # 供顶层 neuro_simulator.persona.PersonaStore 与 templates.TemplateStore 使用；
    # 本模块的 core memory 固定 scope='chatbot'。

    _CORE_SCOPE = "chatbot"

    async def get_prompt(self, name: str) -> Optional[dict[str, Any]]:
        cur = await self.db.execute(
            "SELECT name, content, updated_at FROM prompts WHERE name=?", (name,)
        )
        row = await cur.fetchone()
        if row is None:
            return None
        return {"name": row[0], "content": row[1], "updated_at": row[2]}

    async def upsert_prompt(self, content: str, name: str) -> None:
        await self.db.execute(
            "INSERT INTO prompts (name, content, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET content=excluded.content, "
            "updated_at=excluded.updated_at",
            (name, content, _now()),
        )
        await self.db.commit()

    # ---------- 场景清单：DB 里的那份才是权威 ----------

    async def get_scene(self, name: str = "scenes") -> Optional[dict[str, Any]]:
        cur = await self.db.execute(
            "SELECT name, content, updated_at FROM scenes WHERE name=?", (name,)
        )
        row = await cur.fetchone()
        if row is None:
            return None
        return {"name": row[0], "content": row[1], "updated_at": row[2]}

    async def upsert_scene(self, content: str, name: str = "scenes") -> None:
        await self.db.execute(
            "INSERT INTO scenes (name, content, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET content=excluded.content, "
            "updated_at=excluded.updated_at",
            (name, content, _now()),
        )
        await self.db.commit()

    async def get_core_memories(self) -> list[dict[str, Any]]:
        cur = await self.db.execute(
            "SELECT id, title, content, created_at, updated_at FROM core_memories "
            "WHERE scope=? ORDER BY id",
            (self._CORE_SCOPE,),
        )
        return [
            {
                "id": r[0],
                "title": r[1],
                "content": r[2],
                "created_at": r[3],
                "updated_at": r[4],
            }
            for r in await cur.fetchall()
        ]

    async def add_core_memory(self, title: str, content: str) -> int:
        now = _now()
        cur = await self.db.execute(
            "INSERT INTO core_memories (scope, title, content, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (self._CORE_SCOPE, title, content, now, now),
        )
        await self.db.commit()
        return int(cur.lastrowid)

    async def update_core_memory(self, memory_id: int, title: str, content: str) -> bool:
        cur = await self.db.execute(
            "UPDATE core_memories SET title=?, content=?, updated_at=? "
            "WHERE id=? AND scope=?",
            (title, content, _now(), memory_id, self._CORE_SCOPE),
        )
        await self.db.commit()
        return cur.rowcount > 0

    async def delete_core_memory(self, memory_id: int) -> bool:
        cur = await self.db.execute(
            "DELETE FROM core_memories WHERE id=? AND scope=?",
            (memory_id, self._CORE_SCOPE),
        )
        await self.db.commit()
        return cur.rowcount > 0

    async def get_meta(self, key: str) -> Optional[str]:
        cur = await self.db.execute("SELECT value FROM meta WHERE key=?", (key,))
        row = await cur.fetchone()
        return row[0] if row else None

    async def set_meta(self, key: str, value: str) -> None:
        await self.db.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        await self.db.commit()
