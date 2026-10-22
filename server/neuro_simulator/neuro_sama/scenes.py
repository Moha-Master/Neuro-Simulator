"""neuro 窗口场景清单的服务端视角：读 ui 构建产物里的 `neuro_scenes.json`。

**一份数据、两个执行者**：

- `timeline`（前端脚本）由各渲染器的元素引擎按秒执行，只动 DOM
  （`element.show/hide`、`media.play/pause`、`ACTIONS` 里的命名动作）。
  服务端不执行它，也不接受它的回报——后端是唯一时钟。
- `state_script`（后端脚本）由服务端调度，只改权威状态（`scene.set`），
  广播 `scene_set` 让所有渲染器（OBS 浏览器源、dashboard 预览、独立窗口）收敛。

这条边界由加载校验兜住：`timeline` 里出现 `scene.set` 直接报错；`state_script`
里出现元素/媒体动作或带 `element` 字段同样报错。两个执行者不可能互相漂移。

时长与 `at_end`：场景的 `duration` 可以是秒数，也可以是
`{"media": "<元素 id>", "after": <秒>}`——后者按视频轨道探测，换素材自动跟随。
`at_end: N` 表示"有效时长 - N"，前后端用同一规则解析，所以数据里不必写死
10.475 / 13.475 这类绝对时刻。

文件按 mtime 缓存，重新构建前端后无需重启进程。
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import ui_media

# `POST /ui/intro` 是"切到开场场景"的语义化别名；stream 开播走它。
INTRO_SCENE_ID = "intro"

_cache: Dict[str, Any] = {"key": None, "manifest": None}

_CONTENT_KINDS = {"color", "image", "video", "text", "component"}
_EDGE_X = {"left", "right"}
_EDGE_Y = {"top", "bottom"}
# 后端脚本唯一认可的动作
STATE_ACTIONS = {"scene.set"}
# 前端脚本里禁止出现的动作（那是后端的状态权）
FORBIDDEN_VIEW_ACTIONS = {"scene.set"}


class SceneError(ValueError):
    """场景数据不合法（面向维护者：改 neuro_scenes.json 后重启/重构建）。"""


class UnknownScene(SceneError):
    """请求了数据里不存在的场景 id。"""


def _scenes_path() -> Optional[Path]:
    dist = ui_media.ui_dist_dir()
    if dist is None:
        return None
    p = dist / "neuro_scenes.json"
    return p if p.is_file() else None


def _load_manifest() -> Dict[str, Any]:
    path = _scenes_path()
    if path is None:
        raise SceneError(
            "找不到 neuro_scenes.json：请在 server/neuro_simulator/neuro_sama/ui/ 下构建前端"
        )
    key = (str(path), path.stat().st_mtime_ns)
    if _cache["key"] != key:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise SceneError(f"neuro_scenes.json 解析失败: {e}") from e
        _validate(data)
        _cache.update(key=key, manifest=data)
    return _cache["manifest"]


def clear_cache() -> None:
    """测试用：丢掉 mtime 缓存。"""
    _cache["key"] = None
    _cache["manifest"] = None


def manifest() -> Dict[str, Any]:
    return _load_manifest()


def scenes() -> List[Dict[str, Any]]:
    return _load_manifest()["scenes"]


def find(scene_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if not scene_id:
        return None
    for s in scenes():
        if s.get("id") == scene_id:
            return s
    return None


def exists(scene_id: str) -> bool:
    return find(scene_id) is not None


def require(scene_id: str) -> Dict[str, Any]:
    scene = find(scene_id)
    if scene is None:
        raise UnknownScene(f"未知场景: {scene_id}")
    return scene


def hidden(scene_id: str) -> bool:
    scene = find(scene_id)
    return bool(scene and scene.get("hidden") is True)


def default_scene_id() -> str:
    data = _load_manifest()
    sid = str(data.get("default_scene") or "").strip()
    if not sid or not exists(sid):
        raise SceneError(f"default_scene 缺失或指向不存在的场景: {sid!r}")
    return sid


def label(scene_id: str) -> str:
    scene = find(scene_id)
    return str(scene.get("label") or scene_id) if scene else scene_id


# ---------- 时长与步骤解析 ----------


def _num(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def effective_duration(scene_id: str) -> float:
    """场景有效时长（秒）：数字直接用；`{"media":...}` 按视频轨道探测。"""
    scene = require(scene_id)
    raw = scene.get("duration")
    if raw is None:
        return 0.0
    if isinstance(raw, dict):
        element_id = str(raw.get("media") or "").strip()
        after = _num(raw.get("after")) or 0.0
        video_s = 0.0
        element = _element(scene, element_id)
        if element is not None:
            src = ((element.get("content") or {}).get("src") or "").strip()
            if src:
                probed = ui_media.video_seconds(src)
                video_s = probed if probed is not None else ui_media.FALLBACK_VIDEO_S
        return max(0.0, video_s + after)
    value = _num(raw)
    if value is None or value < 0:
        raise SceneError(f"场景 {scene_id} 的 duration 不合法: {raw!r}")
    return value


def resolve_steps(steps: Optional[List[Dict[str, Any]]], duration: float) -> List[Dict[str, Any]]:
    """把 `at_end` 解析成绝对 `at`（前后端同一规则：at = duration - at_end）。"""
    out: List[Dict[str, Any]] = []
    for step in steps or []:
        at_end = _num(step.get("at_end"))
        at = _num(step.get("at"))
        resolved = duration - at_end if at_end is not None else (at or 0.0)
        item = dict(step)
        item["at"] = max(0.0, resolved)
        item.pop("at_end", None)
        out.append(item)
    out.sort(key=lambda s: s["at"])
    return out


def state_steps(scene_id: str, duration: Optional[float] = None) -> List[Dict[str, Any]]:
    """后端脚本步骤（已解析绝对时刻）。"""
    scene = require(scene_id)
    script = scene.get("state_script") or {}
    dur = effective_duration(scene_id) if duration is None else max(0.0, float(duration))
    return resolve_steps(script.get("steps") if isinstance(script, dict) else None, dur)


def view_steps(scene_id: str, duration: Optional[float] = None) -> List[Dict[str, Any]]:
    """前端脚本步骤（已解析绝对时刻）；随 `scene_set` 下发，画面卡住不影响后端。"""
    scene = require(scene_id)
    timeline = scene.get("timeline") or {}
    dur = effective_duration(scene_id) if duration is None else max(0.0, float(duration))
    return resolve_steps(timeline.get("steps") if isinstance(timeline, dict) else None, dur)


def element_ids(scene_id: str) -> List[str]:
    scene = require(scene_id)
    return [str(e.get("id")) for e in scene.get("elements") or []]


def overview() -> List[Dict[str, Any]]:
    """给 dashboard：场景清单 + 每个场景的元素概览（层开关按这份数据渲染）。"""
    out = []
    for s in scenes():
        sid = str(s.get("id"))
        out.append(
            {
                "id": sid,
                "label": str(s.get("label") or sid),
                "icon": s.get("icon"),
                "hidden": bool(s.get("hidden")),
                "duration": round(effective_duration(sid), 3),
                "auto_returns": bool(state_steps(sid)),
                "elements": [
                    {
                        "id": str(e.get("id")),
                        "name": e.get("name"),
                        "z": e.get("z"),
                        "visible": e.get("visible", True),
                        "content": (e.get("content") or {}).get("kind"),
                        "component": (e.get("content") or {}).get("name"),
                    }
                    for e in s.get("elements") or []
                ],
            }
        )
    return out


# ---------- 校验 ----------


def _element(scene: Dict[str, Any], element_id: str) -> Optional[Dict[str, Any]]:
    for e in scene.get("elements") or []:
        if str(e.get("id")) == element_id:
            return e
    return None


def _check_box(box: Any, where: str) -> None:
    if not isinstance(box, dict):
        raise SceneError(f"{where}: box 必须是对象")
    for key in ("x", "y", "w", "h", "scale", "rotate", "opacity"):
        if key in box and _num(box[key]) is None:
            raise SceneError(f"{where}: box.{key} 必须是数字")
    for key in ("x", "y"):
        if key not in box:
            raise SceneError(f"{where}: box.{key} 必填")
    edge = box.get("edge")
    if edge is not None:
        if not isinstance(edge, dict):
            raise SceneError(f"{where}: box.edge 必须是对象")
        if edge.get("x") not in (None, *_EDGE_X):
            raise SceneError(f"{where}: box.edge.x 只能是 left|right")
        if edge.get("y") not in (None, *_EDGE_Y):
            raise SceneError(f"{where}: box.edge.y 只能是 top|bottom")


def _check_content(content: Any, where: str) -> None:
    if not isinstance(content, dict):
        raise SceneError(f"{where}: content 必须是对象")
    kind = content.get("kind")
    if kind not in _CONTENT_KINDS:
        raise SceneError(f"{where}: content.kind 只能是 {sorted(_CONTENT_KINDS)}")
    if kind in ("image", "video") and not str(content.get("src") or "").strip():
        raise SceneError(f"{where}: content.{kind} 需要 src")
    if kind == "component" and not str(content.get("name") or "").strip():
        raise SceneError(f"{where}: content.component 需要 name")


def _check_steps(
    steps: Any,
    where: str,
    scene: Dict[str, Any],
    *,
    is_state: bool,
    known_scenes: List[str],
) -> None:
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
        if is_state:
            if action not in STATE_ACTIONS:
                raise SceneError(
                    f"{spot}: 后端脚本只能用 {sorted(STATE_ACTIONS)}（画面动作请写进 timeline）"
                )
            if action == "scene.set":
                target = str(step.get("scene") or "").strip()
                if not target:
                    raise SceneError(f"{spot}: scene.set 需要 scene 目标")
                if target not in known_scenes:
                    raise SceneError(f"{spot}: scene.set 目标场景不存在: {target}")
        else:
            if action in FORBIDDEN_VIEW_ACTIONS:
                raise SceneError(f"{spot}: 前端脚本不能改权威状态（{action} 请写进 state_script）")
            element_id = str(step.get("element") or "").strip()
            if not element_id:
                raise SceneError(f"{spot}: 前端脚本需要 element 目标")
            if _element(scene, element_id) is None:
                raise SceneError(f"{spot}: 元素不存在: {element_id}")
        if _num(step.get("at_end")) is None and _num(step.get("at")) is None:
            raise SceneError(f"{spot}: 需要 at 或 at_end 之一")
        for key in ("at", "at_end", "duration"):
            if key in step and (_num(step[key]) is None or _num(step[key]) < 0):
                raise SceneError(f"{spot}: {key} 必须是非负数字")


def _validate(data: Any) -> None:
    if not isinstance(data, dict):
        raise SceneError("neuro_scenes.json 顶层必须是对象")
    items = data.get("scenes")
    if not isinstance(items, list) or not items:
        raise SceneError("neuro_scenes.json 需要非空的 scenes 数组")
    ids: List[str] = []
    for scene in items:
        if not isinstance(scene, dict):
            raise SceneError("场景必须是对象")
        sid = str(scene.get("id") or "").strip()
        if not sid:
            raise SceneError("场景缺少 id")
        if sid in ids:
            raise SceneError(f"场景 id 重复: {sid}")
        ids.append(sid)
        elements = scene.get("elements")
        if not isinstance(elements, list) or not elements:
            raise SceneError(f"场景 {sid}: 需要非空的 elements")
        seen: List[str] = []
        for element in elements:
            where = f"场景 {sid} 元素 {element.get('id') if isinstance(element, dict) else '?'}"
            if not isinstance(element, dict):
                raise SceneError(f"{where}: 元素必须是对象")
            eid = str(element.get("id") or "").strip()
            if not eid:
                raise SceneError(f"{where}: 元素缺少 id")
            if eid in seen:
                raise SceneError(f"场景 {sid}: 元素 id 重复: {eid}")
            seen.append(eid)
            _check_box(element.get("box"), where)
            _check_content(element.get("content"), where)
            if _num(element.get("z")) is None:
                raise SceneError(f"{where}: z 必填（数字）")
        raw_duration = scene.get("duration")
        if isinstance(raw_duration, dict):
            media_id = str(raw_duration.get("media") or "").strip()
            element = _element(scene, media_id)
            if element is None:
                raise SceneError(f"场景 {sid}: duration.media 指向不存在的元素 {media_id!r}")
            if (element.get("content") or {}).get("kind") != "video":
                raise SceneError(f"场景 {sid}: duration.media 必须是 video 元素")
            if _num(raw_duration.get("after")) is None:
                raise SceneError(f"场景 {sid}: duration.after 必填（数字，可为 0）")
        elif raw_duration is not None and (_num(raw_duration) is None or _num(raw_duration) < 0):
            raise SceneError(f"场景 {sid}: duration 不合法")

    for scene in items:
        sid = str(scene.get("id"))
        timeline = scene.get("timeline")
        if timeline is not None:
            if not isinstance(timeline, dict):
                raise SceneError(f"场景 {sid}: timeline 必须是对象")
            _check_steps(
                timeline.get("steps"), f"场景 {sid} timeline", scene, is_state=False,
                known_scenes=ids,
            )
        script = scene.get("state_script")
        if script is not None:
            if not isinstance(script, dict):
                raise SceneError(f"场景 {sid}: state_script 必须是对象")
            _check_steps(
                script.get("steps"), f"场景 {sid} state_script", scene, is_state=True,
                known_scenes=ids,
            )

    default_sid = str(data.get("default_scene") or "").strip()
    if not default_sid:
        raise SceneError("neuro_scenes.json 需要 default_scene")
    if default_sid not in ids:
        raise SceneError(f"default_scene 指向不存在的场景: {default_sid}")
    refs = data.get("refs")
    if refs is not None and not isinstance(refs, dict):
        raise SceneError("refs 必须是对象（如 {\"avatar\": \"avatar\", \"caption\": \"caption\"}）")
