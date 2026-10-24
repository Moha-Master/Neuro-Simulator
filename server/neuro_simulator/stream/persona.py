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

# 消息模板的命名空间（Jinja）。注册表只用于说明文字与搜索提示，**不再当真相**：
# 实际可用路径由 dashboard 从 `GET /manage/state` 的实况投影生成。
_TEMPLATE_VARS = [
    {
        "key": "{{ stream_status }}",
        "description": (
            "stream 自己的状态机投影（分区 → current，不含 graph）。"
            "如 `stream_status.metadata.title`、`stream_status.scene.prompt_context`、"
            "`stream_status.games[stream_status.games.active].summary`。"
        ),
    },
    {
        "key": "{{ neuro_status }}",
        "description": (
            "neuro 最近一次 /status 的投影（不可达时为 {}）。"
            "如 `neuro_status.turn.silent_s`、`neuro_status.turn.idle`。"
        ),
    },
    {
        "key": "{{ neuro_said }}",
        "description": "本轮 neuro 的正文（内容平面）；本场还没说过话时为空串。",
    },
    {
        "key": "{{ messages }}",
        "description": (
            "观众消息块（`- 用户名: 内容` 多行）；队列为空时是 config 的 "
            "placeholder_message。"
        ),
    },
    {
        "key": "{{ datetime }}",
        "description": "当前本地时间字符串（墙钟不属于状态机）。",
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
                placeholders=list(_TEMPLATE_VARS),
            ),
            TemplateSpec(
                name="chatbot_user",
                label="Chatbot · 每轮",
                description="每轮注入 chatbot（模拟观众）的 user message。",
                default=d.get("chatbot_user", ""),
                required=("messages",),
                placeholders=list(_TEMPLATE_VARS),
            ),
        ],
    )
