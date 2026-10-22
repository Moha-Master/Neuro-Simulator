"""neuro 场景系统：数据契约、双脚本边界校验、后端场景状态机。

一句话规则（scenes.py / ui_scene.py 的设计根据）：**一份数据、两个执行者**——
`timeline` 是前端脚本（只动 DOM），`state_script` 是后端脚本（只改权威状态），
服务端加载时把越界的写法直接拒掉，所以渲染器不可能偷偷改场景。
"""

import asyncio
import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from neuro_simulator.neuro_sama import scenes as ui_scenes
from neuro_simulator.neuro_sama import ui_media, ui_scene
from neuro_simulator.neuro_sama.ui_scene import SceneRunner
from neuro_simulator.neuro_sama.ui_state import UiState

ROOT = Path(__file__).resolve().parents[1]
SCENES_JSON = ROOT / "server/neuro_simulator/neuro_sama/ui/public/neuro_scenes.json"
NEURO_VIDEO = ROOT / "server/neuro_simulator/neuro_sama/ui/public/neuro_start.mp4"


def load_data() -> dict:
    return json.loads(SCENES_JSON.read_text(encoding="utf-8"))


def scene_of(manifest: dict, sid: str) -> dict:
    return next(s for s in manifest["scenes"] if s["id"] == sid)


def element_ids(scene: dict) -> list:
    return [e["id"] for e in scene["elements"]]


class TestSceneDataContract(unittest.TestCase):
    def setUp(self):
        self.data = load_data()

    def test_has_normal_and_intro_with_normal_as_default(self):
        ids = [s["id"] for s in self.data["scenes"]]
        self.assertEqual(ids, ["normal", "intro"])
        self.assertEqual(self.data["default_scene"], "normal")

    def test_element_ids_are_shared_across_scenes(self):
        """同 id 跨场景 = 同一 DOM 与同一组件实例：字幕队列才不会因切场景丢失。"""
        normal = scene_of(self.data, "normal")
        intro = scene_of(self.data, "intro")
        self.assertEqual(element_ids(normal), element_ids(intro))
        self.assertEqual(
            element_ids(normal), ["bg_room", "intro_video", "avatar", "caption"]
        )

    def test_scenes_differ_only_by_visibility(self):
        normal = scene_of(self.data, "normal")
        intro = scene_of(self.data, "intro")
        self.assertEqual(
            {e["id"]: e.get("visible", True) for e in normal["elements"]},
            {"bg_room": True, "intro_video": False, "avatar": True, "caption": True},
        )
        self.assertEqual(
            {e["id"]: e.get("visible", True) for e in intro["elements"]},
            {"bg_room": False, "intro_video": True, "avatar": False, "caption": True},
        )

    def test_room_geometry_matches_old_hardcoded_layers(self):
        """几何从 neuro.css 搬进数据，数值必须与原来一致（合成器的整窗换算依赖它）。"""
        avatar = next(e for e in scene_of(self.data, "normal")["elements"] if e["id"] == "avatar")
        self.assertEqual(avatar["box"], {"x": 0.45, "y": -1.25, "w": 0.5, "edge": {"y": "bottom"}})
        caption = next(e for e in scene_of(self.data, "normal")["elements"] if e["id"] == "caption")
        self.assertEqual(
            caption["box"], {"x": 0.05, "y": 0.05, "w": 0.9, "edge": {"y": "bottom"}}
        )

    def test_refs_name_the_elements_the_code_needs(self):
        self.assertEqual(self.data["refs"], {"avatar": "avatar", "caption": "caption"})

    def test_shipped_data_passes_validation(self):
        ui_scenes._validate(self.data)


