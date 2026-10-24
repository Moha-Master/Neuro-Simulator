"""命名模板表（prompts 表的多行）：模块运行期拼给 LLM 的消息模板，人工可编辑。

与 persona.PersonaStore 的区别：
- 这里只管若干条命名模板，没有 core memory
- 模板用 **Jinja2 沙箱** 渲染（内容存 DB、人工可编辑，语法错误/越权访问要挡住）
- 保存走三道关卡（见下）；必填占位符缺失仍只提示，不拦保存
- load() 时对缺失行按默认值播种；模板没有删除操作，播种幂等且安全

三道关卡（保存时执行；preview 只报告、不阻塞）：

- **G1 编译**：`SandboxedEnvironment.parse` 失败 → 400（语法错误带行号）。
- **G2 最严苛**：用"最坏骨架"（`template_shape.build_skeleton()`）+
  `_StrictChainableUndefined` 渲染一遍。字段缺失、值为 null、可选根/可选分区
  未防御取值、沙箱越权，都在这里拒绝。
- **G3 现实**：用实况数据 + `ChainableUndefined` 渲染，只展示（preview 的正文与
  长度），不阻塞。

保存前还有静态路径检查（`template_shape.analyze_paths`）：点号上的字段名不存在
（拼写错误）→ 400；运行时下标之后的访问无法静态验证 → warning。

渲染 context 只允许纯 dict/list/标量（见 `_plain`）：一旦把业务对象放进来，模板
就能调用有副作用的方法。运行期仍可能遇到"绕过保存直接写 DB"的坏模板；这时退回
一个只含 `messages` 事实的最小兜底，并把错误记进 `chatbot.template_error`——
直播循环绝不因模板坏了而中断。不再保留进程内的"上一版成功文本"。
"""

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Optional, Sequence

from jinja2 import ChainableUndefined, TemplateSyntaxError, meta
from jinja2.sandbox import SandboxedEnvironment

from .template_shape import (
    SCALAR_ROOTS,
    SHAPE,
    analyze_paths,
    build_skeleton,
)

logger = logging.getLogger(__name__)

_BLANK_RUNS = re.compile(r"\n{3,}")
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


class _StrictChainableUndefined(ChainableUndefined):
    """G2 专用：允许链式访问，但"真正取用这个值"时报错。

    为什么不用 `StrictUndefined`：后者在第一层缺键时就抛，`|default("")` 也救不
    回来，于是"可选根变量 + 默认值"这种合理写法会被误伤。这个类型把边界放在
    "取值"而不是"访问"：

    - `{{ neuro_status.turn.silent_s }}` → `__str__` 抛错 → 拒；
    - `{{ ...|default("") }}` → default 在取值前替换 → 过；
    - `{% if neuro_status.turn %}` → `__bool__` 为 False → 过（防御式写法）；
    - `{{ ...|int(0) }}` → `__int__` 给 0 → 过；
    - `{% if ...silent_s > 45 %}` → 比较仍抛错（未防御的值不能参与运算）。
    """

    __slots__ = ()

    def __str__(self) -> str:
        self._fail_with_undefined_error()

    def __int__(self) -> int:
        return 0

    def __float__(self) -> float:
        return 0.0


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


def _syntax_message(exc: TemplateSyntaxError) -> str:
    line = getattr(exc, "lineno", None)
    where = f"（第 {line} 行）" if line else ""
    return f"模板语法错误{where}: {exc.message}"


