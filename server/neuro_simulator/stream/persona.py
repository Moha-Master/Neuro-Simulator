"""stream 侧的可编辑内容：chatbot 人设（prompt + core memory）与消息模板。

通用逻辑在顶层 neuro_simulator/persona.py 与 neuro_simulator/templates.py；
本模块提供 stream 的默认内容、占位符说明和装配。

- ChatbotPersonaStore：chatbot 的 system prompt（prompts 表 name='chatbot'）
  + 观众视角 core memory（core_memories 表 scope='chatbot'）
- build_template_store()：stream 每轮拼给 neuro / chatbot 的 user message 模板
"""

import json
from pathlib import Path

from ..persona import CORE_PLACEHOLDER, PersonaDefaults
from ..persona import PersonaStore as SharedPersonaStore
from ..templates import TemplateSpec, TemplateStore

COUNT_PLACEHOLDER = "{{count}}"

CHATBOT_PLACEHOLDERS = [
    {
        "key": CORE_PLACEHOLDER,
        "description": "Core Memory：观众视角的共同知识（Neuro / Vedal / 梗 / 大事记）。",
    },
    {
        "key": COUNT_PLACEHOLDER,
        "description": "本轮要生成的弹幕条数（来自 stream.chatbot.count）。",
    },
]

_CORE_HEADER = (
    "Common knowledge among chat: the people, the running gags, and what has happened. "
    "We all know this. Do not contradict it."
)

_DEFAULTS_DIR = Path(__file__).parent / "defaults"


def stream_info_line(title: str, category: str) -> str:
    """本场的 `Now streaming: 标题 [分区]`。

    两者都为空时返回空串——模板渲染会把因此产生的连续空行压掉，整行自然消失。
    """
    title = (title or "").strip()
    category = (category or "").strip()
    if title and category:
        return f"Now streaming: {title} [{category}]"
    if title:
        return f"Now streaming: {title}"
    if category:
        return f"Now streaming in {category}"
    return ""


def _load_defaults() -> PersonaDefaults:
    prompt = (_DEFAULTS_DIR / "chatbot_prompt.md").read_text(encoding="utf-8").strip()
    data = json.loads(
        (_DEFAULTS_DIR / "chatbot_core_memory.json").read_text(encoding="utf-8")
    )
    blocks = tuple(
        {
            "title": str(b.get("title", "")).strip(),
            "content": str(b.get("content", "")).strip(),
        }
        for b in data.get("blocks", [])
        if str(b.get("title", "")).strip()
    )
    return PersonaDefaults(prompt=prompt, blocks=blocks)


class ChatbotPersonaStore(SharedPersonaStore):
    """chatbot 的观众人设。"""

    def __init__(self, storage) -> None:
        super().__init__(
            storage,
            prompt_name="chatbot",
            defaults=_load_defaults,
            seed_meta_key="chatbot_persona_seeded",
            core_header=_CORE_HEADER,
        )

    def render_prompt(self, count: int) -> str:
        """编译本轮 chatbot system prompt。"""
        return self.render({"count": str(int(count))})

    def preview_prompt(self, count: int, prompt_content: str | None = None) -> dict:
        """预览编译结果（可传未保存草稿）。"""
        return self.preview({"count": str(int(count))}, prompt_content)


# ---------- 消息模板 ----------

_SHARED_VARS = [
    {"key": "{{datetime}}", "description": "当前时间（与直播循环一致的本地时间格式）。"},
    {
        "key": "{{stream_info}}",
        "description": "本场信息整行，例如 `Now streaming: 标题 [分区]`；未设置时整行自动消失。",
    },
    {"key": "{{title}}", "description": "本场直播标题的原始值（可能为空）。"},
    {"key": "{{category}}", "description": "本场分区的原始值（可能为空）。"},
]

_NEURO_VARS = [
    {"key": "{{scene_context}}", "description": "config 的 stream.scene_context（直播场景说明）。"},
    {
        "key": "{{messages}}",
        "description": (
            "必需。观众消息块（`- 用户名: 内容` 多行）；队列为空时这里是"
            " config 的 placeholder_message（相当于一条系统提示，让 Neuro 自己找话说）。"
        ),
    },
    {"key": "{{scene}}", "description": "当前画面场景 id（如 room / gameplay）。"},
    {"key": "{{round}}", "description": "本场已进行的轮次序号。"},
]

_CHATBOT_VARS = [
    {
        "key": "{{neuro_status}}",
        "description": "必需。Neuro 的当前状态：刚开播 / 最近说了什么 / 疑似睡着。",
    },
]


def _load_template_defaults() -> dict[str, str]:
    data = json.loads((_DEFAULTS_DIR / "templates.json").read_text(encoding="utf-8"))
    return {str(k): str(v) for k, v in data.items()}


def build_template_store(storage) -> TemplateStore:
    """装配 stream 的两条 user message 模板（缺失行按默认值播种）。"""
    d = _load_template_defaults()
    return TemplateStore(
        storage,
        [
            TemplateSpec(
                name="neuro_user",
                label="Neuro · 每轮弹幕",
                description=(
                    "直播循环每轮注入 neuro /chat 的 user message。"
                    "队列为空（empty_queue_behavior=placeholder）时 {{messages}} 换成"
                    " config 的 placeholder_message，不再单设空队列模板。"
                ),
                default=d.get("neuro_user", ""),
                required=("messages",),
                placeholders=[*_NEURO_VARS, *_SHARED_VARS],
            ),
            TemplateSpec(
                name="chatbot_user",
                label="Chatbot · 每轮",
                description="每轮注入 chatbot（模拟观众）的 user message。",
                default=d.get("chatbot_user", ""),
                required=("neuro_status",),
                placeholders=[*_CHATBOT_VARS, *_SHARED_VARS],
            ),
        ],
    )
