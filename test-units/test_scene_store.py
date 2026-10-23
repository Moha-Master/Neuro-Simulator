"""场景清单的 DB 存储（Phase C）：DB 是权威，包内 defaults 只是播种模板。

要守的：
- 首次启动用包内默认播种，之后**不再自动覆盖**（用户改过就以 DB 为准）；
- 保存前必须校验（不合法直接拒，DB 保持原样）；
- 能恢复默认；
- 两个模块各存各的（stream 的 data.db / neuro-sama 的 data.db，互不干扰）；
- 没绑定 DB 时（测试/脚本）退回包内默认，模块照常能跑。
"""

import json
import tempfile
import unittest
from pathlib import Path

from neuro_simulator import scene_kit
from neuro_simulator.neuro_sama import scenes as ui_scenes
from neuro_simulator.neuro_sama.storage import Storage as NeuroStorage
from neuro_simulator.stream import scenes as scene_catalog
from neuro_simulator.stream.storage import Storage as StreamStorage

ROOT = Path(__file__).resolve().parents[1]
STREAM_DEFAULT = ROOT / "server/neuro_simulator/stream/defaults/scenes.json"
NEURO_DEFAULT = ROOT / "server/neuro_simulator/neuro_sama/defaults/neuro_scenes.json"

MINIMAL = {
    "version": 1,
    "scenes": [
        {
            "id": "room",
            "label": "房间",
            "elements": [
                {
                    "id": "chat",
                    "z": 20,
                    "box": {"x": 0, "y": 0, "w": 0.25, "h": 0.5},
                    "content": {"kind": "component", "name": "chat.overlay"},
                }
            ],
            "neuro": {"x": 0, "y": 0, "w": 1, "hide": []},
        }
    ],
}


class TestSceneStore(unittest.IsolatedAsyncioTestCase):
    """共用管理器（scene_kit.SceneStore）的行为。"""

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = StreamStorage(Path(self.tmp.name) / "test.db")
        await self.storage.init()
        self.store = scene_kit.SceneStore(
            self.storage, STREAM_DEFAULT, scene_catalog._validate
        )

    async def asyncTearDown(self):
        await self.storage.close()
        self.tmp.cleanup()

    async def test_first_load_seeds_the_packaged_default(self):
        await self.store.load()
        self.assertTrue(self.store.is_default())
        self.assertEqual(
            {s["id"] for s in self.store.manifest()["scenes"]},
            {s["id"] for s in json.loads(STREAM_DEFAULT.read_text(encoding="utf-8"))["scenes"]},
        )
        # 播种后落库了（下次启动直接读 DB，不再靠包内文件）
        row = await self.storage.get_scene("scenes")
        self.assertIsNotNone(row)
        self.assertIn('"scenes"', row["content"])

    async def test_load_twice_keeps_user_edits(self):
        """播种之后不再自动覆盖：用户改过的清单要活过重启。"""
        await self.store.load()
        await self.store.save(json.dumps(MINIMAL, ensure_ascii=False))
        again = scene_kit.SceneStore(
            self.storage, STREAM_DEFAULT, scene_catalog._validate
        )
        await again.load()
        self.assertEqual([s["id"] for s in again.manifest()["scenes"]], ["room"])
        self.assertFalse(again.is_default())

    async def test_save_validates_before_writing(self):
        await self.store.load()
        before = self.store.manifest()
        with self.assertRaises(scene_kit.SceneError):
            await self.store.save('{"scenes": [{"id": "x"}]}')  # 缺 elements/neuro
        # 拒绝之后 DB 与缓存都还是原来那份
        self.assertEqual(self.store.manifest(), before)
        row = await self.storage.get_scene("scenes")
        self.assertEqual(json.loads(row["content"]), before)

    async def test_save_rejects_broken_json(self):
        await self.store.load()
        with self.assertRaises(scene_kit.SceneError):
            await self.store.save("{ 这不是 JSON")
        self.assertTrue(self.store.is_default())

    async def test_save_stores_canonical_json(self):
        await self.store.load()
        await self.store.save(json.dumps(MINIMAL, ensure_ascii=False))
        row = await self.storage.get_scene("scenes")
        self.assertIn("\n  ", row["content"])  # indent=2
        self.assertNotIn("\\u", row["content"])  # ensure_ascii=False

    async def test_reset_restores_the_default(self):
        await self.store.load()
        await self.store.save(json.dumps(MINIMAL, ensure_ascii=False))
        self.assertFalse(self.store.is_default())
        await self.store.reset()
        self.assertTrue(self.store.is_default())
        self.assertIn("play_chess", {s["id"] for s in self.store.manifest()["scenes"]})

    async def test_source_reports_meta(self):
        await self.store.load()
        meta = self.store.source()
        self.assertTrue(meta["is_default"])
        self.assertGreater(meta["chars"], 0)
        self.assertTrue(meta["updated_at"])


