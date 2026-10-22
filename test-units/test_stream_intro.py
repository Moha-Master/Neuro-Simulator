"""开播 intro 与 stream 合成器场景数据的测试。

开播 intro 已移到 neuro 窗口：时长由 neuro 的场景数据（探测视频轨道）给出，
stream 只调用 POST /ui/intro 并按时长推进 starting→live；stream 的 scenes.json
不再包含 intro，只声明背景/游戏/chat/highlight + neuro 窗口层。
"""

import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from neuro_simulator.broadcaster import EventBroadcaster
from neuro_simulator.neuro_sama import scenes as neuro_scenes
from neuro_simulator.neuro_sama import ui_media
from neuro_simulator.stream import scenes as scene_catalog
from neuro_simulator.stream import settings as stream_settings
from neuro_simulator.stream.loop import StreamConflict, StreamLoop
from neuro_simulator.stream.neuro_client import NeuroError
from neuro_simulator.stream.persona import ChatbotPersonaStore, build_template_store
from neuro_simulator.stream.storage import Storage

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_SCENES = ROOT / "server/neuro_simulator/stream/ui/public/scenes.json"
NEURO_SCENES = ROOT / "server/neuro_simulator/neuro_sama/ui/public/neuro_scenes.json"
NEURO_VIDEO = ROOT / "server/neuro_simulator/neuro_sama/ui/public/neuro_start.mp4"


def neuro_scenes_data() -> dict:
    return json.loads(NEURO_SCENES.read_text(encoding="utf-8"))


def scene_of(manifest: dict, sid: str) -> dict:
    return next(s for s in manifest["scenes"] if s["id"] == sid)


class TestNeuroIntroData(unittest.TestCase):
    def test_intro_duration_is_scene_data_probed_by_neuro(self):
        """开场时长来自 neuro 的场景数据（探测视频轨道 + after），不再写死在 stream。"""
        video_s = ui_media.probe_mp4_seconds(NEURO_VIDEO)
        self.assertIsNotNone(video_s)
        declared = scene_of(neuro_scenes_data(), "intro")["duration"]
        self.assertAlmostEqual(
            neuro_scenes.effective_duration("intro"),
            float(video_s) + declared["after"],
            delta=0.02,
        )

    def test_stream_scenes_have_no_intro_and_declare_neuro(self):
        data = json.loads(PUBLIC_SCENES.read_text(encoding="utf-8"))
        scenes = {s["id"]: s for s in data["scenes"]}
        self.assertNotIn("intro", scenes)
        self.assertNotIn("intro_video", json.dumps(data))
        self.assertTrue(scenes["starting_soon"].get("hidden"))
        self.assertTrue(scenes["starting_soon"]["neuro"].get("hidden"))
        self.assertFalse(scenes["room"]["neuro"].get("hidden", False))
        self.assertEqual(scenes["room"]["neuro"]["hide"], [])

        chess = scenes["play_chess"]
        self.assertAlmostEqual(chess["neuro"]["x"], 0.43)
        self.assertAlmostEqual(chess["neuro"]["y"], 0.40)
        self.assertAlmostEqual(chess["neuro"]["w"], 0.60)
        self.assertEqual(chess["neuro"]["hide"], ["bg_room"])
        self.assertEqual(
            [e["id"] for e in chess["elements"]],
            ["bg_media", "game_chess", "chat", "highlight"],
        )

    def test_scene_catalog_hides_starting_soon(self):
        data = json.loads(PUBLIC_SCENES.read_text(encoding="utf-8"))
        with patch.object(scene_catalog, "load_scenes", return_value=data["scenes"]):
            self.assertTrue(scene_catalog.hidden("starting_soon"))
            self.assertNotIn("starting_soon", [s["id"] for s in scene_catalog.overview()])


class _FakeLoop(StreamLoop):
    """给 _main 用的最小循环：到 live 后立刻停止，避免真的调 neuro。"""

    async def _run_round(self, settings, session):
        self._reached_live = True
        self._stop_event.set()


