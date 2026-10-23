"""两条总线：内容（/chat/events 的序列化/过滤/续播）与状态（离散变化 + 新分区）。"""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

import httpx

from neuro_simulator import scene_kit
from neuro_simulator.neuro_sama import scenes as ui_scenes
from neuro_simulator.neuro_sama import server as ns_server
from neuro_simulator.neuro_sama.storage import Storage as NeuroStorage

from neuro_runtime import Harness, ScriptedAgent, next_frame, sse_event, sse_payload

CLOCK = "neuro_simulator.neuro_sama.ui_state.time.monotonic"

SPEECH = {"type": "speech", "step": 0, "index": 0, "text": "hello",
          "duration": 5.0, "audio": "YQ==", "mime": "audio/mpeg"}


def _client(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


async def _collect(stream, wanted: str, limit: int = 20) -> list[dict]:
    """读到 wanted 事件为止（含它）。"""
    out = []
    for _ in range(limit):
        frame = await asyncio.wait_for(next_frame(stream), 2.0)
        out.append({"event": sse_event(frame), "data": sse_payload(frame)})
        if out[-1]["event"] == wanted:
            return out
    raise AssertionError(f"{limit} 帧内没等到 {wanted}")


class TestContentBusAudioSerialization(unittest.IsolatedAsyncioTestCase):
    async def test_audio_zero_only_crops_that_connection(self):
        agent = ScriptedAgent([SPEECH, {"type": "done", "content": "hello"}], delay=0.02)
        h = Harness(agent)
        q_full = h.content_bus.subscribe()
        q_mute = h.content_bus.subscribe()
        full = ns_server._content_events(
            h.ui, h.content_bus, q_full,
            channel="stream", turn_id=None, audio=True, want=None, drop=None,
        )
        mute = ns_server._content_events(
            h.ui, h.content_bus, q_mute,
            channel="stream", turn_id=None, audio=False, want=None, drop=None,
        )
        # 首帧 snapshot 先消费掉（两条连接都要）
        self.assertEqual(sse_event(await next_frame(full)), "snapshot")
        self.assertEqual(sse_event(await next_frame(mute)), "snapshot")

        h.turns.spawn(h.turns.register("stream", "conv"), "hi")
        full_frames = await _collect(full, "done")
        mute_frames = await _collect(mute, "done")

        full_speech = [f for f in full_frames if f["event"] == "speech"][0]
        mute_speech = [f for f in mute_frames if f["event"] == "speech"][0]
        self.assertEqual(full_speech["data"]["audio"], "YQ==")
        self.assertNotIn("audio", mute_speech["data"], "audio=0 只是剥掉这一条连接的键")
        self.assertEqual(mute_speech["data"]["duration"], 5.0, "其余字段不受影响")
        await full.aclose()
        await mute.aclose()
        # 两条订阅者都退订
        self.assertEqual(h.content_bus.subscriber_count, 0)


class TestContentBusFiltering(unittest.IsolatedAsyncioTestCase):
    async def test_drop_removes_only_the_named_types(self):
        agent = ScriptedAgent([
            {"type": "reasoning", "delta": "think"},
            {"type": "content", "step": 0, "delta": "hi"},
            SPEECH,
            {"type": "done", "content": "hi"},
        ])
        h = Harness(agent)
        q = h.content_bus.subscribe()
        stream = ns_server._content_events(
            h.ui, h.content_bus, q,
            channel=None, turn_id=None, audio=True,
            want=None, drop=["reasoning", "content"],
        )
        self.assertEqual(sse_event(await next_frame(stream)), "snapshot")
        h.turns.spawn(h.turns.register("stream", "conv"), "hi")
        frames = await _collect(stream, "done")
        names = [f["event"] for f in frames]
        self.assertNotIn("reasoning", names)
        self.assertNotIn("content", names)
        self.assertIn("speech", names)
        self.assertIn("start", names)

    async def test_turn_id_filter_keeps_only_one_turn(self):
        h = Harness(ScriptedAgent([]))
        box = h.content_bus
        q = box.subscribe()
        stream = ns_server._content_events(
            h.ui, box, q, channel=None, turn_id=2, audio=True, want=None, drop=None
        )
        await next_frame(stream)  # snapshot
        box.publish({"type": "speech", "turn_id": 1, "channel": "stream", "text": "old"})
        box.publish({"type": "speech", "turn_id": 2, "channel": "stream", "text": "new",
                     "index": 0, "duration": 1.0, "audio": "", "mime": ""})
        frame = await asyncio.wait_for(next_frame(stream), 1.0)
        self.assertEqual(sse_payload(frame)["text"], "new")


class TestStateBus(unittest.IsolatedAsyncioTestCase):
    async def test_snapshot_then_scene_delta_with_increasing_rev(self):
        h = Harness(ScriptedAgent([]))
        q = h.state_bus.subscribe()
        stream = ns_server._state_events(
            h.board, h.state_bus, q, want=None, drop=None
        )
        first = sse_payload(await next_frame(stream))
        self.assertEqual(set(first), {"module", "rev", "ts", "sections"})
        self.assertEqual(first["module"], "neuro_sama")
        self.assertEqual(first["rev"], 0)

        h.ui.set_scene("normal", 0.0)
        h.board.note_change("scene")
        frame = await asyncio.wait_for(next_frame(stream), 1.0)
        self.assertEqual(sse_event(frame), "state")
        data = sse_payload(frame)
        self.assertEqual(data["rev"], 1)
        self.assertEqual(data["sections"]["scene"]["current"]["id"], "normal")
        self.assertNotIn("turn", data["sections"], "只带发生变化的分区")

    async def test_want_scene_drops_turn_frames(self):
        h = Harness(ScriptedAgent([]))
        q = h.state_bus.subscribe()
        stream = ns_server._state_events(
            h.board, h.state_bus, q, want=["scene"], drop=None
        )
        await next_frame(stream)  # snapshot（只有 scene 分区）
        h.board.note_change("turn")  # 订阅者不关心：不发空帧
        h.ui.set_scene("normal", 0.0)
        h.board.note_change("scene")
        frame = await asyncio.wait_for(next_frame(stream), 1.0)
        data = sse_payload(frame)
        self.assertEqual(set(data["sections"]), {"scene"})

    async def test_play_head_flips_speaking_to_false(self):
        h = Harness(ScriptedAgent([]))
        q = h.state_bus.subscribe()
        stream = ns_server._state_events(
            h.board, h.state_bus, q, want=["turn"], drop=None
        )
        await next_frame(stream)  # snapshot（还没有轮次）

        turn = h.turns.register("stream", "conv")
        h.agent.event_sink(
            {"type": "speech", "step": 0, "index": 0, "text": "short",
             "duration": 0.15, "audio": "", "mime": ""},
            "stream",
            turn.turn_id,
        )

        saw_speaking = False
        for _ in range(10):
            frame = await asyncio.wait_for(next_frame(stream), 2.0)
            current = sse_payload(frame)["sections"]["turn"]["current"]
            if current["speaking"]:
                saw_speaking = True
            else:
                break
        self.assertTrue(saw_speaking, "先收到 speaking=true")
        self.assertFalse(current["speaking"], "play_head 到点后状态总线推 false")
        self.assertEqual(current["remaining_s"], 0.0)


class TestNewSectionsAndManifest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = NeuroStorage(Path(self.tmp.name) / "test.db")
        await self.storage.init()
        self.store = scene_kit.SceneStore(
            self.storage, ui_scenes._scenes_path(), ui_scenes._validate
        )
        await self.store.load()
        ui_scenes.set_store(self.store)

    async def asyncTearDown(self):
        ui_scenes.set_store(None)
        await self.storage.close()
        self.tmp.cleanup()

    async def test_status_has_caption_and_manifest(self):
        h = Harness(ScriptedAgent([]))
        async with _client(h.app) as client:
            body = (await client.get("/status")).json()
        self.assertEqual(
            set(body["sections"]["caption"]["current"]), {"hide_ms", "step_gap_ms"}
        )
        manifest = body["sections"]["manifest"]["current"]
        self.assertEqual(set(manifest), {"is_default", "updated_at", "chars"})
        self.assertTrue(manifest["is_default"])
        self.assertGreater(manifest["chars"], 0)

    async def test_manifest_save_updates_section_and_pushes_once(self):
        h = Harness(ScriptedAgent([]))
        q = h.state_bus.subscribe()
        before = self.store.source()["updated_at"]

        # updated_at 是秒级时间戳：隔开一秒才能证明"变了"
        await asyncio.sleep(1.05)
        # 深拷贝：不能改到 store 的缓存对象
        data = json.loads(json.dumps(self.store.manifest()))
        data["scenes"][0]["label"] = "改过的房间"
        async with _client(h.app) as client:
            resp = await client.put(
                "/ui/manifest", json={"content": json.dumps(data, ensure_ascii=False)}
            )
        self.assertEqual(resp.status_code, 200)

        after = self.store.source()
        self.assertNotEqual(after["updated_at"], before)
        self.assertFalse(after["is_default"])

        pushed = await asyncio.wait_for(q.get(), 1.0)
        self.assertEqual(pushed["sections"]["manifest"]["current"]["updated_at"],
                         after["updated_at"])
        self.assertIn("manifest", pushed["sections"])
        self.assertIn("scene", pushed["sections"], "清单变了，scene/layers 也一并刷新")


class TestRetiredEndpoints(unittest.IsolatedAsyncioTestCase):
    async def test_old_paths_are_gone(self):
        h = Harness(ScriptedAgent([]))
        async with _client(h.app) as client:
            for path in ("/ui/events", "/ui/state", "/manage/events"):
                resp = await client.get(path)
                self.assertEqual(resp.status_code, 404, path)


if __name__ == "__main__":
    unittest.main()
