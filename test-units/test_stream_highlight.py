"""双轨队列（普通 / 高亮）行为测试：高亮轨先进先出、一次一条、优先不污染普通轨。

高亮消息的**入列**与**注入**是两件事：入列只广播 `highlight_message` 并进聊天层，
真正的浮层在消息被 `_pick` 选中、随 `POST /chat` 发给 neuro 之后才由 neuro 窗口弹出。
暂停开关只影响高亮轨消费，不丢消息、不重排。
"""

import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from neuro_simulator.broadcaster import EventBroadcaster
from neuro_simulator.stream import scenes as scene_catalog
from neuro_simulator.stream.loop import StreamConflict, StreamLoop
from neuro_simulator.stream.neuro_mirror import NeuroMirror
from neuro_simulator.templates import TemplateRenderError
from neuro_simulator.stream.persona import ChatbotPersonaStore, build_template_store
from neuro_simulator.stream.storage import Storage


class _FakeNeuroClient:
    """批量模式替身：生成结果与状态都来自 neuro（stream 不再自己算时间轴）。"""

    def __init__(self, url: str, *, ends_in_s: float = 0.0, text: str = "ta-da") -> None:
        self.url = url
        self.base_url = url
        self.ends_in_s = ends_in_s
        self.text = text
        self.chat_calls = 0
        self.audio_flags: list[bool] = []
        self.highlights: list[list[dict] | None] = []
        self.status_calls = 0
        self.captured: dict[str, str] = {}

    async def chat_batch(
        self,
        message: str,
        channel: str,
        conversation_id: str,
        audio: bool = False,
        highlights: list[dict] | None = None,
    ) -> dict:
        self.chat_calls += 1
        self.audio_flags.append(audio)
        self.highlights.append(highlights)
        self.captured["text"] = message
        return {
            "turn_id": 7,
            "terminal": "done",
            "text": self.text,
            "segments": [],
        }

    async def status(self, channel=None) -> dict:
        self.status_calls += 1
        return {
            "sections": {
                "turn": {
                    "current": {
                        "turn_id": 7,
                        "speaking": self.ends_in_s > 0,
                        "ends_at": time.time() + self.ends_in_s,
                    }
                }
            }
        }


