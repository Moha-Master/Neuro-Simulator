"""Wordle：六次机会猜一个词，每次猜测逐字母得到 green / yellow / grey。

规则与视觉规格照 Neurdle（MIT）：两遍扫描处理重复字母、6 行、词长可变
（Neuro 主题词有 4~16 个字母的，棋盘列数跟着走）。
"""

import random
from typing import Any, Dict, List, Optional

from .base import Game, GameError, register
from .words import WordBanks, load_banks

MAX_ROWS = 6
NEURO_CHANCE = 0.15  # 抽到 Neuro 主题词的概率

CORRECT, PRESENT, ABSENT = "correct", "present", "absent"
_LABEL = {CORRECT: "green", PRESENT: "yellow", ABSENT: "grey"}
_RANK = {ABSENT: 0, PRESENT: 1, CORRECT: 2}


def evaluate(guess: str, answer: str) -> List[str]:
    """Wordle 评估：先标"位置也对"的，再在剩余字母计数里标"有但位置不对"的。

    两遍扫描 + 计数递减，重复字母才不会被重复计分（answer=ABBA、guess=BAAB 这类）。
    """
    res = [ABSENT] * len(guess)
    counts: Dict[str, int] = {}
    for ch in answer:
        counts[ch] = counts.get(ch, 0) + 1
    for i, ch in enumerate(guess):
        if ch == answer[i]:
            res[i] = CORRECT
            counts[ch] -= 1
    for i, ch in enumerate(guess):
        if res[i] != CORRECT and counts.get(ch, 0) > 0:
            res[i] = PRESENT
            counts[ch] -= 1
    return res