class TestSceneDuration(unittest.TestCase):
    def test_intro_duration_is_probed_video_plus_after(self):
        video = ui_media.probe_mp4_seconds(NEURO_VIDEO)
        self.assertIsNotNone(video, "neuro_start.mp4 不在预期位置")
        declared = scene_of(load_data(), "intro")["duration"]
        self.assertEqual(declared["media"], "intro_video")
        # 数据里不写 10.475 / 13.475 这类绝对时刻：时长由轨道探测，换素材自动跟随
        self.assertAlmostEqual(
            ui_scenes.effective_duration("intro"),
            float(video) + declared["after"],
            delta=0.02,
        )

    def test_at_end_resolves_relative_to_effective_duration(self):
        duration = ui_scenes.effective_duration("intro")
        steps = {s["action"]: s["at"] for s in ui_scenes.view_steps("intro", duration)}
        video_s = duration - scene_of(load_data(), "intro")["duration"]["after"]
        self.assertAlmostEqual(steps["media.play"], 0.0)
        self.assertAlmostEqual(steps["element.show"], video_s, delta=0.02)
        self.assertAlmostEqual(steps["avatar.rise_in"], video_s, delta=0.02)
        self.assertAlmostEqual(steps["element.hide"], duration, delta=0.02)

    def test_state_script_returns_to_normal_when_animation_ends(self):
        duration = ui_scenes.effective_duration("intro")
        steps = ui_scenes.state_steps("intro", duration)
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0]["action"], "scene.set")
        self.assertEqual(steps[0]["scene"], "normal")
        self.assertAlmostEqual(steps[0]["at"], duration, delta=0.02)

    def test_normal_scene_has_no_backend_script(self):
        self.assertEqual(ui_scenes.state_steps("normal"), [])


class TestSceneValidation(unittest.TestCase):
    """双脚本的硬边界：越界的写法在加载时就被拒，而不是运行时悄悄漂移。"""

    def bad(self, mutate):
        data = copy.deepcopy(load_data())
        mutate(data)
        with self.assertRaises(ui_scenes.SceneError):
            ui_scenes._validate(data)

    def test_rejects_scene_set_in_view_script(self):
        def mutate(data):
            scene_of(data, "intro")["timeline"]["steps"].append(
                {"at": 0, "element": "bg_room", "action": "scene.set", "scene": "normal"}
            )

        self.bad(mutate)

    def test_rejects_element_action_in_backend_script(self):
        def mutate(data):
            scene_of(data, "intro")["state_script"]["steps"].append(
                {"at": 0, "element": "bg_room", "action": "element.hide"}
            )

        self.bad(mutate)

    def test_rejects_unknown_backend_target(self):
        def mutate(data):
            scene_of(data, "intro")["state_script"]["steps"][0]["scene"] = "ghost"

        self.bad(mutate)

    def test_rejects_view_step_on_missing_element(self):
        def mutate(data):
            scene_of(data, "intro")["timeline"]["steps"][0]["element"] = "ghost"

        self.bad(mutate)

    def test_rejects_duration_media_that_is_not_video(self):
        def mutate(data):
            scene_of(data, "intro")["duration"]["media"] = "avatar"

        self.bad(mutate)

    def test_rejects_default_scene_that_does_not_exist(self):
        self.bad(lambda data: data.update(default_scene="ghost"))

    def test_rejects_duplicate_element_ids(self):
        def mutate(data):
            scene_of(data, "normal")["elements"].append(
                copy.deepcopy(scene_of(data, "normal")["elements"][0])
            )

        self.bad(mutate)

    def test_rejects_missing_at_and_at_end(self):
        def mutate(data):
            scene_of(data, "intro")["timeline"]["steps"][1].pop("at_end")

        self.bad(mutate)

    def test_rejects_unknown_scene_lookup(self):
        with self.assertRaises(ui_scenes.UnknownScene):
            ui_scenes.require("ghost")


# ---------- 后端场景状态机 ----------


class _FakeScenes:
    """替代 scenes 模块注入 SceneRunner：只暴露 runner 用到的那几个函数。"""

    def __init__(self, scenes: dict, default: str = "normal") -> None:
        self.scenes = scenes
        self.default = default

    def require(self, sid: str) -> dict:
        if sid not in self.scenes:
            raise ui_scenes.UnknownScene(f"未知场景: {sid}")
        return self.scenes[sid]

    def exists(self, sid: str) -> bool:
        return sid in self.scenes

    def effective_duration(self, sid: str) -> float:
        self.require(sid)
        return float(self.scenes[sid].get("duration", 0.0))

    def state_steps(self, sid: str, duration=None) -> list:
        self.require(sid)
        return list(self.scenes[sid].get("state", []))

    def default_scene_id(self) -> str:
        return self.default


