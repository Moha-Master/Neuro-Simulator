"""stream `/stream/status` 分区形状 + audience 缓存语义 + 派生不复制。"""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from neuro_simulator.broadcaster import EventBroadcaster
from neuro_simulator.stream import audience as audience_module
from neuro_simulator.stream import settings as stream_settings
from neuro_simulator.stream import status as stream_status
from neuro_simulator.stream.audience import AudienceTracker
from neuro_simulator.stream.loop import StreamLoop
from neuro_simulator.stream.persona import ChatbotPersonaStore, build_template_store
from neuro_simulator.stream.server import create_app
from neuro_simulator.stream.storage import Storage


class _Resp:
    def __init__(self, payload) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self):
        return self._payload


class _Client:
    def __init__(self, payload=None, error=None) -> None:
        self._payload = payload
        self._error = error
        self.gets = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url):
        self.gets += 1
        if self._error:
            raise self._error
        return _Resp(self._payload)


class TestAudienceTracker(unittest.IsolatedAsyncioTestCase):
    def test_initial_state_has_no_number(self):
        snap = AudienceTracker().snapshot()
        self.assertIsNone(snap["viewers"])
        self.assertEqual(snap["viewers_source"], "twitchtracker_avg")
        self.assertIsNone(snap["updated_at"])
        self.assertIsNone(snap["error"])

    async def test_success_caches_average_viewers(self):
        tracker = AudienceTracker()
        with patch.object(
            audience_module.httpx, "AsyncClient", lambda timeout: _Client({"avg_viewers": 1234})
        ):
            self.assertTrue(await tracker.refresh())
        snap = tracker.snapshot()
        self.assertEqual(snap["viewers"], 1234)
        self.assertIsNone(snap["error"])
        self.assertTrue(snap["updated_at"])

    async def test_bad_payload_and_network_failure_clear_the_number(self):
        tracker = AudienceTracker()
        with patch.object(
            audience_module.httpx, "AsyncClient", lambda timeout: _Client({"avg_viewers": "many"})
        ):
            self.assertFalse(await tracker.refresh())
        self.assertIsNone(tracker.snapshot()["viewers"])
        self.assertIn("avg_viewers", tracker.snapshot()["error"])

        with patch.object(
            audience_module.httpx,
            "AsyncClient",
            lambda timeout: _Client(error=RuntimeError("offline")),
        ):
            self.assertFalse(await tracker.refresh())
        self.assertIsNone(tracker.snapshot()["viewers"])
        self.assertIn("offline", tracker.snapshot()["error"])

    async def test_snapshot_never_touches_the_network(self):
        tracker = AudienceTracker()
        with patch.object(
            audience_module.httpx,
            "AsyncClient",
            side_effect=AssertionError("snapshot 不应发 HTTP"),
        ):
            snap = tracker.snapshot()
        self.assertIsNone(snap["viewers"])


class _FakeGame:
    id = "wordle"
    element_id = "game_wordle"

    def __init__(self) -> None:
        self.snapshot = {
            "game": "wordle",
            "status": "playing",
            "answer": None,
            "rows": [["A"]],
            "current_row": 0,
            "guesses_used": 0,
        }

    def state(self):
        return self.snapshot

    def narrative(self, state):
        return "playing, 0 of 6 guesses used"


class _FakeHub:
    def __init__(self) -> None:
        self.active = _FakeGame()

    @property
    def active_id(self):
        return self.active.id

    def element_states(self):
        return {self.active.element_id: self.active.state()}


