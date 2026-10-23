"""开播"开场是否放完"的判定：stream 轮询 neuro 的权威场景，**不倒数任何秒数**。

链路：`POST neuro /ui/intro` 返回**她切到的场景 id**（不是时长）→ 合成器落
`room_intro`（neuro 全屏、不显示 chat/highlight）→ stream 每 0.5s 拉 `GET neuro /status`
→ 她离开那个场景 = 开场放完 → `on_scene("room")` 并同时推进 live。

三条退出路径都有测试：正常放行、人工下播（静默）、硬上限兜底（中止开播 + last_error）。
硬上限与 neuro 的场景时长无关——只有她永远停在开场（state_script 数据坏、进程假死）
或状态通道一直读不到时才会走到那里。
"""

import asyncio
import contextlib
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from neuro_simulator.broadcaster import EventBroadcaster
from neuro_simulator.stream import loop as loop_module
from neuro_simulator.stream import scenes as scene_catalog
from neuro_simulator.stream import settings as stream_settings
from neuro_simulator.stream.loop import SCENE_ROOM, SCENE_ROOM_INTRO, StreamConflict, StreamLoop
from neuro_simulator.stream.neuro_client import NeuroError
from neuro_simulator.stream.persona import ChatbotPersonaStore, build_template_store
from neuro_simulator.stream.storage import Storage

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_SCENES = ROOT / "server/neuro_simulator/stream/defaults/scenes.json"
NEURO_SCENES = ROOT / "server/neuro_simulator/neuro_sama/defaults/neuro_scenes.json"


def stream_scenes() -> list:
    return json.loads(PUBLIC_SCENES.read_text(encoding="utf-8"))["scenes"]


class _ScriptedNeuro:
    """neuro 替身：status() 依次吐出预设场景（最后一项会一直重复）。"""

    def __init__(self, scenes: list, fail_first: int = 0) -> None:
        self.scenes = list(scenes)
        self.calls = 0
        self.fail_first = fail_first

    async def health(self) -> dict:
        return {"status": "ok"}

    async def intro(self) -> str:
        # 新契约：返回场景 id，不是时长
        return "intro"

    async def status(self, channel=None) -> dict:
        self.calls += 1
        if self.calls <= self.fail_first:
            raise NeuroError("connection refused")
        sid = self.scenes[min(self.calls - 1, len(self.scenes) - 1)]
        return {
            "module": "neuro_sama",
            "sections": {
                "scene": {
                    "current": {"id": sid, "duration": 0.0, "elapsed": 0.0} if sid else None
                },
                "turn": {"current": {"speaking": False, "remaining_s": 0.0}},
            },
        }

    async def stop(self, channel, conversation_id) -> None:
        return None


class _SubscribedNeuro(_ScriptedNeuro):
    """有 `/state/events` 的替身：按帧剧本推状态（订阅路径用）。"""

    def __init__(self, frames: list, *, status_scenes: list | None = None) -> None:
        super().__init__(status_scenes or [])
        self.frames = list(frames)
        self.subscriptions = 0

    async def state_events(self, want=None, drop=None):
        self.subscriptions += 1
        for frame in self.frames:
            await asyncio.sleep(0.01)
            yield frame

    async def status(self, channel=None) -> dict:
        raise AssertionError("订阅路径可用时不该轮询 /status")


class _BrokenSubscription(_ScriptedNeuro):
    """订阅一建立就失败（旧 neuro / 代理不支持）→ 必须退回轮询。"""

    def __init__(self, scenes: list) -> None:
        super().__init__(scenes)
        self.subscriptions = 0

    async def state_events(self, want=None, drop=None):
        self.subscriptions += 1
        raise NeuroError("state bus unavailable")
        yield {}  # pragma: no cover - 让函数成为 async generator


class _SilentSubscription(_ScriptedNeuro):
    """订阅活着但一直没有推送（长开场本来就没有离散变化）→ fallback 兜底。"""

    def __init__(self, scenes: list) -> None:
        super().__init__(scenes)
        self.subscriptions = 0

    async def state_events(self, want=None, drop=None):
        self.subscriptions += 1
        await asyncio.sleep(10)
        yield {}  # pragma: no cover


