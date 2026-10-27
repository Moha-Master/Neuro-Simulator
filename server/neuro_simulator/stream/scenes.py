"""stream 场景清单的服务端视角（机制在 `..scene_kit`，这里只写 stream 的策略）。

场景（背景/游戏/chat + neuro 窗口配置）是**服务端的数据**
（`stream/defaults/scenes.json`，包内数据文件），服务端做五件事：

1. 校验切场景的 id 是否存在（`POST /scene`、`on_scene`）；
2. 把清单（id/label/icon + 元素概览）交给 dashboard 渲染只读预览，**加场景只改一处**；
3. 提供场景的 `prompt_context`，让 `{{scene_context}}` 随场景变（玩 Wordle 时她知道
   自己在猜词，但棋盘细节仍只从工具返回里看）；
4. 供 `scene_kit.SceneRunner` 用的策略：有效时长、后端脚本步骤；
5. 通过 `GET /ui/manifest` 把**完整**清单下发给画面（渲染器要的是元素/box/css/script，
   不是 dashboard 用的概览）。

**数据归后端、前端只是渲染器**：这份 json 曾经放在前端 `public/` 里、靠构建拷进
dist，服务端为了读它不得不依赖前端构建产物——那是反向耦合。现在它与 prompt 模板、
人设一样属于服务端数据，前端来取。

stream 特有的策略：时长只能是数据里写的秒数（合成器不拥有素材，不做轨道探测——
开场视频的时长归 neuro）。开播 intro 已移到 neuro 窗口，stream 的 starting→live
由直播循环自己按 neuro 返回的时长推进，**不是**场景脚本（那是会话阶段，不是画面场景）。
文件按 mtime 缓存，改完数据无需重启进程。
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

import json

from .. import scene_kit
from ..scene_kit import SceneError, UnknownScene, resolve_steps  # noqa: F401  (re-export)


def _defaults_path() -> Optional[Path]:
    p = Path(__file__).parent / "defaults" / "scenes.json"
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
        path = _defaults_path()
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
    """完整场景清单（给画面渲染器：`GET /ui/manifest`）。"""
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


def load_scenes() -> List[Dict[str, Any]]:
    """场景清单；没有数据时返回空表（模块要能照常跑，只是画面/校验降级）。"""
    try:
        return manifest().get("scenes") or []
    except SceneError:
        return []


def find(scene_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if not scene_id:
        return None
    for s in load_scenes():
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


def label(scene_id: Optional[str]) -> Optional[str]:
    """场景的人类可读名（供控制面显示当前场景）。"""
    scene = find(scene_id)
    if scene is None:
        return None
    return str(scene.get("label") or scene_id)


def icon(scene_id: Optional[str]) -> Optional[str]:
    """场景图标；数据没写则 None，由调用方决定回退。"""
    scene = find(scene_id)
    if scene is None:
        return None
    value = str(scene.get("icon") or "").strip()
    return value or None


def desc(scene_id: Optional[str]) -> str:
    """场景的**机器/LLM 自述**（英文陈述句）；没写就是空串。

    与 `label`（短名）、`prompt_context`（注入当轮 prompt 的指令）分工见 AGENTS.md。
    """
    scene = find(scene_id)
    return str(scene.get("desc") or "") if scene else ""


def prompt_context(scene_id: Optional[str]) -> str:
    """场景自带说明则用它，否则空串（调用方回落到 config 的 scene_context）。"""
    scene = find(scene_id)
    return str(scene.get("prompt_context") or "") if scene else ""


def highlight_messages_enabled(scene_id: Optional[str]) -> bool:
    """该 stream 场景是否允许高亮消息进入队列消费。

    字段缺失 / 未知场景都按 True 处理（与"所有 stream 场景都启用"的现状一致），
    只有场景数据显式写 false 才关闭。
    """
    scene = find(scene_id)
    if scene is None:
        return True
    flag = scene.get("highlight_messages")
    return flag if isinstance(flag, bool) else True


def element_ids(scene_id: str) -> List[str]:
    return [str(e.get("id")) for e in require(scene_id).get("elements") or []]


# ---------- 时长与脚本（SceneRunner 的策略面） ----------


def effective_duration(scene_id: str) -> float:
    scene = require(scene_id)
    raw = scene.get("duration")
    if raw is None:
        return 0.0
    value = scene_kit.num(raw)
    if value is None or value < 0:
        raise SceneError(f"场景 {scene_id} 的 duration 不合法: {raw!r}")
    return value


def state_steps(scene_id: str, duration: Optional[float] = None) -> List[Dict[str, Any]]:
    scene = require(scene_id)
    script = scene.get("state_script") or {}
    dur = effective_duration(scene_id) if duration is None else max(0.0, float(duration))
    return resolve_steps(script.get("steps") if isinstance(script, dict) else None, dur)


def view_steps(scene_id: str, duration: Optional[float] = None) -> List[Dict[str, Any]]:
    scene = require(scene_id)
    timeline = scene.get("timeline") or {}
    dur = effective_duration(scene_id) if duration is None else max(0.0, float(duration))
    return resolve_steps(timeline.get("steps") if isinstance(timeline, dict) else None, dur)


class StreamScenePolicy:
    """scene_kit 要的查询面。stream 没有 default_scene：进入哪个场景由直播循环决定。"""

    def require(self, scene_id: str) -> Dict[str, Any]:
        return require(scene_id)

    def exists(self, scene_id: str) -> bool:
        return exists(scene_id)

    def effective_duration(self, scene_id: str) -> float:
        return effective_duration(scene_id)

    def state_steps(self, scene_id: str, duration: Optional[float] = None) -> List[Dict[str, Any]]:
        return state_steps(scene_id, duration)

    def default_scene_id(self) -> str:
        raise SceneError("stream 的场景没有 default_scene（由直播循环决定进入哪个场景）")


# ---------- 给 dashboard 的清单 ----------


def overview() -> List[Dict[str, Any]]:
    """给 dashboard 的清单（含元素概览，够渲染只读预览）。"""
    return [
        {
            "id": s.get("id"),
            "label": s.get("label") or s.get("id"),
            "icon": s.get("icon") or "mdi-image-frame",
            "desc": s.get("desc") or "",
            "prompt_context": s.get("prompt_context") or "",
            "highlight_messages": highlight_messages_enabled(str(s.get("id") or "")),
            "elements": [
                {
                    "id": e.get("id"),
                    "name": e.get("name") or e.get("id"),
                    "content": (e.get("content") or {}).get("kind"),
                    "component": (e.get("content") or {}).get("name"),
                    "z": e.get("z"),
                    "visible": e.get("visible", True),
                }
                for e in (s.get("elements") or [])
            ],
        }
        for s in load_scenes()
    ]


# ---------- stream 侧的校验（通用部分交给 scene_kit） ----------


def _check_neuro_block(cfg: Any, where: str) -> None:
    if cfg is None:
        return
    if not isinstance(cfg, dict):
        raise SceneError(f"{where}: neuro 必须是对象")
    for key in ("x", "y", "w", "z"):
        if key in cfg and scene_kit.num(cfg[key]) is None:
            raise SceneError(f"{where}: neuro.{key} 必须是数字")
    for key in ("x", "y", "w"):
        if key not in cfg:
            raise SceneError(f"{where}: neuro.{key} 必填（整窗位置与缩放）")
    hide = cfg.get("hide")
    if hide is not None and (not isinstance(hide, list) or not all(isinstance(i, str) for i in hide)):
        raise SceneError(f'{where}: neuro.hide 必须是层 id 的字符串数组')
    if cfg.get("hidden") is not None and not isinstance(cfg.get("hidden"), bool):
        raise SceneError(f"{where}: neuro.hidden 必须是布尔")


def _validate(data: Any) -> None:
    if not isinstance(data, dict):
        raise SceneError("scenes.json 顶层必须是对象")
    items = data.get("scenes")
    if not isinstance(items, list) or not items:
        raise SceneError("scenes.json 需要非空的 scenes 数组")

    ids: List[str] = []
    element_ids_by_scene: Dict[str, List[str]] = {}
    for scene in items:
        if not isinstance(scene, dict):
            raise SceneError("场景必须是对象")
        # 场景级 hidden 字段已废除：旧库里的残留键在保存/加载校验时静默剥掉。
        # 注意 `neuro.hidden`（神经窗口整窗不显示）是元素层属性，那个保留。
        scene.pop("hidden", None)
        sid = str(scene.get("id") or "").strip()
        if not sid:
            raise SceneError("场景缺少 id")
        if sid in ids:
            raise SceneError(f"场景 id 重复: {sid}")
        ids.append(sid)
        element_ids_by_scene[sid] = scene_kit.check_elements(scene.get("elements"), sid)
        neuro_cfg = scene.get("neuro")
        if not element_ids_by_scene[sid] and neuro_cfg is None:
            # 空场景八成是写漏了：elements 与 neuro 至少得有一个，否则画面全黑
            raise SceneError(f"场景 {sid}: elements 与 neuro 至少要有一个")
        _check_neuro_block(neuro_cfg, f"场景 {sid}")
        raw_duration = scene.get("duration")
        if isinstance(raw_duration, dict):
            raise SceneError(f"场景 {sid}: stream 不支持按媒体探测时长（开场素材归 neuro），请用秒数")
        if raw_duration is not None and (
            scene_kit.num(raw_duration) is None or scene_kit.num(raw_duration) < 0
        ):
            raise SceneError(f"场景 {sid}: duration 必须是非负秒数")
        pc = scene.get("prompt_context")
        if pc is not None and not isinstance(pc, str):
            raise SceneError(f"场景 {sid}: prompt_context 必须是字符串")
        # desc 是面向机器/LLM 的状态自述（英文陈述句）；非必填，保持旧清单可用
        if "desc" in scene and not isinstance(scene.get("desc"), str):
            raise SceneError(f"场景 {sid}: desc 必须是字符串")
        if "highlight_messages" in scene and not isinstance(
            scene.get("highlight_messages"), bool
        ):
            raise SceneError(f"场景 {sid}: highlight_messages 必须是布尔")

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
