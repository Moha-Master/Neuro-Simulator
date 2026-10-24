"""跨模块共用的 Persona 层：prompt 模板 + core memory 块（均存 SQLite，仅人工编辑）。

neuro_sama 用它承载 Neuro 的人设，stream 用它承载 chatbot 的观众人设；
两个模块各自提供 defaults 与占位符说明，模块之间不互相依赖。

存储协议（各模块的 Storage 实现同名方法即可，scope 等细节自行决定）：
- get_prompt(name) / upsert_prompt(content, name)
- get_core_memories() / add_core_memory(title, content)
  update_core_memory(id, title, content) / delete_core_memory(id)
- get_meta(key) / set_meta(key, value)

渲染规则：
- sections 是 {占位符名: 段落文本}，core memory 作为 'core_memory' 段参与替换
- **prompt 就是作者写的文本**：模板里出现 `{{name}}` 才原位替换，缺失不再自动
  追加到末尾（编辑自由由作者负责；preview 的 `missing_placeholders` 只是提示）
- 段落为空时替换成 `## <Name>\n(none yet)`，让模型明确知道"现在没有内容"
- 渲染异常时退回进程内上一版成功文本（直播路径不能因为提示词坏了而 500）
"""

import asyncio
import logging
import sqlite3
from dataclasses import dataclass
from typing import Callable, Optional

from .placeholder_kit import missing_placeholders, substitute

logger = logging.getLogger(__name__)

CORE_SECTION_KEY = "core_memory"
CORE_PLACEHOLDER = "{{core_memory}}"


@dataclass(frozen=True)
class PersonaDefaults:
    """包内预置默认内容（首次启动播种、重置时回填）。"""

    prompt: str
    blocks: tuple[dict, ...] = ()  # [{title, content}]


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


def placeholder_of(section: str) -> str:
    """给说明文字用的规范写法；**匹配请用 `placeholder_kit`**（容忍空白）。"""
    return "{{" + section + "}}"


def _empty_section(key: str) -> str:
    return f"## {key.replace('_', ' ').title()}\n(none yet)"


