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


_PIECE_NAMES = {
    "p": "pawn",
    "n": "knight",
    "b": "bishop",
    "r": "rook",
    "q": "queen",
    "k": "king",
}

_PIECE_VALUES = {"p": 1, "n": 3, "b": 3, "r": 5, "q": 9, "k": 0}

_STATUS_TEXT = {
    "mate": "checkmate",
    "resign": "resignation",
    "outoftime": "timeout",
    "timeout": "timeout",
    "draw": "draw",
    "stalemate": "stalemate",
    "aborted": "aborted",
    "cheat": "cheat detected",
}


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
        # esca 的本地事实快照：只在重放棋局后算一次（facts() 0.2~0.4ms，状态 provider
        # 是同步无 I/O 的，绝不能在那里现算）。viewer() 只读这份缓存。
        self._facts: Optional[Dict[str, Any]] = None
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
        self._facts = None
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
        self._set_flash("info", "Online Chess started")
        header = [
            f"Online Chess started on Lichess against {self._opponent_name()}.",
            "Copy one move from the 'Your legal moves' list below and call game_chess_move with it.",
        ]
        return self._state_text(header)

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

    def narrative(self, state: Dict[str, Any]) -> str:
        """一句叙事：走子方/第几手/是否被将军；**不出现** FEN、棋盘或合法着法。"""
        status = str(state.get("status") or "")
        if status not in ("playing", "ended"):
            return "waiting for the Lichess game to start"
        if status == "ended":
            result = str(state.get("result") or "").strip()
            return f"game over{', ' + result if result else ''}"
        side = "White" if state.get("turn") == "white" else "Black"
        moves = len(state.get("moves") or [])
        check = "in check" if state.get("in_check") else "not in check"
        return f"{side} to move, {moves} moves in, {check}"

    # ---------- 观众解说（chatbot 用；不查 Stockfish / Lichess eval） ----------

    def viewer(self, state: Dict[str, Any]) -> str:
        """面向观众的大众化英文解说：只读本地缓存的事实，不产生任何 I/O。

        禁止 SAN/UCI（`Nf3`、`O-O`、`e2e4`）。`_facts` 缺失时退回 narrative，
        宁可少说，也不在状态 provider 里现算 `facts()`。
        """
        status = str(state.get("status") or "")
        if status not in ("playing", "ended"):
            return self.narrative(state)
        if self._facts is None:
            return self.narrative(state)

        sentences = [f"Chess vs {self._opponent_name()}."]
        last_move = state.get("last_move") or {}
        move_sentence = self._viewer_move(last_move)
        if move_sentence:
            sentences.append(move_sentence)

        material = self._material_sentence(state)
        if status == "ended":
            sentences.extend(self._result_sentences(state))
            if material:
                sentences.append(material)
            return " ".join(sentences[:5])

        turn = str(state.get("turn") or self.turn)
        turn_name = self._named_side(turn)
        if state.get("in_check"):
            sentences.append(f"It is {turn_name}'s turn and {turn_name} is in check.")
        else:
            sentences.append(f"It is {turn_name}'s turn.")
        if material:
            sentences.append(material)

        clocks = self._clock_sentences(state)
        # 战术事实最多 2 条，但要给低时钟留位置（总数 3~5 句）。
        budget = max(0, min(2, 5 - len(sentences) - len(clocks)))
        sentences.extend(self._tactics_sentences(state)[:budget])
        sentences.extend(clocks)
        return " ".join(sentences[:5])

    def _named_side(self, color: Optional[str]) -> str:
        return "Neuro" if color == self.my_color else self._opponent_name()

    @staticmethod
    def _piece_owner(piece: str) -> str:
        return "white" if str(piece).isupper() else "black"

    def _viewer_move(self, info: Dict[str, Any]) -> str:
        """最后一步的观众版措辞（不含 SAN/UCI）。"""
        if not info:
            return ""
        color = str(info.get("by") or "")
        mover = self._named_side(color)
        pronoun = "her" if color == self.my_color else "its"
        san = str(info.get("san") or "")
        target = info.get("to") or ""
        captured = info.get("captured")

        if san.startswith("O-O-O"):
            return f"{mover} castled queenside."
        if san.startswith("O-O"):
            return f"{mover} castled kingside."
        if "=" in san:
            promoted = san.split("=")[-1].lstrip("(").rstrip("+#!")[:1].lower()
            name = _PIECE_NAMES.get(promoted, "queen")
            return f"{mover} promoted {pronoun} pawn to a {name} on {target}."
        if captured:
            victim = self._named_side(self._piece_owner(str(captured)))
            name = _PIECE_NAMES.get(str(captured).lower(), "piece")
            mover_piece = _PIECE_NAMES.get(str(info.get("piece") or "").lower(), "piece")
            return f"{mover}'s {mover_piece} captured {victim}'s {name} on {target}."

        name = _PIECE_NAMES.get(str(info.get("piece") or "").lower(), "piece")
        return f"{mover} moved {pronoun} {name} to {target}."

    def _material_snapshot(self) -> Optional[Dict[str, int]]:
        """facts.material.value 的 1:1 投影；只读缓存，不碰 esca。"""
        values = ((self._facts or {}).get("material") or {}).get("value") or {}
        if not values:
            return None
        return {
            "white": int(values.get("white") or 0),
            "black": int(values.get("black") or 0),
        }

    def _material_sentence(self, state: Dict[str, Any]) -> str:
        values = state.get("material") or {}
        if not values:
            return ""
        white = int(values.get("white") or 0)
        black = int(values.get("black") or 0)
        diff = (white - black) if self.my_color == "white" else (black - white)
        if diff == 0:
            return "Material is even."
        parts = self._material_parts(abs(diff))
        if not parts:
            return ""
        amount = self._join_parts(parts)
        if diff > 0:
            return f"Neuro is up {amount}."
        return f"Neuro is down {amount}."

    @staticmethod
    def _material_parts(value: int) -> List[str]:
        """把子力差拆成 9/5/3/1 的组合（皇后/车/轻子/兵）。"""
        parts: List[str] = []
        for worth, single, plural in ((9, "queen", "queens"), (5, "rook", "rooks"),
                                      (3, "piece", "pieces"), (1, "pawn", "pawns")):
            count, value = divmod(value, worth)
            if count == 1:
                parts.append(f"a {single}")
            elif count > 1:
                parts.append(f"{count} {plural}")
        return parts

    @staticmethod
    def _join_parts(parts: List[str]) -> str:
        if len(parts) == 1:
            return parts[0]
        if len(parts) == 2:
            return f"{parts[0]} and {parts[1]}"
        return ", ".join(parts[:-1]) + f" and {parts[-1]}"

    def _result_sentences(self, state: Dict[str, Any]) -> List[str]:
        """结束状态用 Lichess 的 status/winner（esca 的 outcome 不知道 resign/timeout）。"""
        lichess = str(state.get("lichess_status") or "")
        winner = state.get("winner")
        if lichess == "mate":
            if winner == self.my_color:
                return ["Checkmate — Neuro won."]
            if winner:
                return [f"Checkmate — {self._opponent_name()} won."]
            return ["Checkmate — draw."]
        if lichess == "stalemate":
            return ["Stalemate — draw."]
        if lichess == "aborted":
            return ["The game was aborted."]
        if winner:
            label = _STATUS_TEXT.get(lichess, lichess) or "game over"
            return [f"{label.capitalize()} — {self._named_side(str(winner))} won."]
        return ["Draw."]

    def _tactics_sentences(self, state: Dict[str, Any]) -> List[str]:
        """本地事实里的战术提示，按优先级最多两条。"""
        facts = self._facts or {}
        tactics = facts.get("tactics") or {}
        turn = str(state.get("turn") or self.turn)
        if turn not in ("white", "black"):
            return []
        opponent = "black" if turn == "white" else "white"
        turn_name = self._named_side(turn)
        opponent_name = self._named_side(opponent)
        candidates: List[str] = []

        mate_in_1 = tactics.get("mate_in_1") or {}
        if mate_in_1.get(turn):
            candidates.append(f"{turn_name} can checkmate in one move.")
        elif mate_in_1.get(opponent):
            candidates.append(f"{opponent_name} is threatening checkmate in one move.")
        else:
            hanging = (facts.get("attacks") or {}).get("hanging") or {}
            pick = self._hanging_pick(hanging, opponent) or self._hanging_pick(hanging, turn)
            if pick:
                color, square, piece = pick
                name = _PIECE_NAMES.get(piece, "piece")
                candidates.append(
                    f"{self._named_side(color)}'s {name} on {square} is undefended."
                )
            else:
                winning = tactics.get("winning_capture_available") or {}
                if winning.get(turn):
                    gain = int((tactics.get("winning_capture_max_gain") or {}).get(turn) or 0)
                    candidates.append(
                        f"{turn_name} can win {self._value_phrase(gain)} for free."
                    )

        # 开局双方 back_rank_risk 都是 True，单看这个标志只会制造噪音：
        # 必须对方确实存在底线杀威胁时才提，且报的是受害方。
        risk = (facts.get("king") or {}).get("back_rank_risk") or {}
        threats = (facts.get("tactics") or {}).get("back_rank_mate_threat") or {}
        for victim in (opponent, turn):
            attacker = "black" if victim == "white" else "white"
            if risk.get(victim) and threats.get(attacker):
                candidates.append(f"{self._named_side(victim)}'s back rank is weak.")
                break

        passers = (facts.get("pawns") or {}).get("passer_unstoppable") or {}
        for color in (turn, opponent):
            if passers.get(color):
                candidates.append(
                    f"{self._named_side(color)} has a pawn that cannot be stopped."
                )
                break

        return candidates[:2]

    def _hanging_pick(
        self, hanging: Dict[str, Any], color: str
    ) -> Optional[tuple[str, str, str]]:
        """最高价值的悬子；返回 (颜色, 格子, 兵种字母)。"""
        best: Optional[tuple[str, str, str]] = None
        best_value = 0
        for square in hanging.get(color) or []:
            piece = self._piece_at_from_facts(str(square))
            if not piece:
                continue
            value = _PIECE_VALUES.get(piece, 0)
            if value > best_value:
                best_value = value
                best = (color, str(square), piece)
        return best

    def _piece_at_from_facts(self, square: str) -> str:
        """从缓存的 placement 反查格子上的兵种字母（小写；不调用 facts()）。"""
        placement = (self._facts or {}).get("placement") or {}
        for key, piece in (
            ("pawns", "p"), ("knights", "n"), ("bishops", "b"),
            ("rooks", "r"), ("queens", "q"), ("kings", "k"),
        ):
            for squares in (placement.get(key) or {}).values():
                if square in (squares or []):
                    return piece
        return ""

    @staticmethod
    def _value_phrase(gain: int) -> str:
        if gain >= 9:
            return "a queen"
        if gain >= 5:
            return "a rook"
        if gain >= 3:
            return "a piece"
        return "a pawn"

    def _clock_sentences(self, state: Dict[str, Any]) -> List[str]:
        clock = state.get("clock") or {}
        out: List[str] = []
        for color in ("white", "black"):
            ms = clock.get(f"{color}_ms")
            if isinstance(ms, (int, float)) and ms < 30000:
                seconds = max(0, int(ms // 1000))
                out.append(
                    f"{self._named_side(color)} is low on time "
                    f"({seconds // 60}:{seconds % 60:02d})."
                )
        return out

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
            "material": self._material_snapshot(),
            "board": self._board_array(),
            "legal_moves": legal,
            "legal_moves_san": self._legal_san(),
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
                "Lichess has not confirmed your colour yet. Call game_chess_state to sync the board first."
            )
        if self.turn != self.my_color:
            raise GameError(
                f"It is {self._opponent_name()}'s turn. Wait, or call game_chess_state to refresh."
            )
        uci = self._resolve_move(raw)
        info = self._move_info(self.board.position.parse_uci(uci))
        before = len(self.moves)
        try:
            await self.client.move(self.game_id, info["uci"])
        except LichessError as e:
            raise GameError(str(e)) from e

        await self._wait_for(
            lambda: (
                self.error
                or self.status != "playing"
                or (len(self.moves) > before + 1 and self.turn == self.my_color)
            ),
            timeout=45.0,
        )
        if self.error:
            raise GameError(f"Lichess connection failed after {raw}: {self.error}")
        if len(self.moves) <= before:
            raise GameError(
                "Lichess did not accept that move. Call game_chess_state and pick one of your legal moves."
            )

        header = [self._move_line("You played", info)]
        if len(self.moves) > before + 1 and self.last_move:
            # 对手已经回棋（哪怕这一手直接把棋局终结，也要写出来）
            header.append(self._move_line(f"{self._opponent_name()} replied", self.last_move))
        elif self.status == "playing":
            header.append(f"{self._opponent_name()} has not replied yet.")
        return self._state_text(header)

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

    def _uci_of(self, mv: Any) -> str:
        """标准 UCI。esca 的易位是"王吃车"形式（e1h1），对外一律用 e1g1。"""
        uci = self.board.move_to_uci(mv)
        san = str(self.board.move_to_san(mv))
        if not san.startswith("O-O"):
            return uci
        rank = str(mv.origin)[1:] or uci[1:2]
        return f"e{rank}{'g' if san == 'O-O' else 'c'}{rank}"

    def _move_info(self, mv: Any) -> Dict[str, Any]:
        """把一个合法着法整理成报表用的结构（不改动棋盘）。"""
        pos = self.board.position
        san = str(self.board.move_to_san(mv))
        destination = str(mv.destination)
        captured = pos.piece_at(destination)
        piece = pos.piece_at(str(mv.origin))
        uci = self._uci_of(mv)
        if san.startswith("O-O"):
            # esca 用"王吃车"表示易位：mv.destination 是车格，不是王落点
            destination = uci[2:4]
            captured = None
        return {
            "uci": uci,
            "san": san,
            "from": str(mv.origin),
            "to": destination,
            "piece": piece,
            "by": str(pos.side_to_move),
            "captured": captured,
        }

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
                self.last_move = self._move_info(mv)
                self.board.play(mv)
            except Exception as e:
                self.error = f"Failed to replay Lichess move {uci!r}: {e}"
                logger.warning(self.error)
                break
        self._refresh_facts()

    def _refresh_facts(self) -> None:
        """重放后缓存 esca 的本地事实（状态 provider / viewer 都只读缓存）。"""
        if self.error:
            self._facts = None
            return
        try:
            self._facts = self.board.facts().to_dict()
        except Exception as e:
            self._facts = None
            logger.warning("failed to compute chess facts: %s: %s", type(e).__name__, e)

    def _resolve_move(self, raw: str) -> str:
        text = raw.strip().strip("`'\"").rstrip(".")
        if not text:
            raise GameError("No move given. Copy one from the 'Your legal moves' list, e.g. Nf3 or O-O.")
        legal_san = self._legal_san()
        legal_uci = self._legal_uci()
        if text.isdigit():
            idx = int(text) - 1
            if 0 <= idx < len(legal_uci):
                return legal_uci[idx]
            raise GameError(f"No legal move #{text}. Your legal moves: {' '.join(legal_san)}")
        text = text.replace("0-0-0", "O-O-O").replace("0-0", "O-O")
        try:
            mv = self.board.position.parse_uci(text)
            return self._uci_of(mv)
        except Exception:
            pass
        try:
            mv = self.board.position.parse_san(text)
            return self._uci_of(mv)
        except Exception:
            pass
        raise GameError(
            f"{text!r} is not a legal move in this position. "
            f"Your legal moves ({len(legal_san)}): {' '.join(legal_san)}"
        )

    def _legal_uci(self) -> List[str]:
        if self.status != "playing" or not self.color_known or self.turn != self.my_color:
            return []
        try:
            return [self._uci_of(m) for m in self.board.legal_moves()]
        except Exception:
            return []

    def _legal_san(self) -> List[str]:
        if self.status != "playing" or not self.color_known or self.turn != self.my_color:
            return []
        try:
            return [self.board.move_to_san(m) for m in self.board.legal_moves()]
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
        return self._state_text()

    def _opponent_name(self) -> str:
        return self.black_name if self.my_color == "white" else self.white_name

    def _capture_text(self, piece: Optional[str], square: str) -> str:
        if not piece:
            return ""
        token = str(piece)
        name = _PIECE_NAMES.get(token.lower(), "piece")
        mine = (token.isupper() and self.my_color == "white") or (
            token.islower() and self.my_color == "black"
        )
        owner = "your" if mine else ("White's" if token.isupper() else "Black's")
        return f", capturing {owner} {name} on {square}"

    def _move_line(self, prefix: str, info: Dict[str, Any]) -> str:
        return (
            f"{prefix} {info['san']}"
            f"{self._capture_text(info.get('captured'), str(info.get('to') or ''))} "
            f"({info['uci']})."
        )

    def _clock_line(self) -> str:
        other = "black" if self.my_color == "white" else "white"
        mine_ms = self.clock.get(self.my_color + "_ms")
        other_ms = self.clock.get(other + "_ms")
        return f"Clock: you {_format_clock(mine_ms)}, opponent {_format_clock(other_ms)}."

    def _result_line(self) -> str:
        status = _STATUS_TEXT.get(self.lichess_status, self.lichess_status)
        if self.winner == self.my_color:
            return f"Game over: {status}. You won ({self.result})."
        if self.winner:
            return f"Game over: {status}. You lost ({self.result})."
        return f"Game over: {status}. No winner ({self.result})."

    def _state_text(self, header: Optional[List[str]] = None) -> str:
        if not self.game_id:
            return "Chess is idle. Start an online game with game_chess_start."
        lines: List[str] = []
        if header:
            lines.extend(header)
            lines.append("")
        lines.append(f"You are {self.my_color.capitalize()}. Opponent: {self._opponent_name()}.")
        if self.my_color == "black":
            lines.append("The board is drawn from White's side; your pieces are the lowercase letters.")
        if not header and self.last_move:
            who = "You" if self.last_move.get("by") == self.my_color else self._opponent_name()
            lines.append(self._move_line(f"Last move: {who} played", self.last_move))
        if self.status == "playing":
            if self.turn == self.my_color:
                lines.append("It is your turn to move.")
            else:
                lines.append(f"It is {self._opponent_name()}'s turn. Do not move yet.")
            if self.board.position.in_check:
                lines.append(
                    "You are in check." if self.turn == self.my_color else "The opponent is in check."
                )
            lines.append(self._clock_line())
        else:
            lines.append(self._result_line())
        lines.append("")
        lines.append("Board:")
        lines.extend(self._board_text_lines())
        lines.append("")
        lines.append(f"FEN: {self.board.position.fen}")
        legal = self._legal_san()
        if legal:
            lines.append(f"Your legal moves ({len(legal)}): {' '.join(legal)}")
        return "\n".join(lines)

    def _board_text_lines(self) -> List[str]:
        lines = []
        for rank, row in zip(_RANKS, self._board_array()):
            lines.append(f"{rank} " + " ".join(row))
        lines.append("  " + " ".join(_FILES))
        return lines

    def _over_text(self) -> str:
        status = _STATUS_TEXT.get(self.lichess_status, self.lichess_status)
        return (
            f"This Chess game is over: {status} ({self.result}). "
            "Call game_chess_restart for a rematch, or game_chess_exit to leave."
        )

    def _set_flash(self, kind: str, text: str) -> None:
        self._flash_seq += 1
        self.flash = {"kind": kind, "text": text, "seq": self._flash_seq}
