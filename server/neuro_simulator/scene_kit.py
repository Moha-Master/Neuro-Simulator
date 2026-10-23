"""场景机制（neuro 窗口与 stream 合成器共用）。

**只放机制，不放策略**：

- 机制（这里）：manifest 读取与 mtime 缓存、元素/几何/内容的通用校验、
  `at_end` → 绝对时刻的解析、后端脚本（`state_script`）的排程与取消、
  循环保护、"切场景 → 写权威状态 → 广播" 的一条流水线。
- 策略（各模块自己）：谁能切场景、切完还要做什么、时长从哪里来、场景携带的
  专属字段（stream 的 `prompt_context` / `neuro` 块，neuro 的 `refs` / 视频轨道
  探测）。把这些塞进公共层只会得到一堆 `if module == ...`。

两个概念上的分工（两边的 schema 都遵守）：

- `timeline`（**前端脚本**）：由各渲染器的元素引擎按秒执行，只动 DOM；
  服务端不执行它，也不接受它的回报。
- `state_script`（**后端脚本**）：由 `SceneRunner` 按服务端时钟执行，只改权威状态
  （`scene.set`），改完广播 `scene_set`，所有渲染器按 `elapsed` 收敛。

`at_end: N` = 场景有效时长 - N；时长从哪里来由各模块的 policy 决定（数据里的
秒数、或探测视频轨道 + 余量）。
"""

import asyncio
import json
import logging
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Protocol, Sequence

logger = logging.getLogger(__name__)

# 循环保护：窗口期内允许的切换次数上限（数据写成 A↔B 时不至于把 CPU 转满）
LOOP_WINDOW_S = 2.0
LOOP_MAX_CHANGES = 10

CONTENT_KINDS = {"color", "image", "video", "text", "component"}
EDGE_X = {"left", "right"}
EDGE_Y = {"top", "bottom"}


class SceneError(ValueError):
    """场景数据不合法（面向维护者：改场景 JSON 后重新构建前端）。"""


class UnknownScene(SceneError):
    """请求了数据里不存在的场景 id。"""


class SceneLoopError(RuntimeError):
    """场景在短时间内被切换过多——通常是 state_script 写成了环。"""


# ---------- 场景清单的存储 ----------