class TestIntroPacing(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = Storage(Path(self.tmp.name) / "test.db")
        await self.storage.init()
        self.persona = ChatbotPersonaStore(self.storage)
        await self.persona.load()
        self.templates = build_template_store(self.storage)
        await self.templates.load()
        self.bus = EventBroadcaster()
        self.loop = _FakeLoop(self.storage, self.bus, self.persona, self.templates)
        self.loop._reached_live = False
        queue_row, _ = await self.storage.create_queue("intro test")
        _, session_id = await self.storage.create_session(
            queue_row, "stream", "conv-intro-test"
        )
        row = await self.storage.get_session(session_id)
        self.loop.session = {
            "session_row": row["id"],
            "session_id": session_id,
            "queue_row": queue_row,
            "channel": "stream",
            "conversation_id": "conv-intro-test",
            "cursor": 0,
            "priority_cursor": 0,
        }
        self.loop.state = "starting"
        self.loop.last_scene = "room"
        self.loop._intro_duration = 0.05

    async def asyncTearDown(self):
        await self.storage.close()
        self.tmp.cleanup()

    async def test_main_goes_live_after_neuro_intro_without_report(self):
        q = self.bus.subscribe()
        started = time.monotonic()
        fake_settings = SimpleNamespace()
        with patch(
            "neuro_simulator.stream.loop.stream_settings.load",
            return_value=fake_settings,
        ):
            await asyncio.wait_for(self.loop._main(), timeout=1.0)
        elapsed = time.monotonic() - started

        self.assertGreaterEqual(elapsed, 0.04)
        self.assertTrue(self.loop._reached_live)

        events = []
        while not q.empty():
            events.append(q.get_nowait())
        self.assertTrue(
            any(e["type"] == "scene_set" and e["id"] == "room" for e in events),
            events,
        )
        self.assertTrue(
            any(e["type"] == "state" and e["state"] == "live" for e in events),
            events,
        )

    async def test_game_action_waits_for_speech_end(self):
        self.loop._turn_play_head = time.monotonic() + 0.05
        started = time.monotonic()
        with patch(
            "neuro_simulator.stream.loop.stream_settings.load",
            return_value=SimpleNamespace(game_tool_wait_speech=True),
        ):
            result = await self.loop.run_game_action(lambda: {"ok": True})
        elapsed = time.monotonic() - started
        self.assertTrue(result["ok"])
        self.assertGreaterEqual(elapsed, 0.04)

    async def test_game_actions_are_serialized(self):
        self.loop._turn_play_head = time.monotonic() + 0.04
        order: list[str] = []

        def act(name: str):
            order.append(name)
            self.loop._turn_play_head = time.monotonic() + 0.04
            return {"ok": True, "name": name}

        started = time.monotonic()
        with patch(
            "neuro_simulator.stream.loop.stream_settings.load",
            return_value=SimpleNamespace(game_tool_wait_speech=True),
        ):
            first = asyncio.create_task(self.loop.run_game_action(lambda: act("a")))
            await asyncio.sleep(0)
            second = asyncio.create_task(self.loop.run_game_action(lambda: act("b")))
            await asyncio.gather(first, second)
        elapsed = time.monotonic() - started
        self.assertEqual(order, ["a", "b"])
        self.assertGreaterEqual(elapsed, 0.07)


class _UnreachableNeuro:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url

    async def health(self):
        raise NeuroError("connection refused")

    async def intro(self):
        raise NeuroError("connection refused")


class TestStartGuard(unittest.IsolatedAsyncioTestCase):
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

    async def asyncTearDown(self):
        await self.storage.close()
        self.tmp.cleanup()

    async def test_start_blocked_when_neuro_unreachable(self):
        fake_settings = SimpleNamespace(
            neuro_url="http://127.0.0.1:1",
            channel="stream",
            default_title="t",
            default_category="c",
            default_tags=[],
        )
        with patch.object(
            stream_settings, "load", return_value=fake_settings
        ), patch(
            "neuro_simulator.stream.loop.NeuroClient", _UnreachableNeuro
        ):
            with self.assertRaises(StreamConflict) as ctx:
                await self.loop.start()
        self.assertIn("neuro", str(ctx.exception))
        self.assertIsNone(self.loop.session)
        self.assertEqual(self.loop.state, "offline")


if __name__ == "__main__":
    unittest.main()
