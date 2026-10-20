"""Neuro 侧 Persona：prompt 模板 + core memory（均人工编辑）。

通用逻辑在顶层 neuro_simulator/persona.py（stream 的 chatbot 人设共用同一套）；
本模块只负责提供 Neuro 的默认内容与占位符说明。
短期记忆（LLM 自主维护）见 memory.py。
"""

import json
from pathlib import Path

from ..persona import (  # re-export：调用方按 neuro 侧语义使用
    CORE_PLACEHOLDER,
    CoreMemoryBlock,
    PersonaDefaults,
    PersonaError,
)
from ..persona import PersonaStore as SharedPersonaStore

SHORT_TERM_PLACEHOLDER = "{{short_term_memory}}"

PLACEHOLDERS = [
    {
        "key": CORE_PLACEHOLDER,
        "description": "Core Memory：人工维护的稳定事实（Persona / Vedal / Evil 等），按块顺序渲染。",
    },
    {
        "key": SHORT_TERM_PLACEHOLDER,
        "description": "Short-term Memory：LLM 通过 memory_add 自主积累的近期笔记。",
    },
]

_CORE_HEADER = (
    "Authoritative, manually curated facts about me and my world. "
    "They are always true. Never contradict them."
)

_DEFAULTS_DIR = Path(__file__).parent / "defaults"


def _load_defaults() -> PersonaDefaults:
    prompt = (_DEFAULTS_DIR / "prompt.md").read_text(encoding="utf-8").strip()
    data = json.loads((_DEFAULTS_DIR / "core_memory.json").read_text(encoding="utf-8"))
    blocks = tuple(
        {
            "title": str(b.get("title", "")).strip(),
            "content": str(b.get("content", "")).strip(),
        }
        for b in data.get("blocks", [])
        if str(b.get("title", "")).strip()
    )
    return PersonaDefaults(prompt=prompt, blocks=blocks)


class PersonaStore(SharedPersonaStore):
    """Neuro 的人设存储（prompt name='system'，core 块无 scope）。"""

    def __init__(self, storage) -> None:
        super().__init__(
            storage,
            prompt_name="system",
            defaults=_load_defaults,
            seed_meta_key="persona_seeded",
            core_header=_CORE_HEADER,
        )

    def render_system(self, short_term_section: str) -> str:
        """编译本轮 system prompt：模板 + core memory + 短期记忆。"""
        return self.render({"short_term_memory": short_term_section})

    def preview_system(self, short_term_section: str, prompt_content=None) -> dict:
        """预览编译结果；prompt_content 非 None 时用未保存草稿。"""
        return self.preview({"short_term_memory": short_term_section}, prompt_content)


__all__ = [
    "CORE_PLACEHOLDER",
    "SHORT_TERM_PLACEHOLDER",
    "PLACEHOLDERS",
    "CoreMemoryBlock",
    "PersonaError",
    "PersonaStore",
]
