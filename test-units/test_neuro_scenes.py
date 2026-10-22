"""neuro 场景系统：数据契约、双脚本边界校验、后端场景状态机。

一句话规则（scenes.py / ui_scene.py 的设计根据）：**一份数据、两个执行者**——
`timeline` 是前端脚本（只动 DOM），`state_script` 是后端脚本（只改权威状态），
服务端加载时把越界的写法直接拒掉，所以渲染器不可能偷偷改场景。
"""

import asyncio
import copy
import json
import time
import unittest
from pathlib import Path

from neuro_simulator import scene_kit
from neuro_simulator.neuro_sama import scenes as ui_scenes
from neuro_simulator.neuro_sama import ui_media, ui_scene
from neuro_simulator.scene_kit import SceneRunner
from neuro_simulator.neuro_sama.ui_state import UiState

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
SCENES_JSON = ROOT / "server/neuro_simulator/neuro_sama/defaults/neuro_scenes.json"
NEURO_VIDEO = ROOT / "server/neuro_simulator/neuro_sama/assets/neuro_start.mp4"


def load_data() -> dict:
    return json.loads(SCENES_JSON.read_text(encoding="utf-8"))


def scene_of(manifest: dict, sid: str) -> dict:
    return next(s for s in manifest["scenes"] if s["id"] == sid)


def element_ids(scene: dict) -> list:
    return [e["id"] for e in scene["elements"]]


class TestManifestOwnership(unittest.TestCase):
    """场景数据归后端：包内 defaults/ 是唯一来源。

    这份 json 曾经放在前端 `public/` 里靠构建拷进 dist，服务端为了读它不得不
    依赖前端构建产物——反向耦合。现在由模块自己提供（`GET /ui/manifest`），
    前端来取。这几条守住这个归属。
    """

    def test_manifest_lives_in_package_defaults(self):
        self.assertTrue(SCENES_JSON.is_file(), SCENES_JSON)
        legacy = ROOT / "server/neuro_simulator/neuro_sama/ui/public/neuro_scenes.json"
        self.assertFalse(legacy.exists(), "场景数据不该再放在前端 public/ 里")

    def test_frontend_fetches_manifest_from_module_api(self):
        src = (
            ROOT / "dashboard/src/ui/neuro/scenes.ts"
        ).read_text(encoding="utf-8")
        self.assertIn('apiUrl("ui/manifest")', src)
        self.assertNotIn('asset("neuro_scenes.json")', src)

    def test_module_manifest_is_the_full_data(self):
        """模块下发的清单 = 完整数据（渲染器要 default_scene 与 refs）。"""
        from neuro_simulator.neuro_sama import scenes as ui_scenes

        data = ui_scenes.manifest()
        self.assertEqual(data["default_scene"], "normal")
        self.assertTrue(data.get("refs"))
        self.assertIn("intro", {s["id"] for s in data["scenes"]})


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


# ---------- 后端场景状态机（scene_kit 的机制 + 注入的假 policy） ----------


class _FakePolicy:
    """scene_kit.ScenePolicy 的测试替身：不读真实数据文件，只给调度语义。"""

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
        return float(self.require(sid).get("duration", 0.0))

    def state_steps(self, sid: str, duration=None) -> list:
        dur = self.effective_duration(sid) if duration is None else float(duration)
        return scene_kit.resolve_steps(self.require(sid).get("state"), dur)

    def default_scene_id(self) -> str:
        return self.default