class TemplateStore:
    def __init__(self, storage, specs: Sequence[TemplateSpec]) -> None:
        self._storage = storage
        self._specs = {s.name: s for s in specs}
        self._rows: dict[str, dict] = {}
        self._loaded = False
        self._lock = asyncio.Lock()
        # 实况渲染（G3）：未定义路径渲染为空串，直播不因少一个字段中断。
        self._env = SandboxedEnvironment(undefined=ChainableUndefined, autoescape=False)
        # 保存校验（G2）：同一套沙箱，但 undefined 在"被取用"时抛错。
        self._strict_env = SandboxedEnvironment(
            undefined=_StrictChainableUndefined, autoescape=False
        )
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

    # ---------- 校验（G1 / 静态路径 / G2） ----------

    def _gate(self, source: str) -> tuple[dict, list[str], dict[str, str]]:
        """跑完可静态判定的关卡，返回 (gate, warnings, errors)。

        preview 需要看到"每一关过没过"，所以这里不抛异常；save 的 validate()
        按 g1/roots/paths/g2 的顺序把第一个错误转成 400。
        """
        gate = {"g1_ok": False, "g2_ok": False, "skeleton_rendered": False}
        errors: dict[str, str] = {}
        warnings: list[str] = []

        template = None
        try:
            template = self._env.parse(source)
            gate["g1_ok"] = True
        except TemplateSyntaxError as exc:
            errors["g1"] = _syntax_message(exc)

        if template is not None:
            # 未知根变量（`{{ config }}` 之类）也按拼写错误拒绝；Jinja 自带全局
            # （range/dict/...）与 {% set %}/for 的局部变量不在结果里。
            unknown = sorted(
                meta.find_undeclared_variables(template)
                - set(SHAPE)
                - set(SCALAR_ROOTS)
                - set(self._env.globals)
            )
            if unknown:
                errors["roots"] = "模板引用了未知变量: " + ", ".join(unknown)

            report = analyze_paths(template)
            warnings = list(report.warnings)
            if report.errors:
                errors["paths"] = "模板路径错误: " + "；".join(report.errors)

        if gate["g1_ok"]:
            try:
                # 两个骨架：主骨架（neuro_said="" 走"尚未发言"分支）之外，再给一个
                # 非空 neuro_said，让 `{% elif ... %}` 这类后续分支也真的被渲染到。
                # 只渲染最坏骨架时，`{% if not neuro_said %}` 会把后面的未防御比较
                # 整段跳过，G2 就漏判了。
                skeletons = [build_skeleton(), {**build_skeleton(), "neuro_said": "x"}]
                strict_template = self._strict_env.from_string(source)
                for skeleton in skeletons:
                    strict_template.render(**skeleton)
                gate["g2_ok"] = True
                gate["skeleton_rendered"] = True
            except Exception as exc:  # SecurityError / UndefinedError / TypeError...
                errors["g2"] = (
                    "模板在数据缺失时会出错: "
                    f"{type(exc).__name__}: {exc}"
                )
        return gate, warnings, errors

    def validate(self, name: str, content: str) -> dict:
        """三道关卡里可静态判定的部分；失败抛 TemplateError(400)。"""
        self.spec(name)
        gate, warnings, errors = self._gate(content)
        for key in ("g1", "roots", "paths", "g2"):
            if key in errors:
                raise TemplateError(errors[key], status=400)
        return {"gate": gate, "warnings": warnings}

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

    async def save(self, name: str, content: str) -> dict:
        await self._ensure_loaded()
        self.spec(name)
        content = content.strip()
        # 空内容仍拒绝：那不是"编辑自由"，是手滑丢数据（与 persona 同一政策）
        if not content:
            raise TemplateError(f"template {name!r} must not be empty")
        validation = self.validate(name, content)
        async with self._lock:
            await self._storage.upsert_prompt(content, name)
            row = await self._storage.get_prompt(name)
            self._rows[name] = {
                "content": content,
                "updated_at": row["updated_at"] if row else "",
            }
        return validation

    async def reset(self, name: str) -> dict:
        return await self.save(name, self.spec(name).default)

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
        """绕过保存的坏模板 / 运行期意外时的最小兜底：至少保住"观众消息"这块事实。"""
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
        """渲染已保存模板（content 非 None = 草稿试渲染，不更新错误状态）。"""
        self._check_known(name)
        ctx = context or {}
        if content is not None:
            # 草稿只用于 preview：失败由 preview 报告，不能污染实况的错误状态
            return self._render_source(content, ctx)
        source = self._rows[name]["content"]
        try:
            out = self._render_source(source, ctx)
        except Exception as e:  # 绕过保存的坏模板 / 沙箱拒绝 / 运行时错误
            error = f"{type(e).__name__}: {e}"
            logger.warning("模板 %s 渲染失败，使用最小兜底: %s", name, error)
            self._record(name, error)
            return self._fallback(name, ctx)
        self._record(name, None)
        return out

    def preview(
        self, name: str, context: Optional[dict] = None, content: Optional[str] = None
    ) -> dict:
        """试渲染一次：G1/G2/静态路径只报告，G3 给实况文本；全部不阻塞。"""
        self._check_known(name)
        spec = self.spec(name)
        source = self._rows[name]["content"] if content is None else content
        ctx = context or {}
        gate, warnings, gate_errors = self._gate(source)

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
            # G1/G2 是否通过；skeleton_rendered 与 g2_ok 同义，保留给前端直读
            "gate": gate,
            "gate_errors": gate_errors,
            "warnings": warnings,
            "chars": {"template": len(source), "total": len(rendered)},
            "required": list(spec.required),
            # 非阻塞提示：必需占位符缺失不再拦保存
            "missing_placeholders": [
                key for key in spec.required if _token(key) not in source
            ],
            "expanded": expanded,
            "estimated_tokens": (len(rendered) + 3) // 4,
        }
