"""场景 `desc`：校验、概览投影、两份 defaults 全覆盖。"""

import copy
import json
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from neuro_simulator.neuro_sama import scenes as neuro_scenes
from neuro_simulator.neuro_sama import server as neuro_server
from neuro_simulator.neuro_sama.ui_state import UiState
from neuro_simulator.stream import scenes as stream_scenes
from neuro_simulator.stream.server import create_app as create_stream_app

ROOT = Path(__file__).resolve().parents[1]
STREAM_DEFAULT = ROOT / "server/neuro_simulator/stream/defaults/scenes.json"
NEURO_DEFAULT = ROOT / "server/neuro_simulator/neuro_sama/defaults/neuro_scenes.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class TestSceneDescValidation(unittest.TestCase):
    def test_both_catalogs_accept_a_string_desc(self):
        for path, validate in (
            (STREAM_DEFAULT, stream_scenes._validate),
            (NEURO_DEFAULT, neuro_scenes._validate),
        ):
            data = _load(path)
            data["scenes"][0]["desc"] = "A machine-readable state description."
            validate(data)  # 不抛

    def test_both_catalogs_reject_a_non_string_desc(self):
        for path, validate in (
            (STREAM_DEFAULT, stream_scenes._validate),
            (NEURO_DEFAULT, neuro_scenes._validate),
        ):
            data = copy.deepcopy(_load(path))
            data["scenes"][0]["desc"] = 123
            with self.assertRaises(neuro_scenes.SceneError):
                validate(data)

    def test_desc_is_optional_for_backward_compatibility(self):
        for path, validate in (
            (STREAM_DEFAULT, stream_scenes._validate),
            (NEURO_DEFAULT, neuro_scenes._validate),
        ):
            data = copy.deepcopy(_load(path))
            data["scenes"][0].pop("desc", None)
            validate(data)


class TestDefaultsCoverage(unittest.TestCase):
    def test_every_stream_scene_has_a_non_empty_english_desc(self):
        scenes = _load(STREAM_DEFAULT)["scenes"]
        self.assertEqual(len(scenes), 7)
        for scene in scenes:
            desc = scene.get("desc")
            self.assertIsInstance(desc, str, scene["id"])
            self.assertTrue(desc.strip(), f"{scene['id']} 缺少 desc")
            self.assertRegex(desc, r"^[A-Z].*[.!]$", f"{scene['id']} 应是英文陈述句")

    def test_every_neuro_scene_has_a_non_empty_english_desc(self):
        scenes = _load(NEURO_DEFAULT)["scenes"]
        self.assertEqual(len(scenes), 2)
        for scene in scenes:
            desc = scene.get("desc")
            self.assertIsInstance(desc, str, scene["id"])
            self.assertTrue(desc.strip(), f"{scene['id']} 缺少 desc")
            self.assertRegex(desc, r"^[A-Z].*[.!]$", f"{scene['id']} 应是英文陈述句")

    def test_wordle_desc_is_not_a_copy_of_prompt_context(self):
        wordle = next(s for s in _load(STREAM_DEFAULT)["scenes"] if s["id"] == "play_wordle")
        self.assertTrue(wordle["desc"])
        self.assertNotEqual(wordle["desc"], wordle["prompt_context"])


class TestOverviewCarriesDesc(unittest.TestCase):
    def test_stream_overview_and_endpoint(self):
        by_id = {s["id"]: s for s in stream_scenes.overview()}
        self.assertEqual(
            by_id["room"]["desc"],
            "The default shot: Neuro's room background fills the frame, her avatar is centred "
            "with captions near the bottom, and the chat overlay sits on the left.",
        )
        body = TestClient(create_stream_app()).get("/stream/scenes").json()
        self.assertTrue(all(s["desc"] for s in body["scenes"]))

    def test_neuro_overview_ui_scenes_and_manifest(self):
        by_id = {s["id"]: s for s in neuro_scenes.overview()}
        self.assertIn("intro video", by_id["intro"]["desc"])
        app = neuro_server.create_app()
        app.state.ui_state = UiState()
        client = TestClient(app)
        body = client.get("/ui/scenes").json()
        self.assertTrue(all(s["desc"] for s in body["scenes"]))
        manifest = client.get("/ui/manifest").json()
        self.assertTrue(all(s.get("desc") for s in manifest["scenes"]))


if __name__ == "__main__":
    unittest.main()
