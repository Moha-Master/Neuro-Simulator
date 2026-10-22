"""neuro 窗口的场景状态机：机制在 `..scene_kit`，这里只接 neuro 自己的数据与状态。

- `state_script`（后端脚本）由 `scene_kit.SceneRunner` 按服务端时钟执行，唯一允许的
  动作是 `scene.set`；执行结果是权威场景状态变更 + `scene_set` 广播，所有渲染器
  （OBS 浏览器源 / dashboard 预览 / 独立窗口）按 `elapsed` 收敛到同一画面。
- `timeline`（前端脚本）不在这里执行：那是各渲染器元素引擎的事。

所以"开场动画放完自动回常态"既不依赖前端回报，也不依赖某个渲染器活着。
"""

from typing import Any, Callable, Dict, Optional

from ..scene_kit import SceneLoopError, SceneRunner  # noqa: F401  (server 端 except 用得到)
from . import scenes as ui_scenes
from .ui_state import UiState


class NeuroScenePolicy:
    """scene_kit 要的查询面：全部转发到 neuro 的场景数据（含视频轨道探测）。"""

    def require(self, scene_id: str) -> Dict[str, Any]:
        return ui_scenes.require(scene_id)

    def exists(self, scene_id: str) -> bool:
        return ui_scenes.exists(scene_id)

    def effective_duration(self, scene_id: str) -> float:
        return ui_scenes.effective_duration(scene_id)

    def state_steps(self, scene_id: str, duration: Optional[float] = None) -> list:
        return ui_scenes.state_steps(scene_id, duration)

    def default_scene_id(self) -> str:
        return ui_scenes.default_scene_id()


def make_runner(state: UiState, publish: Callable[[Dict[str, Any]], None]) -> SceneRunner:
    """neuro 窗口的场景执行器（server 在 lifespan 里建一个）。"""
    return SceneRunner(state, publish, NeuroScenePolicy())
