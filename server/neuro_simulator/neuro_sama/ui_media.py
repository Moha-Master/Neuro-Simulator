"""neuro 窗口的媒体资产：定位模块持有的媒体文件 + 探测视频轨道时长（纯标准库）。

场景时长是数据驱动的：`neuro_scenes.json` 里写
`"duration": {"media": "<元素 id>", "after": <秒>}`，本模块负责把那个视频元素的
文件探测成秒数（换素材自动跟随，不必回去改数据里的绝对时刻）。

真实时长取"所有 trak 的 mdhd duration 最大值"——与浏览器 video.duration
和 ffprobe 一致。不能用顶层 mvhd：剪辑过的 mp4 常出现 mvhd 头部未更新
（残留原始时长）而只有轨道被截断，二者会严重不符。

**媒体文件为什么在模块手里、而不是前端构建产物里**：后端要读它来决定
`intro` 场景的后端脚本什么时候执行——那件事发生在服务端，文件就得在服务端
手里。前端（neuro 窗口页）通过 `GET /ui/media/{name}` 取同一个文件。

**工作目录是权威**：包内 `neuro_sama/assets/` 只是模板，首次启动时复制到
`<workdir>/neuro-sama/assets/`（与 `config.json` 同一套做法），之后程序**只**
从工作目录读——用户换开场视频不用动 site-packages。
"""

import shutil
import struct
from pathlib import Path
from typing import Dict, Optional, Tuple

# 探测失败时的回退视频时长（秒）
FALLBACK_VIDEO_S = 20.0

# 包内模板目录（随 wheel 分发）
PACKAGE_ASSETS = Path(__file__).parent / "assets"

# 工作目录里的媒体目录（启动时由 deploy_assets 设置；未初始化时回落到包内模板）
_media_dir: Optional[Path] = None

# 素材名 -> (mtime, 秒数)；换素材（mtime 变）自动失效
_video_s: Dict[str, Tuple[float, float]] = {}


def package_assets_dir() -> Path:
    """包内模板目录（只读，部署时的来源）。"""
    return PACKAGE_ASSETS


def media_dir() -> Path:
    """当前生效的媒体目录（工作目录优先；未部署过时是包内模板）。"""
    return _media_dir or PACKAGE_ASSETS


def set_media_dir(path: Path) -> None:
    """指定媒体目录（工作目录）；切换后时长缓存作废。"""
    global _media_dir
    _media_dir = Path(path)
    clear_cache()


def deploy_assets(data_dir: Path) -> Path:
    """首次启动：把包内模板复制到工作目录的 `assets/`（缺失才复制）。

    与 `config.json` 的部署同一套逻辑：包内那份是**模板**，工作目录那份才是
    程序实际读的东西。复制后设置生效目录。
    """
    target_dir = Path(data_dir) / "assets"
    target_dir.mkdir(parents=True, exist_ok=True)
    for src in PACKAGE_ASSETS.glob("*"):
        if not src.is_file():
            continue
        dst = target_dir / src.name
        if dst.exists():
            continue
        shutil.copyfile(src, dst)
        print(f"[neuro-sama] 已部署媒体素材: {dst}", flush=True)
    set_media_dir(target_dir)
    return target_dir


def media_path(name: str) -> Optional[Path]:
    """按媒体名定位文件（只允许纯文件名，不许穿目录）。"""
    clean = (name or "").strip()
    if not clean or "/" in clean or "\\" in clean or clean.startswith("."):
        return None
    p = media_dir() / clean
    return p if p.is_file() else None


def video_path(src: str) -> Optional[Path]:
    """兼容旧名：按 ui 里的相对素材名（如 `neuro_start.mp4`）定位实际文件。"""
    return media_path((src or "").lstrip("./"))


def video_seconds(src: str) -> Optional[float]:
    """视频素材时长（秒）；按 (文件, mtime) 缓存——换素材自动重新探测。"""
    key = (src or "").lstrip("./")
    path = video_path(key)
    if path is None:
        return None
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    cached = _video_s.get(key)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    dur = probe_mp4_seconds(path)
    if dur is None or dur <= 0:
        return None
    _video_s[key] = (mtime, dur)
    return dur


def clear_cache() -> None:
    _video_s.clear()


def _iter_boxes(buf: bytes, start: int, end: int):
    """遍历 [start,end) 的 box，yield (type, body_start, body_end)。"""
    i = start
    while i + 8 <= end:
        size = struct.unpack_from(">I", buf, i)[0]
        typ = buf[i + 4 : i + 8]
        hdr = 8
        if size == 1:
            size = struct.unpack_from(">Q", buf, i + 8)[0]
            hdr = 16
        elif size == 0:
            size = end - i
        if size < 8:
            return
        yield typ, i + hdr, i + size
        i += size


def _parse_tfhd_like(buf: bytes, body: int) -> Optional[float]:
    """解析 mvhd/mdhd 公共结构：version 与 timescale/duration 的位置随 version 变。"""
    version = buf[body]
    if version == 1:
        ts, dur = struct.unpack_from(">IQ", buf, body + 20)
    else:
        ts, dur = struct.unpack_from(">II", buf, body + 12)
    if ts <= 0:
        return None
    return dur / ts


def _max_track_duration(buf: bytes, moov_start: int, moov_end: int) -> Optional[float]:
    """递归 trak→mdia(hdlr,mdhd)，生成**音视频媒体轨**时长的最大值（=真实呈现时长）。
    跳过 text/subt/hint/meta 轨——它们可能携带剪辑后未更新的陈旧时长。"""
    best: Optional[float] = None
    for typ, bs, be in _iter_boxes(buf, moov_start, moov_end):
        if typ != b"trak":
            continue
        handler: Optional[bytes] = None
        mdhd_dur: Optional[float] = None
        for t2, s2, e2 in _iter_boxes(buf, bs, be):
            if t2 != b"mdia":
                continue
            for t3, s3, e3 in _iter_boxes(buf, s2, e2):
                if t3 == b"hdlr" and e3 - s3 >= 12:
                    handler = buf[s3 + 8 : s3 + 12]
                elif t3 == b"mdhd":
                    mdhd_dur = _parse_tfhd_like(buf, s3)
        if handler in (b"vide", b"soun") and mdhd_dur and mdhd_dur > 0 and (
            best is None or mdhd_dur > best
        ):
            best = mdhd_dur
    return best


def _mvhd_duration(buf: bytes, moov_start: int, moov_end: int) -> Optional[float]:
    for typ, bs, be in _iter_boxes(buf, moov_start, moov_end):
        if typ == b"mvhd":
            return _parse_tfhd_like(buf, bs)
    return None


def probe_mp4_seconds(path: Path) -> Optional[float]:
    """单文件探测：优先最大轨道时长（与浏览器一致），无 mdhd 时退回 mvhd。"""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    for typ, bs, be in _iter_boxes(data, 0, len(data)):
        if typ != b"moov":
            continue
        tracks = _max_track_duration(data, bs, be)
        if tracks:
            return tracks
        mv = _mvhd_duration(data, bs, be)
        if mv and mv > 0:
            return mv
        return None
    return None
