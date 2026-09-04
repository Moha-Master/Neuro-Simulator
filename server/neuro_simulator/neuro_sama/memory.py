"""记忆系统：SQLite 持久化 + 内存缓存，每轮请求时编译渲染进 system prompt。

设计参考 Letta / AstrBot：
- system prompt 永不缓存，每次 LLM 调用前从当前状态渲染（memory_add 后下一 step 即生效）
- 条目持久化在 <workdir>/neuro-sama/data.db 的 memories 表（scope='global'）
- 超限拒绝并引导模型自行整理，绝不静默截断
- 归一化精确去重；返回值始终带 used/limit 用量，让模型对剩余空间有感知
"""

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .storage import Storage


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _normalize(s: str) -> str:
    return re.sub(r"\s+", "", s.lower())


@dataclass
class MemoryEntry:
    id: int
    ts: str
    text: str


class MemoryError(Exception):
    """记忆操作失败。message 可直接回填给模型或作为 API detail。"""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


class MemoryDuplicateError(MemoryError):
    def __init__(self, message: str) -> None:
        super().__init__(message, status=409)


class MemoryFullError(MemoryError):
    def __init__(self, message: str) -> None:
        super().__init__(message, status=400)


class MemoryNotFoundError(MemoryError):
    def __init__(self, memory_id: int) -> None:
        super().__init__(f"memory {memory_id} not found", status=404)


class MemoryStore:
    def __init__(self, storage: "Storage", char_limit: int, scope: str = "global") -> None:
        self._storage = storage
        self.char_limit = char_limit
        self.scope = scope
        self._entries: list[MemoryEntry] = []
        self._loaded = False
        # 写操作串行化：并发 /chat（不同会话同时 memory_add）时，
        # 去重检查与落库之间无竞态（检查-落库-更新缓存 必须原子）
        self._lock = asyncio.Lock()

    async def load(self) -> None:
        """从存储加载全部条目到内存缓存（服务启动时调用一次）。"""
        rows = await self._storage.get_memories(self.scope)
        self._entries = [MemoryEntry(id=r["id"], ts=r["ts"], text=r["content"]) for r in rows]
        self._loaded = True

    async def _ensure_loaded(self) -> None:
        if not self._loaded:
            await self.load()

    @property
    def used(self) -> int:
        return sum(len(e.text) for e in self._entries)

    @property
    def count(self) -> int:
        return len(self._entries)

    def list(self) -> list[dict]:
        """全部条目（含 id），按写入顺序。"""
        return [{"id": e.id, "content": e.text, "ts": e.ts} for e in self._entries]

    def stats(self) -> dict:
        return {"used": self.used, "char_limit": self.char_limit, "count": self.count}

    def _find(self, memory_id: int) -> Optional[MemoryEntry]:
        for e in self._entries:
            if e.id == memory_id:
                return e
        return None

    def _dup_of(self, content: str, exclude_id: Optional[int] = None) -> Optional[MemoryEntry]:
        norm = _normalize(content)
        for e in self._entries:
            if exclude_id is not None and e.id == exclude_id:
                continue
            if _normalize(e.text) == norm:
                return e
        return None

    async def add_entry(self, content: str) -> MemoryEntry:
        """新增一条记忆（API 用）。失败抛 MemoryError 子类。"""
        await self._ensure_loaded()
        content = content.strip()
        if not content:
            raise MemoryError("content must not be empty")
        async with self._lock:  # 去重/上限检查 + 落库 + 更新缓存，原子执行
            dup = self._dup_of(content)
            if dup:
                raise MemoryDuplicateError(
                    f"this memory already exists [{dup.ts}] {dup.text}. "
                    "Do not add duplicates; if the information has changed, add a new corrected statement instead."
                )
            if self.used + len(content) > self.char_limit:
                raise MemoryFullError(
                    f"memory is full ({self.used}/{self.char_limit} chars). "
                    "Consolidate or drop stale entries before adding new ones."
                )
            row_id = await self._storage.add_memory(content, self.scope)
            entry = MemoryEntry(id=row_id, ts=_now(), text=content)
            self._entries.append(entry)
            return entry

    async def add(self, content: str) -> str:
        """LLM 面向（memory_add 工具）。成功返回确认文本；拒绝时返回 Error 文本（原样回填给模型）。"""
        try:
            await self.add_entry(content)
        except MemoryError as e:
            return f"Error: {e.message}"
        return f"Memory added successfully ({self.used}/{self.char_limit} chars used)."

    async def update(self, memory_id: int, content: str) -> MemoryEntry:
        """修改一条记忆内容（保留 created_at）。失败抛 MemoryError 子类。"""
        await self._ensure_loaded()
        content = content.strip()
        if not content:
            raise MemoryError("content must not be empty")
        async with self._lock:  # 去重/上限检查 + 落库 + 更新缓存，原子执行
            entry = self._find(memory_id)
            if entry is None:
                raise MemoryNotFoundError(memory_id)
            dup = self._dup_of(content, exclude_id=memory_id)
            if dup:
                raise MemoryDuplicateError(
                    f"this memory already exists [{dup.ts}] {dup.text}. "
                    "Do not add duplicates; if the information has changed, add a new corrected statement instead."
                )
            delta = len(content) - len(entry.text)
            if self.used + delta > self.char_limit:
                raise MemoryFullError(
                    f"memory would be full ({self.used + delta}/{self.char_limit} chars). "
                    "Consolidate or drop stale entries before adding new ones."
                )
            await self._storage.update_memory(memory_id, content)
            entry.text = content
            return entry

    async def delete(self, memory_id: int) -> None:
        """删除一条记忆。不存在抛 MemoryNotFoundError。"""
        await self._ensure_loaded()
        async with self._lock:
            if self._find(memory_id) is None:
                raise MemoryNotFoundError(memory_id)
            await self._storage.delete_memory(memory_id)
            self._entries = [e for e in self._entries if e.id != memory_id]

    def render(self) -> str:
        """渲染注入 system prompt 的记忆区（读内存缓存）；无记忆时返回空串。"""
        if not self._entries:
            return ""
        lines = [
            "## Memory",
            "Persistent facts about the user and what has happened. Use them naturally in conversation:",
        ]
        lines += [f"- [{e.ts}] {e.text}" for e in self._entries]
        lines.append(f"<!-- {self.used}/{self.char_limit} chars -->")
        return "\n".join(lines)
