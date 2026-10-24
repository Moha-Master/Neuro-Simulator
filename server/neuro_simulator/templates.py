"""命名模板表（prompts 表的多行）：模块运行期拼给 LLM 的消息模板，人工可编辑。

与 persona.PersonaStore 的区别：
- 这里只管若干条命名模板，没有 core memory
- 模板用 **Jinja2 沙箱** 渲染（内容存 DB、人工可编辑，语法错误/越权访问要挡住）
- **不校验内容要求**：必填占位符缺失不再拒绝保存（编辑者自己负责），preview 只给
  非阻塞的 `missing_placeholders` / `unresolved_paths` 提示
- load() 时对缺失行按默认值播种；模板没有删除操作，播种幂等且安全

渲染 context 只允许纯 dict/list/标量（见 `_plain`）：一旦把业务对象放进来，模板
就能调用有副作用的方法。渲染失败（语法错误/沙箱拒绝）时退回**上一版成功文本**，
从未成功过则退回一个只含 `messages` 事实的最小兜底——直播循环绝不因模板坏了而中断。
"""

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Optional, Sequence

from jinja2 import ChainableUndefined
from jinja2.sandbox import SandboxedEnvironment

logger = logging.getLogger(__name__)

_BLANK_RUNS = re.compile(r"\n{3,}")
# 只抽点号路径（`stream_status.scene.desc`）；动态下标 `games[...]` 里的
# `stream_status.games.active` 也会被单独抽出来，恰好是它真实的路径。
_STATUS_PATH = re.compile(
    r"\b(stream_status|neuro_status)((?:\.[A-Za-z_][A-Za-z0-9_]*)+)"
)
_TOP_KEYS = frozenset(("stream_status", "neuro_status", "neuro_said", "messages", "datetime"))


@dataclass(frozen=True)
class TemplateSpec:
    """一个模板的元信息（代码内登记，内容默认值来自包内 defaults 文件）。"""

    name: str
    label: str
    description: str
    default: str
    # 仅作文档/提示用：不再拦保存（用户明确要求编辑自由）
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


