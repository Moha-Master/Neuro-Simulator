"""命名模板表（prompts 表的多行）：模块运行期拼给 LLM 的消息模板，人工可编辑。

与 persona.PersonaStore 的区别：
- 这里只管若干条命名模板，没有 core memory
- 支持"必需占位符"校验：缺失会让消息失去语义（例如注入 neuro 的弹幕块），
  所以保存时直接拒绝，而不是像 prompt 模板那样"缺失就追加到末尾"
- load() 时对缺失行按默认值播种；模板没有删除操作，播种幂等且安全

占位符替换是"原位替换"，未登记的 `{{xxx}}` 原样保留（便于在预览里发现拼写错误）。
"""

import asyncio
import re
from dataclasses import dataclass, field
from typing import Optional, Sequence

_BLANK_RUNS = re.compile(r"\n{3,}")


@dataclass(frozen=True)
class TemplateSpec:
    """一个模板的元信息（代码内登记，内容默认值来自包内 defaults 文件）。"""

    name: str
    label: str
    description: str
    default: str
    required: tuple[str, ...] = ()
    placeholders: tuple[dict, ...] = field(default_factory=tuple)


class TemplateError(Exception):
    """模板操作失败。message 可直接作为 API detail。"""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def _token(key: str) -> str:
    return "{{" + key + "}}"


class TemplateStore:
    def __init__(self, storage, specs: Sequence[TemplateSpec]) -> None:
        self._storage = storage
        self._specs = {s.name: s for s in specs}
        self._rows: dict[str, dict] = {}
        self._loaded = False
        self._lock = asyncio.Lock()

    # ---------- 加载 / 播种 ----------

    async def load(self) -> None:
        for spec in self._specs.values():
            row = await self._storage.get_prompt(spec.name)
            if row is None:
                await self._storage.upsert_prompt(spec.default, spec.name)
                row = await self._storage.get_prompt(spec.name)
            self._rows[spec.name] = {
                "content": row["content"] if row else spec.default,
                "updated_at": row["updated_at"] if row else "",
            }
        self._loaded = True

    async def _ensure_loaded(self) -> None:
        if not self._loaded:
            await self.load()

    # ---------- 读写 ----------

    def spec(self, name: str) -> TemplateSpec:
        spec = self._specs.get(name)
        if spec is None:
            raise TemplateError(f"unknown template {name!r}", status=404)
        return spec

    def content(self, name: str) -> str:
        self._check_known(name)
        return self._rows[name]["content"]

    def _check_known(self, name: str) -> None:
        if name not in self._specs:
            raise TemplateError(f"unknown template {name!r}", status=404)
        if not self._loaded or name not in self._rows:
            raise TemplateError(f"template {name!r} not loaded", status=500)

    async def save(self, name: str, content: str) -> None:
        await self._ensure_loaded()
        spec = self.spec(name)
        content = content.strip()
        if not content:
            raise TemplateError(f"template {name!r} must not be empty")
        missing = [key for key in spec.required if _token(key) not in content]
        if missing:
            raise TemplateError(
                f"template {name!r} 必须包含占位符："
                + "、".join(_token(k) for k in missing)
            )
        async with self._lock:
            await self._storage.upsert_prompt(content, name)
            row = await self._storage.get_prompt(name)
            self._rows[name] = {
                "content": content,
                "updated_at": row["updated_at"] if row else "",
            }

    async def reset(self, name: str) -> None:
        await self.save(name, self.spec(name).default)

    def is_default(self, name: str) -> bool:
        self._check_known(name)
        return self._rows[name]["content"].strip() == self.spec(name).default.strip()

    def list(self) -> list[dict]:
        """全部模板（含元信息），按登记顺序。"""
        result = []
        for name, spec in self._specs.items():
            row = self._rows.get(name, {"content": spec.default, "updated_at": ""})
            result.append(
                {
                    "name": name,
                    "label": spec.label,
                    "description": spec.description,
                    "content": row["content"],
                    "updated_at": row["updated_at"],
                    "is_default": row["content"].strip() == spec.default.strip(),
                    "required": list(spec.required),
                    "placeholders": [dict(p) for p in spec.placeholders],
                }
            )
        return result

    # ---------- 渲染 ----------

    def render(
        self, name: str, sections: Optional[dict] = None, content: Optional[str] = None
    ) -> str:
        """原位替换已登记的占位符；未提供的键替换为空串。

        content 非 None 时用该草稿文本（供"未保存即预览"）。
        """
        self._check_known(name)
        out = self._rows[name]["content"] if content is None else content
        values = sections or {}
        for key, text in values.items():
            out = out.replace(_token(key), str(text if text is not None else "").strip())
        # 模板里引用了但本次没给的占位符 → 清掉，避免把 {{xxx}} 喂给模型
        for spec in self._specs.values():
            for p in spec.placeholders:
                key = p.get("key")
                if key and key not in values:
                    out = out.replace(_token(key), "")
        # 变量为空会留下连续空行（如未设置标题时的 {{stream_info}} 行）：压成一段
        out = _BLANK_RUNS.sub("\n\n", out)
        return out.strip()

    def preview(
        self, name: str, sections: Optional[dict] = None, content: Optional[str] = None
    ) -> dict:
        self._check_known(name)
        spec = self.spec(name)
        source = self._rows[name]["content"] if content is None else content
        rendered = self.render(name, sections, content)
        return {
            "name": name,
            "prompt": rendered,
            "chars": {"template": len(source), "total": len(rendered)},
            "required": list(spec.required),
            # 草稿里缺失的必需占位符（保存会被拒），供前端提前告警
            "missing_placeholders": [
                key for key in spec.required if _token(key) not in source
            ],
            "estimated_tokens": (len(rendered) + 3) // 4,
        }
