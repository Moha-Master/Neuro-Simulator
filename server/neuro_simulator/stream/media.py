"""媒体资产的服务器端感知：mp4 时长探测（纯标准库解析轨道时长）。

用途：开播闸门的兜底超时 = 视频实际时长 + 立绘入场动画 + 余量，
无需任何配置项（视频文件随包分发、内容固定，探测结果进程内缓存）。

真实时长取"所有 trak 的 mdhd duration 最大值"——与浏览器 video.duration
和 ffprobe 一致。不能用顶层 mvhd：剪辑过的 mp4 常出现 mvhd 头部未更新
（残留原始时长）而只有轨道被截断，二者会严重不符。
"""

import struct
from pathlib import Path
from typing import Optional

# avatar.rise_in 入场动画时长（须与 ui/src/core/action.ts 的 duration 保持一致）
RISE_ANIM_S = 3.0
# 视频加载/seek 的兜底余量
GATE_MARGIN_S = 5.0
# 探测失败时的回退视频时长
FALLBACK_VIDEO_S = 20.0

_video_s: Optional[float] = None


def ui_dist_dir() -> Optional[Path]:
    """stream/ui 构建产物目录（wheel 与 editable 路径一致）。"""
    dist = Path(__file__).parent / "ui" / "dist"
    return dist if (dist / "index.html").is_file() else None


def intro_video_path() -> Optional[Path]:
    dist = ui_dist_dir()
    if dist is None:
        return None
    p = dist / "neuro_start.mp4"
    return p if p.is_file() else None


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
    """解析 mvhd/mdhd 公共结构：version 后按 v0(4字节)/v1(8字节) 取 timescale+duration。"""
    version = buf[body]
    try:
        if version == 1:
            # version+flags(4) + creation(8) + modification(8) → timescale @ +20
            timescale, duration = struct.unpack_from(">IQ", buf, body + 20)
        else:
            # version+flags(4) + creation(4) + modification(4) → timescale @ +12
            timescale, duration = struct.unpack_from(">II", buf, body + 12)
    except struct.error:
        return None
    if timescale <= 0:
        return None
    return duration / timescale


def _max_track_duration(buf: bytes, moov_start: int, moov_end: int) -> Optional[float]:
    """递归 trak→mdia(hdlr,mdhd)，返回**音视频媒体轨**时长的最大值（=真实呈现时长）。
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


def intro_video_seconds() -> float:
    """开场视频时长（秒），带进程内缓存与失败回退。"""
    global _video_s
    if _video_s is not None:
        return _video_s
    path = intro_video_path()
    if path is not None:
        dur = probe_mp4_seconds(path)
        if dur and dur > 0:
            _video_s = dur
            return dur
    _video_s = FALLBACK_VIDEO_S
    return _video_s


def intro_gate_seconds() -> float:
    """开播闸门兜底超时：视频 + 入场动画 + 余量。"""
    return intro_video_seconds() + RISE_ANIM_S + GATE_MARGIN_S
