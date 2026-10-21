"""游戏工具：把 Neuro 的工具调用转发到 stream 的本地游戏服务。

分工与 `model_spin` / `model_zoom` 一致：**neuro 只发起调用并转述结果**，
规则判定、状态与画面广播都在 stream 侧（`/game/{game}/action`）。
所以这里不持有游戏状态，stream 没起时工具就返回一句人话，不抛异常打断对话。

工具名统一 `game_<游戏>_<动作>`。当前已接入 Wordle 与在线 Chess（Lichess Bot API）。
neuro_sama 的 ToolRegistry 会按 stream 当前活跃游戏过滤工具定义，避免多个游戏的
工具同时塞进小模型上下文。
"""

import time
from typing import Any, Dict

import httpx

from ..config import get_config, module_url
from .tools import Tool

STREAM_MODULE = "stream"
_REQUEST_TIMEOUT = 120.0  # 等 stream 播完语音再执行 game 动作，留足 TTS 时间


async def _act(game: str, action: str, args: Dict[str, Any] | None = None) -> str:
    """打一次游戏动作，永远返回给模型读的文本。"""
    base = module_url(get_config(), STREAM_MODULE)
    if not base:
        return f"Cannot {action} {game}: the stream service is not configured, so there is no game on air."
    try:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
            resp = await client.post(f"{base}/game/{game}/action", json={"action": action, "args": args or {}})
    except httpx.HTTPError as e:
        return f"The stream service is unreachable ({type(e).__name__}), so the {game} board is not responding."
    try:
        data = resp.json()
    except ValueError:
        data = {}
    if resp.status_code >= 400:
        # 400/409 是"这一步不能这么做"（没开局 / 不是词 / 行已满），直接把话给模型
        return str(data.get("detail") or f"The {game} service rejected that ({resp.status_code}).")
    return str(data.get("result_text") or "(no result)")


class StreamGameProvider:
    """查询 stream 当前活跃游戏，供 ToolRegistry 动态过滤工具定义。"""

    def __init__(self, ttl: float = 1.0, negative_ttl: float = 0.25) -> None:
        self.ttl = ttl
        self.negative_ttl = negative_ttl
        self._value: str | None = None
        self._expires_at = 0.0
        self._known = False

    def invalidate(self) -> None:
        """游戏工具执行完后强制下一次重新查 stream，避免 1s 缓存吞掉新出现的动作。"""
        self._expires_at = 0.0
        self._known = False

    async def active_game(self) -> str | None:
        now = time.monotonic()
        if self._known and now < self._expires_at:
            return self._value
        base = module_url(get_config(), STREAM_MODULE)
        if not base:
            self._value = None
            self._expires_at = now + self.negative_ttl
            self._known = True
            return None
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.get(f"{base}/game/state")
            value = None
            if resp.status_code < 400:
                data = resp.json()
                active = data.get("active")
                value = str(active) if active else None
        except (httpx.HTTPError, ValueError):
            value = None
        self._value = value
        self._expires_at = time.monotonic() + (self.ttl if value else self.negative_ttl)
        self._known = True
        return value


def build_game_tools() -> list[Tool]:
    def tool(
        name: str,
        description: str,
        parameters: dict,
        action: str,
        game: str = "wordle",
        phase: str = "active",
    ) -> Tool:
        async def handler(**args: Any) -> str:
            return await _act(game, action, args)

        return Tool(name, description, parameters, handler, game=game, phase=phase)

    no_args = {"type": "object", "properties": {}, "required": []}

    letters = {
        "type": "object",
        "properties": {
            "letters": {
                "type": "string",
                "description": "The letters to press, e.g. 'crane'. Letters past the word length are ignored.",
            },
        },
        "required": ["letters"],
    }
    delete_args = {
        "type": "object",
        "properties": {
            "count": {
                "type": "integer",
                "description": "How many letters to erase from the end of the row (default 1).",
            },
        },
        "required": [],
    }
    move_args = {
        "type": "object",
        "properties": {
            "move": {
                "type": "string",
                "description": "Chess move in UCI (e2e4, e7e8q, e1g1) or SAN (e4, Nf3, O-O).",
            },
        },
        "required": ["move"],
    }

    return [
        tool(
            "game_wordle_start",
            "Start a fresh game of Wordle on stream: the board and on-screen keyboard appear and you "
            "get six guesses at a hidden word. Use it when you decide to play Wordle with chat.",
            no_args,
            "start",
            phase="start",
        ),
        tool(
            "game_wordle_type",
            "Type letters into the row you are currently filling in Wordle. They show up on the board "
            "and you press the matching on-screen keys.",
            letters,
            "type",
        ),
        tool(
            "game_wordle_delete",
            "Erase the last letter (or more) of the Wordle row you are typing, like the backspace key.",
            delete_args,
            "delete",
        ),
        tool(
            "game_wordle_submit",
            "Submit the current Wordle row as a guess (the Enter key). Tells you each letter's colour "
            "(green = right place, yellow = right letter wrong place, grey = not in the word), how many "
            "guesses are left, and whether you won. The row must be a real word of the right length.",
            no_args,
            "submit",
        ),
        tool(
            "game_wordle_state",
            "Look at the Wordle board again: past guesses with their colours, the row you are typing, "
            "which letters are already ruled out, and how many guesses are left.",
            no_args,
            "state",
        ),
        tool(
            "game_wordle_restart",
            "Start another round of Wordle on the same board (new hidden word, empty rows).",
            no_args,
            "restart",
        ),
        tool(
            "game_wordle_exit",
            "Stop playing Wordle: leave the game board and go back to the normal stream scene.",
            no_args,
            "exit",
        ),
        tool(
            "game_chess_start",
            "Start an online Chess game on Lichess against the Lichess AI. The game appears on Lichess "
            "and you get a board report with legal moves. Use it when chat convinces you to play chess.",
            no_args,
            "start",
            game="chess",
            phase="start",
        ),
        tool(
            "game_chess_move",
            "Play one Chess move in the active Lichess game. Prefer UCI notation like e2e4, e7e8q, or "
            "e1g1; SAN like Nf3 is also accepted. The result shows the board and your legal replies.",
            move_args,
            "move",
            game="chess",
        ),
        tool(
            "game_chess_state",
            "Look at the current Chess board again: FEN, side to move, clock, last move, result if any, "
            "and the legal moves you can choose from.",
            no_args,
            "state",
            game="chess",
        ),
        tool(
            "game_chess_resign",
            "Resign the current online Chess game. Use it when the position is hopeless or you want to "
            "end the game dramatically.",
            no_args,
            "resign",
            game="chess",
        ),
        tool(
            "game_chess_restart",
            "Resign the current online Chess game and start another Lichess AI game.",
            no_args,
            "restart",
            game="chess",
        ),
        tool(
            "game_chess_exit",
            "Stop playing Chess: leave the online game scene and go back to the normal stream scene.",
            no_args,
            "exit",
            game="chess",
        ),
    ]
