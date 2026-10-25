"""neuro 窗口场景清单的服务端视角（机制在 `..scene_kit`，这里只写 neuro 的策略）。

一份数据（`neuro_sama/defaults/neuro_scenes.json`，包内数据文件）、两个执行者：
`timeline` 由渲染器的元素引擎执行（只动 DOM），`state_script` 由 `ui_scene.SceneRunner`
执行（只改权威状态）。两边的边界校验、`at_end` 解析、manifest 缓存都来自 scene_kit。

**数据归后端、前端只是渲染器**：这份 json 曾放在前端 `public/` 里、靠构建拷进 dist，
服务端为了读它不得不依赖前端构建产物——那是反向耦合。现在完整清单通过
`GET /ui/manifest` 下发给窗口页面。

neuro 特有的策略：

- 时长可以是秒数，也可以是 `{"media": "<video 元素 id>", "after": <秒>}`——
  按视频轨道探测（换素材自动跟随），所以数据里不必写死 10.475 / 13.475。
- `refs` 声明代码要认出的元素（立绘、字幕、高亮消息），main.ts 按它取实例。
- `highlight_messages`：该场景是否显示高亮消息浮层（normal=true，intro=false）。
- `INTRO_SCENE_ID`：`POST /ui/intro` 是"切到这个场景"的语义化别名。
"""

import json
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
    p = Path(__file__).parent / "defaults" / "neuro_scenes.json"
    return p if p.is_file() else None


# 存储（管理器在 scene_kit 里共用，数据各模块各的）：启动时绑定一次，之后
# **DB 里的那份就是权威**（人工可编辑）；没绑定时退回包内默认（测试/脚本直接用）。
_store: Optional[scene_kit.SceneStore] = None
_default_cache: Optional[Dict[str, Any]] = None


def set_store(store: Optional[scene_kit.SceneStore]) -> None:
    """绑定 DB 存储（启动时调用一次）。"""
    global _store
    _store = store


def store() -> Optional[scene_kit.SceneStore]:
    """已绑定的存储；None = 还没初始化（此时读包内默认）。"""
    return _store


def _default_manifest() -> Dict[str, Any]:
    """包内默认清单（未绑定 DB 时的兜底；结果缓存）。"""
    global _default_cache
    if _default_cache is None:
        path = _scenes_path()
        if path is None:
            raise SceneError("找不到包内默认场景清单")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as e:
            raise SceneError(f"场景清单 JSON 解析失败: {e}") from e
        _validate(data)
        _default_cache = data
    return _default_cache


def manifest() -> Dict[str, Any]:
    """完整场景清单（给窗口渲染器：`GET /ui/manifest`）。"""
    if _store is not None:
        return _store.manifest()
    return _default_manifest()


def manifest_source() -> Dict[str, Any]:
    """元信息：是不是默认值 / 什么时候改的 / 多少字符（给 UI）。"""
    if _store is not None:
        return _store.source()
    return {
        "is_default": True,
        "updated_at": "",
        "chars": len(json.dumps(manifest(), ensure_ascii=False, indent=2)),
    }


async def save_manifest(text: str) -> None:
    """保存场景清单（校验 → 落库 → 刷新缓存）。"""
    if _store is None:
        raise SceneError("场景清单尚未绑定数据库")
    await _store.save(text)


async def reset_manifest() -> None:
    """恢复包内默认清单。"""
    if _store is None:
        raise SceneError("场景清单尚未绑定数据库")
    await _store.reset()


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


def desc(scene_id: Optional[str]) -> str:
    """场景的**机器/LLM 自述**（英文陈述句）；没写就是空串。

    与 `label`（短名）、`prompt_context`（注入当轮 prompt 的指令）分工见 AGENTS.md。
    """
    scene = find(scene_id)
    return str(scene.get("desc") or "") if scene else ""


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
                "desc": str(s.get("desc") or ""),
                "icon": s.get("icon"),
                "hidden": bool(s.get("hidden")),
                "highlight_messages": bool(s.get("highlight_messages", True)),
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
        # desc 是面向机器/LLM 的状态自述（英文陈述句）；非必填，保持旧清单可用
        if "desc" in scene and not isinstance(scene.get("desc"), str):
            raise SceneError(f"场景 {sid}: desc 必须是字符串")
        if "highlight_messages" in scene and not isinstance(
            scene.get("highlight_messages"), bool
        ):
            raise SceneError(f"场景 {sid}: highlight_messages 必须是布尔")

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
