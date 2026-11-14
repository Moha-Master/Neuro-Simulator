"""Chatbot 表情大类：类内加权采样 + DB 权威的清单编辑。

机制层，不含策略词表。清单形状是一份 JSON 文档::

    {"version": 1,
     "categories": [
        {"id": "funny_moment", "label": "Funny Moment", "desc": "…",
         "emotes": [{"name": "om", "weight": 7292}, …],
         "templates": []}
     ]}

数据流与场景清单同构：
- 包内 ``defaults/emote_categories.json`` 由 ``scripts/bake_emote_categories.py``
  烘焙，只是**首次播种模板**；
- ``stream/data.db`` 的 ``emote_categories`` 单行 JSON 文档才是权威，编辑走
  ``PUT /manage/emote-categories``；
- ``load()`` 之后 ``categories`` 是内存缓存，``sample()`` 同步、无 I/O
  （state_kit 纪律：状态面只读缓存）。

类内采样是服务端职责：LLM 只选「大类 + 连发次数」，具体抽哪个表情按
``by_tag.neuro_solo`` 权重做累积分布，属于统计事实而不是模型发挥。
"""

from __future__ import annotations

import asyncio
import copy
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

CATEGORY_ID = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
_OVERVIEW_PREVIEW = 8


class CategoryError(Exception):
    """表情大类操作失败（message 可直接作为 API detail）。"""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class EmoteCategory:
    id: str
    label: str
    desc: str
    emotes: tuple[tuple[str, int], ...]
    templates: tuple[str, ...]

    @property
    def member_count(self) -> int:
        return len(self.emotes)

    # 兼容"每项是个 dict"的读法（manage/测试/未来脚本都按字段名取）。
    def __getitem__(self, key: str):
        try:
            return getattr(self, key)
        except AttributeError as e:
            raise KeyError(key) from e

    def get(self, key: str, default=None):
        return getattr(self, key, default)


def validate_content(doc: Any) -> list[str]:
    """Return a list of human-readable problems (empty = valid)."""
    errors: list[str] = []
    if not isinstance(doc, dict):
        return ["顶层必须是对象"]

    version = doc.get("version")
    if isinstance(version, bool) or not isinstance(version, int):
        errors.append("version 必须是整数")

    categories = doc.get("categories")
    if not isinstance(categories, list) or not categories:
        errors.append("categories 必须是非空数组")
        return errors

    seen_ids: set[str] = set()
    for index, category in enumerate(categories):
        where = f"categories[{index}]"
        if not isinstance(category, dict):
            errors.append(f"{where}: 必须是对象")
            continue
        cid = category.get("id")
        if not isinstance(cid, str) or not CATEGORY_ID.fullmatch(cid):
            errors.append(
                f"{where}: id 必须匹配 {CATEGORY_ID.pattern}（收到 {cid!r}）"
            )
        elif cid in seen_ids:
            errors.append(f"{where}: id 重复: {cid}")
        else:
            seen_ids.add(cid)
        label = category.get("label")
        if not isinstance(label, str) or not label.strip():
            errors.append(f"{where}: label 必须是非空字符串")
        if "desc" in category and not isinstance(category.get("desc"), str):
            errors.append(f"{where}: desc 必须是字符串")

        emotes = category.get("emotes")
        if not isinstance(emotes, list):
            errors.append(f"{where}: emotes 必须是数组")
        else:
            for j, member in enumerate(emotes):
                mwhere = f"{where}.emotes[{j}]"
                if not isinstance(member, dict):
                    errors.append(f"{mwhere}: 必须是 {{name, weight}} 对象")
                    continue
                name = member.get("name")
                if not isinstance(name, str) or not name.strip():
                    errors.append(f"{mwhere}: name 必须是非空字符串")
                weight = member.get("weight")
                if isinstance(weight, bool) or not isinstance(weight, int) or weight < 1:
                    errors.append(f"{mwhere}: weight 必须是 >= 1 的整数")

        if "templates" in category:
            templates = category.get("templates")
            if not isinstance(templates, list) or any(
                not isinstance(item, str) for item in templates
            ):
                errors.append(f"{where}: templates 必须是字符串数组")

    return errors


def _parse_categories(doc: dict) -> list[EmoteCategory]:
    result: list[EmoteCategory] = []
    for category in doc.get("categories") or []:
        emotes = tuple(
            (str(member["name"]), int(member["weight"]))
            for member in category.get("emotes") or []
        )
        templates = tuple(str(item) for item in category.get("templates") or [])
        result.append(
            EmoteCategory(
                id=str(category.get("id")),
                label=str(category.get("label")),
                desc=str(category.get("desc") or ""),
                emotes=emotes,
                templates=templates,
            )
        )
    return result