class SceneStore:
    """场景清单的存储（机制层）：**DB 是权威**，包内 defaults 只是播种模板。

    - `load()`：读 DB；没有行就用包内默认播种（播种之后不再自动覆盖——
      用户改过就以 DB 为准，要回到默认走 `reset()`）。
    - `manifest()`：同步返回**已校验**的清单（内存缓存）——各模块的
      `find/require/overview` 都是同步读缓存，不需要 await。
    - `save(text)`：校验 → 落库 → 刷新缓存；不合法直接抛 SceneError。
    - `reset()`：恢复包内默认。
    - `set_data(data)`：测试用——直接注入清单（不落库）。

    存的是**规范化后的 JSON**（`indent=2`），所以 UI 读到的永远是同一个格式，
    "是不是默认值"也比的是数据而不是文本。
    """

    def __init__(
        self,
        storage: Any,
        default_path: Path,
        validate: Callable[[Any], None],
        name: str = "scenes",
    ) -> None:
        self._storage = storage
        self._default_path = Path(default_path)
        self._validate = validate
        self._name = name
        self._data: Optional[Dict[str, Any]] = None
        self._updated_at = ""
        # `source()` 的缓存：`/status` 的 manifest 分区会被别的模块高频拉取，
        # 而 `is_default` 要读包内文件、`chars` 要整份 JSON 序列化——不能每次
        # 轮询都算。清单只在 load/save/set_data 时变，那几处刷新缓存即可。
        self._source: Optional[Dict[str, Any]] = None
        self._lock = asyncio.Lock()

    # ---------- 加载 / 播种 ----------

    async def load(self) -> None:
        row = await self._storage.get_scene(self._name)
        if row is None:
            await self._storage.upsert_scene(self._default_text(), self._name)
            row = await self._storage.get_scene(self._name)
        self._apply(
            row["content"] if row else self._default_text(),
            row["updated_at"] if row else "",
        )

    def _default_text(self) -> str:
        try:
            return self._default_path.read_text(encoding="utf-8")
        except OSError as e:
            raise SceneError(f"包内默认场景清单读取失败: {e}") from e

    def _apply(self, text: str, updated_at: str) -> None:
        try:
            data = json.loads(text)
        except ValueError as e:
            raise SceneError(f"场景清单 JSON 解析失败: {e}") from e
        self._validate(data)
        self._data = data
        self._updated_at = updated_at
        self._source = None

    def set_data(self, data: Dict[str, Any], updated_at: str = "") -> None:
        """测试用：直接注入清单（校验但不落库）。"""
        self._validate(data)
        self._data = data
        self._updated_at = updated_at
        self._source = None

    # ---------- 读 ----------

    def manifest(self) -> Dict[str, Any]:
        if self._data is None:
            raise SceneError("场景清单尚未加载")
        return self._data

    def is_default(self) -> bool:
        try:
            default = json.loads(self._default_text())
        except (OSError, ValueError):
            return False
        return json.dumps(self.manifest(), sort_keys=True, ensure_ascii=False) == json.dumps(
            default, sort_keys=True, ensure_ascii=False
        )

    def source(self) -> Dict[str, Any]:
        """给 UI 的元信息：是不是默认值 / 什么时候改的 / 多少字符。

        结果在清单变化前是常量，缓存起来：这是 `/status` 的 `manifest` 分区
        现读的同一份数据，不能被高频轮询拖成"每半秒读一次文件 + 序列化整份 JSON"。
        """
        if self._source is None:
            self._source = {
                "is_default": self.is_default(),
                "updated_at": self._updated_at,
                "chars": len(json.dumps(self.manifest(), ensure_ascii=False, indent=2)),
            }
        return dict(self._source)

    # ---------- 写 ----------

    async def save(self, text: str) -> None:
        """校验后落库（存规范化 JSON）；不合法抛 SceneError，调用方转 400。"""
        raw = (text or "").strip()
        if not raw:
            raise SceneError("场景清单不能为空")
        try:
            data = json.loads(raw)
        except ValueError as e:
            raise SceneError(f"场景清单 JSON 解析失败: {e}") from e
        self._validate(data)
        canonical = json.dumps(data, ensure_ascii=False, indent=2)
        async with self._lock:
            await self._storage.upsert_scene(canonical, self._name)
            row = await self._storage.get_scene(self._name)
            self._apply(canonical, row["updated_at"] if row else "")

    async def reset(self) -> None:
        await self.save(self._default_text())


# ---------- 通用校验 ----------


