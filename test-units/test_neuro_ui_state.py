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




class TestStepGapOnServerTimeline(unittest.TestCase):
    """服务端时间线必须计入 step 间停顿——前端 `speech.ts` 就是按这个节奏播的。

    少算 gap 会让 `turn_projection().remaining_s` 比画面短，于是下游消费它的一方
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
            speech = st.turn_projection("stream")
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
            speech = st.turn_projection("stream")
        self.assertAlmostEqual(speech["remaining_s"], 2.0, places=2)

    def test_first_sentence_never_waits_for_a_gap(self):
        """工具链之后的第一轮 speech 可能直接出现在 step 5：那不是"step 变化"。"""
        st = UiState()
        with patch(CLOCK, return_value=100.0):
            tid = st.new_turn("stream")
        with patch(CLOCK, return_value=101.0):
            st.observe(self.speech(5, 0, "a"), "stream", tid)
        with patch(CLOCK, return_value=101.0):
            speech = st.turn_projection("stream")
        self.assertAlmostEqual(speech["remaining_s"], 2.0, places=2)


class TestStatusProjection(unittest.TestCase):
    """`turn_projection()` 是给别的模块**高频轮询**的投影：轻量、不含音频。

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
            status = st.turn_projection("stream")
        self.assertNotIn("audio", str(status))
        self.assertNotIn("YQ==", str(status))

    def test_status_fields_while_generating_and_speaking(self):
        st = UiState()
        with patch(CLOCK, return_value=100.0):
            tid = st.new_turn("stream")
            self._speaking(st, tid)
        with patch(CLOCK, return_value=101.0):
            status = st.turn_projection("stream")
        self.assertEqual(status["turn_id"], tid)
        self.assertEqual(status["channel"], "stream")
        self.assertTrue(status["generating"], "本轮还没 done")
        self.assertTrue(status["speaking"])
        self.assertAlmostEqual(status["remaining_s"], 4.0, places=2)
        self.assertNotIn("text", status, "正文属于内容平面，状态投影不再带 text")
        self.assertEqual(status["segments"], 1)
        self.assertEqual(status["segments_done"], 0)
        self.assertEqual(status["in_flight"], 1)
        self.assertEqual(st.scene_snapshot(), {}, "没切过场景就是空对象，分区 current 为 null")

    def test_ends_at_is_none_without_speech(self):
        """TTS 关闭/纯工具轮没有 speech 事件：ends_at 必须是 None，不能给个假时刻。"""
        st = UiState()
        with patch(CLOCK, return_value=100.0):
            tid = st.new_turn("stream")
            st.observe({"type": "content", "step": 1, "delta": "no tts here"}, "stream", tid)
        with patch(CLOCK, return_value=105.0):
            speech = st.turn_projection("stream")
        self.assertIsNone(speech["ends_at"])
        self.assertFalse(speech["speaking"])

    def test_ends_at_is_wall_clock(self):
        """跨进程不能对 monotonic：绝对时刻必须是 unix 秒。"""
        import time as real_time

        st = UiState()
        with patch(CLOCK, return_value=100.0):
            tid = st.new_turn("stream")
            self._speaking(st, tid)
        with patch(CLOCK, return_value=101.0):
            speech = st.turn_projection("stream")
        self.assertAlmostEqual(speech["ends_at"], real_time.time() + speech["remaining_s"], delta=1.0)

    def test_finished_turn_stops_speaking(self):
        st = UiState()
        with patch(CLOCK, return_value=100.0):
            tid = st.new_turn("stream")
            self._speaking(st, tid)
        with patch(CLOCK, return_value=106.0):
            st.observe({"type": "done"}, "stream", tid)
            speech = st.turn_projection("stream")
        self.assertFalse(speech["generating"])
        self.assertFalse(speech["speaking"])
        self.assertEqual(speech["remaining_s"], 0.0)
        self.assertNotIn("text", speech)
        # 正文在内容平面仍有全量值（chatbot 用批量响应里的 text，不再读 status）
        self.assertEqual(st.turn_text("stream"), "hello there")

    def test_status_never_carries_text(self):
        st = UiState()
        with patch(CLOCK, return_value=100.0):
            tid = st.new_turn("stream")
            for i in range(30):
                st.observe(
                    {"type": "content", "step": 0, "delta": "x" * 80},
                    "stream", tid,
                )
                st.observe(
                    {"type": "speech", "step": 0, "index": i, "text": "x" * 80,
                     "duration": 0.1, "audio": "", "mime": ""},
                    "stream", tid,
                )
        speech = st.turn_projection("stream")
        self.assertNotIn("text", speech)

    def test_other_channel_does_not_leak(self):
        st = UiState()
        with patch(CLOCK, return_value=100.0):
            tid = st.new_turn("stream")
            self._speaking(st, tid)
        with patch(CLOCK, return_value=101.0):
            speech = st.turn_projection("web")
        self.assertIsNone(speech["turn_id"])
        self.assertFalse(speech["speaking"])
        self.assertEqual(speech["remaining_s"], 0.0)
        self.assertNotIn("text", speech)

    def test_abort_clears_generating(self):
        st = UiState()
        with patch(CLOCK, return_value=100.0):
            tid = st.new_turn("stream")
            self._speaking(st, tid)
            st.abort("stream", tid)
            speech = st.turn_projection("stream")
        self.assertFalse(speech["generating"])
        self.assertEqual(st.turn_projection("stream")["in_flight"], 0)


