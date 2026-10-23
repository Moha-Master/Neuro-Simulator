"""stream 场景机制：与 neuro 共用 scene_kit 之后的契约（载荷、降级、校验、后端脚本）。

要点：
- `scene_set` 载荷与 neuro 窗口一致：`{id, duration, elapsed}`，中途连接能按
  `elapsed` 恢复场景脚本；
- 未知场景 id 不抛异常（游戏场景 id 来自 GameHub，抛出来会把直播循环带崩），
  只降级为"广播裸 id"+ 日志；
- 校验把两个执行者的边界钉死（timeline 不许 scene.set、state_script 不许碰元素）；
- stream 的场景时长只能是秒数（开场素材归 neuro，不做轨道探测）。
"""

import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from neuro_simulator import scene_kit
from neuro_simulator.broadcaster import EventBroadcaster
from neuro_simulator.stream import scenes as scene_catalog
from neuro_simulator.stream.loop import StreamLoop
from neuro_simulator.stream.persona import ChatbotPersonaStore, build_template_store
from neuro_simulator.stream.storage import Storage

async def _wait_until(predicate, timeout=5.0, message="条件未在超时前成立"):
    """等 predicate() 为真（带超时）。

    固定 sleep + 断言在机器繁忙时会把偶发的调度延迟变成测试失败；
    条件等待只在条件成立时立刻返回，机器慢也只是多等一会儿。
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError(f"{message}（等 {timeout}s）")


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_SCENES = ROOT / "server/neuro_simulator/stream/defaults/scenes.json"

ROOM = {
    "id": "room",
    "label": "房间",
    "elements": [
        {"id": "chat", "z": 20, "box": {"x": 0, "y": 0, "w": 0.25, "h": 0.5},
         "content": {"kind": "component", "name": "chat.overlay"}}
    ],
    "neuro": {"x": 0, "y": 0, "w": 1, "hide": []},
}
BUMPER = {
    "id": "bumper",
    "label": "转场",
    "hidden": True,
    "duration": 0.05,
    "timeline": {"steps": [{"at": 0, "element": "card", "action": "element.show"}]},
    "state_script": {"steps": [{"at_end": 0, "action": "scene.set", "scene": "room"}]},
    "elements": [
        {"id": "card", "z": 30, "box": {"x": 0, "y": 0, "w": 1, "h": 1},
         "content": {"kind": "color", "color": "#000000"}}
    ],
    "neuro": {"x": 0, "y": 0, "w": 1, "hide": ["bg_room"]},
}
MANIFEST = {"version": 1, "scenes": [ROOM, BUMPER]}


def _with_scenes(scenes):
    """把内存里的场景清单喂给 stream 的服务端视角（各函数都走 load_scenes）。"""
    return patch.object(scene_catalog, "load_scenes", return_value=scenes)


class TestManifestOwnership(unittest.TestCase):
    """场景数据归后端：包内 defaults/ 是唯一来源。

    这份 json 曾经放在前端 `public/` 里靠构建拷进 dist，服务端为了读它不得不
    依赖前端构建产物——反向耦合。现在由模块自己提供（`GET /ui/manifest`），
    前端来取。这几条守住这个归属。
    """

    def test_manifest_lives_in_package_defaults(self):
        self.assertTrue(PUBLIC_SCENES.is_file(), PUBLIC_SCENES)
        legacy = ROOT / "server/neuro_simulator/stream/ui/public/scenes.json"
        self.assertFalse(legacy.exists(), "场景数据不该再放在前端 public/ 里")

    def test_frontend_fetches_manifest_from_module_api(self):
        """前端不再拉静态 json，而是问模块要（`apiUrl` 拼出模块 API 地址）。"""
        src = (
            ROOT / "dashboard/src/ui/stream/scenes.ts"
        ).read_text(encoding="utf-8")
        # 断言不依赖引号风格（eslint 可能把双引号改成单引号）
        self.assertIn("ui/manifest", src)
        self.assertIn("apiUrl", src)
        # 不再拉静态 json（asset(...) 拼出来的地址）：只问模块要
        self.assertNotIn("asset(", src)

    def test_manifest_returns_full_data_for_renderer(self):
        """渲染器要的是完整定义（元素/box/script/neuro），不是 dashboard 的概览。"""
        data = scene_catalog.manifest()
        room = next(s for s in data["scenes"] if s["id"] == "room")
        self.assertTrue(room.get("elements"))
        self.assertIn("neuro", room)
        self.assertIn("box", room["elements"][0], "完整清单要带 box（渲染器要摆位）")
        overview = next(s for s in scene_catalog.overview() if s["id"] == "room")
        self.assertNotIn("box", overview["elements"][0], "概览是子集，不带 box")

    def test_dashboard_overview_stays_a_subset(self):
        ids = {s["id"] for s in scene_catalog.overview()}
        self.assertIn("room", ids)
        self.assertNotIn("room_intro", ids, "hidden 内部场景不进 dashboard 清单")


class TestSceneValidation(unittest.TestCase):
    def test_shipped_scenes_pass_validation(self):
        import json

        scene_catalog._validate(json.loads(PUBLIC_SCENES.read_text(encoding="utf-8")))

    def bad(self, mutate):
        import copy

        data = copy.deepcopy(MANIFEST)
        mutate(data)
        with self.assertRaises(scene_kit.SceneError):
            scene_catalog._validate(data)

    def test_rejects_scene_set_in_view_script(self):
        def mutate(data):
            data["scenes"][1]["timeline"]["steps"].append(
                {"at": 0, "element": "card", "action": "scene.set", "scene": "room"}
            )

        self.bad(mutate)

    def test_rejects_element_action_in_backend_script(self):
        self.bad(
            lambda data: data["scenes"][1]["state_script"]["steps"].append(
                {"at": 0, "element": "card", "action": "element.hide"}
            )
        )

    def test_rejects_backend_script_touching_elements(self):
        self.bad(
            lambda data: data["scenes"][1]["state_script"]["steps"][0].update(
                {"element": "card"}
            )
        )

    def test_rejects_unknown_backend_target(self):
        self.bad(
            lambda data: data["scenes"][1]["state_script"]["steps"][0].update(
                {"scene": "ghost"}
            )
        )

    def test_rejects_media_probe_duration(self):
        # 合成器不拥有素材：开场视频时长归 neuro，stream 场景只写秒数
        self.bad(lambda data: data["scenes"][1].update(duration={"media": "card", "after": 1}))

    def test_rejects_bad_neuro_hide(self):
        self.bad(lambda data: data["scenes"][0]["neuro"].update(hide="bg_room"))

    def test_rejects_missing_neuro_geometry(self):
        self.bad(lambda data: data["scenes"][0]["neuro"].pop("w"))


class TestSceneSteps(unittest.TestCase):
    def test_at_end_resolves_against_declared_duration(self):
        with _with_scenes([ROOM, BUMPER]):
            steps = scene_catalog.state_steps("bumper")
        self.assertEqual(len(steps), 1)
        self.assertAlmostEqual(steps[0]["at"], 0.05, places=3)
        self.assertNotIn("at_end", steps[0])

    def test_room_has_no_scripts(self):
        with _with_scenes([ROOM, BUMPER]):
            self.assertEqual(scene_catalog.state_steps("room"), [])
            self.assertEqual(scene_catalog.view_steps("room"), [])
            self.assertEqual(scene_catalog.effective_duration("room"), 0.0)


class TestLoopSceneContract(unittest.IsolatedAsyncioTestCase):
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
        self.q = self.bus.subscribe()

    async def asyncTearDown(self):
        await self.loop._scenes.stop()
        await self.storage.close()
        self.tmp.cleanup()

    def drain(self):
        events = []
        while not self.q.empty():
            events.append(self.q.get_nowait())
        return events

    async def test_scene_set_payload_matches_neuro_contract(self):
        with _with_scenes([ROOM, BUMPER]):
            self.loop.on_scene("room")
        event = self.drain()[0]
        self.assertEqual(event["type"], "scene_set")
        self.assertEqual(event["id"], "room")
        self.assertIn("duration", event)
        self.assertIn("elapsed", event)
        snap = self.loop.scene_snapshot()
        self.assertEqual(snap["id"], "room")
        self.assertEqual(set(snap), {"id", "duration", "elapsed"})

    async def test_unknown_scene_degrades_instead_of_raising(self):
        with _with_scenes([ROOM, BUMPER]):
            self.loop.on_scene("ghost")  # 不抛
        event = self.drain()[0]
        self.assertEqual(event["id"], "ghost")
        self.assertEqual(event["duration"], 0.0)
        self.assertEqual(self.loop.last_scene, "ghost")

    async def test_backend_script_returns_to_room(self):
        """转场场景放完自动回 room：由后端时钟执行，不依赖任何渲染器。"""
        with _with_scenes([ROOM, BUMPER]):
            self.loop.on_scene("bumper")
            self.assertEqual(self.loop.last_scene, "bumper")
            self.drain()  # 先收走进入转场时的那条
            await _wait_until(
                lambda: self.loop.last_scene == "room", message="后端脚本没有切回 room"
            )
            ids = [e["id"] for e in self.drain() if e["type"] == "scene_set"]
        self.assertEqual(self.loop.last_scene, "room")
        self.assertEqual(ids, ["room"])

    async def test_manual_switch_cancels_pending_backend_script(self):
        with _with_scenes([ROOM, BUMPER]):
            self.loop.on_scene("bumper")
            self.loop.on_scene("room")
            await asyncio.sleep(0.15)
        self.assertEqual(self.loop.last_scene, "room")

    async def test_finalize_clears_scene_and_script(self):
        self.loop.session = None
        with _with_scenes([ROOM, BUMPER]):
            self.loop.on_scene("bumper")
            await self.loop._finalize()
        self.assertIsNone(self.loop.last_scene)
        self.assertEqual(self.loop.scene_snapshot(), {})
        self.assertIsNone(self.loop._scenes._task)


if __name__ == "__main__":
    unittest.main()
