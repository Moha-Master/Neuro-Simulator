"""在线 Chess：Neuro 通过 Lichess Bot API 对战 Lichess AI。

分工与 Wordle 一致：stream 是工具层与画面层的权威出口。区别是棋局权威
在 lichess.org，stream 只维护一份本地镜像，用来给 LLM 生成可读报表和给画面
广播快照。Lichess 游戏流会重放全量 moves，所以断线重连后也能恢复局面。
"""

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

from .. import settings as stream_settings
from ..lichess import LichessClient, LichessError
from .base import Game, GameError, register

try:
    import esca
except ImportError:  # pragma: no cover - optional dependency
    esca = None

logger = logging.getLogger(__name__)

START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
_FILES = "abcdefgh"
_RANKS = range(8, 0, -1)
_END_STATUSES = {
    "aborted",
    "mate",
    "resign",
    "stalemate",
    "timeout",
    "draw",
    "outoftime",
    "cheat",
    "noStart",
    "unknownFinish",
    "variantEnd",
    "insufficientMaterialClaim",
}
_RESULT_BY_WINNER = {"white": "1-0", "black": "0-1", None: "*", "": "*"}


def _format_clock(ms: Optional[int]) -> str:
    if ms is None:
        return "--:--"
    total = max(0, int(ms // 1000))
    return f"{total // 60:02d}:{total % 60:02d}"


@register
class ChessGame(Game):
    id = "chess"
    label = "Chess"
    scene_id = "play_chess"
    element_id = "game_chess"

    def __init__(self, hub: Any) -> None:
        super().__init__(hub)
        if esca is None:
            raise GameError("Chess needs the optional dependency 'esca'. Install it with: pip install esca")
        self.client: Optional[LichessClient] = None
        self.game_id: Optional[str] = None
        self.url: Optional[str] = None
        self.my_color: str = "white"
        self.color_known: bool = False
        self.bot_username: Optional[str] = None
        self.initial_fen: str = START_FEN
        self.moves: List[str] = []
        self.board = esca.Game()
        self.status: str = "idle"
        self.lichess_status: str = "created"
        self.winner: Optional[str] = None
        self.result: str = "*"
        self.last_move: Optional[Dict[str, str]] = None
        self.error: Optional[str] = None
        self.white_name = "Neuro"
        self.black_name = "Lichess AI"
        self.clock = {"white_ms": None, "black_ms": None, "white_inc": 0, "black_inc": 0}
        self._task: Optional[asyncio.Task] = None
        self._event = asyncio.Event()
        self._closing = False
        self._flash_seq = 0
        self.flash: Optional[Dict[str, Any]] = None

    # ---------- 生命周期 ----------

    async def _bot_username(self) -> Optional[str]:
        """取当前 token 的账号名，用来在 gameFull 里认出哪一侧是自己。"""
        if not self.client:
            return None
        try:
            account = await self.client.account()
        except LichessError as e:
            logger.warning("cannot read Lichess account: %s", e)
            return None
        name = account.get("username") or account.get("id")
        return str(name) if name else None

    async def start(self) -> str:
        s = stream_settings.load().chess
        if not s.enabled:
            raise GameError("Chess is disabled in stream.chess.enabled.")
        if not s.token:
            raise GameError("Chess is not configured: set stream.chess.token to a Lichess bot token.")

        self.client = LichessClient(s.token, s.base_url)
        try:
            data = await self.client.challenge_ai(
                level=s.ai_level,
                clock_limit=s.clock_limit,
                clock_increment=s.clock_increment,
                color=s.color,
            )
        except LichessError as e:
            await self.client.aclose()
            self.client = None
            raise GameError(str(e)) from e

        self.game_id = str(data.get("id") or "")
        self.url = str(data.get("url") or f"{s.base_url}/{self.game_id}")
        # 注意：challenge 响应里的 `player` 永远是 "white"（= 先走的一方），
        # **不代表**我们抽到的颜色。颜色只能从 gameFull 里认（见 _apply_game_full）。
        # 这里只把"显式指定 white/black"当作临时值，random 一律等 gameFull 确认。
        self.my_color = s.color if s.color in ("white", "black") else "white"
        self.color_known = s.color in ("white", "black")
        self.bot_username = await self._bot_username()
        self.initial_fen = START_FEN
        self.moves = []
        self.board = esca.Game()
        self.status = "playing"
        self.lichess_status = "started"
        self.error = None
        self._closing = False
        self._task = asyncio.create_task(self._consume_stream(), name=f"lichess-stream-{self.game_id}")
        # 等 gameFull 确认我方颜色；若 Neuro 抽到黑方，还要等 Lichess AI 先走第一步，
        # 这样 start 报表里的合法着法才是轮到我方时的。
        await self._wait_for(
            lambda: (
                self.error
                or self.status != "playing"
                or (self.color_known and self.turn == self.my_color)
            ),
            timeout=8.0,
        )
        self._set_flash("info", f"Online Chess started on Lichess ({self.game_id})")
        return (
            f"Online Chess started on Lichess against the Lichess AI (level {s.ai_level}). "
            f"You are {self.my_color}. Game URL: {self.url}. "
            "Call game_chess_move with UCI like e2e4 or SAN like Nf3."
        )

    async def act(self, action: str, args: Dict[str, Any]) -> str:
        if action == "move":
            return await self._move(str(args.get("move") or ""))
        if action == "state":
            return self.describe()
        if action == "resign":
            return await self._resign()
        if action == "restart":
            await self._end_remote_game(resign=True)
            await self._stop_stream()
            if self.client:
                await self.client.aclose()
                self.client = None
            return await self.start()
        raise GameError(f"Unknown chess action {action!r}.")

    def state(self) -> Dict[str, Any]:
        legal = self._legal_uci() if self.status == "playing" else []
        return {
            "game": self.id,
            "mode": "lichess",
            "status": self.status,
            "lichess_status": self.lichess_status,
            "game_id": self.game_id,
            "url": self.url,
            "fen": self.board.position.fen,
            "turn": self.board.position.side_to_move,
            "orientation": self.my_color,
            "color_known": self.color_known,
            "in_check": self.status == "playing" and bool(getattr(self.board.position, "in_check", False)),
            "my_color": self.my_color,
            "players": {"white": self.white_name, "black": self.black_name},
            "clock": {
                **self.clock,
                "white": _format_clock(self.clock.get("white_ms")),
                "black": _format_clock(self.clock.get("black_ms")),
            },
            "board": self._board_array(),
            "legal_moves": legal,
            "legal_move_count": len(legal),
            "last_move": self.last_move,
            "moves": list(self.moves),
            "result": self.result,
            "winner": self.winner,
            "error": self.error,
            "flash": self.flash,
        }

    async def exit(self) -> str:
        await self._end_remote_game(resign=self.status == "playing")
        await self._stop_stream()
        if self.client:
            await self.client.aclose()
            self.client = None
        self.status = "idle"
        return "Left the online Chess game."

    # ---------- 动作 ----------

    async def _move(self, raw: str) -> str:
        if self.status != "playing" or not self.client or not self.game_id:
            return self._over_text()
        if not self.color_known:
            raise GameError(
                "Lichess has not confirmed your color yet. Call game_chess_state to sync the board first."
            )
        if self.turn != self.my_color:
            raise GameError("It is not your turn yet. Wait for the opponent reply or call game_chess_state.")
        try:
            uci = self._resolve_move(raw)
        except GameError:
            raise
        before = len(self.moves)
        try:
            await self.client.move(self.game_id, uci)
        except LichessError as e:
            raise GameError(str(e)) from e

        ok = await self._wait_for(
            lambda: self.error or self.status != "playing" or (len(self.moves) > before and self.turn == self.my_color),
            timeout=45.0,
        )
        if self.error:
            raise GameError(f"Lichess connection failed after {raw}: {self.error}")
        if not ok:
            raise GameError("Lichess did not update the game after that move. Check game_chess_state before moving again.")
        return self.describe()

    async def _resign(self) -> str:
        if self.status != "playing" or not self.client or not self.game_id:
            return self._over_text()
        try:
            await self.client.resign(self.game_id)
        except LichessError as e:
            raise GameError(str(e)) from e
        await self._wait_for(lambda: self.error or self.status != "playing", timeout=10.0)
        if self.status == "playing":
            self.status = "ended"
            self.lichess_status = "resign"
            self.winner = "black" if self.my_color == "white" else "white"
            self.result = "1-0" if self.winner == "white" else "0-1"
        return self.describe()

    # ---------- Lichess 流 ----------

    async def _consume_stream(self) -> None:
        attempt = 0
        while not self._closing and self.client and self.game_id:
            try:
                async for event in self.client.stream_game(self.game_id):
                    attempt = 0
                    self._apply_event(event)
                    self._event.set()
                    if self.status != "playing":
                        return
            except LichessError as e:
                attempt += 1
                self.error = str(e)
                self._event.set()
                logger.warning("Lichess stream error (attempt %s): %s", attempt, e)
                if self._closing or self.status != "playing":
                    return
                await asyncio.sleep(self.client.sleep_backoff(attempt))
            except asyncio.CancelledError:
                raise
            except Exception as e:  # pragma: no cover - defensive
                attempt += 1
                self.error = f"{type(e).__name__}: {e}"
                self._event.set()
                logger.exception("unexpected Lichess stream failure")
                if self._closing or self.status != "playing":
                    return
                await asyncio.sleep(self.client.sleep_backoff(attempt))

    def _apply_event(self, event: Dict[str, Any]) -> None:
        typ = event.get("type")
        if typ == "gameFull":
            self._apply_game_full(event)
        elif typ == "gameState":
            self._apply_game_state(event)

    def _apply_game_full(self, event: Dict[str, Any]) -> None:
        self.game_id = str(event.get("id") or self.game_id or "")
        fen = str(event.get("initialFen") or "startpos")
        self.initial_fen = START_FEN if fen in ("startpos", "") else fen
        white = event.get("white") or {}
        black = event.get("black") or {}
        self.white_name = self._player_name(white, "White")
        self.black_name = self._player_name(black, "Black")
        # gameFull 才是权威：AI 一侧只有 aiLevel，我方一侧有 id/name/title(BOT)。
        color = self._detect_my_color(white, black)
        if color:
            self.my_color = color
            self.color_known = True
        clock = event.get("clock") or {}
        self.clock["white_inc"] = int(clock.get("increment") or 0)
        self.clock["black_inc"] = int(clock.get("increment") or 0)
        self._apply_game_state(event.get("state") or {})

    @staticmethod
    def _player_name(player: Dict[str, Any], fallback: str) -> str:
        if "aiLevel" in player:
            return f"Stockfish level {player.get('aiLevel')}"
        return str(player.get("name") or player.get("id") or fallback)

    def _detect_my_color(self, white: Dict[str, Any], black: Dict[str, Any]) -> Optional[str]:
        """判断我方执白还是执黑。优先用账号 id 精确匹配，退化到 title==BOT。"""
        for color, player in (("white", white), ("black", black)):
            if "aiLevel" in player:
                continue  # 这一侧是 AI
            pid = player.get("id")
            if self.bot_username and pid and str(pid).lower() == self.bot_username.lower():
                return color
        for color, player in (("white", white), ("black", black)):
            if "aiLevel" in player:
                continue
            if str(player.get("title") or "").upper() == "BOT":
                return color
        return None

    def _apply_game_state(self, state: Dict[str, Any]) -> None:
        self.error = None
        moves = str(state.get("moves") or "")
        self._set_moves(moves)
        self.clock["white_ms"] = state.get("wtime")
        self.clock["black_ms"] = state.get("btime")
        self.clock["white_inc"] = state.get("winc", self.clock.get("white_inc", 0))
        self.clock["black_inc"] = state.get("binc", self.clock.get("black_inc", 0))
        status = str(state.get("status") or self.lichess_status)
        self.lichess_status = status
        winner = state.get("winner")
        self.winner = str(winner) if winner else None
        if status in _END_STATUSES:
            self.status = "ended"
            self.result = _RESULT_BY_WINNER.get(self.winner, "*")
            if status == "draw" or self.winner is None:
                self.result = "1/2-1/2" if status in ("draw", "stalemate") else self.result
            self._set_flash("win" if self.winner == self.my_color else "lose", self.result)
        else:
            self.status = "playing"
            self.result = "*"

    async def _stop_stream(self) -> None:
        self._closing = True
        task = self._task
        self._task = None
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._event.set()

    async def _end_remote_game(self, *, resign: bool) -> None:
        if not resign or not self.client or not self.game_id or self.status != "playing":
            return
        try:
            await self.client.resign(self.game_id)
        except LichessError as e:
            logger.warning("failed to resign Lichess game %s: %s", self.game_id, e)

    async def _wait_for(self, predicate, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while not predicate():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            self._event.clear()
            try:
                await asyncio.wait_for(self._event.wait(), timeout=min(remaining, 1.0))
            except asyncio.TimeoutError:
                pass
        return True

    # ---------- 棋盘镜像 ----------

    def _set_moves(self, moves_text: str) -> None:
        self.moves = [m for m in moves_text.split() if m]
        self._replay()

    def _replay(self) -> None:
        self.board = esca.Game.from_fen(self.initial_fen)
        self.last_move = None
        self.error = None
        for uci in self.moves:
            try:
                mv = self.board.position.parse_uci(uci)
                san = self.board.move_to_san(mv)
                self.board.play(mv)
                self.last_move = {
                    "uci": uci,
                    "san": san,
                    "from": str(mv.origin),
                    "to": str(mv.destination),
                }
            except Exception as e:
                self.error = f"Failed to replay Lichess move {uci!r}: {e}"
                logger.warning(self.error)
                break

    def _resolve_move(self, raw: str) -> str:
        text = raw.strip()
        if not text:
            raise GameError("No move given. Use UCI like e2e4 or SAN like Nf3.")
        legal = self._legal_uci()
        if text.isdigit():
            idx = int(text) - 1
            if 0 <= idx < len(legal):
                return legal[idx]
            raise GameError(f"No legal move #{text}. Legal moves are: {' '.join(legal)}")
        try:
            mv = self.board.position.parse_uci(text)
            return self.board.move_to_uci(mv)
        except Exception:
            pass
        try:
            mv = self.board.position.parse_san(text)
            return self.board.move_to_uci(mv)
        except Exception:
            pass
        raise GameError(f"Illegal move {text!r}. Legal moves are: {' '.join(legal)}")

    def _legal_uci(self) -> List[str]:
        if self.status != "playing" or not self.color_known or self.turn != self.my_color:
            return []
        try:
            return [self.board.move_to_uci(m) for m in self.board.legal_moves()]
        except Exception:
            return []

    def _board_array(self) -> List[List[str]]:
        rows = []
        for rank in _RANKS:
            row = []
            for file in _FILES:
                piece = self.board.position.piece_at(f"{file}{rank}")
                row.append(piece or ".")
            rows.append(row)
        return rows

    @property
    def turn(self) -> str:
        return str(self.board.position.side_to_move)

    # ---------- 文本 ----------

    def describe(self) -> str:
        if not self.game_id:
            return "Chess is idle. Start an online game with game_chess_start."
        lines = [
            f"Online Chess on Lichess ({self.game_id}).",
            f"You are {self.my_color}. White: {self.white_name}. Black: {self.black_name}.",
        ]
        if self.last_move:
            lines.append(f"Last move: {self.last_move['san']} ({self.last_move['uci']}).")
        if self.status == "playing":
            if self.turn == self.my_color:
                lines.append("It is YOUR turn to move.")
            else:
                lines.append(
                    f"It is the opponent's turn ({self.turn}). Do not move yet; "
                    "wait until game_chess_state says it is your turn."
                )
            if self.board.position.in_check:
                lines.append("Check: yes.")
            lines.append(f"Clock: White {_format_clock(self.clock.get('white_ms'))}, Black {_format_clock(self.clock.get('black_ms'))}.")
        else:
            lines.append(f"Game over: {self.lichess_status}. Result: {self.result}.")
        lines.append("")
        lines.append("Board:")
        lines.extend(self._board_text_lines())
        lines.append("")
        lines.append(f"FEN: {self.board.position.fen}")
        legal = self._legal_uci()
        if legal:
            lines.append(f"Legal moves ({len(legal)}): {' '.join(legal)}")
        return "\n".join(lines)

    def _board_text_lines(self) -> List[str]:
        lines = []
        for rank, row in zip(_RANKS, self._board_array()):
            lines.append(f"{rank} " + " ".join(row))
        lines.append("  " + " ".join(_FILES))
        return lines

    def _over_text(self) -> str:
        return f"This Chess game is over: {self.result}. Start another game or exit to stop playing."

    def _set_flash(self, kind: str, text: str) -> None:
        self._flash_seq += 1
        self.flash = {"kind": kind, "text": text, "seq": self._flash_seq}
