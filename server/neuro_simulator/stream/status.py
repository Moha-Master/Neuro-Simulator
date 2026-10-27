"""stream 的状态机投影：把 loop / 场景 / 游戏 / 缓存拼成 `/stream/status` 的分区。

**派生不复制**（本文件最重要的纪律）：
- `loop` 读 `StreamLoop` 自己的字段与缓存（队列深度、persisted、neuro_link 都是
  loop 在产生事件时顺手记的，不在这里重算）；
- `scene` 的 `desc/label/icon` 现查场景清单，`duration/elapsed` 用 loop 的权威快照；
- `scene.graph.states` 从场景清单现生成（清单是唯一权威，不另抄一份表）；
- `games` 直接复用 `GameHub.element_states()` —— 与 `element_state` 广播同一份
  全量快照，不改写、不二次计算；wordle 的 `answer` 只在结束后由快照本身揭晓；
- `audience` 只读 `AudienceTracker.snapshot()`（I/O 在它自己的后台任务里）。
"""

from typing import Any, Dict, List, Optional

from .. import state_kit
from . import scenes as scene_catalog
from . import settings as stream_settings

MODULE = "stream"

_LOOP_GRAPH = {
    "states": [
        {"id": "offline", "label": "Offline", "desc": "No stream session is running."},
        {"id": "starting", "label": "Starting", "desc": "A session exists and the loop is waiting for Neuro to leave her intro scene."},
        {"id": "live", "label": "Live", "desc": "The loop is running rounds and consuming the viewer queue."},
        {"id": "paused", "label": "Paused", "desc": "The session is on air but the loop is not consuming rounds."},
    ],
    "transitions": [
        {"from": "offline", "to": "starting", "by": "POST /stream/start"},
        {"from": "starting", "to": "live", "by": "neuro left the intro scene"},
        {"from": "starting", "to": "offline", "by": "intro_timeout or POST /stream/stop"},
        {"from": "live", "to": "paused", "by": "POST /stream/pause"},
        {"from": "paused", "to": "live", "by": "POST /stream/resume"},
        {"from": "live", "to": "offline", "by": "POST /stream/stop"},
        {"from": "paused", "to": "offline", "by": "POST /stream/stop"},
    ],
}

# 每个游戏的 phase 表是手写的（游戏自身没有状态机引擎）；`current.phase` 由
# 既有快照字段推出。graph 的 id 加游戏前缀，避免两个游戏都有 "ended" 时混淆。
_GAME_PHASES: Dict[str, List[Dict[str, str]]] = {
    "wordle": [
        {"id": "empty", "label": "Empty", "desc": "No letters have been typed for the current row."},
        {"id": "typing", "label": "Typing", "desc": "A partial guess is being typed into the current row."},
        {"id": "submitted", "label": "Submitted", "desc": "A guess was submitted and the next row is empty."},
        {"id": "won", "label": "Won", "desc": "The hidden word was guessed."},
        {"id": "lost", "label": "Lost", "desc": "All rows were used without guessing the word."},
    ],
    "chess": [
        {"id": "challenging", "label": "Challenging", "desc": "Waiting for the Lichess game to start."},
        {"id": "white_to_move", "label": "White to move", "desc": "The game is running and White must move."},
        {"id": "black_to_move", "label": "Black to move", "desc": "The game is running and Black must move."},
        {"id": "ended", "label": "Ended", "desc": "The game is over."},
    ],
}

_GAME_TRANSITIONS: Dict[str, List[Dict[str, str]]] = {
    "wordle": [
        {"from": "empty", "to": "typing", "by": "type"},
        {"from": "typing", "to": "empty", "by": "delete all letters"},
        {"from": "typing", "to": "submitted", "by": "submit"},
        {"from": "submitted", "to": "typing", "by": "type"},
        {"from": "submitted", "to": "won", "by": "submit a correct guess"},
        {"from": "submitted", "to": "lost", "by": "submit the last wrong guess"},
    ],
    "chess": [
        {"from": "challenging", "to": "white_to_move", "by": "Lichess game starts with White to move"},
        {"from": "white_to_move", "to": "black_to_move", "by": "White moves"},
        {"from": "black_to_move", "to": "white_to_move", "by": "Black moves"},
        {"from": "white_to_move", "to": "ended", "by": "checkmate, draw, or resign"},
        {"from": "black_to_move", "to": "ended", "by": "checkmate, draw, or resign"},
    ],
}


