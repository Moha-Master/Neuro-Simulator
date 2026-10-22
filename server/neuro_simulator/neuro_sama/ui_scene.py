"""后端脚本执行器：场景是 neuro 窗口的权威状态，`state_script` 由这里按后端时钟执行。

分工（详见 scenes.py）：

- 前端脚本 `timeline` 由各渲染器的元素引擎执行，只动 DOM；
- 后端脚本 `state_script` 由本模块执行，只能改状态（`scene.set`），
  改完广播 `scene_set`，所有渲染器（OBS 浏览器源 / dashboard 预览 / 独立窗口）
  按 `elapsed` 收敛到同一画面。

因此"开场动画放完自动回常态"不需要前端回报，也不依赖某个渲染器活着；
手动切场景（dashboard / curl / stream 开播）与自动回切走的是同一条路径。
"""

import asyncio
import contextlib
import logging
import time
from collections import deque
from typing import Any, Callable, Deque, Dict, List, Optional

from . import scenes as ui_scenes
# 异常类型直接引用（不经 ui_scenes 命名空间）：测试里替换场景数据源时，
# 类型不该跟着被换掉——except 子句求值时会炸。
from .scenes import SceneError
from .ui_state import UiState

logger = logging.getLogger(__name__)

# 循环保护：窗口期内允许的切换次数上限（数据写成 A↔B 时不至于把 CPU 转满）
LOOP_WINDOW_S = 2.0
LOOP_MAX_CHANGES = 10


class SceneLoopError(RuntimeError):
    """场景在短时间内被切换过多——通常是 state_script 写成了环。"""


class SceneRunner:
    def __init__(self, state: UiState, publish: Callable[[Dict[str, Any]], None]) -> None:
        self._state = state
        self._publish = publish
        self._task: Optional[asyncio.Task] = None
        self._changes: Deque[float] = deque()

    # ---------- 查询 ----------

    def current(self) -> Dict[str, Any]:
        return self._state.scene_snapshot()

    def scene_id(self) -> Optional[str]:
        return self._state.scene_id()

    # ---------- 启动 ----------

    def init_default(self) -> Dict[str, Any]:
        """进程启动时落到 default_scene：只写状态，不执行它的后端脚本。"""
        scene_id = ui_scenes.default_scene_id()
        return self._state.set_scene(scene_id, ui_scenes.effective_duration(scene_id))

    # ---------- 切换 ----------

    async def switch(self, scene_id: str) -> Dict[str, Any]:
        """切到某个场景（校验存在 → 广播 → 排程它的后端脚本）。"""
        ui_scenes.require(scene_id)
        info = self._enter(scene_id)
        self._schedule(scene_id, float(info["duration"]), 0.0)
        return info

    def _enter(self, scene_id: str) -> Dict[str, Any]:
        self._guard_loop(scene_id)
        task = self._task
        # 自己就是那个在跑的后端脚本时不能取消自己（否则会打断自己的 await）
        if task is not None and task is not asyncio.current_task():
            task.cancel()
        self._task = None
        duration = ui_scenes.effective_duration(scene_id)
        info = self._state.set_scene(scene_id, duration)
        self._publish({"type": "scene_set", **info})
        logger.info("scene → %s (%.3fs)", scene_id, duration)
        return info

    def _guard_loop(self, scene_id: str) -> None:
        now = time.monotonic()
        self._changes.append(now)
        while self._changes and now - self._changes[0] > LOOP_WINDOW_S:
            self._changes.popleft()
        if len(self._changes) > LOOP_MAX_CHANGES:
            self._changes.clear()
            raise SceneLoopError(
                f"场景切换过于频繁（>{LOOP_MAX_CHANGES} 次/{LOOP_WINDOW_S:g}s），"
                f"疑似 state_script 成环；最后请求的场景: {scene_id}"
            )

    # ---------- 后端脚本 ----------

    def _schedule(self, scene_id: str, duration: float, elapsed: float) -> None:
        # `>=`：`at` 正好等于进入时刻（如 at=0 的即时回切）也算将来，不能丢掉
        steps = [s for s in ui_scenes.state_steps(scene_id, duration) if s["at"] >= elapsed]
        if not steps:
            return
        self._task = asyncio.create_task(self._run(steps, scene_id))

    async def _run(self, steps: List[Dict[str, Any]], origin: str) -> None:
        started = time.monotonic()
        for step in steps:
            remaining = float(step["at"]) - (time.monotonic() - started)
            if remaining > 0:
                await asyncio.sleep(remaining)
            target = str(step.get("scene") or "").strip()
            # 场景在后端脚本等待期间被手动改掉 → 旧脚本作废，不再回切
            if not target or self._state.scene_id() != origin:
                return
            if target == self._state.scene_id():
                return
            try:
                # 链式：目标场景自己的后端脚本照样排程（成环由循环保护截断）
                await self.switch(target)
            except (SceneError, SceneLoopError) as e:
                # 数据问题只影响这条脚本，不能把整个窗口状态机带崩
                logger.error("后端脚本自动切场景失败: %s", e)
            return  # 已切换（或被拒），旧场景的剩余步骤没有意义

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