class PersonaStore:
    """一个 persona = 一条 prompt 模板 + 一组 core memory 块。"""

    def __init__(
        self,
        storage,
        *,
        prompt_name: str,
        defaults: Callable[[], PersonaDefaults],
        seed_meta_key: str,
        core_header: str = (
            "Authoritative, manually curated facts. They are always true. "
            "Never contradict them."
        ),
    ) -> None:
        self._storage = storage
        self._prompt_name = prompt_name
        self._defaults = defaults
        self._seed_meta_key = seed_meta_key
        self._core_header = core_header
        self._prompt = ""
        self._prompt_updated_at = ""
        self._blocks: list[CoreMemoryBlock] = []
        self._loaded = False
        self._lock = asyncio.Lock()
        # 进程内最近一次成功编译的 system prompt（渲染异常时退回它）
        self._last_render: str = ""

    # ---------- 加载 / 播种 ----------

    async def load(self) -> None:
        await self._seed_defaults()
        row = await self._storage.get_prompt(self._prompt_name)
        if row is None:
            # 理论上 seed 已插入；兜底避免空 prompt
            await self._storage.upsert_prompt(self._defaults().prompt, self._prompt_name)
            row = await self._storage.get_prompt(self._prompt_name)
        self._prompt = row["content"] if row else self._defaults().prompt
        self._prompt_updated_at = row["updated_at"] if row else ""
        await self._reload_blocks()
        self._loaded = True

    async def _seed_defaults(self) -> None:
        """首次升级/首次启动时播种默认 prompt + core blocks。

        meta 标记置位后不再播种——用户删光 core blocks 是明确意图，
        重启不能把默认内容加回来（想回到默认走 reset 端点）。
        """
        if await self._storage.get_meta(self._seed_meta_key):
            return
        defaults = self._defaults()
        if await self._storage.get_prompt(self._prompt_name) is None:
            await self._storage.upsert_prompt(defaults.prompt, self._prompt_name)
        if not await self._storage.get_core_memories():
            for block in defaults.blocks:
                await self._storage.add_core_memory(block["title"], block["content"])
        await self._storage.set_meta(self._seed_meta_key, "1")

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

    # ---------- prompt 模板 ----------

    @property
    def prompt_name(self) -> str:
        return self._prompt_name

    @property
    def prompt(self) -> str:
        return self._prompt

    @property
    def prompt_updated_at(self) -> str:
        return self._prompt_updated_at

    @property
    def default_prompt(self) -> str:
        return self._defaults().prompt.strip()

    @property
    def is_default_prompt(self) -> bool:
        return self._prompt.strip() == self.default_prompt

    async def save_prompt(self, content: str) -> None:
        await self._ensure_loaded()
        content = content.strip()
        if not content:
            raise PersonaError("prompt must not be empty")
        async with self._lock:
            await self._storage.upsert_prompt(content, self._prompt_name)
            self._prompt = content
            row = await self._storage.get_prompt(self._prompt_name)
            self._prompt_updated_at = row["updated_at"] if row else ""

    async def reset_prompt(self) -> None:
        await self.save_prompt(self._defaults().prompt)

    # ---------- core memory ----------

    @property
    def blocks(self) -> list[CoreMemoryBlock]:
        return self._blocks

    @property
    def core_used(self) -> int:
        """Core Memory 渲染进 prompt 的字符数（与 preview 的 chars.core 一致）。"""
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
                raise PersonaError(
                    f"core memory block {title!r} already exists", status=409
                )
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
                raise PersonaError(
                    f"core memory block {title!r} already exists", status=409
                )
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
    def default_blocks(self) -> list[dict]:
        """包内默认块（标题去重，只取第一条，避免撞 UNIQUE 约束）。"""
        seen: set[str] = set()
        result: list[dict] = []
        for b in self._defaults().blocks:
            title = str(b.get("title", "")).strip()
            key = title.lower()
            if not title or key in seen:
                continue
            seen.add(key)
            result.append(
                {"title": title, "content": str(b.get("content", "")).strip()}
            )
        return result

    @property
    def is_default_core(self) -> bool:
        """当前 core memory 是否与包内默认块完全一致（供 UI 决定重置按钮是否可用）。"""
        defaults = self.default_blocks
        if len(defaults) != len(self._blocks):
            return False
        return all(
            b.title == d["title"] and b.content == d["content"]
            for b, d in zip(self._blocks, defaults)
        )

    async def reset_core(self) -> list[CoreMemoryBlock]:
        """用包内默认块整体替换现有 core memory（人工内容会被丢弃）。"""
        await self._ensure_loaded()
        defaults = self.default_blocks
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
        lines = ["## Core Memory", self._core_header, ""]
        for b in self._blocks:
            lines.append(f"### {b.title}")
            lines.append(b.content)
            lines.append("")
        return "\n".join(lines).rstrip()

    def _assemble(self, prompt: str, extra_sections: Optional[dict]) -> str:
        sections: dict[str, str] = {CORE_SECTION_KEY: self.render_core()}
        sections.update(extra_sections or {})
        out = prompt
        for key, text in sections.items():
            # 走 placeholder_kit：作者写 `{{ core_memory }}`（带空格）与
            # `{{core_memory}}` 是同一个占位符。曾经的裸字符串比对让带空格的
            # 写法静默不替换——core memory 直接从 system prompt 里消失。
            out = substitute(out, key, text.strip() or _empty_section(key))
        # 不再自动追加缺失的段落：prompt 是作者写的文本，替他加东西会掩盖误删
        return out.strip()

    def render(self, extra_sections: Optional[dict] = None) -> str:
        """用已保存的模板 + core/额外段落拼出完整 prompt。

        渲染异常（理论上只会来自极端输入）退回上一版成功文本；从未成功过就退回
        模板原文——直播循环不能因为提示词坏了而中断。
        """
        try:
            out = self._assemble(self._prompt, extra_sections)
        except Exception as e:  # pragma: no cover - 防御性兜底
            logger.warning("persona prompt 渲染失败，使用上一版: %s: %s", type(e).__name__, e)
            return self._last_render or self._prompt
        self._last_render = out
        return out

    def preview(
        self,
        extra_sections: Optional[dict] = None,
        prompt_content: Optional[str] = None,
    ) -> dict:
        """预览编译结果。prompt_content 非 None 时用它代替已保存模板（未保存草稿）。"""
        prompt = self._prompt if prompt_content is None else prompt_content
        sections: dict[str, str] = {CORE_SECTION_KEY: self.render_core()}
        sections.update(extra_sections or {})
        bodies = {k: (v.strip() or _empty_section(k)) for k, v in sections.items()}
        rendered = self._assemble(prompt, sections)
        return {
            "prompt": rendered,
            "chars": {
                "template": len(prompt),
                "core": len(bodies[CORE_SECTION_KEY]),
                "sections": {k: len(v) for k, v in bodies.items() if k != CORE_SECTION_KEY},
                "total": len(rendered),
            },
            # 非阻塞提示：缺失占位符不会再被自动追加，也不会拦保存
            "missing_placeholders": missing_placeholders(prompt, sections),
            "estimated_tokens": (len(rendered) + 3) // 4,
        }

    def stats(self) -> dict:
        return {
            "core_count": len(self._blocks),
            "core_used": self.core_used,
            "prompt_chars": len(self._prompt),
        }
