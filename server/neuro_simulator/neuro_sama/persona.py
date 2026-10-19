"""Persona 三层：system prompt 模板（人工）+ core memory（人工）+ short-term memory（LLM）。

- prompt 模板存在 prompts 表（name='system'），支持两个占位符：
  {{core_memory}} / {{short_term_memory}}；缺失时自动追加到末尾，避免人工误删导致记忆丢失。
- core memory 存在 core_memories 表，一条一个 subject（title + content），
  只能由 dashboard 人工编辑，不向 LLM 暴露任何写工具。
- short-term memory 仍是 memories 表（MemoryStore），由 memory_add 工具自主维护。
"""

import asyncio
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .storage import Storage

CORE_PLACEHOLDER = "{{core_memory}}"
SHORT_TERM_PLACEHOLDER = "{{short_term_memory}}"

PLACEHOLDERS = [
    {
        "key": CORE_PLACEHOLDER,
        "description": "Core Memory：人工维护的稳定事实（Persona / Vedal / Evil 等），按下方块顺序渲染。",
    },
    {
        "key": SHORT_TERM_PLACEHOLDER,
        "description": "Short-term Memory：LLM 通过 memory_add 自主积累的近期笔记。",
    },
]

_DEFAULTS_DIR = Path(__file__).parent / "defaults"


def _read_default_prompt() -> str:
    return (_DEFAULTS_DIR / "prompt.md").read_text(encoding="utf-8").strip()


def _read_default_core() -> list[dict]:
    data = json.loads((_DEFAULTS_DIR / "core_memory.json").read_text(encoding="utf-8"))
    blocks = data.get("blocks", [])
    seen: set[str] = set()
    result: list[dict] = []
    for b in blocks:
        title = str(b.get("title", "")).strip()
        key = title.lower()
        if not title or key in seen:  # 默认文件自身标题重复时只取第一条
            continue
        seen.add(key)
        result.append({"title": title, "content": str(b.get("content", "")).strip()})
    return result


@dataclass
class CoreMemoryBlock:
    id: int
    title: str
    content: str
    created_at: str
    updated_at: str


class PersonaError(Exception):
    """Persona 操作失败。message 可直接作为 API detail。"""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