def num(value: Any) -> Optional[float]:
    """真正的数值。bool 不算数字（它是 int 的子类，容易漏）。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def check_box(box: Any, where: str) -> None:
    if not isinstance(box, dict):
        raise SceneError(f"{where}: box 必须是对象")
    for key in ("x", "y"):
        if key not in box:
            raise SceneError(f"{where}: box.{key} 必填")
    for key in ("x", "y", "w", "h", "scale", "rotate", "opacity"):
        if key in box and num(box[key]) is None:
            raise SceneError(f"{where}: box.{key} 必须是数字")
    edge = box.get("edge")
    if edge is not None:
        if not isinstance(edge, dict):
            raise SceneError(f"{where}: box.edge 必须是对象")
        if edge.get("x") not in (None, *EDGE_X):
            raise SceneError(f"{where}: box.edge.x 只能是 left|right")
        if edge.get("y") not in (None, *EDGE_Y):
            raise SceneError(f"{where}: box.edge.y 只能是 top|bottom")


def check_content(content: Any, where: str) -> None:
    if not isinstance(content, dict):
        raise SceneError(f"{where}: content 必须是对象")
    kind = content.get("kind")
    if kind not in CONTENT_KINDS:
        raise SceneError(f"{where}: content.kind 只能是 {sorted(CONTENT_KINDS)}")
    if kind in ("image", "video") and not str(content.get("src") or "").strip():
        raise SceneError(f"{where}: content.{kind} 需要 src")
    if kind == "component" and not str(content.get("name") or "").strip():
        raise SceneError(f"{where}: content.component 需要 name")


def check_elements(elements: Any, scene_id: str) -> List[str]:
    """校验元素清单，返回元素 id 列表（声明顺序）。

    允许空清单：一个场景可以**只有宿主层**（如 stream 的开场场景只有一层 neuro 窗口
    iframe，画面内容全在窗口里）。"这个场景该不该有元素"是各模块的策略，
    由各自的 `_validate` 兜（stream 要求 elements 与 neuro 至少一个，neuro 要求非空）。
    """
    if not isinstance(elements, list):
        raise SceneError(f"场景 {scene_id}: elements 必须是数组")
    seen: List[str] = []
    for element in elements:
        eid_raw = element.get("id") if isinstance(element, dict) else "?"
        where = f"场景 {scene_id} 元素 {eid_raw}"
        if not isinstance(element, dict):
            raise SceneError(f"{where}: 元素必须是对象")
        eid = str(eid_raw or "").strip()
        if not eid:
            raise SceneError(f"{where}: 元素缺少 id")
        if eid in seen:
            raise SceneError(f"场景 {scene_id}: 元素 id 重复: {eid}")
        seen.append(eid)
        check_box(element.get("box"), where)
        check_content(element.get("content"), where)
        if num(element.get("z")) is None:
            raise SceneError(f"{where}: z 必填（数字）")
    return seen


def check_steps(
    steps: Any,
    *,
    where: str,
    kind: str,
    element_ids: Sequence[str],
    scene_ids: Sequence[str],
    state_actions: Sequence[str] = ("scene.set",),
    forbidden_view_actions: Sequence[str] = ("scene.set",),
) -> None:
    """校验脚本步骤，并把两个执行者的边界钉死。

    kind="view"  → 前端脚本：必须有 `element`，且不许出现状态动作（如 `scene.set`）。
    kind="state" → 后端脚本：只允许 `state_actions`，且不许带 `element`。
    """
    if steps is None:
        return
    if not isinstance(steps, list):
        raise SceneError(f"{where}: steps 必须是数组")
    for i, step in enumerate(steps):
        spot = f"{where}[{i}]"
        if not isinstance(step, dict):
            raise SceneError(f"{spot}: 步骤必须是对象")
        action = str(step.get("action") or "").strip()
        if not action:
            raise SceneError(f"{spot}: 缺少 action")
        if kind == "state":
            if action not in state_actions:
                raise SceneError(
                    f"{spot}: 后端脚本只能用 {sorted(state_actions)}（画面动作请写进 timeline）"
                )
            if "element" in step:
                raise SceneError(f"{spot}: 后端脚本不该指定 element（那是前端脚本的事）")
            if action == "scene.set":
                target = str(step.get("scene") or "").strip()
                if not target:
                    raise SceneError(f"{spot}: scene.set 需要 scene 目标")
                if target not in scene_ids:
                    raise SceneError(f"{spot}: scene.set 目标场景不存在: {target}")
        else:
            if action in forbidden_view_actions:
                raise SceneError(
                    f"{spot}: 前端脚本不能改权威状态（{action} 请写进 state_script）"
                )
            element_id = str(step.get("element") or "").strip()
            if not element_id:
                raise SceneError(f"{spot}: 前端脚本需要 element 目标")
            if element_id not in element_ids:
                raise SceneError(f"{spot}: 元素不存在: {element_id}")
        if num(step.get("at_end")) is None and num(step.get("at")) is None:
            raise SceneError(f"{spot}: 需要 at 或 at_end 之一")
        for key in ("at", "at_end", "duration"):
            if key in step and (num(step[key]) is None or num(step[key]) < 0):
                raise SceneError(f"{spot}: {key} 必须是非负数字")


def resolve_steps(steps: Optional[Sequence[Dict[str, Any]]], duration: float) -> List[Dict[str, Any]]:
    """把 `at_end` 解析成绝对 `at` 并按时间排序（前后端同一规则：at = 时长 - at_end）。"""
    out: List[Dict[str, Any]] = []
    for step in steps or []:
        at_end = num(step.get("at_end"))
        at = num(step.get("at"))
        item = dict(step)
        item["at"] = max(0.0, (duration - at_end) if at_end is not None else (at or 0.0))
        item.pop("at_end", None)
        out.append(item)
    out.sort(key=lambda s: s["at"])
    return out


# ---------- 后端脚本执行器 ----------


class ScenePolicy(Protocol):
    """各模块提供的场景查询面（stream / neuro 各一个实现）。"""

    def require(self, scene_id: str) -> Dict[str, Any]: ...

    def effective_duration(self, scene_id: str) -> float: ...

    def state_steps(self, scene_id: str, duration: Optional[float] = None) -> List[Dict[str, Any]]: ...

    def default_scene_id(self) -> str: ...


class SceneState(Protocol):
    """权威场景状态的写入面（neuro 的 UiState、stream 的 StreamLoop 都实现它）。"""

    def set_scene(self, scene_id: str, duration: float) -> Dict[str, Any]: ...

    def scene_id(self) -> Optional[str]: ...


class SceneRunner:
    """切场景 = 写权威状态 + 广播 `scene_set` + 排程该场景的后端脚本。

    手动切换与脚本自动切换走同一条路径，所以"动画放完自动回常态"既不依赖前端回报，
    也不依赖某个渲染器活着。
    """

    def __init__(self, state: SceneState, publish: Callable[[Dict[str, Any]], None], policy: ScenePolicy) -> None:
        self._state = state
        self._publish = publish
        self._policy = policy
        self._task: Optional[asyncio.Task] = None
        self._changes: deque = deque()

    # ---------- 查询 ----------

    def scene_id(self) -> Optional[str]:
        return self._state.scene_id()

    # ---------- 启动 ----------

    def init_default(self) -> Dict[str, Any]:
        """落到 default_scene：只写状态，不广播也不排程（还没有人在听）。"""
        scene_id = self._policy.default_scene_id()
        return self._state.set_scene(scene_id, self._policy.effective_duration(scene_id))

    # ---------- 切换 ----------

    def switch(self, scene_id: str) -> Dict[str, Any]:
        """切到某个场景（校验存在 → 写状态并广播 → 排程它的后端脚本）。"""
        self._policy.require(scene_id)
        info = self._enter(scene_id)
        self._schedule(scene_id, float(info.get("duration") or 0.0), 0.0)
        return info

    def _enter(self, scene_id: str) -> Dict[str, Any]:
        self._guard_loop(scene_id)
        self.cancel()
        duration = self._policy.effective_duration(scene_id)
        info = self._state.set_scene(scene_id, duration)
        self._publish({"type": "scene_set", **info})
        return info

    def cancel(self) -> None:
        """作废在跑的后端脚本（新切换接管，或外部要求停下）。"""
        task = self._task
        self._task = None
        if task is not None and task is not _current_task():
            task.cancel()

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
        steps = [s for s in self._policy.state_steps(scene_id, duration) if s["at"] >= elapsed]
        if not steps:
            return
        try:
            self._task = asyncio.get_running_loop().create_task(self._run(steps, scene_id))
        except RuntimeError:
            logger.warning("场景 %s 有后端脚本但当前没有事件循环，已跳过排程", scene_id)

    async def _run(self, steps: List[Dict[str, Any]], origin: str) -> None:
        started = time.monotonic()
        for step in steps:
            remaining = float(step["at"]) - (time.monotonic() - started)
            if remaining > 0:
                await asyncio.sleep(remaining)
            target = str(step.get("scene") or "").strip()
            # 等待期间场景已被手动改掉 → 旧脚本作废，不能把画面拽回脚本的目标
            if not target or self._state.scene_id() != origin:
                return
            if target == self._state.scene_id():
                return
            try:
                # 链式：目标场景自己的后端脚本照样排程（成环由循环保护截断）
                self.switch(target)
            except (SceneError, SceneLoopError) as e:
                # 数据问题只影响这条脚本，不能把整个状态机带崩
                logger.error("后端脚本自动切场景失败: %s", e)
            return  # 已切换（或被拒），旧场景的剩余步骤没有意义

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


def _current_task() -> Optional[asyncio.Task]:
    try:
        return asyncio.current_task()
    except RuntimeError:  # 不在事件循环里
        return None