class TestContentPlane(unittest.TestCase):
    """内容平面（`/chat` 批量响应）：整轮全量文本 + 已含 gap 的相对 start。"""

    def test_turn_text_covers_every_step_not_just_done_content(self):
        st = UiState()
        with patch(CLOCK, return_value=100.0):
            tid = st.new_turn("stream")
        with patch(CLOCK, return_value=100.1):
            st.observe({"type": "content", "step": 1, "delta": "Let me check the board!"}, "stream", tid)
            st.observe({"type": "speech", "step": 1, "index": 0, "text": "Let me check the board!",
                        "duration": 1.0, "audio": "", "mime": ""}, "stream", tid)
        with patch(CLOCK, return_value=101.0):
            st.observe({"type": "content", "step": 2, "delta": "LOLA is my guess."}, "stream", tid)
            st.observe({"type": "speech", "step": 2, "index": 1, "text": "LOLA is my guess.",
                        "duration": 1.0, "audio": "", "mime": ""}, "stream", tid)
        # done.content 只有最后一步；helper 必须把工具调用前的话也带上
        with patch(CLOCK, return_value=101.1):
            st.observe({"type": "done", "content": "LOLA is my guess."}, "stream", tid)
        self.assertEqual(
            st.turn_text("stream"),
            "Let me check the board! LOLA is my guess.",
        )

    def test_speech_segments_start_includes_step_gap(self):
        st = UiState()
        with patch(CLOCK, return_value=100.0):
            tid = st.new_turn("stream")
        with patch(CLOCK, return_value=101.0):
            st.observe(
                {"type": "speech", "step": 0, "index": 0, "text": "a",
                 "duration": 2.31, "audio": "YQ==", "mime": "audio/mpeg"},
                "stream", tid,
            )
        with patch(CLOCK, return_value=103.5):
            st.observe(
                {"type": "speech", "step": 2, "index": 1, "text": "b",
                 "duration": 1.87, "audio": "YQ==", "mime": "audio/mpeg"},
                "stream", tid,
            )
        segs = st.turn_segments("stream", audio=True)
        self.assertEqual([s["step"] for s in segs], [0, 2])
        self.assertAlmostEqual(segs[0]["start"], 0.0)
        # 2.31 时长 + 1.0s 的 caption_step_gap_ms = 3.31
        self.assertAlmostEqual(segs[1]["start"], 3.31, places=2)
        self.assertEqual(segs[1]["audio"], "YQ==")

    def test_segments_fall_back_to_text_when_tts_disabled(self):
        st = UiState()
        with patch(CLOCK, return_value=100.0):
            tid = st.new_turn("stream")
            st.observe({"type": "content", "step": 1, "delta": "first step"}, "stream", tid)
            st.observe({"type": "content", "step": 2, "delta": "second step"}, "stream", tid)
            st.observe({"type": "done", "content": "second step"}, "stream", tid)
        payload = st.batch("stream", "conv", tid, "done", audio=True)
        self.assertEqual(payload["text"], "first step second step")
        self.assertEqual(
            payload["segments"],
            [
                {"step": 1, "index": 0, "kind": "text", "text": "first step", "duration": 0},
                {"step": 2, "index": 0, "kind": "text", "text": "second step", "duration": 0},
            ],
        )


if __name__ == "__main__":
    unittest.main()