class TestModuleWiring(unittest.IsolatedAsyncioTestCase):
    """两个模块各存各的：绑定 DB 后读的是 DB 那份。"""

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.stream_store = StreamStorage(Path(self.tmp.name) / "stream.db")
        await self.stream_store.init()
        self.neuro_store = NeuroStorage(Path(self.tmp.name) / "neuro.db")
        await self.neuro_store.init()
        self.scenes = scene_kit.SceneStore(
            self.stream_store, STREAM_DEFAULT, scene_catalog._validate
        )
        await self.scenes.load()
        self.ui_scenes = scene_kit.SceneStore(
            self.neuro_store, NEURO_DEFAULT, ui_scenes._validate
        )
        await self.ui_scenes.load()
        scene_catalog.set_store(self.scenes)
        ui_scenes.set_store(self.ui_scenes)

    async def asyncTearDown(self):
        # 解绑：让其它测试继续用"包内默认"的兜底路径
        scene_catalog.set_store(None)
        ui_scenes.set_store(None)
        await self.stream_store.close()
        await self.neuro_store.close()
        self.tmp.cleanup()

    async def test_stream_reads_its_own_db(self):
        self.assertTrue(
            {s["id"] for s in scene_catalog.manifest()["scenes"]}
            >= {"room", "play_chess"}
        )
        await scene_catalog.save_manifest(json.dumps(MINIMAL, ensure_ascii=False))
        self.assertEqual([s["id"] for s in scene_catalog.load_scenes()], ["room"])

    async def test_neuro_reads_its_own_db(self):
        self.assertEqual(
            {s["id"] for s in ui_scenes.manifest()["scenes"]}, {"normal", "intro"}
        )
        # stream 那边改了，neuro 这边不受影响（各存各的）
        await scene_catalog.save_manifest(json.dumps(MINIMAL, ensure_ascii=False))
        self.assertEqual(
            {s["id"] for s in ui_scenes.manifest()["scenes"]}, {"normal", "intro"}
        )

    async def test_reset_through_the_module_api(self):
        await scene_catalog.save_manifest(json.dumps(MINIMAL, ensure_ascii=False))
        self.assertFalse(scene_catalog.manifest_source()["is_default"])
        await scene_catalog.reset_manifest()
        self.assertTrue(scene_catalog.manifest_source()["is_default"])


class TestFallbackWithoutDatabase(unittest.TestCase):
    """没绑定 DB 时退回包内默认——模块照常能跑（测试/脚本直接 import）。"""

    def test_stream_falls_back_to_packaged_default(self):
        scene_catalog.set_store(None)
        self.assertIn("room", {s["id"] for s in scene_catalog.load_scenes()})

    def test_neuro_falls_back_to_packaged_default(self):
        ui_scenes.set_store(None)
        self.assertEqual(
            {s["id"] for s in ui_scenes.manifest()["scenes"]}, {"normal", "intro"}
        )


if __name__ == "__main__":
    unittest.main()
