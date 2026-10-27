"""消息模板的形状模型：显式声明状态投影里**有哪些字段**，供校验器使用。

为什么是手写声明，而不是反射 provider / 正则解析 `status.py` / 依赖一次运行样本：

- 投影里的**值**可能是 `None`（没有场景、没有游戏、neuro 不可达），但字段名是
  静态契约；靠样本判断"这次有没有这个键"会在分支没走到时漏掉。
- 形状是给模板作者看的承诺：`status.py` / `ui_state.py` 改了字段，就应该同时改
  这里。`test_templates.py` 的漂移测试会拿真实 provider 的返回键来对账，漏改会
  直接失败（照 `dashboard/src/types/status.ts` 那套"类型当契约守卫"的先例）。

边界（有意的）：

- `games.<id>` 的键由运行时决定（当前游戏 id），形状只声明到"值是个 dict"这一层；
  再往里取字段无法静态确定，校验器只给 warning、不拒绝。
- `stream_status` 的分区恒在（provider 总返回 dict）；`scene` 的值可能是 `None`，
  但分区名始终存在，所以骨架里仍给一个字段齐全的 dict。
- `neuro_status` 整轮可能不可用（provider 返回 `{}`），`turn` 也可能缺席；它们是
  可选根/可选分区，模板必须防御式引用（`|default` / `|int(0)` / `{% if %}`）。
"""

from dataclasses import dataclass
from typing import Dict, FrozenSet, Iterable, List, Sequence, Tuple

SCALAR = ("str", "int", "float", "bool", "none")

# 分区里的"任意运行时键"标记：`games.<runtime_key>`。
RUNTIME_KEY = "<runtime_key>"

# 由运行时决定下标的路径在静态检查里的占位写法。
DYNAMIC_INDEX = "[<dynamic>]"


def T(*types: str) -> FrozenSet[str]:
    for name in types:
        if name not in SCALAR and name not in ("list", "dict"):
            raise ValueError(f"未知类型 {name!r}")
    return frozenset(types)


@dataclass(frozen=True)
class Shape:
    """一个状态命名空间的形状：分区名 → 字段名 → 可能的类型集合。"""

    sections: Dict[str, Dict[str, FrozenSet[str]]]
    # 分区名整块可能缺席（`/status` 的 sections 里没有它）
    optional_sections: Tuple[str, ...] = ()


STREAM_SHAPE = Shape(
    sections={
        "loop": {
            "state": T("str"),
            "session_id": T("str", "none"),
            "queue_id": T("int", "str", "none"),
            "channel": T("str", "none"),
            "conversation_id": T("str", "none"),
            "round": T("int"),
            "cursor": T("int", "none"),
            "highlight_cursor": T("int", "none"),
            "highlight_paused": T("bool"),
            "highlight_source": T("str", "none"),
            "recording": T("bool"),
            "intro": T("dict", "none"),
            "last_error": T("str", "none"),
            "started_at": T("str", "none"),
            "persisted": T("dict", "none"),
        },
        "scene": {
            "id": T("str"),
            "label": T("str"),
            "icon": T("str", "none"),
            "desc": T("str"),
            "prompt_context": T("str"),
            "highlight_messages": T("bool"),
            "duration": T("float", "int", "none"),
            "elapsed": T("float", "int", "none"),
        },
        "metadata": {
            "title": T("str"),
            "category": T("str"),
            "tags": T("list"),
            "live": T("bool"),
        },
        "audience": {
            "viewers": T("int", "none"),
            "viewers_source": T("str"),
            "updated_at": T("str", "none"),
            "error": T("str", "none"),
        },
        "chat": {
            "normal_pending": T("int", "none"),
            "highlight_pending": T("int", "none"),
            "last_message_id": T("int", "none"),
        },
        "chatbot": {
            "enabled": T("bool"),
            "model": T("str", "none"),
            "model_ref": T("str", "none"),
            "interval_s": T("float", "int"),
            "count": T("int"),
            "max_concurrent": T("int"),
            "interval_jitter": T("float", "int"),
            "in_flight": T("int"),
            "discarded": T("int"),
            "last_discard": T("dict", "none"),
            "template_error": T("dict", "none"),
        },
        "games": {
            "active": T("str", "none"),
            # 当前游戏的快照键（wordle / chess）：键名运行时才知道，值只保证是
            # dict 或 None。更深一层（phase/summary/element）不建形状。
            RUNTIME_KEY: T("dict", "none"),
        },
        "neuro_link": {
            "reachable": T("bool"),
            "observed_at": T("str", "none"),
            "url": T("str"),
            "error": T("str", "none"),
        },
    },
)


