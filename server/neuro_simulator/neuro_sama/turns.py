"""一轮生成的执行单元：生成活在后台任务里，HTTP 连接只是它的观察者。

为什么要有这一层：过去生成写在 `/chat` 的响应生成器里——客户端一断开，
`finally` 就把流 abort 掉，"谁在读这条流"决定了生成能否继续（dashboard 的
Chat 页一刷新，她的话说到一半就停了）。现在任务独立于任何连接：

- 断开连接 = 退订，不影响任务；
- **只有** `POST /manage/chats/{channel}/{conversation_id}/stop` 与
  `POST /manage/reload` 能取消（置位 `cancel_event`）；
- `/chat` 的 SSE 与批量模式都从同一个任务取数据，不存在第二套生成逻辑。

记账仍然在 agent 的产出侧（`event_sink`，见 agent.run），这里只做三件事：

1. 把 agent 的每个事件**一次**发布到内容总线（全量，含 base64 音频；
   `audio=false` 的裁剪在交付/序列化层做）；
2. 结束时把批量载荷存进 `ActiveTurn.result`（`audio` 同样留给交付层裁）；
3. 维护注册表：并发 409 的判据、`/stop` 的落点、结果保留 TTL。
"""

import asyncio
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from ..broadcaster import EventBroadcaster
from ..config import get_config
from .agent import NeuroAgent
from .memory import MemoryStore
from .ui_state import UiState

# 一轮结束后结果在注册表里保留多久：批量调用方可能比生成结束晚一点才来取
# （网络抖动/事件循环排队），但没必要留到下一轮。
TURN_RESULT_TTL_S = 60.0

TERMINAL_EVENTS = ("done", "cancelled", "error")
_TERMINAL_SET = frozenset(TERMINAL_EVENTS)


@dataclass
class ActiveTurn:
    """一轮在途生成的可观察句柄（注册表里的值）。"""

    turn_id: int
    channel: str
    conversation_id: str
    cancel_event: asyncio.Event
    # 生成结束（done/cancelled/error）时置位；`/reload` 等它做有界等待
    done_event: asyncio.Event
    # 批量契约（terminal/text/segments/...）；结束时才非 None
    result: Optional[dict] = None
    finished_at: Optional[float] = None


class TurnConflict(RuntimeError):
    """同一 (channel, conversation_id) 已有在途生成（→ 409）。"""