class TestSceneRunner(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.state = UiState()
        self.events: list = []
        self.fake = _FakeScenes(
            {
                "normal": {"duration": 0.0},
                "intro": {
                    "duration": 0.05,
                    "state": [{"action": "scene.set", "scene": "normal", "at": 0.05}],
                },
                "long": {
                    "duration": 30.0,
                    "state": [{"action": "scene.set", "scene": "normal", "at": 30.0}],
                },
                "a": {"duration": 0.0, "state": [{"action": "scene.set", "scene": "b", "at": 0}]},
                "b": {"duration": 0.0, "state": [{"action": "scene.set", "scene": "a", "at": 0}]},
            }
        )
        self._patch = patch("neuro_simulator.neuro_sama.ui_scene.ui_scenes", self.fake)
        self._patch.start()
        self.runner = SceneRunner(self.state, self.events.append)

    async def asyncTearDown(self):
        await self.runner.stop()
        self._patch.stop()

    def scene_ids(self):
        return [e["id"] for e in self.events]

    async def test_switch_broadcasts_scene_set(self):
        info = await self.runner.switch("normal")
        self.assertEqual(info["id"], "normal")
        self.assertEqual(self.scene_ids(), ["normal"])
        # 广播的内容就是画面需要的全部：id + 时长 + 已过秒数
        self.assertEqual(set(self.events[0]), {"type", "id", "duration", "elapsed"})

    async def test_auto_return_runs_on_backend_clock(self):
        await self.runner.switch("intro")
        self.assertEqual(self.state.scene_id(), "intro")
        await asyncio.sleep(0.15)
        self.assertEqual(self.state.scene_id(), "normal")
        self.assertEqual(self.scene_ids(), ["intro", "normal"])

    async def test_manual_switch_cancels_pending_auto_return(self):
        await self.runner.switch("long")  # 30s 后才回 normal
        pending = self.runner._task
        self.assertIsNotNone(pending)
        await self.runner.switch("normal")
        await asyncio.sleep(0.1)
        self.assertTrue(pending.cancelled() or pending.done())
        self.assertEqual(self.scene_ids(), ["long", "normal"])

    async def test_pending_script_does_not_override_manual_change(self):
        """脚本到点时场景已被手动改走 → 作废，不能把画面拽回脚本的目标。"""
        self.fake.scenes["waiter"] = {
            "duration": 0.02,
            "state": [{"action": "scene.set", "scene": "normal", "at": 0.02}],
        }
        await self.runner.switch("waiter")
        await self.runner.switch("long")  # 手动改走
        await asyncio.sleep(0.1)
        self.assertEqual(self.state.scene_id(), "long")
        self.assertEqual(self.scene_ids(), ["waiter", "long"])

    async def test_unknown_scene_is_rejected(self):
        with self.assertRaises(ui_scenes.UnknownScene):
            await self.runner.switch("ghost")

    async def test_init_default_does_not_schedule(self):
        info = self.runner.init_default()
        self.assertEqual(info["id"], "normal")
        self.assertIsNone(self.runner._task)
        self.assertEqual(self.events, [])  # 启动落地不广播（还没有人在听）

    async def test_state_script_loop_is_cut_off(self):
        """A↔B 成环时靠循环保护停下，不能把 CPU 转满。"""
        with self.assertLogs("neuro_simulator.neuro_sama.ui_scene", level="ERROR") as logs:
            await self.runner.switch("a")
            await asyncio.sleep(0.2)
        self.assertLessEqual(len(self.events), ui_scene.LOOP_MAX_CHANGES)
        self.assertTrue(any("成环" in m for m in logs.output), logs.output)

    async def test_guard_release_allows_manual_switch_again(self):
        with self.assertLogs("neuro_simulator.neuro_sama.ui_scene", level="ERROR"):
            await self.runner.switch("a")
            await asyncio.sleep(0.1)
        await self.runner.switch("normal")
        self.assertEqual(self.state.scene_id(), "normal")


if __name__ == "__main__":
    unittest.main()
