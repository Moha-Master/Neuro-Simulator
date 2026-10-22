"""stream 画面资产的服务器端感知：只负责定位 `stream/ui` 构建产物。

开播视频/立绘/字幕已移到 neuro 窗口（`neuro_sama/ui`），stream 不再解析 mp4
时长，也不再把 intro 场景当作自己的资产。
"""

from pathlib import Path
from typing import Optional


def ui_dist_dir() -> Optional[Path]:
    """stream/ui 构建产物目录（editable 在 ui/dist，wheel 在 ui/）。"""
    base = Path(__file__).parent / "ui"
    for cand in (base / "dist", base):
        if (cand / "index.html").is_file():
            return cand
    return None