NEURO_SHAPE = Shape(
    sections={
        "scene": {
            "id": T("str"),
            "label": T("str"),
            "desc": T("str"),
            "icon": T("str", "none"),
            "duration": T("float", "int", "none"),
            "elapsed": T("float", "int", "none"),
            "auto_returns": T("bool"),
            "highlight_messages": T("bool"),
        },
        "turn": {
            "turn_id": T("int", "none"),
            "channel": T("str", "none"),
            "generating": T("bool"),
            "speaking": T("bool"),
            "idle": T("bool"),
            "silent_s": T("float", "int", "none"),
            "remaining_s": T("float", "int"),
            "ends_at": T("float", "int", "none"),
            "segments": T("int"),
            "segments_done": T("int"),
            "in_flight": T("int"),
        },
        "layers": {
            # 列表本身没有字段；元素形状不建（模板不读它）
        },
        "model": {"service_ok": T("bool")},
        "tts": {
            "effective_provider": T("str", "none"),
            "degraded": T("bool"),
        },
        "caption": {
            "hide_ms": T("int"),
            "step_gap_ms": T("int"),
        },
        "manifest": {
            "is_default": T("bool"),
            "updated_at": T("str"),
            "chars": T("int"),
        },
    },
    # neuro 不可达时整个命名空间是 {}；turn 也可能缺席（从未收到过 /chat）
    optional_sections=("turn",),
)


SHAPE: Dict[str, Shape] = {
    "stream_status": STREAM_SHAPE,
    "neuro_status": NEURO_SHAPE,
}

# 根变量整轮可能不可用：引用它必须防御式（G2 会拒未防御的直接取值）。
OPTIONAL_ROOTS = frozenset({"neuro_status"})

# 固定标量根（不是状态投影，但模板会用到）。
SCALAR_ROOTS = {"chat_messages": "str", "datetime": "str", "neuro_said": "str"}


def worst_value(types: Iterable[str]) -> object:
    """字段的"最坏形态"：G2 骨架里用的值。

    `none`/`dict` 优先给 `None`：作者最需要防的就是"字段存在但值是 null"。
    `int/float` 也给 `None`（投影里的数字常来自可空列），而不是 0——给 0 会让
    `a / b`、`a > 45` 这类表达式"碰巧"通过，失去校验意义。
    """
    types = frozenset(types)
    if "none" in types or "dict" in types:
        return None
    if "int" in types or "float" in types:
        return None
    if "bool" in types:
        return False
    if "list" in types:
        return []
    if "str" in types:
        return ""
    return None


class _RuntimeValue(dict):
    """运行时下标后面的"任意值"：链式访问都返回自己，文本化为空串。

    `games[games.active].summary` 是安全写法（运行期 active 一定是合法游戏 id），
    但骨架里 active 必须取最坏值 None，静态无法知道 `games[None]` 里有什么。
    这个占位对象让 G2 能渲染到底；真正的"无法静态验证"由路径检查记 warning。

    继承 dict 是为了让 `|tojson`、`{% for %}` 这类"把值当容器"的用法也能安全
    落地（空 dict），而不是在 JSON 序列化时抛 TypeError 误伤合理模板。
    """

    __slots__ = ()

    def __getattr__(self, name: str) -> "_RuntimeValue":
        if name[:2] == "__" and name[-2:] == "__":
            raise AttributeError(name)
        return self

    def __getitem__(self, _key: object) -> "_RuntimeValue":
        return self

    def __str__(self) -> str:
        return ""

    def __repr__(self) -> str:
        return "<runtime>"

    def __bool__(self) -> bool:
        return False

    def __len__(self) -> int:
        return 0

    def __iter__(self):
        return iter(())

    def __int__(self) -> int:
        return 0

    def __float__(self) -> float:
        return 0.0


class _RuntimeDict(dict):
    """允许任意键的分区（games）：缺键 → 运行时占位值，而不是 KeyError。"""

    def __missing__(self, _key: object) -> _RuntimeValue:
        return _RuntimeValue()


def _section_skeleton(fields: Dict[str, FrozenSet[str]]) -> dict:
    if RUNTIME_KEY in fields:
        out: dict = _RuntimeDict()
    else:
        out = {}
    for name, types in fields.items():
        if name == RUNTIME_KEY:
            continue
        out[name] = worst_value(types)
    return out


