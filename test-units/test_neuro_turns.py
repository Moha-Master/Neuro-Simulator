"""生成与请求解耦：断连不终止、/stop 唯一取消、reload 有界等待、重连续播。"""

import asyncio
import contextlib
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from neuro_simulator.neuro_sama import server as ns_server
from neuro_simulator.neuro_sama.ui_state import UiState

from neuro_runtime import Harness, ScriptedAgent, SlowAgent, next_frame

CLOCK = "neuro_simulator.neuro_sama.ui_state.time.monotonic"

EVENTS = [
    {"type": "content", "step": 1, "delta": "hello"},
    {"type": "speech", "step": 1, "index": 0, "text": "hello",
     "duration": 0.2, "audio": "YQ==", "mime": "audio/mpeg"},
    {"type": "done", "content": "hello"},
]


def _client(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


class TestDisconnectDoesNotStopGeneration(unittest.IsolatedAsyncioTestCase):
    async def test_sse_disconnect_only_unsubscribes(self):
        agent = ScriptedAgent(EVENTS, delay=0.05)
        h = Harness(agent)
        queue = h.content_bus.subscribe()
        turn = h.turns.register("stream", "conv")
        h.turns.spawn(turn, "hi")

        stream = ns_server._chat_sse(turn, h.content_bus, queue, audio=True)
        first = await next_frame(stream)
        second = await next_frame(stream)
        self.assertEqual(first.splitlines()[0], "event: start")
        self.assertEqual(second.splitlines()[0], "event: content")
        await stream.aclose()  # = SSE 客户端断开

        payload = await h.wait_result(turn)
        self.assertEqual(payload["terminal"], "done")
        self.assertEqual(payload["text"], "hello")
        async with _client(h.app) as client:
            body = (await client.get("/status?channel=stream")).json()
        turn_proj = body["sections"]["turn"]["current"]
        self.assertFalse(turn_proj["generating"])
        self.assertEqual(turn_proj["in_flight"], 0)
        self.assertEqual(
            [s["text"] for s in h.ui.turn_segments("stream")], ["hello"],
            "断连后 UiState 时间轴仍然完整",
        )
        # 断连的订阅者已经被移除（生成器自己也退订了）
        self.assertEqual(h.content_bus.subscriber_count, 0)

    async def test_batch_caller_cancel_does_not_stop_generation(self):
        agent = ScriptedAgent(EVENTS, delay=0.1)
        h = Harness(agent)
        async with _client(h.app) as client:
            request = asyncio.create_task(
                client.post(
                    "/chat",
                    json={"message": "hi", "channel": "stream",
                          "conversation_id": "conv", "stream": False},
                )
            )
            await agent.started.wait()
            turn = h.turns.find("stream", "conv")
            self.assertIsNotNone(turn)
            request.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await request
            # 请求没了，生成还在跑
            self.assertIsNotNone(h.turns.find("stream", "conv"))
            payload = await h.wait_result(turn)
        self.assertEqual(payload["terminal"], "done")
        self.assertEqual(payload["text"], "hello")


class TestStopEndpoint(unittest.IsolatedAsyncioTestCase):
    async def test_stop_cancels_a_batch_generation(self):
        agent = SlowAgent()
        h = Harness(agent)
        async with _client(h.app) as client:
            request = asyncio.create_task(
                client.post(
                    "/chat",
                    json={"message": "hi", "channel": "stream",
                          "conversation_id": "conv", "stream": False},
                )
            )
            await asyncio.wait_for(agent.started.wait(), 2.0)
            resp = await client.post("/manage/chats/stream/conv/stop")
            self.assertEqual(resp.status_code, 200)
            self.assertTrue(resp.json()["stopped"])
            payload = (await asyncio.wait_for(request, 3.0)).json()
        self.assertEqual(payload["terminal"], "cancelled")
        self.assertEqual(payload["text"], "half", "半截内容照常返回（并已落库）")


class TestReload(unittest.IsolatedAsyncioTestCase):
    async def test_reload_cancels_turns_and_waits_bounded(self):
        agent = SlowAgent()
        h = Harness(agent)
        agent.registry = SimpleNamespace(all_names=lambda: [])
        h.app.state.mcp = SimpleNamespace(servers=[])
        turn = h.turns.register("stream", "conv")
        h.turns.spawn(turn, "hi")
        await asyncio.wait_for(agent.started.wait(), 2.0)

        cfg = SimpleNamespace(
            MEMORY_CHAR_LIMIT=999,
            MCP_SERVERS=[],
            NEURO_MODEL_REF="fake",
            HOST="127.0.0.1",
            PORT=8000,
            reload=lambda: None,
            neuro_llm=lambda: None,
        )
        with patch.object(ns_server, "get_config", return_value=cfg), patch.object(
            ns_server.tts, "reset_latch", lambda: None
        ):
            await asyncio.wait_for(ns_server._hot_reload(h.app), 2.0)

        self.assertTrue(turn.done_event.is_set(), "reload 返回时在途任务已经结束")
        self.assertEqual(h.turns.all_active(), [], "不留下未结束的任务")
        self.assertEqual(h.memory.char_limit, 999)


class TestReconnectSnapshot(unittest.IsolatedAsyncioTestCase):
    async def test_snapshot_has_consumed_and_audio_for_the_speaking_sentence(self):
        ui = UiState()
        h = Harness(ScriptedAgent([]), ui=ui)
        with patch(CLOCK, return_value=100.0):
            turn = h.turns.register("stream", "conv")
        with patch(CLOCK, return_value=101.0):
            ui.observe(
                {"type": "speech", "step": 0, "index": 0, "text": "one",
                 "duration": 5.0, "audio": "YQ==", "mime": "audio/mpeg"},
                "stream", turn.turn_id,
            )
            ui.observe(
                {"type": "speech", "step": 0, "index": 1, "text": "two",
                 "duration": 3.0, "audio": "Yg==", "mime": "audio/mpeg"},
                "stream", turn.turn_id,
            )
        with patch(CLOCK, return_value=102.0):
            snap = ui.content_snapshot("stream")

        speech = snap["speech"]
        self.assertEqual([s["text"] for s in speech], ["one", "two"])
        self.assertAlmostEqual(speech[0]["consumed"], 1.0)
        self.assertAlmostEqual(speech[1]["consumed"], 0.0, "第二句还没开始（排队中）")
        self.assertEqual(speech[0]["audio"], "YQ==", "正在发声那句必须带音频")
        self.assertEqual(speech[1]["audio"], "Yg==", "未播句也要带（重连才能续播）")
        self.assertTrue(snap["active"])


if __name__ == "__main__":
    unittest.main()
