"""场景清单的服务端视角：读 ui 构建产物里的 `scenes.json`。

场景（元素清单）由画面侧拥有（`stream/ui/public/scenes.json` → 构建到 `dist/scenes.json`），
服务端只做三件事：
1. 校验手动切场景的 id 是否存在（`POST /scene`）；
2. 把清单（id/label/icon + 元素概览）交给 dashboard 渲染场景按钮，**加场景只改一处**；
3. 提供场景的 `prompt_context`，让 `{{scene_context}}` 随场景变（玩 Wordle 时她知道自己
   在猜词，但棋盘细节仍只从工具返回里看）。

文件按 mtime 缓存，重新构建前端后无需重启进程。
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import media

_cache: Dict[str, Any] = {"key": None, "scenes": []}


def _dist_scenes_path() -> Optional[Path]:
    dist = media.ui_dist_dir()
    if dist is None:
        return None
    p = dist / "scenes.json"
    return p if p.is_file() else None


def load_scenes() -> List[Dict[str, Any]]:
    path = _dist_scenes_path()
    if path is None:
        return []
    try:
        stat = path.stat()
        key = f"{stat.st_mtime_ns}:{stat.st_size}"
    except OSError:
        return _cache["scenes"]
    if _cache["key"] != key:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            scenes = data.get("scenes") or []
        except (OSError, ValueError) as e:
            raise RuntimeError(f"scenes.json 读取失败: {e}") from e
        _cache.update({"key": key, "scenes": scenes})
    return _cache["scenes"]


def find(scene_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if not scene_id:
        return None
    for s in load_scenes():
        if s.get("id") == scene_id:
            return s
    return None


def exists(scene_id: str) -> bool:
    return find(scene_id) is not None


def prompt_context(scene_id: Optional[str]) -> str:
    """场景自带说明则用它，否则空串（调用方回落到 config 的 scene_context）。"""
    scene = find(scene_id)
    return str(scene.get("prompt_context") or "") if scene else ""


def overview() -> List[Dict[str, Any]]:
    """给 dashboard 的清单（含元素概览，够渲染按钮与只读预览）。"""
    return [
        {
            "id": s.get("id"),
            "label": s.get("label") or s.get("id"),
            "icon": s.get("icon") or "mdi-image-frame",
            "prompt_context": s.get("prompt_context") or "",
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
