"""游戏工具：把 Neuro 的工具调用转发到 stream 的本地游戏服务。

分工与 `model_spin` / `model_zoom` 一致：**neuro 只发起调用并转述结果**，
规则判定、状态与画面广播都在 stream 侧（`/game/{game}/action`）。
所以这里不持有游戏状态，stream 没起时工具就返回一句人话，不抛异常打断对话。

工具名统一 `game_<游戏>_<动作>`；将来接第二个游戏就是再加一组同前缀的工具
（或按规划换成动态工具系统，只暴露当前活跃游戏的一组）。
"""

from typing import Any, Dict

import httpx

from ..config import get_config, module_url
from .tools import Tool

STREAM_MODULE = "stream"
_REQUEST_TIMEOUT = 15.0


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


def build_game_tools() -> list[Tool]:
    def tool(name: str, description: str, parameters: dict, action: str, game: str = "wordle") -> Tool:
        async def handler(**args: Any) -> str:
            return await _act(game, action, args)

        return Tool(name, description, parameters, handler)

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

    return [
        tool(
            "game_wordle_start",
            "Start a fresh game of Wordle on stream: the board and on-screen keyboard appear and you "
            "get six guesses at a hidden word. Use it when you decide to play Wordle with chat.",
            no_args,
            "start",
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
    ]
