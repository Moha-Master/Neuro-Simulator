"""neuro 窗口场景清单的服务端视角（机制在 `..scene_kit`，这里只写 neuro 的策略）。

一份数据（`ui/public/neuro_scenes.json`）、两个执行者：`timeline` 由渲染器的元素
引擎执行（只动 DOM），`state_script` 由 `ui_scene.SceneRunner` 执行（只改权威状态）。
两边的边界校验、`at_end` 解析、manifest 缓存都来自 scene_kit。

neuro 特有的策略：

- 时长可以是秒数，也可以是 `{"media": "<video 元素 id>", "after": <秒>}`——
  按视频轨道探测（换素材自动跟随），所以数据里不必写死 10.475 / 13.475。
- `refs` 声明代码要认出的元素（立绘、字幕），main.ts 按它取实例。
- `INTRO_SCENE_ID`：`POST /ui/intro` 是"切到这个场景"的语义化别名。
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import scene_kit
from ..scene_kit import SceneError, UnknownScene, num, resolve_steps  # noqa: F401  (re-export)
from . import ui_media

# `POST /ui/intro` 是"切到开场场景"的语义化别名；stream 开播走它。
INTRO_SCENE_ID = "intro"

# 探测不到视频轨道时的回退时长（秒）
FALLBACK_VIDEO_S = ui_media.FALLBACK_VIDEO_S


def _scenes_path() -> Optional[Path]:
    dist = ui_media.ui_dist_dir()
    if dist is None:
        return None
    p = dist / "neuro_scenes.json"
    return p if p.is_file() else None


_cache = scene_kit.ManifestCache(_scenes_path, lambda data: _validate(data))


def clear_cache() -> None:
    """测试用：丢掉 mtime 缓存。"""
    _cache.clear()


def manifest() -> Dict[str, Any]:
    return _cache.load()


def scenes() -> List[Dict[str, Any]]:
    return manifest()["scenes"]


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


def label(scene_id: str) -> str:
    scene = find(scene_id)
    return str(scene.get("label") or scene_id) if scene else scene_id


def default_scene_id() -> str:
    sid = str(manifest().get("default_scene") or "").strip()
    if not sid or not exists(sid):
        raise SceneError(f"default_scene 缺失或指向不存在的场景: {sid!r}")
    return sid


def refs() -> Dict[str, str]:
    raw = manifest().get("refs") or {}
    return {str(k): str(v) for k, v in raw.items()} if isinstance(raw, dict) else {}


# ---------- 时长与步骤 ----------


def _element(scene: Dict[str, Any], element_id: str) -> Optional[Dict[str, Any]]:
    for e in scene.get("elements") or []:
        if str(e.get("id")) == element_id:
            return e
    return None


def effective_duration(scene_id: str) -> float:
    """场景有效时长（秒）：数字直接用；`{"media":...}` 按视频轨道探测 + after。"""
    scene = require(scene_id)
    raw = scene.get("duration")
    if raw is None:
        return 0.0
    if isinstance(raw, dict):
        element = _element(scene, str(raw.get("media") or "").strip())
        src = str((element or {}).get("content", {}).get("src") or "")
        video_s = ui_media.video_seconds(src) if src else None
        if video_s is None:
            video_s = FALLBACK_VIDEO_S
        return max(0.0, video_s + (num(raw.get("after")) or 0.0))
    value = num(raw)
    if value is None or value < 0:
        raise SceneError(f"场景 {scene_id} 的 duration 不合法: {raw!r}")
    return value


def state_steps(scene_id: str, duration: Optional[float] = None) -> List[Dict[str, Any]]:
    """后端脚本步骤（已解析绝对时刻）。"""
    scene = require(scene_id)
    script = scene.get("state_script") or {}
    dur = effective_duration(scene_id) if duration is None else max(0.0, float(duration))
    return resolve_steps(script.get("steps") if isinstance(script, dict) else None, dur)


def view_steps(scene_id: str, duration: Optional[float] = None) -> List[Dict[str, Any]]:
    """前端脚本步骤（已解析绝对时刻）；画面按 snapshot 的 elapsed 自行恢复。"""
    scene = require(scene_id)
    timeline = scene.get("timeline") or {}
    dur = effective_duration(scene_id) if duration is None else max(0.0, float(duration))
    return resolve_steps(timeline.get("steps") if isinstance(timeline, dict) else None, dur)


def element_ids(scene_id: str) -> List[str]:
    return [str(e.get("id")) for e in require(scene_id).get("elements") or []]


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


# ---------- neuro 侧的校验（通用部分交给 scene_kit） ----------


def _validate(data: Any) -> None:
    if not isinstance(data, dict):
        raise SceneError("neuro_scenes.json 顶层必须是对象")
    items = data.get("scenes")
    if not isinstance(items, list) or not items:
        raise SceneError("neuro_scenes.json 需要非空的 scenes 数组")

    ids: List[str] = []
    element_ids_by_scene: Dict[str, List[str]] = {}
    for scene in items:
        if not isinstance(scene, dict):
            raise SceneError("场景必须是对象")
        sid = str(scene.get("id") or "").strip()
        if not sid:
            raise SceneError("场景缺少 id")
        if sid in ids:
            raise SceneError(f"场景 id 重复: {sid}")
        ids.append(sid)
        element_ids_by_scene[sid] = scene_kit.check_elements(scene.get("elements"), sid)
        if not element_ids_by_scene[sid]:
            raise SceneError(f"场景 {sid}: neuro 窗口至少需要一个元素（立绘/背景/字幕）")

        raw_duration = scene.get("duration")
        if isinstance(raw_duration, dict):
            media_id = str(raw_duration.get("media") or "").strip()
            element = _element(scene, media_id)
            if element is None:
                raise SceneError(f"场景 {sid}: duration.media 指向不存在的元素 {media_id!r}")
            if (element.get("content") or {}).get("kind") != "video":
                raise SceneError(f"场景 {sid}: duration.media 必须是 video 元素")
            if num(raw_duration.get("after")) is None:
                raise SceneError(f"场景 {sid}: duration.after 必填（数字，可为 0）")
        elif raw_duration is not None and (num(raw_duration) is None or num(raw_duration) < 0):
            raise SceneError(f"场景 {sid}: duration 不合法")

    for scene in items:
        sid = str(scene.get("id"))
        timeline = scene.get("timeline")
        if timeline is not None:
            if not isinstance(timeline, dict):
                raise SceneError(f"场景 {sid}: timeline 必须是对象")
            scene_kit.check_steps(
                timeline.get("steps"),
                where=f"场景 {sid} timeline",
                kind="view",
                element_ids=element_ids_by_scene[sid],
                scene_ids=ids,
            )
        script = scene.get("state_script")
        if script is not None:
            if not isinstance(script, dict):
                raise SceneError(f"场景 {sid}: state_script 必须是对象")
            scene_kit.check_steps(
                script.get("steps"),
                where=f"场景 {sid} state_script",
                kind="state",
                element_ids=element_ids_by_scene[sid],
                scene_ids=ids,
            )

    default_sid = str(data.get("default_scene") or "").strip()
    if not default_sid:
        raise SceneError("neuro_scenes.json 需要 default_scene")
    if default_sid not in ids:
        raise SceneError(f"default_scene 指向不存在的场景: {default_sid}")
    refs_raw = data.get("refs")
    if refs_raw is not None and not isinstance(refs_raw, dict):
        raise SceneError('refs 必须是对象（如 {"avatar": "avatar", "caption": "caption"}）')