def _plain(value: Any) -> Any:
    """把 context 递归净化成纯 dict/list/标量（业务对象一律转字符串）。

    模板文本存在 DB、人工可编辑；context 里只要残留一个对象，模板就能调用它的
    方法（读文件、发请求）。这里在边界上做一次归一，而不是信任调用方。
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_plain(v) for v in value]
    return str(value)


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)


def _lookup(context: dict, path: str) -> tuple[bool, Any]:
    """按点号路径在 context 里取值；返回 (是否存在, 值)。"""
    node: Any = context
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return False, None
        node = node[part]
    return True, node


def unresolved_paths(source: str, context: dict) -> list[str]:
    """模板里引用了、但实况投影里不存在的状态路径（只提示，不拒绝保存）。

    这里只做"键树上有这一层吗"的存在性检查；值是不是 None 不算缺失（状态投影
    允许 current=null）。结果去重保序，方便前端直接列出来。
    """
    seen: list[str] = []
    for match in _STATUS_PATH.finditer(source):
        path = match.group(1) + match.group(2)
        if path in seen:
            continue
        exists, _ = _lookup(context, path)
        if not exists:
            seen.append(path)
    return seen


class TemplateStore:
    def __init__(self, storage, specs: Sequence[TemplateSpec]) -> None:
        self._storage = storage
        self._specs = {s.name: s for s in specs}
        self._rows: dict[str, dict] = {}
        self._loaded = False
        self._lock = asyncio.Lock()
        # ChainableUndefined：默认 Undefined 在 `a.b.c` 这种链式取值上会抛
        # UndefinedError；模板作者需要"未定义就是空串"（不阻塞直播），而不是
        # 因为少了一个字段整段渲染失败。它仍然不是 StrictUndefined。
        self._env = SandboxedEnvironment(undefined=ChainableUndefined, autoescape=False)
        # 进程内"最近一次成功渲染"：模板坏了就退回它（按 name 分开存）
        self._last_ok: dict[str, str] = {}
        self._errors: dict[str, dict] = {}
        # 渲染成功/失败时通知归属者（stream 用它 bump chatbot 分区的 rev）
        self.on_render_result: Optional[Callable[[], None]] = None

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
        self.spec(name)
        content = content.strip()
        # 空内容仍拒绝：那不是"编辑自由"，是手滑丢数据（与 persona 同一政策）
        if not content:
            raise TemplateError(f"template {name!r} must not be empty")
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

    @property
    def template_error(self) -> Optional[dict]:
        """最近一次**实况**渲染失败（成功时清除）；供 stream 的 chatbot 分区展示。"""
        if not self._errors:
            return None
        # 同时最多一个模板在实况路径上渲染；有多个时给最近的那个
        return max(self._errors.values(), key=lambda e: e["at"])

    def _render_source(self, source: str, context: dict) -> str:
        template = self._env.from_string(source)
        out = template.render(**_plain(context))
        # 变量为空会留下连续空行（如未设置标题时的整行）：压成一段
        return _BLANK_RUNS.sub("\n\n", out).strip()

    def _fallback(self, name: str, context: dict) -> str:
        """从未成功渲染过时的最小兜底：至少保住"观众消息"这块事实。"""
        messages = str(context.get("messages") or "").strip()
        if not messages:
            return ""
        label = "Recent chat messages:" if name == "chatbot_user" else "Recent viewer messages:"
        return f"{label}\n{messages}"

    def _record(self, name: str, error: Optional[str]) -> None:
        changed = False
        if error is None:
            if name in self._errors:
                self._errors.pop(name, None)
                changed = True
        else:
            self._errors[name] = {
                "name": name,
                "at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "message": error,
            }
            changed = True
        if changed and self.on_render_result is not None:
            try:
                self.on_render_result()
            except Exception:  # 通知失败不能反过来拖垮渲染
                logger.exception("template render listener failed")

    def render(self, name: str, context: Optional[dict] = None, content: Optional[str] = None) -> str:
        """渲染已保存模板（content 非 None = 草稿试渲染，不更新兜底与错误状态）。"""
        self._check_known(name)
        ctx = context or {}
        if content is not None:
            # 草稿只用于 preview：失败由 preview 报告，不能污染实况的上一版/错误
            return self._render_source(content, ctx)
        source = self._rows[name]["content"]
        try:
            out = self._render_source(source, ctx)
        except Exception as e:  # 语法错误 / 沙箱拒绝 / 运行时错误
            error = f"{type(e).__name__}: {e}"
            logger.warning("模板 %s 渲染失败，使用上一版兜底: %s", name, error)
            self._record(name, error)
            return self._last_ok.get(name) or self._fallback(name, ctx)
        self._last_ok[name] = out
        self._record(name, None)
        return out

    def preview(
        self, name: str, context: Optional[dict] = None, content: Optional[str] = None
    ) -> dict:
        """试渲染一次（不阻塞保存）：返回渲染信息 + 非阻塞提示。"""
        self._check_known(name)
        spec = self.spec(name)
        source = self._rows[name]["content"] if content is None else content
        ctx = context or {}
        rendered_ok = True
        error: Optional[str] = None
        try:
            rendered = self._render_source(source, ctx)
        except Exception as e:
            rendered_ok = False
            error = f"{type(e).__name__}: {e}"
            rendered = self._fallback(name, ctx)
        expanded = {
            key: len(_stringify(value))
            for key, value in ctx.items()
            if key in _TOP_KEYS and re.search(r"\b" + re.escape(key) + r"\b", source)
        }
        return {
            "name": name,
            "prompt": rendered,
            "rendered": rendered_ok,
            "error": error,
            "chars": {"template": len(source), "total": len(rendered)},
            "required": list(spec.required),
            # 非阻塞提示：必需占位符缺失不再拦保存
            "missing_placeholders": [
                key for key in spec.required if _token(key) not in source
            ],
            "unresolved_paths": unresolved_paths(source, ctx),
            "expanded": expanded,
            "estimated_tokens": (len(rendered) + 3) // 4,
        }