@register
class WordleGame(Game):
    id = "wordle"
    label = "Wordle"
    scene_id = "play_wordle"
    element_id = "game_wordle"

    def __init__(self, hub: Any) -> None:
        super().__init__(hub)
        self.words: WordBanks = load_banks()
        self.rng = random.Random()
        self._flash_seq = 0
        self.flash: Optional[Dict[str, Any]] = None
        self.new_game()

    # ---------- 局面 ----------

    def new_game(self) -> None:
        """开新局：抽答案（Neuro 主题词按概率混入，长度可变）。"""
        self.neuro_word = self.rng.random() < NEURO_CHANCE
        if self.neuro_word:
            self.answer = self.rng.choice(self.words.neuro).upper()
        else:
            self.answer = self.rng.choice(self.words.answers).upper()
        self.word_len = len(self.answer)
        self.rows: List[List[str]] = [[] for _ in range(MAX_ROWS)]
        self.statuses: List[Optional[List[str]]] = [None] * MAX_ROWS
        self.row = 0
        self.done = False
        self.win = False
        self.keyboard: Dict[str, str] = {}
        self.flash = None

    @property
    def status(self) -> str:
        if not self.done:
            return "playing"
        return "won" if self.win else "lost"

    @property
    def typing(self) -> List[str]:
        """当前正在填的那一行。"""
        return self.rows[self.row]

    @property
    def guesses_used(self) -> int:
        return sum(1 for s in self.statuses if s)

    def _set_flash(self, kind: str, text: str) -> None:
        """给画面的一次性提示（toast）；seq 让组件判断"这是新的一条"。"""
        self._flash_seq += 1
        self.flash = {"kind": kind, "text": text, "seq": self._flash_seq}

    # ---------- 生命周期 ----------

    def start(self) -> str:
        hint = " Hint: it is a Neuro-themed word." if self.neuro_word else ""
        rule = (
            " Every guess must be a real word."
            if self.words.covers(self.word_len)
            else " A word this long is not in our dictionary, so any letters count as a guess."
        )
        return (
            f"Wordle started. The secret word has {self.word_len} letters and you get "
            f"{MAX_ROWS} guesses.{hint}{rule} Type letters, then submit."
        )

    def act(self, action: str, args: Dict[str, Any]) -> str:
        if action == "restart":
            self.new_game()
            return self.start() + " (fresh board)"
        if action == "type":
            return self.type_letters(str(args.get("letters") or ""))
        if action == "delete":
            return self.delete_letters(int(args.get("count") or 1))
        if action == "submit":
            return self.submit()
        if action == "state":
            return self.describe()
        raise GameError(f"Unknown wordle action {action!r}.")

    # ---------- 动作 ----------

    def type_letters(self, letters: str) -> str:
        if self.done:
            return self._over_text()
        clean = "".join(ch for ch in letters.upper() if "A" <= ch <= "Z")
        if not clean:
            return "Nothing to type: give me actual letters (A-Z)."
        room = self.word_len - len(self.typing)
        taken = clean[:room]
        if not taken:
            self._set_flash("error", "Row is full - submit or delete first")
            return f"Row {self.row + 1} is already full ({''.join(self.typing)}). Submit it or delete letters first."
        self.typing.extend(taken)
        note = f" Row was full, so {clean[room:]} was ignored." if len(clean) > room else ""
        return f"Typed {' '.join(taken)}.{note} {self._row_text()}"

    def delete_letters(self, count: int) -> str:
        if self.done:
            return self._over_text()
        count = max(1, min(count, 10))
        removed = 0
        while removed < count and self.typing:
            self.typing.pop()
            removed += 1
        if not removed:
            return f"Row {self.row + 1} is already empty."
        return f"Deleted {removed} letter{'s' if removed > 1 else ''}. {self._row_text()}"

    def submit(self) -> str:
        if self.done:
            return self._over_text()
        guess = "".join(self.typing)
        if len(guess) < self.word_len:
            self._set_flash("error", f"Need {self.word_len} letters")
            return (
                f"Cannot submit: the word has {self.word_len} letters but you typed "
                f"{len(guess)} ({guess or 'nothing'}). {self._row_text()}"
            )
        if not self.words.valid(guess):
            self._set_flash("error", "Not in the word list")
            return f'"{guess}" is not in the word list, so that guess did not count. {self._row_text()}'

        statuses = evaluate(guess, self.answer)
        self.statuses[self.row] = statuses
        for ch, st in zip(guess, statuses):
            prev = self.keyboard.get(ch)
            if prev is None or _RANK[st] > _RANK[prev]:
                self.keyboard[ch] = st
        marks = " ".join(f"{ch}={_LABEL[st]}" for ch, st in zip(guess, statuses))
        self.win = all(st == CORRECT for st in statuses)
        self.done = self.win or self.row == MAX_ROWS - 1

        if self.done:
            self._set_flash("win" if self.win else "lose", self.answer)
            if self.win:
                return (
                    f"Guess {self.row + 1} {guess}: {marks}. YOU GOT IT in {self.row + 1} of "
                    f"{MAX_ROWS}! The word was {self.answer}. Restart for a new board or exit to stop."
                )
            return (
                f"Guess {self.row + 1} {guess}: {marks}. Out of guesses — the word was "
                f"{self.answer}. Restart for a new board or exit to stop."
            )

        self.row += 1
        return (
            f"Guess {self.row} {guess}: {marks}. {MAX_ROWS - self.row} guesses left. "
            f"{self._row_text()}"
        )

    # ---------- 给 LLM 的文本 ----------

    def describe(self) -> str:
        head = (
            f"Wordle: {self.word_len}-letter word, "
            f"{self.guesses_used} of {MAX_ROWS} guesses used"
        )
        lines = [head + (f" — {self.status.upper()}" if self.done else "")]
        for i in range(MAX_ROWS):
            row = "".join(self.rows[i])
            if self.statuses[i]:
                marks = " ".join(_LABEL[s] for s in self.statuses[i])
                lines.append(f"  {row}  {marks}")
            elif row:
                lines.append(f"  {row}{'_' * (self.word_len - len(row))}  (typing)")
        if self.keyboard:
            groups: Dict[str, List[str]] = {}
            for ch, st in self.keyboard.items():
                groups.setdefault(st, []).append(ch)
            parts = [f"{_LABEL[st]}={''.join(sorted(chs))}" for st, chs in groups.items()]
            lines.append("  keyboard " + " ".join(parts))
        if not self.done:
            lines.append(f"  {MAX_ROWS - self.guesses_used} guesses left")
        return "\n".join(lines)

    def _row_text(self) -> str:
        row = "".join(self.typing)
        return (
            f"Row {self.row + 1} holds {row or 'nothing'} "
            f"({self.word_len - len(row)} letters to go)."
        )

    def _over_text(self) -> str:
        return (
            f"This game is over — the word was {self.answer}. "
            "Restart for a new board or exit to stop playing."
        )

    # ---------- 画面状态（全量快照） ----------

    def narrative(self, state: Dict[str, Any]) -> str:
        """一句叙事：只给"第几次猜测/输赢"，不 dump 棋盘字母。"""
        used = int(state.get("guesses_used") or 0)
        status = str(state.get("status") or "")
        if status == "won":
            return f"won in {used} of {MAX_ROWS} guesses"
        if status == "lost":
            return f"lost after {used} of {MAX_ROWS} guesses"
        return f"playing, {used} of {MAX_ROWS} guesses used"

    def viewer(self, state: Dict[str, Any]) -> str:
        """面向观众的大众化解说：棋盘摘要 + 每行 green/yellow 数量。

        进行中**绝不**提答案（`state["answer"]` 本来只在结束后有值，仍然防御）。
        """
        word_len = int(state.get("word_len") or 0)
        used = int(state.get("guesses_used") or 0)
        status = str(state.get("status") or "")
        theme = " (Neuro-themed word)" if state.get("neuro_word") else ""
        head = f"Wordle{theme}, {word_len} letters, {used}/{MAX_ROWS} guesses."

        rows = state.get("rows") or []
        statuses = state.get("statuses") or []
        summaries: List[str] = []
        for row, marks in zip(rows, statuses):
            if not marks:
                continue
            guess = "".join(str(ch) for ch in row)
            green = sum(1 for m in marks if m == CORRECT)
            yellow = sum(1 for m in marks if m == PRESENT)
            summaries.append(f"{guess}: {green} green, {yellow} yellow.")
        if not summaries:
            summaries.append("No guesses yet.")

        tail = ""
        answer = state.get("answer")
        if status == "won" and answer:
            tail = f" Solved! The answer was {answer}."
        elif status == "lost" and answer:
            tail = f" Out of guesses. The answer was {answer}."
        return " ".join([head, *summaries]) + tail

    def state(self) -> Dict[str, Any]:
        return {
            "game": self.id,
            "status": self.status,
            "word_len": self.word_len,
            "max_rows": MAX_ROWS,
            "neuro_word": self.neuro_word,
            "rows": [list(r) for r in self.rows],
            "statuses": [list(s) if s else None for s in self.statuses],
            "current_row": self.row,
            "guesses_used": self.guesses_used,
            "keyboard": dict(self.keyboard),
            "answer": self.answer if self.done else None,  # 结束时才揭晓，别从这条流里漏答案
            "flash": self.flash,
        }
