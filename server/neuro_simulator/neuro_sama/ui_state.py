"""neuro 窗口的共享状态：当前场景 + 当前/最近一轮语音（speech）时间线。

这里对外提供**两个面**，用途和频率不同：

- `snapshot()` → **画面快照**（`/ui/events` 与 `/ui/state`）：逐句带 `consumed`，
  正在发声那句还带 base64 `audio`，供渲染器（OBS 重载、dashboard 预览）断点续播。
- `status()` → **状态投影**（`/status`）：只有"当前场景 + 还剩多久说话 + 最近说了什么"，
  不含音频，供其他模块高频轮询（stream 判开场是否放完、判下一轮节奏；chatbot 判她是否
  睡着；将来的 Discord/QQ 接入同样消费它）。

时间基准是服务端 `time.monotonic()`；`status()` 里的 `ends_at` 已换算成 unix 秒——
**跨进程不要对 monotonic 时钟**，所以对外只给"还剩多少秒"和换算后的绝对时刻。

**场景是权威状态**：只有 `SceneRunner`（后端脚本执行器）与管理端点能改，
渲染器只读快照并如实渲染；`timeline` 是前端脚本、`state_script` 是后端脚本，
分工见 scenes.py。

turn 时间线的推进方式：收到 speech 事件时，从 `max(now, play_head + step_gap)` 开始排，
`play_head += duration`。`step_gap`（`neuro_sama.caption_step_gap_ms`）只在**同一轮的
step 发生变化**时计入，与前端 `speech.ts` 的实际播放一致——这样"画面里还有多少话没播完"
在服务端和浏览器里是同一个数。
"""

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..config import get_config

# 一轮结束后保留快照的时间窗口：足够覆盖字幕淡出，避免重连时闪出很久以前的字幕。
TURN_GRACE_S = 10.0

# status() 里"最近说了什么"的截断长度（弹幕/字幕级别的文本，给消费者回看用）
STATUS_TEXT_CHARS = 400


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
    last_step: Optional[int] = None


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
            step = int(ev.get("step") or 0)
            # 同一轮的工具链 step 之间，窗口会停 caption_step_gap_ms 再播下一句
            # （前端 speech.ts 的实际播放就是这样）。服务端时间线必须计入同一个 gap，
            # 否则 play_head 比画面短，下游消费 remaining_s/ends_at 的一方
            # （stream 的轮次节奏、chatbot 的睡眠判定）都会提前。
            gap_s = 0.0
            if turn.last_step is not None and step != turn.last_step:
                gap_s = max(0.0, self.settings()["caption"]["step_gap_ms"] / 1000.0)
            start = max(now, turn.play_head + gap_s)
            turn.play_head = start + duration
            turn.last_step = step
            turn.speech.append(
                _Speech(
                    step=step,
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

    # ---------- status（其他模块消费的精简投影） ----------

    def status(self, channel: Optional[str] = None) -> Dict[str, Any]:
        """"我在哪个场景 / 还剩多久说话"——给别的模块轮询用，**不含音频**。

        与 `snapshot()` 的分工：snapshot 是**画面快照**（逐句 consumed，正在发声那句还带
        base64 音频），供渲染器断点续播；status 是**状态投影**，供 stream 的直播循环判
        定开场是否放完、chatbot 判定她是不是睡着了、将来的 Discord/QQ 接入判节奏。
        字段可以重叠，但用途和频率不同——status 被 0.5s 一次地拉也不该拖几 MB。
        """
        now = time.monotonic()
        pick = channel or self._last_channel
        turn = self._turns.get(pick) if pick else None
        remaining = max(0.0, turn.play_head - now) if turn is not None else 0.0
        text = ""
        if turn is not None and turn.speech:
            text = " ".join(s.text for s in turn.speech if s.text)[:STATUS_TEXT_CHARS]
        return {
            "scene": self.scene_snapshot(now) or None,
            "speech": {
                "channel": pick,
                "turn_id": turn.id if turn is not None else None,
                # generating = 本轮还在生成；speaking = 还有声音/字幕要播
                "generating": bool(turn and turn.active),
                "speaking": remaining > 0.05,
                "remaining_s": round(remaining, 2),
                # 本轮语音预测播完的 unix 时刻；可能落在过去（= 她上次说完的时刻）
                "ends_at": round(time.time() + (turn.play_head - now), 3) if turn else None,
                "text": text,
            },
            "turns": {"in_flight": sum(1 for t in self._turns.values() if t.active)},
        }

    # ---------- snapshot（画面消费） ----------

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
