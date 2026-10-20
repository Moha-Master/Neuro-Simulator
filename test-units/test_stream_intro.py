"""开播 intro 的固定时长与后端推进测试。

intro 现在是 scenes.json 里的普通脚本场景：时长/时间线由场景数据声明，
后端只按自己的时钟等待并广播 scene_set room，不依赖任何前端回报。
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
from neuro_simulator.stream import media
from neuro_simulator.stream import scenes as scene_catalog
from neuro_simulator.stream.loop import StreamLoop
from neuro_simulator.stream.persona import ChatbotPersonaStore, build_template_store
from neuro_simulator.stream.storage import Storage

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_SCENES = ROOT / "server/neuro_simulator/stream/ui/public/scenes.json"
PUBLIC_VIDEO = ROOT / "server/neuro_simulator/stream/ui/public/neuro_start.mp4"


class TestIntroSceneData(unittest.TestCase):
    def test_timeline_matches_video_plus_rise(self):
        data = json.loads(PUBLIC_SCENES.read_text(encoding="utf-8"))
        intro = next(s for s in data["scenes"] if s["id"] == "intro")
        timeline = intro["timeline"]

        video_s = media.probe_mp4_seconds(PUBLIC_VIDEO)
        self.assertIsNotNone(video_s)
        self.assertAlmostEqual(
            float(timeline["duration"]),
            float(video_s) + media.RISE_ANIM_S,
            delta=0.02,
        )
        self.assertEqual(
            [step["action"] for step in timeline["steps"]],
            ["media.play", "element.show", "avatar.rise_in", "element.hide"],
        )
        self.assertEqual(
            [step["element"] for step in timeline["steps"]],
            ["intro_video", "bg_room", "avatar", "intro_video"],
        )

    def test_intro_is_scripted_but_not_manually_selectable(self):
        data = json.loads(PUBLIC_SCENES.read_text(encoding="utf-8"))
        intro = next(s for s in data["scenes"] if s["id"] == "intro")
        self.assertIs(intro.get("manual"), False)

        with patch.object(scene_catalog, "load_scenes", return_value=data["scenes"]):
            self.assertAlmostEqual(scene_catalog.duration("intro"), 13.475)
            self.assertFalse(scene_catalog.manual("intro"))
            self.assertNotIn("intro", [s["id"] for s in scene_catalog.overview()])


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
        self.loop.last_scene = "intro"
        self.loop._intro_started = time.monotonic()

    async def asyncTearDown(self):
        await self.storage.close()
        self.tmp.cleanup()

    async def test_main_goes_live_after_scene_duration_without_report(self):
        q = self.bus.subscribe()
        started = time.monotonic()
        fake_settings = SimpleNamespace()
        with patch.object(scene_catalog, "duration", return_value=0.05):
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
        self.assertFalse(any(e["type"] == "intro" for e in events), events)


if __name__ == "__main__":
    unittest.main()
