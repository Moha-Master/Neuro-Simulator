"""neuro 后台生成 + 两条总线的测试脚手架（文件名不以 test_ 开头，避免被单独发现）。

只给测试用：不跑 lifespan（那需要真实 config/DB），手工把 `create_app()` 需要的
state 装好——端点自身就是闭包读 `app.state`，所以这里装什么、端点就吃什么。
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from neuro_simulator.broadcaster import EventBroadcaster
from neuro_simulator.neuro_sama import server as ns_server
from neuro_simulator.neuro_sama.agent import NeuroAgent
from neuro_simulator.neuro_sama.turns import TurnRunner
from neuro_simulator.neuro_sama.ui_state import UiState


class ScriptedAgent(NeuroAgent):
    """按脚本吐事件（可加每帧延迟），跳过 LLM。"""

    def __init__(self, events: list[dict], delay: float = 0.0) -> None:
        super().__init__(None, None, None, None, None)
        self._events = list(events)
        self.delay = delay
        self.started = asyncio.Event()

    async def _run(self, channel, conversation_id, user_text, cancel_event=None):
        self.started.set()
        for ev in self._events:
            if self.delay:
                await asyncio.sleep(self.delay)
            else:
                await asyncio.sleep(0)
            yield ev


class SlowAgent(NeuroAgent):
    """吐一句后一直等 cancel_event（模拟慢生成，验证取消/断连）。"""

    def __init__(self) -> None:
        super().__init__(None, None, None, None, None)
        self.started = asyncio.Event()

    async def _run(self, channel, conversation_id, user_text, cancel_event=None):
        yield {"type": "content", "step": 1, "delta": "half"}
        self.started.set()
        while cancel_event is None or not cancel_event.is_set():
            await asyncio.sleep(0.01)
        yield {"type": "cancelled"}


class Harness:
    def __init__(self, agent: NeuroAgent, ui: UiState | None = None) -> None:
        self.app = ns_server.create_app()
        self.ui = ui or UiState()
        self.memory = SimpleNamespace(count=0, used=0, char_limit=1000)
        self.agent = agent
        ctx = {"channel": None}
        # 两个有 I/O 的 provider 用固定值：测试只关心总线/分区形状，不探测配置
        with patch.object(ns_server, "_model_current", lambda: {"service_ok": True}), patch.object(
            ns_server,
            "_tts_current",
            lambda: {"effective_provider": "null", "degraded": False},
        ):
            self.board = ns_server.build_status_board(self.ui, ctx)
        self.state_bus = ns_server.build_state_bus(self.board)
        self.content_bus = EventBroadcaster()
        self.signal = ns_server.TurnSignal(self.board, self.ui)
        agent.event_sink = ns_server.make_observer(self.board, self.ui, self.signal)
        self.turns = TurnRunner(agent, self.ui, self.memory, self.content_bus)

        state = self.app.state
        state.agent = agent
        state.memory = self.memory
        state.ui_state = self.ui
        state.status_board = self.board
        state.status_channel = ctx
        state.state_bus = self.state_bus
        state.content_bus = self.content_bus
        state.turn_signal = self.signal
        state.turns = self.turns

    async def wait_result(self, turn, timeout: float = 3.0) -> dict:
        await asyncio.wait_for(turn.done_event.wait(), timeout)
        assert turn.result is not None
        return turn.result


async def next_frame(stream):
    """`anext(stream)` 的兼容写法（ruff 的 target 仍是 py3.8）。"""
    return await stream.__anext__()


def sse_payload(frame: str) -> dict:
    """从 `event: x\\ndata: {...}\\n\\n` 里取 data（测试断言用）。"""
    import json

    for line in frame.splitlines():
        if line.startswith("data: "):
            return json.loads(line[6:])
    raise AssertionError(f"不是 SSE 数据帧: {frame!r}")


def sse_event(frame: str) -> str:
    for line in frame.splitlines():
        if line.startswith("event: "):
            return line[7:].strip()
    raise AssertionError(f"不是 SSE 事件帧: {frame!r}")