class TestStreamStatus(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = Storage(Path(self.tmp.name) / "test.db")
        await self.storage.init()
        self.persona = ChatbotPersonaStore(self.storage)
        await self.persona.load()
        self.templates = build_template_store(self.storage)
        await self.templates.load()
        self.loop = StreamLoop(self.storage, EventBroadcaster(), self.persona, self.templates)
        self.tracker = AudienceTracker()
        cb = SimpleNamespace(
            enabled=True,
            model="m",
            model_ref="ref",
            interval_s=10.0,
            count=2,
            max_concurrent=3,
            interval_jitter=0.1,
        )
        self._settings = patch.object(
            stream_settings,
            "load",
            return_value=SimpleNamespace(
                chatbot=cb,
                default_title="title",
                default_category="category",
                default_tags=["tag"],
            ),
        )
        self._settings.start()
        self.board = stream_status.build_board(self.loop, self.tracker)
        self.loop.state_board = self.board

    async def asyncTearDown(self):
        self._settings.stop()
        await self.storage.close()
        self.tmp.cleanup()

    def _app(self):
        app = create_app()
        app.state.status_board = self.board
        return app

    def _status(self) -> dict:
        return TestClient(self._app()).get("/stream/status").json()

    async def test_envelope_sections_and_no_flat_legacy_keys(self):
        self.loop.session = {
            "session_id": "s1",
            "queue_id": "q1",
            "channel": "stream",
            "conversation_id": "c1",
            "cursor": 0,
            "priority_cursor": 0,
            "rounds": 0,
        }
        self.loop.last_scene = "room"
        self.loop.queue_depth = 3
        self.loop.priority_depth = 1
        self.loop.last_message_id = 42
        body = self._status()
        self.assertEqual(set(body), {"module", "rev", "ts", "sections"})
        self.assertEqual(body["module"], "stream")
        self.assertEqual(
            set(body["sections"]),
            {
                "loop",
                "scene",
                "metadata",
                "audience",
                "chat",
                "chatbot",
                "games",
                "neuro_link",
            },
        )
        self.assertEqual(body["sections"]["scene"]["current"]["id"], "room")
        self.assertTrue(body["sections"]["scene"]["current"]["desc"])
        self.assertEqual(
            {s["id"] for s in body["sections"]["scene"]["graph"]["states"]},
            {
                "starting_soon",
                "room_intro",
                "room",
                "community_art",
                "gameplay",
                "play_wordle",
                "play_chess",
            },
        )
        self.assertEqual(
            body["sections"]["chat"]["current"],
            {"normal_pending": 3, "priority_pending": 1, "last_message_id": 42},
        )

    async def test_audience_three_states(self):
        self.assertIsNone(self._status()["sections"]["audience"]["current"]["viewers"])
        with patch.object(
            audience_module.httpx, "AsyncClient", lambda timeout: _Client({"avg_viewers": 777})
        ):
            await self.tracker.refresh()
        self.assertEqual(self._status()["sections"]["audience"]["current"]["viewers"], 777)
        with patch.object(
            audience_module.httpx, "AsyncClient", lambda timeout: _Client({"avg_viewers": None})
        ):
            await self.tracker.refresh()
        cur = self._status()["sections"]["audience"]["current"]
        self.assertIsNone(cur["viewers"])
        self.assertIn("avg_viewers", cur["error"])

    async def test_neuro_link_reads_cache_and_has_observed_at(self):
        self.loop.note_neuro_link("http://neuro", True)
        link = self._status()["sections"]["neuro_link"]["current"]
        self.assertTrue(link["reachable"])
        self.assertEqual(link["url"], "http://neuro")
        self.assertTrue(link["observed_at"])
        self.assertIsNone(link["error"])

    async def test_status_does_not_hit_network(self):
        # 已有缓存时，即使 httpx 完全不可用，/status 也必须照常返回
        self.tracker._viewers = 10
        with patch.object(
            audience_module.httpx,
            "AsyncClient",
            side_effect=AssertionError("status 不应发 HTTP"),
        ):
            body = self._status()
        self.assertEqual(body["sections"]["audience"]["current"]["viewers"], 10)

    async def test_games_three_layers_and_reuses_element_snapshot(self):
        hub = _FakeHub()
        self.loop.games = hub  # type: ignore[assignment]
        # 直接调 provider：HTTP 会 JSON 序列化，对象身份（is）只能在这一层断言
        games = stream_status._games_current(self.loop)
        self.assertEqual(games["active"], "wordle")
        entry = games["wordle"]
        self.assertEqual(entry["phase"], "typing")
        self.assertEqual(entry["summary"], "playing, 0 of 6 guesses used")
        # element 必须复用 GameHub.element_states() 的那份对象，不复制
        self.assertIs(entry["element"], hub.active.snapshot)
        self.assertEqual(
            sorted(entry), ["element", "phase", "summary"], "三层结构不能被原始快照字段撑开"
        )
        self.assertIsNone(entry["element"]["answer"], "结束前不得揭晓答案")
        graph = self._status()["sections"]["games"]["graph"]
        self.assertIn("wordle.typing", {s["id"] for s in graph["states"]})
        self.assertIn("chess.white_to_move", {s["id"] for s in graph["states"]})

    async def test_scene_projection_carries_prompt_context(self):
        self.loop.last_scene = "play_wordle"
        scene = self._status()["sections"]["scene"]["current"]
        self.assertIn("prompt_context", scene)
        self.assertTrue(scene["prompt_context"], "Wordle 场景应带规则指令")

    async def test_chatbot_partition_reports_template_error(self):
        # 保存路径会拒绝坏模板；这里模拟"绕过保存直接写 DB"的遗留数据
        await self.storage.upsert_prompt("{{ 'unterminated", "neuro_user")
        await self.templates.load()
        self.templates.render("neuro_user", {"messages": "hi"})
        cur = self._status()["sections"]["chatbot"]["current"]
        self.assertEqual(cur["template_error"]["name"], "neuro_user")
        self.assertTrue(cur["template_error"]["message"])
        await self.templates.reset("neuro_user")
        self.templates.render("neuro_user", {"messages": "hi"})
        self.assertIsNone(self._status()["sections"]["chatbot"]["current"]["template_error"])

    async def test_template_context_is_plain_data_only(self):
        ctx = self.loop.template_context("- a: hi")
        self.assertEqual(set(ctx), {"stream_status", "neuro_status", "neuro_said", "messages", "datetime"})

        def plain(value):
            if value is None or isinstance(value, (bool, int, float, str)):
                return True
            if isinstance(value, dict):
                return all(isinstance(k, str) and plain(v) for k, v in value.items())
            if isinstance(value, (list, tuple)):
                return all(plain(v) for v in value)
            return False

        self.assertTrue(plain(ctx), "模板 context 里混进了业务对象")
        self.assertIn("metadata", ctx["stream_status"])

    async def test_rev_bumps_on_note_change(self):
        before = self._status()["rev"]
        self.loop._touch("loop")
        self.assertEqual(self._status()["rev"], before + 1)

    async def test_loop_graph_states(self):
        graph = self._status()["sections"]["loop"]["graph"]
        self.assertEqual(
            {s["id"] for s in graph["states"]}, {"offline", "starting", "live", "paused"}
        )
        self.assertIn(
            {"from": "starting", "to": "offline", "by": "intro_timeout or POST /stream/stop"},
            graph["transitions"],
        )
        blob = json.dumps(self._status(), ensure_ascii=False)
        self.assertNotIn("audio", blob)

    async def test_speech_ends_at_uses_new_turn_section_and_checks_turn_id(self):
        class _Client:
            base_url = "http://neuro"

            def __init__(self, turn_id: int) -> None:
                self.turn_id = turn_id

            async def status(self, channel=None):
                return {
                    "sections": {
                        "turn": {
                            "current": {
                                "turn_id": self.turn_id,
                                "ends_at": 1234.5,
                            }
                        }
                    }
                }

        self.assertEqual(await self.loop._speech_ends_at(_Client(7), "stream", 7), 1234.5)
        self.assertIsNone(
            await self.loop._speech_ends_at(_Client(8), "stream", 7),
            "turn_id 不匹配时按无语音处理（旧轮次已被覆盖）",
        )


class TestStreamStatusReaderHelpers(unittest.IsolatedAsyncioTestCase):
    """三处 /status 读取者依赖的辅助函数（开场闸门 / 语音结束 / game 闸门）。"""

    def test_neuro_turn_reads_new_shape(self):
        from neuro_simulator.stream.loop import _neuro_turn

        self.assertEqual(_neuro_turn({"sections": {"turn": {"current": {"ends_at": 1}}}})["ends_at"], 1)
        self.assertEqual(_neuro_turn({"sections": {"turn": {"current": None}}}), {})
        self.assertEqual(_neuro_turn({"speech": {"ends_at": 1}}), {}, "旧形状不再被读取")


if __name__ == "__main__":
    unittest.main()
