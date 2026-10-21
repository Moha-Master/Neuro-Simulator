"""Lichess Bot API 客户端（在线 Chess 游戏的状态来源）。

只负责 HTTP 与 NDJSON 流，不保存棋局语义；棋盘合法性、FEN、合法着法
等由 `games/chess.py` 用本地镜像重建。这样 stream 仍然是画面与工具层的
唯一权威出口，Lichess 只是外部对局服务器。
"""

import asyncio
import json
import logging
from typing import Any, AsyncIterator, Optional

import httpx

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://lichess.org"
_USER_AGENT = "neuro-simulator/0.7 (+https://github.com/Moha-Master/Neuro-Simulator)"


class LichessError(RuntimeError):
    """面向游戏层的 Lichess 调用失败：转成 GameError 的文本。"""


class LichessClient:
    def __init__(self, token: str, base_url: str = DEFAULT_BASE_URL) -> None:
        if not token:
            raise LichessError("Lichess token is not configured. Set stream.chess.token.")
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(30.0),
            headers={
                "Authorization": f"Bearer {token}",
                "User-Agent": _USER_AGENT,
                "Accept": "application/json",
            },
        )
        self._account: dict[str, Any] | None = None

    async def aclose(self) -> None:
        await self._client.aclose()

    async def account(self) -> dict[str, Any]:
        """当前 token 所属账号（含 username / title）。用于识别对局里哪一边是自己。"""
        if self._account is None:
            resp = await self._client.get("/api/account")
            if resp.status_code != 200:
                detail = resp.text.strip() or f"HTTP {resp.status_code}"
                raise LichessError(f"Cannot read Lichess account: {detail}")
            self._account = resp.json()
        return self._account

    async def challenge_ai(
        self,
        *,
        level: int,
        clock_limit: int,
        clock_increment: int,
        color: str = "white",
        variant: str = "standard",
    ) -> dict[str, Any]:
        data = {
            "level": str(max(1, min(level, 8))),
            "clock.limit": str(clock_limit),
            "clock.increment": str(clock_increment),
            "color": color,
            "variant": variant,
        }
        resp = await self._post("/api/challenge/ai", data=data, ok=(201,))
        return resp.json()

    async def move(self, game_id: str, uci: str, *, offering_draw: bool = False) -> None:
        await self._post(
            f"/api/bot/game/{game_id}/move/{uci}",
            params={"offeringDraw": "true" if offering_draw else "false"},
            ok=(200, 201),
        )

    async def resign(self, game_id: str) -> None:
        await self._post(f"/api/bot/game/{game_id}/resign", ok=(200, 201))

    async def abort(self, game_id: str) -> None:
        await self._post(f"/api/bot/game/{game_id}/abort", ok=(200, 201))

    async def stream_game(self, game_id: str) -> AsyncIterator[dict[str, Any]]:
        """订阅单局 NDJSON 流。Lichess 会周期性发送空行 keep-alive。"""
        try:
            async with self._client.stream(
                "GET",
                f"/api/bot/game/stream/{game_id}",
                timeout=httpx.Timeout(30.0, read=None),
            ) as resp:
                if resp.status_code != 200:
                    await resp.aread()
                    detail = resp.text.strip() or f"HTTP {resp.status_code}"
                    raise LichessError(f"Cannot open Lichess game stream: {detail}")
                async for line in resp.aiter_lines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        logger.warning("忽略 Lichess 游戏流中的非 JSON 行: %r", line[:120])
        except httpx.HTTPError as e:
            raise LichessError(f"Lichess game stream failed: {type(e).__name__}: {e}") from e

    async def _post(
        self,
        path: str,
        *,
        data: Optional[dict[str, str]] = None,
        params: Optional[dict[str, str]] = None,
        ok: tuple[int, ...] = (200,),
    ) -> httpx.Response:
        try:
            resp = await self._client.post(path, data=data, params=params)
        except httpx.HTTPError as e:
            raise LichessError(f"Lichess API request failed: {type(e).__name__}: {e}") from e
        if resp.status_code not in ok:
            try:
                detail = str(resp.json().get("error") or resp.text)
            except ValueError:
                detail = resp.text
            raise LichessError(f"Lichess API {path} failed ({resp.status_code}): {detail.strip()}")
        return resp

    @staticmethod
    def sleep_backoff(attempt: int) -> float:
        return min(10.0, 1.0 * (2 ** max(0, attempt - 1)))

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)
