"""NeuroMirror：stream 作为画面事件唯一聚合者的单元测试。

覆盖：订阅参数、内容/状态帧的原样转发、首帧 snapshot 基线、说话句 consumed=0、
终态 active=False、高亮开关（scene:neuro）、断线重连、stop 取消、neuro 不可达。
"""

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from neuro_simulator.broadcaster import EventBroadcaster
from neuro_simulator.stream import neuro_mirror as mirror_module
from neuro_simulator.stream.loop import StreamLoop
from neuro_simulator.stream.neuro_client import NeuroError
from neuro_simulator.stream.neuro_mirror import NeuroMirror
from neuro_simulator.stream.persona import ChatbotPersonaStore, build_template_store
from neuro_simulator.stream.storage import Storage

_SETTINGS = SimpleNamespace(neuro_url="http://neuro.test", channel="stream")


class _FakeNeuro:
    """可编排的 NeuroClient 替身：内容/状态两条 async generator。"""

    def __init__(
        self,
        content_frames: list[dict] | None = None,
        state_frames: list[dict] | None = None,
        *,
        content_fail_first: int = 0,
        content_down: bool = False,
        state_down: bool = False,
    ) -> None:
        self.content_frames = list(content_frames or [])
        self.state_frames = list(state_frames or [])
        self.content_fail_first = content_fail_first
        self.content_down = content_down
        self.state_down = state_down
        self.content_calls = 0
        self.state_calls = 0
        self.content_kwargs: dict | None = None
        self.state_kwargs: dict | None = None

    async def chat_events(
        self,
        channel=None,
        want=None,
        drop=None,
        audio=True,
    ):
        self.content_calls += 1
        self.content_kwargs = {
            "channel": channel,
            "want": want,
            "drop": drop,
            "audio": audio,
        }
        if self.content_down or self.content_calls <= self.content_fail_first:
            raise NeuroError("connection refused")
        for frame in self.content_frames:
            await asyncio.sleep(0)
            yield frame
        await asyncio.sleep(10)

    async def state_events(self, want=None, drop=None):
        self.state_calls += 1
        self.state_kwargs = {"want": want, "drop": drop}
        if self.state_down:
            raise NeuroError("connection refused")
        for frame in self.state_frames:
            await asyncio.sleep(0)
            yield frame
        await asyncio.sleep(10)


class _Harness(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = Storage(Path(self.tmp.name) / "test.db")
        await self.storage.init()
        self.persona = ChatbotPersonaStore(self.storage)
        await self.persona.load()
        self.templates = build_template_store(self.storage)
        await self.templates.load()
        self.bus = EventBroadcaster()
        self.loop = StreamLoop(self.storage, self.bus, self.persona, self.templates)
        self.q = self.bus.subscribe()

    async def asyncTearDown(self) -> None:
        await self.storage.close()
        self.tmp.cleanup()

    def start_mirror(self, fake: _FakeNeuro, *, retry_s: float = 0.01):
        patchers = [
            patch.object(mirror_module, "NeuroClient", lambda base_url: fake),
            patch.object(mirror_module.stream_settings, "load", return_value=_SETTINGS),
            patch.object(mirror_module, "NEURO_MIRROR_RETRY_S", retry_s),
        ]
        for p in patchers:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patchers])
        mirror = NeuroMirror(self.loop)
        self.addAsyncCleanup(mirror.stop)
        mirror.start()
        return mirror

    def drain(self) -> list[dict]:
        out = []
        while not self.q.empty():
            out.append(self.q.get_nowait())
        return out

    def drain_keep(self) -> list[dict]:
        """把已消费的事件累积下来，便于 wait_for 之后统一断言。"""
        self._seen = getattr(self, "_seen", []) + self.drain()
        return list(self._seen)

    async def wait_for(self, predicate, timeout: float = 2.0) -> None:
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            if predicate():
                return
            await asyncio.sleep(0.005)
        self.fail("等待条件超时")