class PersonaStore:
    def __init__(self, storage: Storage) -> None:
        self._storage = storage
        self._prompt = ""
        self._prompt_updated_at = ""
        self._blocks: list[CoreMemoryBlock] = []
        self._loaded = False
        self._lock = asyncio.Lock()

    # ---------- 加载 / 播种 ----------

    async def load(self) -> None:
        await self._seed_defaults()
        row = await self._storage.get_prompt("system")
        if row is None:
            # 理论上 seed 已插入；兜底避免空 prompt
            await self._storage.upsert_prompt(_read_default_prompt(), "system")
            row = await self._storage.get_prompt("system")
        self._prompt = row["content"] if row else _read_default_prompt()
        self._prompt_updated_at = row["updated_at"] if row else ""
        await self._reload_blocks()
        self._loaded = True

    async def _seed_defaults(self) -> None:
        """首次升级/首次启动时播种默认 prompt + core blocks。

        meta['persona_seeded'] 置位后不再播种——用户删光 core blocks 是明确意图，
        重启不能把默认内容加回来。
        """
        if await self._storage.get_meta("persona_seeded"):
            return
        if await self._storage.get_prompt("system") is None:
            await self._storage.upsert_prompt(_read_default_prompt(), "system")
        if not await self._storage.get_core_memories():
            for block in _read_default_core():
                await self._storage.add_core_memory(block["title"], block["content"])
        await self._storage.set_meta("persona_seeded", "1")

    async def _ensure_loaded(self) -> None:
        if not self._loaded:
            await self.load()

    async def _reload_blocks(self) -> None:
        rows = await self._storage.get_core_memories()
        self._blocks = [
            CoreMemoryBlock(
                id=r["id"],
                title=r["title"],
                content=r["content"],
                created_at=r["created_at"],
                updated_at=r["updated_at"],
            )
            for r in rows
        ]

    # ---------- prompt ----------

    @property
    def prompt(self) -> str:
        return self._prompt

    @property
    def prompt_updated_at(self) -> str:
        return self._prompt_updated_at

    @property
    def is_default_prompt(self) -> bool:
        return self._prompt.strip() == _read_default_prompt().strip()

    async def save_prompt(self, content: str) -> None:
        await self._ensure_loaded()
        content = content.strip()
        if not content:
            raise PersonaError("prompt must not be empty")
        async with self._lock:
            await self._storage.upsert_prompt(content, "system")
            self._prompt = content
            row = await self._storage.get_prompt("system")
            self._prompt_updated_at = row["updated_at"] if row else ""

    async def reset_prompt(self) -> None:
        await self.save_prompt(_read_default_prompt())

    # ---------- core memory ----------

    @property
    def blocks(self) -> list[CoreMemoryBlock]:
        return self._blocks

    @property
    def core_used(self) -> int:
        """Core Memory 渲染进 prompt 的字符数（与预览的 chars.core 一致）。"""
        return len(self.render_core()) if self._blocks else 0

    def list_core(self) -> list[dict]:
        return [
            {
                "id": b.id,
                "title": b.title,
                "content": b.content,
                "created_at": b.created_at,
                "updated_at": b.updated_at,
            }
            for b in self._blocks
        ]

    def _find(self, block_id: int) -> Optional[CoreMemoryBlock]:
        for b in self._blocks:
            if b.id == block_id:
                return b
        return None

    def _title_taken(self, title: str, exclude_id: Optional[int] = None) -> bool:
        key = title.strip().lower()
        for b in self._blocks:
            if exclude_id is not None and b.id == exclude_id:
                continue
            if b.title.strip().lower() == key:
                return True
        return False

    @staticmethod
    def _clean(title: str, content: str) -> tuple[str, str]:
        title = title.strip()
        content = content.strip()
        if not title:
            raise PersonaError("core memory title must not be empty")
        if not content:
            raise PersonaError("core memory content must not be empty")
        return title, content

    async def add_core(self, title: str, content: str) -> CoreMemoryBlock:
        await self._ensure_loaded()
        title, content = self._clean(title, content)
        async with self._lock:
            if self._title_taken(title):
                raise PersonaError(f"core memory block {title!r} already exists", status=409)
            try:
                block_id = await self._storage.add_core_memory(title, content)
            except sqlite3.IntegrityError:
                raise PersonaError(
                    f"core memory block {title!r} already exists", status=409
                ) from None
            await self._reload_blocks()
            block = self._find(block_id)
            if block is None:  # 理论上不会发生
                raise PersonaError("failed to load created core memory block", status=500)
            return block

    async def update_core(self, block_id: int, title: str, content: str) -> CoreMemoryBlock:
        await self._ensure_loaded()
        title, content = self._clean(title, content)
        async with self._lock:
            if self._find(block_id) is None:
                raise PersonaError(f"core memory block {block_id} not found", status=404)
            if self._title_taken(title, exclude_id=block_id):
                raise PersonaError(f"core memory block {title!r} already exists", status=409)
            try:
                updated = await self._storage.update_core_memory(block_id, title, content)
            except sqlite3.IntegrityError:
                raise PersonaError(
                    f"core memory block {title!r} already exists", status=409
                ) from None
            if not updated:
                raise PersonaError(f"core memory block {block_id} not found", status=404)
            await self._reload_blocks()
            block = self._find(block_id)
            if block is None:  # 理论上不会发生
                raise PersonaError("failed to load updated core memory block", status=500)
            return block

    async def delete_core(self, block_id: int) -> None:
        await self._ensure_loaded()
        async with self._lock:
            if self._find(block_id) is None:
                raise PersonaError(f"core memory block {block_id} not found", status=404)
            await self._storage.delete_core_memory(block_id)
            await self._reload_blocks()

    @property
    def is_default_core(self) -> bool:
        """当前 core memory 是否与包内默认块完全一致（用于 UI 禁用重置按钮）。"""
        defaults = _read_default_core()
        if len(defaults) != len(self._blocks):
            return False
        return all(
            b.title == d["title"] and b.content == d["content"]
            for b, d in zip(self._blocks, defaults)
        )

    async def reset_core(self) -> list[CoreMemoryBlock]:
        """用包内默认块整体替换现有 core memory（人工内容会被丢弃）。"""
        await self._ensure_loaded()
        defaults = _read_default_core()
        async with self._lock:
            for b in list(self._blocks):
                await self._storage.delete_core_memory(b.id)
            for block in defaults:
                await self._storage.add_core_memory(block["title"], block["content"])
            await self._reload_blocks()
        return self._blocks

    async def reset_all(self) -> None:
        """prompt 模板 + core memory 一起恢复内置默认。"""
        await self.reset_prompt()
        await self.reset_core()

    # ---------- 渲染 ----------

    def render_core(self) -> str:
        if not self._blocks:
            return "## Core Memory\n(none yet)"
        lines = [
            "## Core Memory",
            (
                "Authoritative, manually curated facts about me and my world. "
                "They are always true. Never contradict them."
            ),
            "",
        ]
        for b in self._blocks:
            lines.append(f"### {b.title}")
            lines.append(b.content)
            lines.append("")
        return "\n".join(lines).rstrip()

    def _assemble(self, prompt: str, short_term_section: str) -> str:
        core = self.render_core()
        short_term = short_term_section.strip() or "## Short-term Memory\n(none yet)"

        out = prompt
        if CORE_PLACEHOLDER in out:
            out = out.replace(CORE_PLACEHOLDER, core)
        else:
            out = out.rstrip() + "\n\n" + core
        if SHORT_TERM_PLACEHOLDER in out:
            out = out.replace(SHORT_TERM_PLACEHOLDER, short_term)
        else:
            out = out.rstrip() + "\n\n" + short_term
        return out.strip()

    def render(self, short_term_section: str) -> str:
        """用 prompt 模板 + core/short-term 段落拼出完整 system prompt。"""
        return self._assemble(self._prompt, short_term_section)

    def preview(self, short_term_section: str, prompt_content: Optional[str] = None) -> dict:
        """预览编译结果。prompt_content 非空时用它代替已保存模板（用于未保存草稿）。"""
        prompt = self._prompt if prompt_content is None else prompt_content
        core = self.render_core()
        short_term = short_term_section.strip() or "## Short-term Memory\n(none yet)"
        rendered = self._assemble(prompt, short_term_section)
        return {
            "prompt": rendered,
            "chars": {
                "template": len(prompt),
                "core": len(core),
                "short_term": len(short_term),
                "total": len(rendered),
            },
            "estimated_tokens": (len(rendered) + 3) // 4,
        }

    def stats(self) -> dict:
        return {
            "core_count": len(self._blocks),
            "core_used": self.core_used,
            "prompt_chars": len(self._prompt),
        }
