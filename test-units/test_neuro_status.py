"""neuro `/status`：状态机分区形状、turn 现算字段、不含音频/正文。"""

import json
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from neuro_simulator.neuro_sama import server as ns_server
from neuro_simulator.neuro_sama.ui_state import UiState

CLOCK = "neuro_simulator.neuro_sama.ui_state.time.monotonic"


def _app_for(ui_state: UiState, ctx: dict):
    """不跑 lifespan：只装 /status 需要的 state（端点测试用）。"""
    app = ns_server.create_app()
    app.state.status_board = ns_server.build_status_board(ui_state, ctx)
    app.state.status_channel = ctx
    return app


class TestNeuroStatusShape(unittest.TestCase):
    def setUp(self) -> None:
        self.ui = UiState()
        self.ctx = {"channel": None}
        self._patchers = [
            patch.object(ns_server, "_model_current", lambda: {"service_ok": True}),
            patch.object(
                ns_server,
                "_tts_current",
                lambda: {"effective_provider": "null", "degraded": False},
            ),
        ]
        for p in self._patchers:
            p.start()

    def tearDown(self) -> None:
        for p in self._patchers:
            p.stop()

    def _client(self) -> TestClient:
        return TestClient(_app_for(self.ui, self.ctx))

    def test_envelope_and_sections_without_flat_legacy_keys(self):
        with patch(CLOCK, return_value=100.0):
            self.ui.set_scene("intro", 13.0)
        body = self._client().get("/status?channel=stream").json()
        self.assertEqual(set(body), {"module", "rev", "ts", "sections"})
        self.assertEqual(body["module"], "neuro_sama")
        self.assertEqual(
            set(body["sections"]),
            {"scene", "turn", "layers", "model", "tts", "caption", "manifest"},
        )
        for name, section in body["sections"].items():
            self.assertIn("current", section, name)

        scene = body["sections"]["scene"]["current"]
        self.assertEqual(scene["id"], "intro")
        self.assertEqual(scene["label"], "开场")
        self.assertTrue(scene["desc"], "desc 必须从清单带上")
        self.assertIn("video", scene["desc"].lower())
        self.assertAlmostEqual(scene["duration"], 13.0)
        self.assertIn("elapsed", scene)
        self.assertTrue(scene["auto_returns"])
        self.assertFalse(scene["highlight_messages"], "intro 场景不显示高亮浮层")

        layers = body["sections"]["layers"]["current"]
        self.assertEqual(
            {e["id"] for e in layers},
            {"bg_room", "intro_video", "avatar", "caption", "highlight"},
        )
        self.assertTrue(all("visible" in e and "name" in e for e in layers))

        graph = body["sections"]["scene"]["graph"]
        self.assertEqual({s["id"] for s in graph["states"]}, {"normal", "intro"})
        self.assertTrue(all(s["desc"] for s in graph["states"]))

    def test_turn_fields_and_no_audio_or_text(self):
        with patch(CLOCK, return_value=100.0):
            tid = self.ui.new_turn("stream")
            self.ui.observe(
                {
                    "type": "speech",
                    "step": 0,
                    "index": 0,
                    "text": "hello there",
                    "duration": 5.0,
                    "audio": "YQ==",
                    "mime": "audio/mp3",
                },
                "stream",
                tid,
            )
        with patch(CLOCK, return_value=101.0):
            body = self._client().get("/status?channel=stream").json()

        turn = body["sections"]["turn"]["current"]
        self.assertEqual(turn["turn_id"], tid)
        self.assertEqual(turn["channel"], "stream")
        self.assertTrue(turn["generating"])
        self.assertTrue(turn["speaking"])
        self.assertAlmostEqual(turn["remaining_s"], 4.0, places=2)
        self.assertEqual(turn["segments"], 1)
        self.assertEqual(turn["segments_done"], 0)
        self.assertEqual(turn["in_flight"], 1)
        self.assertIsNotNone(turn["ends_at"])

        blob = json.dumps(body, ensure_ascii=False)
        self.assertNotIn("audio", blob)
        self.assertNotIn("YQ==", blob)
        self.assertNotIn("hello there", blob)
        # 旧扁平键一个都不能留
        for legacy in ("speech", "turns", "model_service_ok"):
            self.assertNotIn(legacy, body)

    def test_ends_at_is_null_without_speech(self):
        with patch(CLOCK, return_value=100.0):
            tid = self.ui.new_turn("stream")
            self.ui.observe({"type": "content", "step": 0, "delta": "no tts"}, "stream", tid)
        with patch(CLOCK, return_value=101.0):
            turn = self._client().get("/status?channel=stream").json()["sections"]["turn"]["current"]
        self.assertIsNone(turn["ends_at"])
        self.assertFalse(turn["speaking"])
        self.assertEqual(turn["segments"], 0)

    def test_want_drop_are_section_level(self):
        client = self._client()
        only = client.get("/status?want=turn,scene").json()["sections"]
        self.assertEqual(set(only), {"turn", "scene"})
        dropped = client.get("/status?drop=layers,tts,model").json()["sections"]
        self.assertEqual(
            set(dropped), {"scene", "turn", "caption", "manifest"}
        )
        self.assertEqual(client.get("/status?drop=*").json()["sections"], {})

    def test_provider_exception_keeps_endpoint_200(self):
        self.ui.set_scene("intro", 1.0)

        def boom():
            raise RuntimeError("场景投影坏了")

        from neuro_simulator.state_kit import StateBoard

        app = _app_for(self.ui, self.ctx)
        board = StateBoard("neuro_sama")
        board.register("scene", boom)
        app.state.status_board = board
        resp = TestClient(app).get("/status")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["sections"]["scene"]["current"], None)
        self.assertIn("RuntimeError", resp.json()["sections"]["scene"]["error"])


if __name__ == "__main__":
    unittest.main()