def build_board(loop: Any, audience: Any) -> state_kit.StateBoard:
    """装配 stream 的分区（启动时调用一次）。"""
    board = state_kit.StateBoard(MODULE)
    board.register("loop", lambda: _loop_current(loop), graph=_LOOP_GRAPH)
    board.register("scene", lambda: _scene_current(loop), graph=_scene_graph())
    board.register("metadata", lambda: _metadata_current(loop))
    board.register("audience", audience.snapshot)
    board.register("chat", lambda: _chat_current(loop))
    board.register("chatbot", lambda: _chatbot_current(loop))
    board.register("games", lambda: _games_current(loop), graph=_games_graph())
    board.register("neuro_link", lambda: dict(loop.neuro_link))
    return board


# ---------- 各分区 ----------


def _loop_current(loop: Any) -> dict:
    session = loop.session or {}
    return {
        "state": loop.state,
        "session_id": session.get("session_id"),
        "queue_id": session.get("queue_id"),
        "channel": session.get("channel"),
        "conversation_id": session.get("conversation_id"),
        "round": int(session.get("rounds") or 0),
        "cursor": session.get("cursor"),
        "highlight_cursor": session.get("highlight_cursor"),
        "highlight_paused": bool(getattr(loop, "highlight_paused", False)),
        "highlight_source": getattr(loop, "highlight_source", None),
        # 模拟直播（record）：loop 在跑但不算"开播"（见 metadata.live 判据）
        "recording": bool(getattr(loop, "recording", False)),
        "intro": loop.intro_status(),
        "last_error": loop.last_error,
        "started_at": session.get("started_at"),
        "persisted": loop.persisted,
    }


def _scene_current(loop: Any) -> Optional[dict]:
    snap = loop.scene_snapshot()
    sid = str(snap.get("id") or "")
    if not sid:
        return None
    # 场景自带的规则指令（Wordle/Chess 玩法）走状态投影；没有就用 config 的
    # 全局 scene_context。消息模板只需读 `stream_status.scene.prompt_context`，
    # 不再由 loop 侧单独拼一个 scene_context 变量。
    prompt_context = scene_catalog.prompt_context(sid) or _global_scene_context()
    return {
        "id": sid,
        "label": scene_catalog.label(sid) or sid,
        "icon": scene_catalog.icon(sid),
        "desc": scene_catalog.desc(sid),
        "prompt_context": prompt_context,
        "highlight_messages": scene_catalog.highlight_messages_enabled(sid),
        "duration": snap.get("duration"),
        "elapsed": snap.get("elapsed"),
    }


def _global_scene_context() -> str:
    try:
        return str(stream_settings.load().scene_context or "")
    except (RuntimeError, AttributeError):
        return ""


def _metadata_current(loop: Any) -> dict:
    title, category = loop.session_metadata()
    return {
        "title": title,
        "category": category,
        "tags": loop.session_tags(),
        # `running` 与 `live` 正交：record 场次循环在跑（starting/live/paused），但不对外
        # 宣称开播。正常 starting 也算 live——观众要能看开场。
        "live": loop.session is not None and not bool(getattr(loop, "recording", False)),
    }


def _chat_current(loop: Any) -> dict:
    # 无场次时队列不存在（旧契约也是 null），不能报 0 假装有空队列
    if loop.session is None:
        return {"normal_pending": None, "highlight_pending": None, "last_message_id": None}
    return {
        "normal_pending": loop.queue_depth,
        "highlight_pending": loop.highlight_depth,
        "last_message_id": loop.last_message_id,
    }