def build_skeleton() -> dict:
    """G2 用的"最坏骨架"：必需分区齐全、字段值取最坏形态、可选分区整块缺席。"""
    root: dict = {
        name: worst_value((kind,)) for name, kind in SCALAR_ROOTS.items()
    }
    for name, shape in SHAPE.items():
        if name in OPTIONAL_ROOTS:
            # 整轮不可用时 provider 返回 {}；不能给任何分区，否则会掩盖未防御引用
            root[name] = {}
            continue
        sections: dict = {}
        for section, fields in shape.sections.items():
            if section in shape.optional_sections:
                continue
            sections[section] = _section_skeleton(fields)
        root[name] = sections
    return root


# ---------- 静态路径检查 ----------


@dataclass(frozen=True)
class PathReport:
    errors: Tuple[str, ...] = ()
    warnings: Tuple[str, ...] = ()


def _chain(node) -> Tuple[str, List[Tuple[str, str]]] | None:
    """把 Name/Getattr/Getitem 链还原成 (根变量, [(attr|item, 值)])。"""
    from jinja2 import nodes

    if isinstance(node, nodes.Name):
        return node.name, []
    if isinstance(node, nodes.Getattr):
        base = _chain(node.node)
        if base is not None:
            return base[0], [*base[1], ("attr", node.attr)]
    if isinstance(node, nodes.Getitem):
        base = _chain(node.node)
        if base is not None:
            # 下标表达式本身由另一条链单独分析；这里只记"运行时下标"
            return base[0], [*base[1], ("item", "")]
    return None


def _path_text(root: str, parts: Sequence[Tuple[str, str]]) -> str:
    out = root
    for kind, value in parts:
        out += f".{value}" if kind == "attr" else DYNAMIC_INDEX
    return out


def _walk(node):
    from jinja2 import nodes

    yield node
    if isinstance(node, nodes.Node):
        for child in node.iter_child_nodes():
            yield from _walk(child)


def analyze_paths(template) -> PathReport:
    """遍历 AST，对照形状检查 `stream_status.*` / `neuro_status.*` 路径。

    只有**点号上的字段名不存在**才拒绝（明确的拼写错误）；运行时下标之后的访问
    无法静态确定，记 warning。可选根/可选分区的"未防御取值"由 G2 负责。
    """
    from jinja2 import nodes

    errors: List[str] = []
    warnings: List[str] = []
    seen_errors: set = set()
    seen_warnings: set = set()

    nodes_with_chains = []
    all_paths: set = set()
    for node in _walk(template):
        if not isinstance(node, (nodes.Getattr, nodes.Getitem)):
            continue
        chain = _chain(node)
        if chain is None:
            continue
        root, parts = chain
        if root not in SHAPE or not parts:
            continue
        nodes_with_chains.append((root, parts))
        all_paths.add(_path_text(root, parts))

    for root, parts in nodes_with_chains:
        shape = SHAPE[root]
        section_name = None
        fields = None
        dynamic = False
        dynamic_use = False
        opaque = False
        typo = None

        for kind, value in parts:
            if kind == "item":
                # 根上下标（stream_status["x"]）或字段上下标都无法静态确定
                dynamic = True
                continue

            if section_name is None:
                if value.startswith("_"):
                    # 沙箱越权（__class__ 等）交给 G2 报 SecurityError
                    dynamic = True
                    continue
                if value not in shape.sections:
                    typo = (
                        f"{root}.{value} 不存在（已声明分区："
                        f"{', '.join(shape.sections)}）"
                    )
                    break
                section_name = value
                fields = shape.sections[value]
                continue

            if dynamic or opaque or fields is None:
                # 运行时下标之后 / 已进入嵌套 dict/list / 标量的方法：静态无法确定
                dynamic = True
                if kind == "attr":
                    dynamic_use = True
                continue

            if value in fields:
                # 形状只声明到分区字段这一层；再往里访问（含 str 的方法）只能 warning
                opaque = True
                continue

            if RUNTIME_KEY in fields:
                # games.<game_id>：键名运行时才知道
                dynamic = True
                continue

            typo = (
                f"{_path_text(root, parts)} 不存在（{section_name} 的字段："
                f"{', '.join(k for k in fields if k != RUNTIME_KEY)}）"
            )
            break

        path = _path_text(root, parts)
        if typo is not None:
            if typo not in seen_errors:
                seen_errors.add(typo)
                errors.append(typo)
        elif dynamic_use:
            # 外层链已经报过更长的路径时，不再为它的前缀重复报（`element.foo`
            # 与 `element` 是同一个无法静态验证的位置）。
            longer = any(
                other != path
                and (other.startswith(path + ".") or other.startswith(path + "["))
                for other in all_paths
            )
            message = f"cannot statically verify {path}（下标键由运行时决定）"
            if not longer and message not in seen_warnings:
                seen_warnings.add(message)
                warnings.append(message)

    return PathReport(tuple(errors), tuple(warnings))
