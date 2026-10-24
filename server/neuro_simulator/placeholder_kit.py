"""占位符匹配的**机制**（消息模板与 persona 共用，与 `state_kit`/`scene_kit` 同级）。

判据留在这一处、策略留在调用方：后端 `templates.py`（Jinja 沙箱渲染）与
`persona.py`（system prompt 的字符串替换）都要回答"`{{ key }}` 在这段文本里吗"，
而两边各自的字面量比对踩过同一个坑——

Jinja 允许 `{{key}}`、`{{ key }}`、`{{- key -}}` 三种写法指同一个变量。曾经的裸
字符串比对让**默认模板自己**（写的是 `{{ chat_messages }}`）被报成"缺少占位符"，
在编辑器顶上亮一条假警告；persona 侧更糟：`{{ core_memory }}` 带空格就匹配不上，
core memory **静默不注入**，而界面上的提示说"没问题"。

> 前端 `dashboard/src/utils/placeholders.ts` 是这条规则的镜像实现（草稿要即时提示，
> 等不到后端）。改这里必须同步改那里——两边不一致时，UI 会给出与运行期相反的答案。
"""

import re

_CACHE: dict[str, re.Pattern[str]] = {}


def placeholder_re(key: str) -> re.Pattern[str]:
    """`{{ key }}` 的宽容匹配（空白的有无/多少、Jinja 的 `-` 空白控制都算命中）。"""
    try:
        return _CACHE[key]
    except KeyError:
        pat = re.compile(r"\{\{-?\s*" + re.escape(key) + r"\s*-?\}\}")
        _CACHE[key] = pat
        return pat


def has_placeholder(source: str, key: str) -> bool:
    return placeholder_re(key).search(source) is not None


def missing_placeholders(source: str, keys) -> list[str]:
    """`keys` 里没出现在 source 中的那些（顺序保持）。只作提示，不作拦截。"""
    return [key for key in keys if not has_placeholder(source, key)]


def substitute(source: str, key: str, text: str) -> str:
    """把 source 里所有 `{{ key }}`（任意空白写法）换成 text。

    没有命中就原样返回——**不自动追加**：文本是作者写的，替他加东西会掩盖误删。
    """
    return placeholder_re(key).sub(lambda _m: text, source)