def _chatbot_current(loop: Any) -> dict:
    cb = stream_settings.load().chatbot
    return {
        "enabled": cb.enabled,
        "model": cb.model or None,
        "model_ref": cb.model_ref or None,
        "interval_s": cb.interval_s,
        "count": cb.count,
        "max_concurrent": cb.max_concurrent,
        "interval_jitter": cb.interval_jitter,
        "in_flight": loop.chatbot.in_flight,
        "discarded": loop.chatbot.discarded,
        "last_discard": loop.chatbot.last_discard,
        # 消息模板最近一次实况渲染失败（成功即清除）；dashboard 的 State 页一眼可见
        "template_error": loop.templates.template_error,
    }


def _games_current(loop: Any) -> dict:
    """三层 + 观众解说：`{active, <game_id>: {phase, summary, viewer, element}}`。

    `element` 直接复用 `GameHub.element_states()` 的那份对象（与画面广播同一份
    快照），不重新构造、也不把 phase/summary/viewer 混进原始快照里。
    `summary` 给 neuro（`narrative`），`viewer` 给 chatbot（大众化解说）。
    """
    hub = loop.games
    current: Dict[str, Any] = {"active": hub.active_id}
    if hub.active is None:
        return current
    game = hub.active
    game_id = game.id
    snap = hub.element_states().get(game.element_id) or {}
    current[game_id] = {
        "phase": _game_phase(game_id, snap),
        "summary": game.narrative(snap),
        "viewer": game.viewer(snap),
        "element": snap,
    }
    return current


def _game_phase(game_id: str, snap: Dict[str, Any]) -> Optional[str]:
    if not snap:
        return None
    status = snap.get("status")
    if game_id == "wordle":
        if status in ("won", "lost"):
            return status
        if status == "playing":
            rows = snap.get("rows") or []
            row = int(snap.get("current_row") or 0)
            if 0 <= row < len(rows) and rows[row]:
                return "typing"
            return "submitted" if snap.get("guesses_used") else "empty"
        return None
    if game_id == "chess":
        if status == "ended":
            return "ended"
        if status == "playing":
            return "white_to_move" if snap.get("turn") == "white" else "black_to_move"
        return "challenging"
    return None


# ---------- graph ----------


def _scene_graph() -> dict:
    """states 从清单现生成（含 hidden 场景）；transitions 是手写小表。"""
    states = [
        {
            "id": str(s.get("id")),
            "label": str(s.get("label") or s.get("id")),
            "desc": str(s.get("desc") or ""),
        }
        for s in scene_catalog.load_scenes()
    ]
    return {
        "states": states,
        "transitions": [
            {"from": "starting_soon", "to": "room_intro", "by": "loop start"},
            {"from": "room_intro", "to": "room", "by": "loop once neuro leaves the intro scene"},
            {"from": "room", "to": "play_wordle", "by": "game start"},
            {"from": "play_wordle", "to": "room", "by": "game exit"},
            {"from": "room", "to": "play_chess", "by": "game start"},
            {"from": "play_chess", "to": "room", "by": "game exit"},
            {"from": "room", "to": "community_art", "by": "manual"},
            {"from": "community_art", "to": "room", "by": "manual"},
            {"from": "room", "to": "gameplay", "by": "manual"},
            {"from": "gameplay", "to": "room", "by": "manual"},
            {"from": "room", "to": "starting_soon", "by": "manual (debug)"},
        ],
    }


def _games_graph() -> dict:
    states: List[Dict[str, str]] = []
    transitions: List[Dict[str, str]] = []
    for game_id, phases in _GAME_PHASES.items():
        states.extend(
            {
                "id": f"{game_id}.{p['id']}",
                "label": f"{game_id}: {p['label']}",
                "desc": p["desc"],
            }
            for p in phases
        )
        transitions.extend(
            {
                "from": f"{game_id}.{t['from']}",
                "to": f"{game_id}.{t['to']}",
                "by": t["by"],
            }
            for t in _GAME_TRANSITIONS.get(game_id, [])
        )
    return {"states": states, "transitions": transitions}
