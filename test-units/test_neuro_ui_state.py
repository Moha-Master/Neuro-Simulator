"""neuro 窗口的 UiState：字幕时间线快照与当前场景。

这些逻辑决定重连的 OBS/dashboard 画面能否从"正在播放的那一句"续上、
能否按 `scene.elapsed` 恢复场景时间线——纯时间计算，用假时钟测试。
"""

import unittest
from unittest.mock import patch

from neuro_simulator.neuro_sama.ui_state import STATUS_TEXT_CHARS, TURN_GRACE_S, UiState

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




class TestStepGapOnServerTimeline(unittest.TestCase):
    """服务端时间线必须计入 step 间停顿——前端 `speech.ts` 就是按这个节奏播的。

    少算 gap 会让 `status().remaining_s` 比画面短，于是下游消费它的一方
    （stream 的轮次节奏、chatbot 的睡眠判定）全部提前，表现是"她还在说话，
    下一条弹幕已经发进去了"。
    """

    @staticmethod
    def speech(step: int, index: int, text: str, duration: float = 2.0) -> dict:
        return {"type": "speech", "step": step, "index": index, "text": text,
                "duration": duration, "audio": "", "mime": ""}

    def test_step_change_inserts_one_gap(self):
        st = UiState()
        with patch(CLOCK, return_value=100.0):
            tid = st.new_turn("stream")
        with patch(CLOCK, return_value=101.0):
            st.observe(self.speech(0, 0, "a"), "stream", tid)  # 101..103
        with patch(CLOCK, return_value=103.5):
            st.observe(self.speech(1, 1, "b"), "stream", tid)  # +1s gap → 104..106
        with patch(CLOCK, return_value=103.6):
            speech = st.status("stream")["speech"]
        self.assertAlmostEqual(speech["remaining_s"], 106.0 - 103.6, places=2)

    def test_sentences_in_the_same_step_are_back_to_back(self):
        st = UiState()
        with patch(CLOCK, return_value=100.0):
            tid = st.new_turn("stream")
        with patch(CLOCK, return_value=101.0):
            st.observe(self.speech(0, 0, "a"), "stream", tid)
        with patch(CLOCK, return_value=101.2):
            st.observe(self.speech(0, 1, "b"), "stream", tid)  # 无 gap：103..105
        with patch(CLOCK, return_value=103.0):
            speech = st.status("stream")["speech"]
        self.assertAlmostEqual(speech["remaining_s"], 2.0, places=2)

    def test_first_sentence_never_waits_for_a_gap(self):
        """工具链之后的第一轮 speech 可能直接出现在 step 5：那不是"step 变化"。"""
        st = UiState()
        with patch(CLOCK, return_value=100.0):
            tid = st.new_turn("stream")
        with patch(CLOCK, return_value=101.0):
            st.observe(self.speech(5, 0, "a"), "stream", tid)
        with patch(CLOCK, return_value=101.0):
            speech = st.status("stream")["speech"]
        self.assertAlmostEqual(speech["remaining_s"], 2.0, places=2)


class TestStatusProjection(unittest.TestCase):
    """`status()` 是给别的模块**高频轮询**的投影：轻量、不含音频。

    画面续播走 `snapshot()`（带 base64，只在连接/重连时取一次）；
    两者混用会让 0.5s 一次的轮询拖几 MB。
    """

    def _speaking(self, st: UiState, tid: int) -> None:
        st.observe(
            {"type": "speech", "step": 0, "index": 0, "text": "hello there",
             "duration": 5.0, "audio": "YQ==", "mime": "audio/mp3"},
            "stream", tid,
        )

    def test_status_carries_no_audio(self):
        st = UiState()
        with patch(CLOCK, return_value=100.0):
            tid = st.new_turn("stream")
            self._speaking(st, tid)
        with patch(CLOCK, return_value=101.0):
            self.assertIn("audio", str(st.snapshot("stream")["turn"]))  # 画面要能续播
            status = st.status("stream")
        self.assertNotIn("audio", str(status))
        self.assertNotIn("YQ==", str(status))

    def test_status_fields_while_generating_and_speaking(self):
        st = UiState()
        with patch(CLOCK, return_value=100.0):
            tid = st.new_turn("stream")
            self._speaking(st, tid)
        with patch(CLOCK, return_value=101.0):
            status = st.status("stream")
        speech = status["speech"]
        self.assertEqual(speech["turn_id"], tid)
        self.assertTrue(speech["generating"], "本轮还没 done")
        self.assertTrue(speech["speaking"])
        self.assertAlmostEqual(speech["remaining_s"], 4.0, places=2)
        self.assertEqual(speech["text"], "hello there")
        self.assertEqual(status["turns"], {"in_flight": 1})
        self.assertIsNone(status["scene"], "没切过场景就是 null，不是空对象")

    def test_ends_at_is_wall_clock(self):
        """跨进程不能对 monotonic：绝对时刻必须是 unix 秒。"""
        import time as real_time

        st = UiState()
        with patch(CLOCK, return_value=100.0):
            tid = st.new_turn("stream")
            self._speaking(st, tid)
        with patch(CLOCK, return_value=101.0):
            speech = st.status("stream")["speech"]
        self.assertAlmostEqual(speech["ends_at"], real_time.time() + speech["remaining_s"], delta=1.0)

    def test_finished_turn_stops_speaking_but_keeps_text(self):
        st = UiState()
        with patch(CLOCK, return_value=100.0):
            tid = st.new_turn("stream")
            self._speaking(st, tid)
        with patch(CLOCK, return_value=106.0):
            st.observe({"type": "done"}, "stream", tid)
            speech = st.status("stream")["speech"]
        self.assertFalse(speech["generating"])
        self.assertFalse(speech["speaking"])
        self.assertEqual(speech["remaining_s"], 0.0)
        self.assertEqual(speech["text"], "hello there", "chatbot 要拿它说 Neurdle says: ...")

    def test_text_is_capped(self):
        st = UiState()
        with patch(CLOCK, return_value=100.0):
            tid = st.new_turn("stream")
            for i in range(30):
                st.observe(
                    {"type": "speech", "step": 0, "index": i, "text": "x" * 80,
                     "duration": 0.1, "audio": "", "mime": ""},
                    "stream", tid,
                )
        self.assertEqual(len(st.status("stream")["speech"]["text"]), STATUS_TEXT_CHARS)

    def test_other_channel_does_not_leak(self):
        st = UiState()
        with patch(CLOCK, return_value=100.0):
            tid = st.new_turn("stream")
            self._speaking(st, tid)
        with patch(CLOCK, return_value=101.0):
            speech = st.status("web")["speech"]
        self.assertIsNone(speech["turn_id"])
        self.assertFalse(speech["speaking"])
        self.assertEqual(speech["remaining_s"], 0.0)
        self.assertEqual(speech["text"], "")

    def test_abort_clears_generating(self):
        st = UiState()
        with patch(CLOCK, return_value=100.0):
            tid = st.new_turn("stream")
            self._speaking(st, tid)
            st.abort("stream", tid)
            speech = st.status("stream")["speech"]
        self.assertFalse(speech["generating"])
        self.assertEqual(st.status("stream")["turns"]["in_flight"], 0)


if __name__ == "__main__":
    unittest.main()