class TestSubscriptionsAndForwarding(_Harness):
    async def test_subscription_parameters(self):
        fake = _FakeNeuro()
        self.start_mirror(fake)
        await self.wait_for(lambda: fake.content_kwargs and fake.state_kwargs)
        self.assertEqual(fake.content_kwargs["channel"], "stream")
        self.assertTrue(fake.content_kwargs["audio"])
        self.assertEqual(fake.content_kwargs["drop"], ["reasoning", "content"])
        self.assertEqual(fake.state_kwargs["want"], ["scene", "caption", "layers", "manifest"])

    async def test_content_frames_are_forwarded_verbatim(self):
        snapshot = {"type": "snapshot", "channel": "stream", "turn": None}
        start = {"type": "start", "turn_id": 7, "model": "m"}
        speech = {
            "type": "speech",
            "turn_id": 7,
            "step": 1,
            "index": 0,
            "text": "hello",
            "duration": 1.25,
            "audio": "BASE64",
            "mime": "audio/mpeg",
        }
        done = {"type": "done", "turn_id": 7, "content": "hello"}
        fake = _FakeNeuro(content_frames=[snapshot, start, speech, done])
        mirror = self.start_mirror(fake)
        await self.wait_for(
            lambda: len([e for e in self.drain_keep() if e.get("type") == "neuro"]) >= 4
        )
        events = [e for e in self.drain_keep() if e.get("type") == "neuro"]
        self.assertTrue(all(e["plane"] == "content" for e in events))
        self.assertEqual([e["event"] for e in events], ["snapshot", "start", "speech", "done"])
        for event, original in zip(events, [snapshot, start, speech, done]):
            self.assertEqual(event["data"], original, "data 必须逐字段不变（含 type）")

        cached = mirror.snapshot()
        turn = cached["turn"]
        self.assertEqual(turn["id"], 7)
        self.assertFalse(turn["active"], "done 之后 turn.active 必须为 False")
        self.assertEqual(len(turn["speech"]), 1)
        seg = turn["speech"][0]
        self.assertEqual(seg["text"], "hello")
        self.assertEqual(seg["audio"], "BASE64", "音频原样缓存，不解码不裁剪")
        self.assertEqual(seg["mime"], "audio/mpeg")
        self.assertEqual(seg["consumed"], 0)

    async def test_state_frames_update_baseline_and_forward(self):
        frames = [
            {
                "type": "snapshot",
                "rev": 3,
                "ts": "t0",
                "sections": {
                    "scene": {"current": {
                        "id": "normal", "duration": 0, "elapsed": 0,
                        "highlight_messages": True,
                    }},
                    "caption": {"current": {"hide_ms": 1000, "step_gap_ms": 1000}},
                    "layers": {"current": [{"id": "bg_room", "visible": True}]},
                    "manifest": {"current": {
                        "is_default": True, "updated_at": "2026-10-25T00:00:00", "chars": 5429,
                    }},
                },
            },
            {
                "type": "state",
                "rev": 4,
                "sections": {"scene": {"current": {
                    "id": "intro", "highlight_messages": False,
                }}},
            },
            {
                "type": "state",
                "rev": 5,
                "sections": {"scene": {"current": None}},
            },
            {
                "type": "state",
                "rev": 6,
                "sections": {"scene": {"current": {
                    "id": "normal", "highlight_messages": True,
                }}},
            },
        ]
        calls: list[tuple[bool, str]] = []
        original = self.loop._set_highlight_paused

        def record(paused, source):
            calls.append((bool(paused), str(source)))
            original(paused, source)

        self.loop._set_highlight_paused = record  # type: ignore[method-assign]
        fake = _FakeNeuro(state_frames=frames)
        mirror = self.start_mirror(fake)
        await self.wait_for(lambda: len(calls) >= 4)
        await asyncio.sleep(0.02)

        self.assertEqual(
            calls,
            [
                (False, "scene:neuro"),
                (True, "scene:neuro"),
                (True, "scene:neuro"),
                (False, "scene:neuro"),
            ],
            "true→恢复、false→暂停、null→暂停、true→恢复",
        )
        self.assertFalse(self.loop.highlight_paused)
        self.assertEqual(self.loop.highlight_source, "scene:neuro")

        state = mirror.snapshot()["state"]
        self.assertEqual(state["scene"]["id"], "normal")
        self.assertEqual(state["caption"], {"hide_ms": 1000, "step_gap_ms": 1000})
        self.assertEqual(state["layers"], [{"id": "bg_room", "visible": True}])
        self.assertEqual(state["manifest"]["chars"], 5429)

        events = [e for e in self.drain_keep() if e.get("type") == "neuro"]
        self.assertTrue(all(e["plane"] == "state" for e in events))
        self.assertEqual([e["event"] for e in events], ["snapshot", "state", "state", "state"])
        for event, original_frame in zip(events, frames):
            self.assertEqual(event["data"], original_frame)

    async def test_none_current_pauses_highlight(self):
        fake = _FakeNeuro(state_frames=[{
            "type": "snapshot",
            "sections": {"scene": {"current": None}},
        }])
        self.start_mirror(fake)
        await self.wait_for(lambda: self.loop.highlight_paused)
        self.assertTrue(self.loop.highlight_paused)
        self.assertEqual(self.loop.highlight_source, "scene:neuro")