class TestSceneRunner(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.state = UiState()
        self.events: list = []
        self.policy = _FakePolicy(
            {
                "normal": {"duration": 0.0},
                "intro": {
                    "duration": 0.05,
                    "state": [{"action": "scene.set", "scene": "normal", "at_end": 0}],
                },
                "long": {
                    "duration": 30.0,
                    "state": [{"action": "scene.set", "scene": "normal", "at_end": 0}],
                },
                "a": {"duration": 0.0, "state": [{"action": "scene.set", "scene": "b", "at": 0}]},
                "b": {"duration": 0.0, "state": [{"action": "scene.set", "scene": "a", "at": 0}]},
            }
        )
        self.runner = SceneRunner(self.state, self.events.append, self.policy)

    async def asyncTearDown(self):
        await self.runner.stop()

    def scene_ids(self):
        return [e["id"] for e in self.events]

    async def test_switch_writes_state_and_broadcasts(self):
        info = self.runner.switch("normal")
        self.assertEqual(info["id"], "normal")
        self.assertEqual(self.scene_ids(), ["normal"])
        # 广播的内容就是画面需要的全部：id + 时长 + 已过秒数
        self.assertEqual(set(self.events[0]), {"type", "id", "duration", "elapsed"})

    async def test_auto_return_runs_on_backend_clock(self):
        self.runner.switch("intro")
        self.assertEqual(self.state.scene_id(), "intro")
        await _wait_until(lambda: self.state.scene_id() == "normal", message="开场没有自动回常态")
        self.assertEqual(self.state.scene_id(), "normal")
        self.assertEqual(self.scene_ids(), ["intro", "normal"])

    async def test_manual_switch_cancels_pending_auto_return(self):
        self.runner.switch("long")  # 30s 后才回 normal
        pending = self.runner._task
        self.assertIsNotNone(pending)
        self.runner.switch("normal")
        await _wait_until(lambda: pending.done(), message="旧脚本没有被取消")
        self.assertTrue(pending.cancelled() or pending.done())
        self.assertEqual(self.scene_ids(), ["long", "normal"])

    async def test_pending_script_does_not_override_manual_change(self):
        """脚本到点时场景已被手动改走 → 作废，不能把画面拽回脚本的目标。"""
        self.policy.scenes["waiter"] = {
            "duration": 0.02,
            "state": [{"action": "scene.set", "scene": "normal", "at": 0.02}],
        }
        self.runner.switch("waiter")
        self.runner.switch("long")  # 手动改走（同时取消 waiter 的脚本）
        await asyncio.sleep(0.1)
        self.assertEqual(self.state.scene_id(), "long")
        self.assertEqual(self.scene_ids(), ["waiter", "long"])

    async def test_unknown_scene_is_rejected(self):
        with self.assertRaises(ui_scenes.UnknownScene):
            self.runner.switch("ghost")

    async def test_init_default_writes_state_without_broadcast(self):
        info = self.runner.init_default()
        self.assertEqual(info["id"], "normal")
        self.assertIsNone(self.runner._task)
        self.assertEqual(self.events, [])  # 启动落地不广播（还没有人在听）

    async def test_state_script_loop_is_cut_off(self):
        """A↔B 成环时靠循环保护停下，不能把 CPU 转满。"""
        with self.assertLogs("neuro_simulator.scene_kit", level="ERROR") as logs:
            self.runner.switch("a")
            await _wait_until(
                lambda: any("成环" in m for m in logs.output), message="循环保护没有触发"
            )
        self.assertLessEqual(len(self.events), scene_kit.LOOP_MAX_CHANGES)
        self.assertTrue(any("成环" in m for m in logs.output), logs.output)

    async def test_guard_release_allows_manual_switch_again(self):
        with self.assertLogs("neuro_simulator.scene_kit", level="ERROR") as logs:
            self.runner.switch("a")
            await _wait_until(
                lambda: any("成环" in m for m in logs.output), message="循环保护没有触发"
            )
        self.runner.switch("normal")
        self.assertEqual(self.state.scene_id(), "normal")


class TestNeuroRunnerWiring(unittest.IsolatedAsyncioTestCase):
    """真数据下的接线：policy 读 neuro_scenes.json，时长来自视频轨道探测。"""

    async def asyncSetUp(self):
        self.events: list = []
        self.runner = ui_scene.make_runner(UiState(), self.events.append)

    async def asyncTearDown(self):
        await self.runner.stop()

    async def test_default_scene_and_intro_script(self):
        info = self.runner.init_default()
        self.assertEqual(info["id"], "normal")
        self.assertEqual(self.events, [])
        intro = ui_scenes.state_steps("intro")
        self.assertEqual(len(intro), 1)
        self.assertEqual(intro[0]["scene"], "normal")
        self.assertGreater(intro[0]["at"], 10.0)

    async def test_switching_to_intro_uses_probed_duration(self):
        self.runner.init_default()
        info = self.runner.switch("intro")
        self.assertEqual(info["id"], "intro")
        self.assertGreater(info["duration"], 10.0)
        self.assertEqual(self.events[0]["type"], "scene_set")
        # 自动回常态的脚本确实被排程了（不等满 13.5s，交给上面的调度测试）
        self.assertIsNotNone(self.runner._task)


if __name__ == "__main__":
    unittest.main()