class TurnRunner:
    """生成任务注册表：spawn、取消、有界等待、结果保留。

    注册表键是 `(channel, conversation_id)`（与 `/stop`、并发 409 同一口径）。
    `UiState` 的时间轴按 channel 存（每个 channel 至多一条可见轮次），那是
    另一个机制；这里只管"任务还在不在跑"。
    """

    def __init__(
        self,
        agent: NeuroAgent,
        ui_state: UiState,
        memory: MemoryStore,
        bus: EventBroadcaster,
        *,
        printer: Optional[Callable[[str, dict], None]] = None,
        on_finish: Optional[Callable[[ActiveTurn], None]] = None,
    ) -> None:
        self._agent = agent
        self._ui_state = ui_state
        self._memory = memory
        self._bus = bus
        self._printer = printer
        self._on_finish = on_finish
        self._turns: Dict[Tuple[str, str], ActiveTurn] = {}
        self._tasks: Dict[Tuple[str, str], asyncio.Task] = {}

    # ---------- 注册表 ----------

    def find(self, channel: str, conversation_id: str) -> Optional[ActiveTurn]:
        """在途（还没结束）的那一轮；已结束但结果仍在 TTL 内不算。"""
        turn = self._turns.get((channel, conversation_id))
        if turn is None or turn.done_event.is_set():
            return None
        return turn

    def all_active(self) -> List[ActiveTurn]:
        return [t for t in self._turns.values() if not t.done_event.is_set()]

    def register(self, channel: str, conversation_id: str) -> ActiveTurn:
        """登记一轮并分配 turn_id；同会话已有在途轮次时抛 TurnConflict。

        **调用方必须在没有 await 的同步段里完成"检查 + 登记"**（单事件循环内的
        原子性）：中间让出控制权就可能两个请求同时通过检查。
        """
        key = (channel, conversation_id)
        if self.find(channel, conversation_id) is not None:
            raise TurnConflict(f"{channel}:{conversation_id} 已有在途生成")
        turn = ActiveTurn(
            turn_id=self._ui_state.new_turn(channel),
            channel=channel,
            conversation_id=conversation_id,
            cancel_event=asyncio.Event(),
            done_event=asyncio.Event(),
        )
        self._turns[key] = turn
        return turn

    def spawn(self, turn: ActiveTurn, message: str) -> asyncio.Task:
        """在后台跑这一轮；返回的 task 只用于错误观测，不要 await 它（会拖住断连）。"""
        key = (turn.channel, turn.conversation_id)
        task = asyncio.create_task(self._run(turn, message), name=f"neuro-turn-{turn.turn_id}")
        self._tasks[key] = task
        return task

    # ---------- 取消 / 有界等待 ----------

    def cancel_all(self) -> List[ActiveTurn]:
        """置位全部在途轮次的 cancel_event（`/manage/reload` 用）。"""
        turns = self.all_active()
        for turn in turns:
            turn.cancel_event.set()
        return turns

    @staticmethod
    async def wait_all(turns: List[ActiveTurn], timeout: float) -> bool:
        """等这些轮次真正结束；超时返回 False（调用方记日志后继续）。"""
        if not turns:
            return True
        waiters = [asyncio.ensure_future(t.done_event.wait()) for t in turns]
        try:
            _, pending = await asyncio.wait(waiters, timeout=timeout)
            return not pending
        finally:
            for waiter in waiters:
                if not waiter.done():
                    waiter.cancel()

    # ---------- 生成 ----------

    async def _run(self, turn: ActiveTurn, message: str) -> None:
        terminal: Optional[str] = None
        error_message: Optional[str] = None
        try:
            self._publish(turn, {"type": "start", "model": _model_name()})
            async for ev in self._agent.run(
                turn.channel,
                turn.conversation_id,
                message,
                cancel_event=turn.cancel_event,
                turn_id=turn.turn_id,
            ):
                if self._printer is not None:
                    self._printer(ev["type"], ev)
                if ev["type"] == "done":
                    # 终端观察用的记忆统计；它属于交付载荷，不属于事件本身
                    ev = {
                        **ev,
                        "_memory": {
                            "count": self._memory.count,
                            "used": self._memory.used,
                            "char_limit": self._memory.char_limit,
                        },
                    }
                self._publish(turn, ev)
                if ev["type"] in _TERMINAL_SET:
                    terminal = ev["type"]
                    if terminal == "error":
                        error_message = str(ev.get("message") or "")
        except asyncio.CancelledError:
            # 任务被外部取消（进程收尾）：仍要让等待方拿到三态之一，然后照常传播取消
            terminal = "cancelled"
            self._publish(turn, {"type": "cancelled"})
            raise
        except Exception as e:  # agent 正常走 error 事件；这里兜住意外异常
            terminal = "error"
            error_message = f"{type(e).__name__}: {e}"
            self._publish(turn, {"type": "error", "message": error_message})
        finally:
            if terminal is None:
                # 理论不该发生；仍必须满足三态之一（与旧 _collect_batch 的兜底一致）
                terminal = "error"
                error_message = error_message or "generation ended without a terminal event"
                self._publish(turn, {"type": "error", "message": error_message})
            # 防御：agent 异常退出时 UiState 可能还挂着 active
            self._ui_state.abort(turn.channel, turn.turn_id)
            if self._on_finish is not None:
                # 让状态平面看到"generating 变 false"（正常路径事件已经喂过
                # observer，这里的 abort 只是兜底；重复喊一次 rev 无害）
                self._on_finish(turn)
            # 结果存全量（含 audio）；audio=false 的调用方在交付层裁剪
            turn.result = self._ui_state.batch(
                turn.channel,
                turn.conversation_id,
                turn.turn_id,
                terminal,
                message=error_message,
                audio=True,
            )
            turn.finished_at = time.monotonic()
            key = (turn.channel, turn.conversation_id)
            self._tasks.pop(key, None)
            # 先存结果再置位：等 done_event 的一方醒来时 result 一定已就绪
            turn.done_event.set()
            self._schedule_evict(key, turn.turn_id)

    def _publish(self, turn: ActiveTurn, ev: dict) -> None:
        """内容总线只发一份全量事件（音频不裁）。"""
        self._bus.publish(
            {
                "channel": turn.channel,
                "conversation_id": turn.conversation_id,
                **ev,
                "turn_id": turn.turn_id,
            }
        )

    # ---------- 结果清理 ----------

    def _schedule_evict(self, key: Tuple[str, str], turn_id: int) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # 没有循环（测试收尾）时不必清理
            return
        loop.call_later(TURN_RESULT_TTL_S, self._evict, key, turn_id)

    def _evict(self, key: Tuple[str, str], turn_id: int) -> None:
        turn = self._turns.get(key)
        # turn_id 校验：同会话的新一轮可能已经覆盖了这个键，别把新轮删了
        if turn is not None and turn.turn_id == turn_id and turn.done_event.is_set():
            self._turns.pop(key, None)


def _model_name() -> Optional[str]:
    """start 事件里的模型名（与 /health 同一判断）；配置没加载时给 None。"""
    try:
        svc = get_config().neuro_llm()
    except RuntimeError:
        return None
    return svc.model if svc else None
