"""neuro 窗口的共享状态：当前场景 + 当前/最近一轮字幕时间线。

`/ui/events` 的 snapshot 从这里取：新连接的画面（OBS 重载、dashboard 预览刷新）
可以按 `scene.elapsed` 恢复场景时间线、按 `consumed` 续播正在发声的句子。
时间基准是服务端 `time.monotonic()`，与 stream 的 turn 时间线互相独立——stream
仍按自己消费到的 speech 事件节奏排下一轮，neuro 只负责"窗口里怎么播"。

**场景是权威状态**：只有 `SceneRunner`（后端脚本执行器）与管理端点能改，
渲染器只读快照并如实渲染；`timeline` 是前端脚本、`state_script` 是后端脚本，
分工见 scenes.py。

turn 时间线的推进方式：收到 speech 事件时，从 max(now, play_head) 开始排，
play_head += duration。这样断线重连时能估算"这句话现在应该播到哪里"。
"""

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..config import get_config

# 一轮结束后保留快照的时间窗口：足够覆盖字幕淡出，避免重连时闪出很久以前的字幕。
TURN_GRACE_S = 10.0


@dataclass
class _Speech:
    step: int
    index: int
    text: str
    duration: float
    start: float
    audio: str = ""
    mime: str = ""


@dataclass
class _Turn:
    id: int
    channel: str
    active: bool
    play_head: float
    speech: List[_Speech] = field(default_factory=list)
    updated_at: float = 0.0
    ended_at: Optional[float] = None


@dataclass
class _Scene:
    id: str
    duration: float
    started_at: float


class UiState:
    def __init__(self) -> None:
        self._turns: Dict[str, _Turn] = {}
        self._last_channel: Optional[str] = None
        self._next_turn_id = 1
        self._scene: Optional[_Scene] = None

    # ---------- turn ----------

    def new_turn(self, channel: str) -> int:
        """为一次 /chat 分配 turn_id；同一 channel 的新轮覆盖旧轮。"""
        turn_id = self._next_turn_id
        self._next_turn_id += 1
        now = time.monotonic()
        self._turns[channel] = _Turn(
            id=turn_id,
            channel=channel,
            active=True,
            play_head=now,
            updated_at=now,
        )
        self._last_channel = channel
        return turn_id

    def observe(self, ev: Dict[str, Any], channel: str, turn_id: int) -> None:
        """把 /chat 的 agent 事件喂进时间线（在 publish 之前调用）。"""
        typ = ev.get("type")
        turn = self._turns.get(channel)
        if turn is None or turn.id != turn_id:
            return

        now = time.monotonic()
        if typ == "speech":
            try:
                duration = max(0.0, float(ev.get("duration") or 0.0))
            except (TypeError, ValueError):
                duration = 0.0
            start = max(now, turn.play_head)
            turn.play_head = start + duration
            turn.speech.append(
                _Speech(
                    step=int(ev.get("step") or 0),
                    index=int(ev.get("index") or 0),
                    text=str(ev.get("text") or ""),
                    duration=duration,
                    start=start,
                    audio=str(ev.get("audio") or ""),
                    mime=str(ev.get("mime") or ""),
                )
            )
            turn.updated_at = now
        elif typ in ("done", "cancelled", "error"):
            turn.active = False
            turn.ended_at = now
            turn.updated_at = now

    def abort(self, channel: str, turn_id: int) -> None:
        """客户端断开/生成被中断：把仍在 active 的轮标成结束（不产生事件）。"""
        turn = self._turns.get(channel)
        if turn is not None and turn.id == turn_id and turn.active:
            turn.active = False
            turn.ended_at = time.monotonic()
            turn.updated_at = turn.ended_at

    def clear_turns(self, channel: Optional[str] = None) -> None:
        if channel is None:
            self._turns.clear()
            self._last_channel = None
            return
        self._turns.pop(channel, None)
        if self._last_channel == channel:
            self._last_channel = None

    # ---------- scene（权威状态，只有 SceneRunner / 管理端点能改） ----------

    def set_scene(self, scene_id: str, duration: float) -> Dict[str, Any]:
        """切到某个场景并返回它的快照（elapsed 从 0 起算）。"""
        self._scene = _Scene(
            id=scene_id,
            duration=max(0.0, float(duration)),
            started_at=time.monotonic(),
        )
        return self.scene_snapshot()

    def scene_id(self) -> Optional[str]:
        return self._scene.id if self._scene else None

    def clear_scene(self) -> None:
        self._scene = None

    def scene_snapshot(self, now: Optional[float] = None) -> Dict[str, Any]:
        """当前场景；没有场景（数据缺失/启动失败）时返回空 dict。"""
        scene = self._scene
        if scene is None:
            return {}
        now = time.monotonic() if now is None else now
        return {
            "id": scene.id,
            "duration": round(scene.duration, 3),
            "elapsed": round(max(0.0, now - scene.started_at), 3),
        }

    # ---------- snapshot ----------

    def settings(self) -> Dict[str, Any]:
        try:
            cfg = get_config()
            hide = int(getattr(cfg, "CAPTION_HIDE_MS", 1000))
            gap = int(getattr(cfg, "CAPTION_STEP_GAP_MS", 1000))
        except RuntimeError:
            hide, gap = 1000, 1000
        return {"caption": {"hide_ms": hide, "step_gap_ms": gap}}

    def snapshot(self, channel: Optional[str] = None) -> Dict[str, Any]:
        now = time.monotonic()
        out: Dict[str, Any] = {"settings": self.settings()}
        scene = self.scene_snapshot(now)
        if scene:
            out["scene"] = scene

        pick = channel or self._last_channel
        turn = self._turns.get(pick) if pick else None
        if turn is not None and self._turn_visible(turn, now):
            out["turn"] = {
                "id": turn.id,
                "active": turn.active,
                "speech": [self._segment(s, now) for s in turn.speech],
            }
        return out

    @staticmethod
    def _turn_visible(turn: _Turn, now: float) -> bool:
        if turn.active:
            return True
        return (now - turn.updated_at) <= TURN_GRACE_S

    @staticmethod
    def _segment(s: _Speech, now: float) -> Dict[str, Any]:
        consumed = min(max(now - s.start, 0.0), s.duration)
        seg: Dict[str, Any] = {
            "step": s.step,
            "index": s.index,
            "text": s.text,
            "duration": s.duration,
            "consumed": round(consumed, 2),
        }
        # 只有"正在发声"的句子带音频；已完成的句子只回文本，未来句由实时事件携带。
        if consumed < s.duration - 0.05 and s.audio:
            seg["audio"] = s.audio
            seg["mime"] = s.mime
        return seg
