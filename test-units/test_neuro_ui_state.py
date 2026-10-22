"""neuro 窗口的 UiState：字幕时间线快照与当前场景。

这些逻辑决定重连的 OBS/dashboard 画面能否从"正在播放的那一句"续上、
能否按 `scene.elapsed` 恢复场景时间线——纯时间计算，用假时钟测试。
"""

import unittest
from unittest.mock import patch

from neuro_simulator.neuro_sama.ui_state import TURN_GRACE_S, UiState

CLOCK = "neuro_simulator.neuro_sama.ui_state.time.monotonic"


class TestTurnTimeline(unittest.TestCase):
    def setUp(self) -> None:
        self.state = UiState()

    def test_speech_snapshot_consumed_and_inflight_audio(self):
        with patch(CLOCK, return_value=100.0):
            turn_id = self.state.new_turn("stream")
        with patch(CLOCK, return_value=101.0):
            self.state.observe(
                {
                    "type": "speech",
                    "step": 0,
                    "index": 0,
                    "text": "hello",
                    "duration": 2.0,
                    "audio": "YQ==",
                    "mime": "audio/mp3",
                },
                "stream",
                turn_id,
            )

        with patch(CLOCK, return_value=101.5):
            snap = self.state.snapshot("stream")
        turn = snap["turn"]
        self.assertEqual(turn["id"], turn_id)
        self.assertTrue(turn["active"])
        seg = turn["speech"][0]
        self.assertAlmostEqual(seg["consumed"], 0.5)
        self.assertEqual(seg["audio"], "YQ==")

        with patch(CLOCK, return_value=103.5):
            seg = self.state.snapshot("stream")["turn"]["speech"][0]
        self.assertAlmostEqual(seg["consumed"], 2.0)
        self.assertNotIn("audio", seg)

    def test_wrong_turn_id_is_ignored(self):
        with patch(CLOCK, return_value=100.0):
            turn_id = self.state.new_turn("stream")
            self.state.observe(
                {"type": "speech", "index": 0, "text": "old", "duration": 1.0},
                "stream",
                turn_id + 1,
            )
            self.assertEqual(self.state.snapshot("stream")["turn"]["speech"], [])

    def test_terminal_marks_inactive_and_snapshot_grace(self):
        with patch(CLOCK, return_value=100.0):
            turn_id = self.state.new_turn("stream")
            self.state.observe(
                {"type": "speech", "index": 0, "text": "hi", "duration": 1.0},
                "stream",
                turn_id,
            )
        with patch(CLOCK, return_value=101.0):
            self.state.observe({"type": "done"}, "stream", turn_id)
            self.assertFalse(self.state.snapshot("stream")["turn"]["active"])
        with patch(CLOCK, return_value=101.0 + TURN_GRACE_S + 1):
            self.assertNotIn("turn", self.state.snapshot("stream"))

    def test_abort_and_clear(self):
        with patch(CLOCK, return_value=100.0):
            turn_id = self.state.new_turn("web")
            self.state.abort("web", turn_id)
            self.assertFalse(self.state.snapshot("web")["turn"]["active"])
            self.state.clear_turns("web")
            self.assertNotIn("turn", self.state.snapshot("web"))

    def test_snapshot_without_channel_uses_last_turn(self):
        with patch(CLOCK, return_value=100.0):
            tid_web = self.state.new_turn("web")
            tid_stream = self.state.new_turn("stream")
        with patch(CLOCK, return_value=100.1):
            snap = self.state.snapshot()
        self.assertEqual(snap["turn"]["id"], tid_stream)
        self.assertNotEqual(tid_web, tid_stream)


class TestScene(unittest.TestCase):
    """场景是权威状态：只有时长与 elapsed，没有"过期"概念（下次切换才变）。"""

    def test_scene_elapsed_and_duration(self):
        state = UiState()
        with patch(CLOCK, return_value=200.0):
            info = state.set_scene("intro", 13.0)
        self.assertEqual(info["id"], "intro")
        self.assertAlmostEqual(info["elapsed"], 0.0)
        with patch(CLOCK, return_value=205.0):
            scene = state.snapshot()["scene"]
        self.assertAlmostEqual(scene["elapsed"], 5.0)
        self.assertAlmostEqual(scene["duration"], 13.0)

    def test_scene_never_expires_until_next_switch(self):
        state = UiState()
        with patch(CLOCK, return_value=200.0):
            state.set_scene("intro", 13.0)
        with patch(CLOCK, return_value=200.0 + 3600.0):
            self.assertEqual(state.snapshot()["scene"]["id"], "intro")
            self.assertGreater(state.snapshot()["scene"]["elapsed"], 3000.0)

    def test_new_scene_restarts_clock(self):
        state = UiState()
        with patch(CLOCK, return_value=300.0):
            state.set_scene("intro", 13.0)
        with patch(CLOCK, return_value=301.0):
            state.set_scene("normal", 0.0)
        with patch(CLOCK, return_value=302.0):
            snap = state.snapshot()["scene"]
        self.assertEqual(snap["id"], "normal")
        self.assertAlmostEqual(snap["elapsed"], 1.0)
        self.assertEqual(snap["duration"], 0.0)

    def test_snapshot_without_scene(self):
        self.assertNotIn("scene", UiState().snapshot())
        self.assertEqual(UiState().scene_snapshot(), {})


if __name__ == "__main__":
    unittest.main()