class TestHighlightQueue(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = Storage(Path(self.tmp.name) / "test.db")
        await self.storage.init()
        self.persona = ChatbotPersonaStore(self.storage)
        await self.persona.load()
        self.templates = build_template_store(self.storage)
        await self.templates.load()
        self.bus = EventBroadcaster()
        self.loop = StreamLoop(self.storage, self.bus, self.persona, self.templates)
        self.queue_row, _ = await self.storage.create_queue("test queue")
        _, session_id = await self.storage.create_session(
            self.queue_row, "stream", "conv-highlight-test"
        )
        row = await self.storage.get_session(session_id)
        self.session = {
            "session_row": row["id"],
            "queue_row": self.queue_row,
            "channel": "stream",
            "conversation_id": "conv-highlight-test",
            "cursor": 0,
            "highlight_cursor": 0,
        }
        self.settings = SimpleNamespace(
            messages_per_round=10,
            speech_cooldown_ms=0,
            empty_queue_behavior="silent",
            placeholder_message="placeholder",
            scene_context="ctx",
            neuro_url="http://fake",
        )

    async def asyncTearDown(self):
        await self.loop._scenes.stop()
        await self.storage.close()
        self.tmp.cleanup()

    async def test_highlight_first_then_normal(self):
        n1 = await self.storage.append_message(self.queue_row, "a", "normal 1")
        p1 = await self.storage.append_message(
            self.queue_row, "b", "pay 1", highlight_type="bits", value=100
        )
        p2 = await self.storage.append_message(
            self.queue_row, "c", "pay 2", highlight_type="points", value=200
        )
        n2 = await self.storage.append_message(self.queue_row, "d", "normal 2")

        picked = await self.loop._pick(self.settings, self.session)
        self.assertEqual(picked["kind"], "highlight")
        self.assertEqual([m["id"] for m in picked["messages"]], [p1])
        self.session["highlight_cursor"] = p1

        picked = await self.loop._pick(self.settings, self.session)
        self.assertEqual(picked["kind"], "highlight")
        self.assertEqual([m["id"] for m in picked["messages"]], [p2])
        self.session["highlight_cursor"] = p2

        # 高亮轨空后回落普通轨；高亮消息不会混入普通候选
        picked = await self.loop._pick(self.settings, self.session)
        self.assertEqual(picked["kind"], "normal")
        self.assertEqual([m["id"] for m in picked["messages"]], [n1, n2])

    async def test_depths_separate(self):
        await self.storage.append_message(self.queue_row, "a", "normal")
        await self.storage.append_message(
            self.queue_row, "b", "paid", highlight_type="bits", value=10
        )
        self.assertEqual(await self.storage.queue_depth_after(self.queue_row, 0), 1)
        self.assertEqual(await self.storage.highlight_depth_after(self.queue_row, 0), 1)

    async def test_append_broadcasts_highlight_event(self):
        self.loop.session = self.session
        q = self.bus.subscribe()
        msg_id = await self.loop.append_message(
            "fan", "hello", highlight_type="points", value=250
        )
        ev = q.get_nowait()
        self.assertEqual(ev["type"], "highlight_message")
        self.assertEqual(ev["id"], msg_id)
        self.assertEqual(ev["highlight_type"], "points")
        self.assertEqual(ev["value"], 250)

    async def test_append_broadcasts_normal_event(self):
        self.loop.session = self.session
        q = self.bus.subscribe()
        msg_id = await self.loop.append_message("viewer", "hi")
        ev = q.get_nowait()
        self.assertEqual(ev["type"], "chat_message")
        self.assertEqual(ev["id"], msg_id)
        self.assertNotIn("highlight_type", ev)

    async def test_append_messages_batches_in_one_entrypoint(self):
        """chatbot 批量入列：逐条广播、深度累加、整批只唤醒一次。"""
        self.loop.session = self.session
        calls: list[int] = []
        original = self.loop.on_new_message

        def _count():
            calls.append(1)
            original()

        self.loop.on_new_message = _count  # type: ignore[method-assign]
        q = self.bus.subscribe()
        ids = await self.loop.append_messages([("a", "one"), ("b", "two")])

        events = [q.get_nowait(), q.get_nowait()]
        self.assertEqual([e["type"] for e in events], ["chat_message", "chat_message"])
        self.assertEqual([e["id"] for e in events], ids)
        self.assertEqual([e["content"] for e in events], ["one", "two"])
        self.assertEqual(self.loop.queue_depth, 2)
        self.assertEqual(self.loop.last_message_id, ids[-1])
        self.assertEqual(len(calls), 1, "整批只能唤醒一次")

        rows = await self.storage.list_messages(self.queue_row)
        self.assertEqual([r["content"] for r in rows], ["one", "two"])

    async def test_pause_skips_highlight_track_without_advancing_cursor(self):
        n1 = await self.storage.append_message(self.queue_row, "a", "normal 1")
        p1 = await self.storage.append_message(
            self.queue_row, "b", "pay 1", highlight_type="bits", value=100
        )
        self.loop.highlight_paused = True
        self.loop.highlight_source = "manual"

        picked = await self.loop._pick(self.settings, self.session)
        self.assertEqual(picked["kind"], "normal")
        self.assertEqual([m["id"] for m in picked["messages"]], [n1])

        fake = _FakeNeuroClient("http://fake")
        with patch("neuro_simulator.stream.loop.NeuroClient", lambda url: fake):
            await self.loop._run_round(self.settings, self.session)

        self.assertEqual(self.session["cursor"], n1)
        self.assertEqual(self.session["highlight_cursor"], 0, "暂停时游标不能推进")
        self.assertEqual(self.loop.highlight_depth, 1, "积压数保留")
        self.assertEqual(fake.chat_calls, 1)
        self.assertIsNone(fake.highlights[0], "普通轮不带 highlights")
        backlog = await self.storage.list_highlight_messages(self.queue_row, 0)
        self.assertEqual([m["id"] for m in backlog], [p1])

    async def test_resume_consumes_backlog_from_the_oldest(self):
        p1 = await self.storage.append_message(
            self.queue_row, "b", "pay 1", highlight_type="bits", value=100
        )
        p2 = await self.storage.append_message(
            self.queue_row, "c", "pay 2", highlight_type="points", value=200
        )
        self.loop.highlight_paused = True
        self.assertIsNone(await self.loop._pick(self.settings, self.session))

        self.loop.highlight_paused = False
        picked = await self.loop._pick(self.settings, self.session)
        self.assertEqual(picked["kind"], "highlight")
        self.assertEqual([m["id"] for m in picked["messages"]], [p1])

        fake = _FakeNeuroClient("http://fake")
        with patch("neuro_simulator.stream.loop.NeuroClient", lambda url: fake):
            await self.loop._run_round(self.settings, self.session)
        self.assertEqual(self.session["highlight_cursor"], p1)
        self.assertEqual(self.session["cursor"], 0)
        self.assertEqual(self.loop.highlight_depth, 1)

        picked = await self.loop._pick(self.settings, self.session)
        self.assertEqual([m["id"] for m in picked["messages"]], [p2])

    async def test_selected_highlight_sends_metadata_to_neuro(self):
        msg_id = await self.storage.append_message(
            self.queue_row, "fan", "hi there", highlight_type="bits", value=420
        )
        fake = _FakeNeuroClient("http://fake")
        with patch("neuro_simulator.stream.loop.NeuroClient", lambda url: fake):
            await self.loop._run_round(self.settings, self.session)

        self.assertEqual(
            fake.highlights,
            [
                [
                    {
                        "id": msg_id,
                        "username": "fan",
                        "content": "hi there",
                        "highlight_type": "bits",
                        "value": 420,
                    }
                ]
            ],
        )
        self.assertEqual(self.session["highlight_cursor"], msg_id)
        # prompt 里仍然与普通消息完全同格式（元数据不发进模板）
        self.assertIn("- fan: hi there", fake.captured["text"])
        self.assertNotIn("bits", fake.captured["text"])

    async def test_stream_scene_flag_pauses_on_scene_change(self):
        scenes = [
            {
                "id": "room",
                "label": "房间",
                "highlight_messages": False,
                "elements": [
                    {
                        "id": "chat",
                        "z": 20,
                        "box": {"x": 0, "y": 0, "w": 0.25, "h": 0.5},
                        "content": {"kind": "component", "name": "chat.overlay"},
                    }
                ],
                "neuro": {"x": 0, "y": 0, "w": 1, "hide": []},
            }
        ]
        with patch.object(scene_catalog, "load_scenes", return_value=scenes):
            self.loop.on_scene("room")
        self.assertTrue(self.loop.highlight_paused)
        self.assertEqual(self.loop.highlight_source, "scene:stream")

    async def test_manual_set_requires_a_session(self):
        with self.assertRaises(StreamConflict):
            self.loop.set_highlight_paused(True, source="manual")
        self.loop.session = self.session
        self.loop.set_highlight_paused(True, source="manual")
        self.assertTrue(self.loop.highlight_paused)
        self.assertEqual(self.loop.highlight_source, "manual")

    async def test_neuro_scene_sse_drives_pause_switch(self):
        """NeuroMirror 的状态订阅：normal=true/unpause、intro=false/pause、null=pause。"""

        class _SceneNeuro(_FakeNeuroClient):
            async def chat_events(self, channel=None, want=None, drop=None, audio=True):
                await asyncio.sleep(10)
                yield {}  # pragma: no cover - 内容平面与本用例无关

            async def state_events(self, want=None, drop=None):
                yield {"sections": {"scene": {"current": None}}}
                yield {"sections": {"scene": {"current": {
                    "id": "normal", "highlight_messages": True}}}}
                yield {"sections": {"scene": {"current": {
                    "id": "intro", "highlight_messages": False}}}}
                yield {"sections": {"scene": {"current": {
                    "id": "normal", "highlight_messages": True}}}}
                await asyncio.sleep(10)

        calls = []
        original = self.loop._set_highlight_paused

        def record(paused, source):
            calls.append((paused, source))
            original(paused, source)

        self.loop._set_highlight_paused = record  # type: ignore[method-assign]
        fake = _SceneNeuro("http://fake")
        mirror = NeuroMirror(self.loop)
        with patch("neuro_simulator.stream.neuro_mirror.NeuroClient", lambda url: fake), patch(
            "neuro_simulator.stream.neuro_mirror.stream_settings.load",
            return_value=SimpleNamespace(neuro_url="http://fake", channel="stream"),
        ):
            mirror.start()
            try:
                for _ in range(400):
                    if len(calls) >= 4:
                        break
                    await asyncio.sleep(0.005)
            finally:
                await mirror.stop()

        self.assertEqual(
            calls,
            [
                (True, "scene:neuro"),
                (False, "scene:neuro"),
                (True, "scene:neuro"),
                (False, "scene:neuro"),
            ],
        )
        self.assertFalse(self.loop.highlight_paused)
        self.assertTrue(self.loop.neuro_link["reachable"])

    async def test_empty_queue_behavior(self):
        self.assertIsNone(await self.loop._pick(self.settings, self.session))
        self.settings.empty_queue_behavior = "placeholder"
        picked = await self.loop._pick(self.settings, self.session)
        self.assertEqual(picked["kind"], "placeholder")
        self.assertEqual(picked["body"], "placeholder")

    async def test_empty_queue_renders_placeholder_as_messages_block(self):
        """空队列不再走独立模板：{{chat_messages}} 直接换成 placeholder_message。"""
        self.settings.empty_queue_behavior = "placeholder"
        self.settings.placeholder_message = "Nobody is talking. Start a topic yourself."
        self.session["rounds"] = 0
        captured: dict[str, str] = {}

        class _CapturingClient(_FakeNeuroClient):
            async def chat_batch(
                self, message, channel, conversation_id, audio=False, highlights=None
            ):
                result = await super().chat_batch(
                    message, channel, conversation_id, audio, highlights
                )
                captured["text"] = message
                return result

        with patch("neuro_simulator.stream.loop.NeuroClient", _CapturingClient):
            await self.loop._run_round(self.settings, self.session)

        text = captured["text"]
        self.assertIn("Recent viewer messages:", text)
        self.assertIn("Nobody is talking. Start a topic yourself.", text)
        self.assertNotIn("{{", text)
        # 占位轮不推进 cursor
        self.assertEqual(self.session["cursor"], 0)

    async def test_bad_template_cancels_round_without_consuming_queue(self):
        """实况渲染失败 → 本轮作废、弹幕不消费、错误可见。

        程序不代笔（已经没有"最小兜底"），也不推进水位线。坏模板的表现必须是
        "她停摆、原因看得见"，而不是"队列被清空、她安静地沉默"——后者把大事
        降级成没人注意的小事，还顺手销毁了内容平面的事实。
        """
        await self.storage.append_message(self.queue_row, "viewer", "hello")
        await self.storage.upsert_prompt("BROKEN {{", "neuro_user")
        await self.templates.load()
        sent: list[str] = []

        class _CapturingClient(_FakeNeuroClient):
            async def chat_batch(
                self, message, channel, conversation_id, audio=False, highlights=None
            ):
                sent.append(message)
                return await super().chat_batch(
                    message, channel, conversation_id, audio, highlights
                )

        with patch("neuro_simulator.stream.loop.NeuroClient", _CapturingClient):
            with self.assertRaises(TemplateRenderError):
                await self.loop._run_round(self.settings, self.session)

        self.assertEqual(sent, [], "坏模板不该向 neuro 发出任何请求")
        self.assertEqual(self.session["cursor"], 0, "渲染失败却推进了水位线 = 弹幕被吃掉")
        self.assertIsNotNone(self.templates.template_error)

        # 修好之后，同一批弹幕仍在队列里，下一轮照常消费
        await self.storage.upsert_prompt("Say: {{ chat_messages }}", "neuro_user")
        await self.templates.load()
        with patch("neuro_simulator.stream.loop.NeuroClient", _CapturingClient):
            await self.loop._run_round(self.settings, self.session)
        self.assertIn("- viewer: hello", sent[-1])
        self.assertEqual(self.session["cursor"], 1)
        self.assertIsNone(self.templates.template_error)

    async def test_only_two_message_templates(self):
        names = [t["name"] for t in self.templates.list()]
        self.assertEqual(names, ["neuro_user", "chatbot_user"])

    async def test_does_not_mirror_tool_or_speech_events(self):
        await self.storage.append_message(self.queue_row, "viewer", "do a trick")
        self.settings.scene_context = "ctx"
        self.settings.neuro_url = "http://fake"
        # settings 以 SimpleNamespace 充当 StreamSettings（_run_round 只读这几个字段）
        q = self.bus.subscribe()
        fake = _FakeNeuroClient("http://fake")
        with patch("neuro_simulator.stream.loop.NeuroClient", lambda url: fake):
            await self.loop._run_round(self.settings, self.session)

        events = []
        while not q.empty():
            events.append(q.get_nowait())
        # 字幕/音频/工具动作由 neuro 窗口页自己订阅 /chat/events，stream 不再镜像。
        self.assertFalse(any(e["type"] in ("tool_start", "tool_result") for e in events))
        self.assertFalse(any(e["type"] == "speech" for e in events))
        self.assertFalse(any(e["type"] in ("turn_begin", "turn_end") for e in events))
        # 批量调用不带音频（音频只归 neuro 窗口）
        self.assertEqual(fake.audio_flags, [False])

    async def test_round_wait_comes_from_status_ends_at_not_local_gap(self):
        """等待时长取 neuro `/status` 的 ends_at；本地不再累加 step_gap。"""
        await self.storage.append_message(self.queue_row, "viewer", "do a trick")
        self.settings.speech_cooldown_ms = 0
        # 「陷阱」：如果 stream 还在本地累加 gap，这个值会让等待多出 5 秒
        self.settings.caption_step_gap_ms = 5000
        sleeps: list[float] = []
        fake = _FakeNeuroClient("http://fake", ends_in_s=0.12)

        async def record_sleep(ms: float) -> None:
            sleeps.append(float(ms))
            await asyncio.sleep(0)  # 不真的睡，只记录

        with patch("neuro_simulator.stream.loop.NeuroClient", lambda url: fake):  # noqa: SIM117
            with patch.object(self.loop, "_sleep_ms", new=record_sleep):
                await self.loop._run_round(self.settings, self.session)

        self.assertEqual(fake.chat_calls, 1)
        self.assertGreater(fake.status_calls, 0)
        # 只等 ends_at（约 120ms）+ cooldown 0；绝不该出现 5000ms 级别的本地 gap
        self.assertTrue(any(100 <= ms <= 200 for ms in sleeps), sleeps)
        self.assertFalse(any(ms > 1000 for ms in sleeps), sleeps)

    async def test_pacing_uses_last_speech_end_not_generation_end(self):
        """语音在生成结束前早已播完（ends_at 在过去）时，不应再等一整句。"""
        await self.storage.append_message(self.queue_row, "viewer", "say something")
        self.settings.speech_cooldown_ms = 0
        sleeps: list[float] = []
        fake = _FakeNeuroClient("http://fake", ends_in_s=-5.0)

        async def _record_sleep(ms: float) -> None:
            sleeps.append(float(ms))
            await asyncio.sleep(0)

        with patch("neuro_simulator.stream.loop.NeuroClient", lambda url: fake):
            with patch.object(self.loop, "_sleep_ms", new=_record_sleep):
                await self.loop._run_round(self.settings, self.session)

        self.assertTrue(all(ms == 0 for ms in sleeps), sleeps)

    async def test_no_turn_or_speech_events_are_emitted(self):
        """speech/turn 事件已移出 stream：neuro 窗口页直接订阅 neuro 的 /chat/events。"""
        await self.storage.append_message(self.queue_row, "viewer", "do a trick")
        q = self.bus.subscribe()
        fake = _FakeNeuroClient("http://fake")
        with patch("neuro_simulator.stream.loop.NeuroClient", lambda url: fake):
            await self.loop._run_round(self.settings, self.session)

        events = []
        while not q.empty():
            events.append(q.get_nowait())
        self.assertFalse(any(e["type"] in ("turn_begin", "turn_end", "speech") for e in events))


if __name__ == "__main__":
    unittest.main()