class TestLifecycle(_Harness):
    async def test_reconnects_after_failure(self):
        fake = _FakeNeuro(
            content_frames=[{"type": "snapshot", "turn": None}],
            content_fail_first=2,
        )
        self.start_mirror(fake, retry_s=0.01)
        await self.wait_for(lambda: fake.content_calls >= 3)
        self.assertGreaterEqual(fake.content_calls, 3, "断线后必须重连")
        await self.wait_for(
            lambda: any(e.get("type") == "neuro" for e in self.drain_keep())
        )

    async def test_stop_cancels_tasks_without_raising(self):
        fake = _FakeNeuro()
        mirror = self.start_mirror(fake)
        await asyncio.sleep(0.02)
        await mirror.stop()
        self.assertEqual(mirror._tasks, [])
        await mirror.stop()  # 幂等

    async def test_unreachable_neuro_updates_link_and_does_not_crash(self):
        fake = _FakeNeuro(content_down=True, state_down=True)
        mirror = self.start_mirror(fake)
        await self.wait_for(lambda: self.loop.neuro_link["observed_at"] is not None)
        self.assertFalse(self.loop.neuro_link["reachable"])
        self.assertIn("NeuroError", self.loop.neuro_link["error"])
        # 任务仍存活（重连中），stop 能干净收尾
        await mirror.stop()
        self.assertEqual(mirror._tasks, [])


class TestSnapshotShape(_Harness):
    async def test_empty_snapshot_is_stable(self):
        mirror = NeuroMirror(self.loop)
        snap = mirror.snapshot()
        self.assertEqual(snap, {"turn": None, "state": {}})

    async def test_snapshot_reads_cached_turn_without_io(self):
        mirror = NeuroMirror(self.loop)
        mirror._turn = {"id": 12, "active": True, "speech": []}
        mirror._state = {"scene": {"id": "normal"}}
        snap = mirror.snapshot()
        self.assertEqual(snap["turn"]["id"], 12)
        self.assertEqual(snap["state"]["scene"]["id"], "normal")
        # 返回副本的浅层容器：调用方改顶层键不影响缓存
        snap["state"]["caption"] = {}
        self.assertNotIn("caption", mirror.snapshot()["state"])

    async def test_cached_snapshot_turn_restarts_speech_at_zero(self):
        """mirror 不知道播放进度：首帧里的 consumed 也归零，不做逐句续播。"""
        mirror = NeuroMirror(self.loop)
        event = {
            "type": "snapshot",
            "turn": {
                "id": 3,
                "active": True,
                "speech": [{"step": 0, "index": 0, "text": "hi", "duration": 1.0,
                            "consumed": 0.75, "audio": "AAA"}],
            },
        }
        mirror._apply_content(event)
        seg = mirror.snapshot()["turn"]["speech"][0]
        self.assertEqual(seg["consumed"], 0)
        self.assertEqual(seg["audio"], "AAA")
        self.assertEqual(event["turn"]["speech"][0]["consumed"], 0.75, "不能改到转发用的原始帧")


if __name__ == "__main__":
    unittest.main()