class CategoryStore:
    """单行 JSON 文档存储（与 ``scene_kit.SceneStore`` 同款，策略在本模块）。"""

    def __init__(self, storage: Any, default_path: Path, name: str = "categories") -> None:
        self._storage = storage
        self._default_path = Path(default_path)
        self._name = name
        self._doc: Optional[dict] = None
        self._categories: list[EmoteCategory] = []
        self._updated_at = ""
        self._lock = asyncio.Lock()

    # ---------- 加载 / 播种 ----------

    async def load(self) -> None:
        """读 DB；没有行就用包内默认播种（之后不再自动覆盖）。"""
        row = await self._storage.get_emote_categories(self._name)
        if row is None:
            text = self._default_text()
            await self._storage.upsert_emote_categories(text, self._name)
            row = await self._storage.get_emote_categories(self._name)
        self._apply(row["content"] if row else self._default_text(),
                    row["updated_at"] if row else "")

    def _default_text(self) -> str:
        try:
            return self._default_path.read_text(encoding="utf-8")
        except OSError as e:
            raise CategoryError(f"包内默认表情大类读取失败: {e}") from e

    def _apply(self, text: str, updated_at: str = "") -> None:
        try:
            doc = json.loads(text)
        except ValueError as e:
            raise CategoryError(f"表情大类 JSON 解析失败: {e}") from e
        errors = validate_content(doc)
        if errors:
            raise CategoryError("表情大类不合法: " + "；".join(errors))
        self._doc = doc
        self._categories = _parse_categories(doc)
        self._updated_at = updated_at
        self._merge_canon()

    def _merge_canon(self) -> None:
        """把全部成员名并入 ``chatbot.EMOTE_CANON``（原地 update，见下）。

        ``stream/emotes.py`` 与 chatbot 引用的是**同一个 dict 对象**，所以这里
        就地更新即可让 7TV 搜索预置名单跟随大类清单；不做模块级替换。
        """
        from .chatbot import EMOTE_CANON

        for category in self._categories:
            for name, _weight in category.emotes:
                EMOTE_CANON[name.lower()] = name

    # ---------- 读（同步、无 I/O） ----------

    @property
    def categories(self) -> list[EmoteCategory]:
        return self._categories

    @property
    def member_count(self) -> int:
        return sum(c.member_count for c in self._categories)

    @property
    def updated_at(self) -> str:
        return self._updated_at

    def category_ids(self) -> list[str]:
        return [c.id for c in self._categories]

    def find(self, category_id: str) -> Optional[EmoteCategory]:
        for category in self._categories:
            if category.id == category_id:
                return category
        return None

    def sample(self, category_id: str, rng) -> Optional[str]:
        """类内按 weight 的累积分布抽 1 个表情名；未知/空类返回 None。

        ``rng`` 必须是 ``random.Random`` 实例（或同接口），由调用方传入以便
        测试用固定种子复现。
        """
        category = self.find(category_id)
        if category is None or not category.emotes:
            return None
        total = sum(weight for _name, weight in category.emotes)
        if total <= 0:
            return None
        threshold = rng.random() * total
        acc = 0
        for name, weight in category.emotes:
            acc += weight
            if threshold < acc:
                return name
        return category.emotes[-1][0]

    def get_doc(self) -> dict:
        return copy.deepcopy(self._doc or {})

    def overview(self, preview: int = _OVERVIEW_PREVIEW) -> dict:
        """给 manage 页的概览：不含完整成员表，只给数量与前 N 个名字。"""
        categories = []
        total = 0
        for category in self._categories:
            total += category.member_count
            categories.append(
                {
                    "id": category.id,
                    "label": category.label,
                    "desc": category.desc,
                    "emote_count": category.member_count,
                    "preview": [name for name, _ in category.emotes[:preview]],
                }
            )
        return {"categories": categories, "total_members": total}

    def payload(self) -> dict:
        """保存/重置成功的响应：概览 + 全量文档（DB 权威内容）。"""
        return {
            "status": "ok",
            "content": self.get_doc(),
            **self.overview(),
        }

    # ---------- 写 ----------

    async def save(self, content: Any) -> dict:
        """校验 → 落库 → 刷新缓存；不合法抛 ``CategoryError``。"""
        doc = self._coerce(content)
        errors = validate_content(doc)
        if errors:
            raise CategoryError("表情大类不合法: " + "；".join(errors))
        canonical = json.dumps(doc, ensure_ascii=False, indent=2)
        async with self._lock:
            await self._storage.upsert_emote_categories(canonical, self._name)
            row = await self._storage.get_emote_categories(self._name)
            self._apply(canonical, row["updated_at"] if row else "")
        return self.payload()

    async def reset(self) -> dict:
        return await self.save(self._default_text())

    @staticmethod
    def _coerce(content: Any) -> dict:
        if isinstance(content, dict):
            return content
        if not isinstance(content, str):
            raise CategoryError("content 必须是 JSON 文本或对象")
        raw = content.strip()
        if not raw:
            raise CategoryError("表情大类不能为空")
        try:
            doc = json.loads(raw)
        except ValueError as e:
            raise CategoryError(f"表情大类 JSON 解析失败: {e}") from e
        if not isinstance(doc, dict):
            raise CategoryError("表情大类顶层必须是对象")
        return doc

    def validate(self, doc: Any) -> list[str]:
        return validate_content(doc)

    # 与模块级函数同名：调用方拿 store 直接校验也行。
    def validate_content(self, doc: Any) -> list[str]:
        return validate_content(doc)


def all_member_names(doc: Iterable[dict]) -> list[str]:
    """测试/工具辅助：按文档顺序导出全部成员名（去重保序）。"""
    seen: set[str] = set()
    names: list[str] = []
    for category in doc:
        for member in category.get("emotes") or []:
            name = str((member or {}).get("name") or "")
            if name and name not in seen:
                seen.add(name)
                names.append(name)
    return names