class _IntroThenSilent(_ScriptedNeuro):
    """订阅报了开场场景后就不再有变化（脚本坏了），硬上限不能被 fallback 拖长。"""

    async def state_events(self, want=None, drop=None):
        yield {"type": "snapshot", **_scene_frame("intro")}
        await asyncio.sleep(10)


def _scene_frame(scene_id: str | None) -> dict:
    return {"sections": {"scene": {"current": {"id": scene_id} if scene_id else None}}}


class _NoopChatbot:
    def start_session(self, *args, **kwargs) -> None:
        pass

    def stop_session(self) -> None:
        pass


def _patch_client(fake):
    """让 loop 内部 NeuroClient(url) 都拿到同一个替身（跨 start/_main 共享计数）。"""
    return patch.object(loop_module, "NeuroClient", lambda base_url: fake)


class TestIntroSceneData(unittest.TestCase):
    """场景数据侧：开场画面归 stream 自己声明，neuro 的场景 id 不出现在 stream 清单里。"""

    def test_room_intro_is_a_hidden_neuro_only_scene(self):
        scenes = {s["id"]: s for s in stream_scenes()}
        intro = scenes[SCENE_ROOM_INTRO]
        self.assertTrue(intro["hidden"], "开场画面不该出现在手动/只读场景清单里")
        self.assertEqual(intro["elements"], [], "开场只有 neuro 窗口层，不叠 chat/highlight")
        self.assertEqual(intro["neuro"], {"x": 0, "y": 0, "w": 1, "z": 10, "hide": []})

    def test_room_reveals_chat_with_an_enter_script(self):
        """离开开场后 chat 的出现由数据声明（淡入），不靠前端猜 neuro 的场景。"""
        room = next(s for s in stream_scenes() if s["id"] == SCENE_ROOM)
        by_id = {e["id"]: e for e in room["elements"]}
        for eid in ("chat", "highlight"):
            enter = by_id[eid].get("script", {}).get("enter")
            self.assertIsNotNone(enter, f"{eid} 需要 enter 脚本才能在切回 room 时淡入")
            self.assertIn("opacity", enter.get("tween", {}))
            self.assertGreater(enter.get("duration", 0), 0)

    def test_neuro_scene_namespace_is_separate(self):
        """两套场景各自命名：stream 用 room_intro，不把 neuro 的 intro id 搬过来。"""
        stream_ids = {s["id"] for s in stream_scenes()}
        neuro_ids = {s["id"] for s in json.loads(NEURO_SCENES.read_text(encoding="utf-8"))["scenes"]}
        self.assertIn("intro", neuro_ids)
        self.assertNotIn("intro", stream_ids)
        self.assertEqual(stream_ids & neuro_ids, set())

    def test_catalog_excludes_room_intro_from_manual_list(self):
        with patch.object(scene_catalog, "load_scenes", return_value=stream_scenes()):
            self.assertTrue(scene_catalog.hidden(SCENE_ROOM_INTRO))
            self.assertNotIn(SCENE_ROOM_INTRO, [s["id"] for s in scene_catalog.overview()])
            self.assertEqual(scene_catalog.label(SCENE_ROOM_INTRO), "开场")


class _LiveOnceLoop(StreamLoop):
    """到 live 后立刻收工的最小循环，避免真的走一轮对话。"""

    async def _run_round(self, settings, session):
        self._reached_live = True
        self._stop_event.set()
        return None


