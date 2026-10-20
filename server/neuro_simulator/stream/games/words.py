"""Wordle 词表：答案池 + 校验词典。

数据来源（都是宽松许可，出处记在 AGENTS.md）：
- `wordle_dictionary.txt`（39,039 词）：Neurdle(MIT) 英文词典的 5 字母全集（12,653，
  等同 Wordle 官方可猜词表）+ 该词典里 3~4、6~10 字母且出现在公开词频表
  （hermitdave/FrequencyWords en_50k）中的常用词。按词长建索引，判定"这串字母算不算一个词"。
- `wordle_answers_common.txt`（1,989 词）：5 字母词典词按词频取前 ~2,000 并滤掉专名/品牌。
  全是观众和模型都认得的常用词，比 Neurdle 自带的 60 词答案池大 30 倍
  （观众不容易秒猜，也不会撞出 hasta/sahib 这种废词）。
- `wordle_answers_neuro.txt`：Neurdle 的 Neuro 主题词（56 个，长度 3~16，
  如 FILTERED / GIGANEURO / ABANDONEDARCHIVE）。按概率混入，保留"Neuro 猜自己的梗"的味道，
  棋盘列数随词长自适应；长度超出词典覆盖（如 16 字母）时不做拼词校验。
"""

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Dict, FrozenSet, Tuple

DEFAULTS = Path(__file__).resolve().parent.parent / "defaults"


def _read(name: str) -> Tuple[str, ...]:
    return tuple(
        line.strip()
        for line in (DEFAULTS / name).read_text(encoding="utf-8").splitlines()
        if line.strip()
    )


@dataclass(frozen=True)
class WordBanks:
    answers: Tuple[str, ...]  # 常用答案池（小写 5 字母，按词频降序）
    neuro: Tuple[str, ...]  # Neuro 主题答案池（大写，变长）
    dictionary: Tuple[str, ...]  # 合法猜测词（小写，按词长建索引）
    _by_len: Dict[int, FrozenSet[str]] = field(init=False, compare=False, repr=False)
    _neuro_set: FrozenSet[str] = field(init=False, compare=False, repr=False)

    def __post_init__(self) -> None:
        by_len: Dict[int, set] = {}
        for w in self.dictionary:
            by_len.setdefault(len(w), set()).add(w)
        object.__setattr__(self, "_by_len", {k: frozenset(v) for k, v in by_len.items()})
        object.__setattr__(self, "_neuro_set", frozenset(self.neuro))

    def covers(self, length: int) -> bool:
        """这个词长有没有词典可校验（没有就只能放行）。"""
        return length in self._by_len

    def valid(self, word: str) -> bool:
        """猜测是否算"一个词"：命中 Neuro 主题词直接放行；否则查该词长的词典，
        该词长无覆盖时同样放行（16 字母的 ABANDONEDARCHIVE 没有词表可校验）。"""
        if word.upper() in self._neuro_set:
            return True
        pool = self._by_len.get(len(word))
        return True if pool is None else word.lower() in pool


@lru_cache(maxsize=1)
def load_banks() -> WordBanks:
    return WordBanks(
        answers=_read("wordle_answers_common.txt"),
        neuro=tuple(w.upper() for w in _read("wordle_answers_neuro.txt")),
        dictionary=_read("wordle_dictionary.txt"),
    )
