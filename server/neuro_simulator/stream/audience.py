"""频道观众数：后端缓存 + 后台刷新（前端不再直连外部接口）。

TwitchTracker 提供的是**平均观众**（不是实时在线数），所以 `viewers_source`
固定写 `twitchtracker_avg`，前端 tooltip 也照这个口径说明"非实时"。

I/O 归这里，状态投影只读缓存：`snapshot()` 同步返回最近一次观测结果，
`/stream/status` 的 provider 直接用它，绝不 await / 发 HTTP。

拿不到时 `viewers = null`，**不编造数字**；观众数是频道级事实，未开播也照常刷新。
"""

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)

ENDPOINT = "https://twitchtracker.com/api/channels/summary/vedal987"
# 平均观众几个小时才更新一次，没必要勤拉；失败则一分钟后再试。
REFRESH_S = 5 * 3600
RETRY_S = 60
TIMEOUT_S = 8


class AudienceTracker:
    def __init__(self) -> None:
        self._viewers: Optional[float] = None
        self._updated_at: Optional[str] = None
        self._error: Optional[str] = None
        self._task: Optional[asyncio.Task] = None

    # ---------- 刷新（唯一有 I/O 的地方） ----------

    async def refresh(self) -> bool:
        """拉一次摘要；成功返回 True。失败只记原因，不抛。"""
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
                r = await client.get(ENDPOINT)
                r.raise_for_status()
                data = r.json()
            avg = data.get("avg_viewers") if isinstance(data, dict) else None
            # bool 是 int 的子类，别被 true/false 骗过去
            if isinstance(avg, bool) or not isinstance(avg, (int, float)):
                raise ValueError(f"avg_viewers 不是数字: {avg!r}")
            self._viewers = avg
            self._error = None
            self._updated_at = _local_iso()
            return True
        except Exception as e:  # 网络/JSON/载荷都归一类：保持上次的"不可用"
            self._viewers = None
            self._error = f"{type(e).__name__}: {e}"
            self._updated_at = _local_iso()
            logger.warning("TwitchTracker 观众数刷新失败: %s", self._error)
            return False

    async def run(self) -> None:
        """后台循环：成功 → REFRESH_S；失败 → RETRY_S。shutdown() 时取消。"""
        try:
            while True:
                ok = await self.refresh()
                await asyncio.sleep(REFRESH_S if ok else RETRY_S)
        except asyncio.CancelledError:
            raise

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run(), name="audience-refresh")

    async def shutdown(self) -> None:
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    # ---------- 状态投影（同步、无 I/O） ----------

    def snapshot(self) -> Dict[str, Any]:
        return {
            "viewers": self._viewers,
            "viewers_source": "twitchtracker_avg",
            "updated_at": self._updated_at,
            "error": self._error,
        }


def _local_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")