class TestIntroGate(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = Storage(Path(self.tmp.name) / "test.db")
        await self.storage.init()
        self.persona = ChatbotPersonaStore(self.storage)
        await self.persona.load()
        self.templates = build_template_store(self.storage)
        await self.templates.load()
        self.bus = EventBroadcaster()
        # 订阅必须在发布之前（EventBroadcaster 只把事件送给已存在的订阅者）
        self.q = self.bus.subscribe()
        self.loop = _LiveOnceLoop(self.storage, self.bus, self.persona, self.templates)
        self.loop._reached_live = False
        queue_row, _ = await self.storage.create_queue("intro test")
        _, session_id = await self.storage.create_session(queue_row, "stream", "conv-intro")
        row = await self.storage.get_session(session_id)
        self.loop.session = {
            "session_row": row["id"],
            "session_id": session_id,
            "queue_row": queue_row,
            "channel": "stream",
            "conversation_id": "conv-intro",
            "cursor": 0,
            "priority_cursor": 0,
        }
        self.loop.state = "starting"
        self.loop._intro_gate = {
            "waiting_for": "intro",
            "neuro_scene": "intro",
            "polls": 0,
            "started_at": time.monotonic(),
            "timeout_s": loop_module.START_READY_TIMEOUT_S,
        }
        # 快速轮询：测试里不等 0.5 秒
        self._patchers = [
            patch.object(loop_module, "READY_POLL_INTERVAL_S", 0.02),
            patch.object(
                stream_settings, "load", return_value=SimpleNamespace(neuro_url="http://x", channel="stream")
            ),
            patch.object(scene_catalog, "load_scenes", return_value=stream_scenes()),
        ]
        for p in self._patchers:
            p.start()

    async def asyncTearDown(self):
        for p in self._patchers:
            p.stop()
        await self.storage.close()
        self.tmp.cleanup()

    def drain(self):
        out = []
        while not self.q.empty():
            out.append(self.q.get_nowait())
        return out

    async def run_main(self, fake, timeout=2.0, ready_timeout=None, fallback=None):
        patchers = [_patch_client(fake)]
        if ready_timeout is not None:
            patchers.append(patch.object(loop_module, "START_READY_TIMEOUT_S", ready_timeout))
        if fallback is not None:
            patchers.append(patch.object(loop_module, "INTRO_FALLBACK_S", fallback))
        for p in patchers:
            p.start()
        try:
            await asyncio.wait_for(self.loop._main(), timeout=timeout)
        finally:
            for p in patchers:
                p.stop()

    async def test_goes_live_when_neuro_leaves_the_intro_scene(self):
        fake = _ScriptedNeuro(["intro", "intro", "normal"])
        await self.run_main(fake)
        self.assertTrue(self.loop._reached_live)
        self.assertEqual(fake.calls, 3, "离开开场场景的那一刻就该放行")
        self.assertIsNone(self.loop.last_error)
        scenes = [e["id"] for e in self.drain() if e["type"] == "scene_set"]
        self.assertEqual(scenes, [SCENE_ROOM], "开场画面 → 常态画面是这一次真实的场景切换")

    async def test_empty_scene_counts_as_ready(self):
        """neuro 刚重启/场景数据缺失时 scene 为空——不能因为读不到就一直等。"""
        fake = _ScriptedNeuro([None])
        await self.run_main(fake)
        self.assertTrue(self.loop._reached_live)
        self.assertIsNone(self.loop.last_error)

    async def test_subscription_path_releases_on_scene_change(self):
        """主路径：订阅状态总线，她离开开场场景的那一帧就放行。"""
        fake = _SubscribedNeuro([
            {"type": "snapshot", **_scene_frame("intro")},
            {"type": "state", "rev": 1, **_scene_frame("normal")},
        ])
        gate = self.loop._intro_gate
        await self.run_main(fake)
        self.assertTrue(self.loop._reached_live)
        self.assertEqual(fake.subscriptions, 1)
        self.assertEqual(gate["ready_via"], "sse")
        self.assertIsNone(self.loop.last_error)

    async def test_subscription_none_current_also_releases(self):
        """`current` 为 null（neuro 刚重启/场景缺失）与轮询路径同样算放行。"""
        fake = _SubscribedNeuro([
            {"type": "snapshot", **_scene_frame(None)},
        ])
        await self.run_main(fake)
        self.assertTrue(self.loop._reached_live)

    async def test_broken_subscription_falls_back_to_polling(self):
        """订阅建立失败 → 退回 0.5s 轮询，判据不变。"""
        fake = _BrokenSubscription(["intro", "normal"])
        gate = self.loop._intro_gate
        await self.run_main(fake, ready_timeout=2.0)
        self.assertTrue(self.loop._reached_live)
        self.assertEqual(fake.subscriptions, 1)
        self.assertEqual(gate["ready_via"], "poll")
        self.assertEqual(fake.calls, 2)

    async def test_silent_subscription_falls_back_after_the_window(self):
        """订阅一直没消息（长开场没有中间变化）→ INTRO_FALLBACK_S 后走轮询。"""
        fake = _SilentSubscription(["intro", "normal"])
        gate = self.loop._intro_gate
        await self.run_main(fake, ready_timeout=2.0, fallback=0.05)
        self.assertTrue(self.loop._reached_live)
        self.assertEqual(gate["ready_via"], "poll")

    async def test_hard_deadline_is_not_extended_by_the_fallback_window(self):
        """订阅报完 intro 就没声了：到硬上限立刻中止，不能先睡满 3s 兜底窗口。"""
        fake = _IntroThenSilent(["intro"])
        started = time.monotonic()
        await self.run_main(fake, ready_timeout=0.12)
        elapsed = time.monotonic() - started
        self.assertFalse(self.loop._reached_live)
        self.assertEqual(self.loop.last_error["code"], "intro_timeout")
        self.assertLess(elapsed, 1.0, "硬上限优先于 fallback 窗口")

    async def test_stalled_intro_aborts_start(self):
        """她一直停在开场场景（脚本坏了/进程假死）→ 到硬上限中止开播，不硬转 live。"""
        fake = _ScriptedNeuro(["intro"])
        await self.run_main(fake, ready_timeout=0.12)
        self.assertFalse(self.loop._reached_live)
        self.assertEqual(self.loop.state, "offline")
        self.assertIsNone(self.loop.session, "中止开播不留半场场次")
        self.assertEqual(self.loop.last_error["code"], "intro_timeout")
        detail = self.loop.last_error["detail"]
        self.assertEqual(detail["waiting_for"], "intro")
        self.assertEqual(detail["neuro_scene"], "intro")
        self.assertGreater(detail["polls"], 0)
        ended = [e for e in self.drain() if e["type"] == "session_end"]
        self.assertEqual(ended[0]["reason"], "intro_timeout")

    async def test_status_failures_are_tolerated_until_the_deadline(self):
        """轮询失败（neuro 重启中）不单独终止：先重试，最终仍由硬上限兜住。"""
        fake = _ScriptedNeuro(["intro"], fail_first=999)
        await self.run_main(fake, ready_timeout=0.12)
        self.assertFalse(self.loop._reached_live)
        self.assertEqual(self.loop.last_error["code"], "intro_timeout")
        self.assertIn("connection refused", self.loop.last_error["detail"]["status_error"])

    async def test_recovered_polling_still_goes_live(self):
        """前几次轮询失败（neuro 正在重启），恢复后读到离开开场 → 照常放行。"""
        fake = _ScriptedNeuro(["intro", "normal"], fail_first=2)
        await self.run_main(fake, ready_timeout=1.0)
        self.assertTrue(self.loop._reached_live)
        self.assertIsNone(self.loop.last_error)

    async def test_stop_during_intro_exits_silently(self):
        """人工下播：即时退出，不记 last_error（那不是故障）。"""
        fake = _ScriptedNeuro(["intro"])
        self.loop._stop_event.set()
        await self.run_main(fake, ready_timeout=5.0)
        self.assertFalse(self.loop._reached_live)
        self.assertIsNone(self.loop.last_error)
        self.assertEqual(fake.calls, 0, "已经要停了就不再拉状态")

    async def test_intro_status_shape(self):
        """starting 阶段 dashboard 能看到"在等什么、等了多久、还剩多久"；其他阶段为 null。"""
        st = self.loop.intro_status()
        self.assertEqual(st["waiting_for"], "intro")
        self.assertEqual(st["timeout_s"], loop_module.START_READY_TIMEOUT_S)
        self.assertIn("waited_s", st)
        self.assertIn("remaining_s", st)
        self.assertNotIn("started_at", st, "monotonic 时刻不外泄（跨进程无意义）")
        self.loop.state = "live"
        self.assertIsNone(self.loop.intro_status())


class TestStartPath(unittest.IsolatedAsyncioTestCase):
    """start() 侧：开场画面落地 + 把"要等她离开的场景 id"交给闸门。"""

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
        # 开播链路只关心"闸门"，chatbot 循环与 fake settings 的字段无关，替身掉
        self.loop.chatbot = _NoopChatbot()
        self.q = self.bus.subscribe()
        self.settings = SimpleNamespace(
            neuro_url="http://x", channel="stream",
            default_title="T", default_category="C", default_tags=["a"],
            messages_per_round=1, speech_cooldown_ms=0, empty_queue_behavior="silent",
            placeholder_message="",
        )
        self._patchers = [
            patch.object(stream_settings, "load", return_value=self.settings),
            patch.object(scene_catalog, "load_scenes", return_value=stream_scenes()),
            patch.object(StreamLoop, "_main", lambda self: asyncio.sleep(0)),
        ]
        for p in self._patchers:
            p.start()

    async def asyncTearDown(self):
        for p in self._patchers:
            p.stop()
        await self.storage.close()
        self.tmp.cleanup()

    def drain(self):
        out = []
        while not self.q.empty():
            out.append(self.q.get_nowait())
        return out

    async def test_start_lands_on_room_intro_and_records_the_gate(self):
        fake = _ScriptedNeuro(["intro"])
        with _patch_client(fake):
            await self.loop.start()
        gate = self.loop._intro_gate
        self.assertEqual(gate["waiting_for"], "intro", "判据是 neuro 实际切到的场景 id")
        self.assertEqual(gate["timeout_s"], loop_module.START_READY_TIMEOUT_S)
        scenes = [e["id"] for e in self.drain() if e["type"] == "scene_set"]
        self.assertEqual(scenes, [SCENE_ROOM_INTRO], "开播先落开场画面，不是 room")
        self.assertEqual(self.loop.state, "starting")

    async def test_start_blocked_when_neuro_unreachable(self):
        class _Down:
            async def health(self):
                raise NeuroError("connection refused")

            async def intro(self):
                raise NeuroError("connection refused")

        with _patch_client(_Down()):
            with self.assertRaises(StreamConflict) as ctx:
                await self.loop.start()
        self.assertIn("neuro", str(ctx.exception))
        self.assertIsNone(self.loop.session)
        self.assertEqual(self.loop.state, "offline")


class _FakeLoop(StreamLoop):
    """给 game 闸门用的最小循环。"""

    async def _run_round(self, settings, session):
        self._reached_live = True
        self._stop_event.set()


class _FakeSpeechStatus:
    """game 闸门的 neuro 替身：status 给出本轮语音预测播完的绝对时刻。"""

    def __init__(self, ends_in_s: float) -> None:
        self.ends_at = time.time() + ends_in_s

    async def status(self, channel=None) -> dict:
        return {
            "sections": {
                "turn": {
                    "current": {
                        "turn_id": 1,
                        "speaking": True,
                        "ends_at": self.ends_at,
                    }
                }
            }
        }


class TestGameSpeechGate(unittest.IsolatedAsyncioTestCase):
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
        self.settings = SimpleNamespace(
            game_tool_wait_speech=True, neuro_url="http://fake", channel="stream"
        )

    async def asyncTearDown(self):
        await self.storage.close()
        self.tmp.cleanup()

    def _patchers(self, fake):
        return [
            patch.object(loop_module, "NeuroClient", lambda base_url: fake),
            patch.object(stream_settings, "load", return_value=self.settings),
        ]

    async def test_game_action_waits_for_speech_end(self):
        fake = _FakeSpeechStatus(0.05)
        started = time.monotonic()
        with contextlib.ExitStack() as stack:
            for p in self._patchers(fake):
                stack.enter_context(p)
            result = await self.loop.run_game_action(lambda: {"ok": True})
        self.assertTrue(result["ok"])
        self.assertGreaterEqual(time.monotonic() - started, 0.04)

    async def test_game_actions_are_serialized(self):
        fake = _FakeSpeechStatus(0.04)
        order: list[str] = []

        def act(name: str):
            order.append(name)
            # 第二动作开始前，neuro 又排了新句子（循环内重读 status 才能看到）
            fake.ends_at = time.time() + 0.04
            return {"ok": True, "name": name}

        started = time.monotonic()
        with contextlib.ExitStack() as stack:
            for p in self._patchers(fake):
                stack.enter_context(p)
            first = asyncio.create_task(self.loop.run_game_action(lambda: act("a")))
            await asyncio.sleep(0)
            second = asyncio.create_task(self.loop.run_game_action(lambda: act("b")))
            await asyncio.gather(first, second)
        self.assertEqual(order, ["a", "b"])
        self.assertGreaterEqual(time.monotonic() - started, 0.07)


if __name__ == "__main__":
    unittest.main()
