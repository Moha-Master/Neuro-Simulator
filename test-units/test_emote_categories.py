"""chatbot 表情大类：store 播种/校验/采样、烘焙产物守卫、manage 端点。"""

import json
import random
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from neuro_simulator.stream import server as stream_server
from neuro_simulator.stream.emote_categories import (
    CATEGORY_ID,
    CategoryStore,
    validate_content,
)

ROOT = Path(__file__).resolve().parents[1]
BAKED = ROOT / "server/neuro_simulator/stream/defaults/emote_categories.json"
USAGE = Path.home() / ".config/neuro-simulator/stream/emotes/usage/usage.json"


class _FakeStorage:
    """只实现 CategoryStore 需要的两条方法，避免 aiosqlite 跨事件循环。"""

    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}
        self.upserts = 0

    async def get_emote_categories(self, name="categories"):
        row = self.rows.get(name)
        return dict(row) if row else None

    async def upsert_emote_categories(self, content, name="categories"):
        self.upserts += 1
        self.rows[name] = {
            "name": name,
            "content": content,
            "updated_at": "2026-11-14 00:00:00",
        }


def _doc(categories=None, version=1):
    return {"version": version, "categories": categories or []}


def _category(cid="funny", emotes=None, label="Funny"):
    return {
        "id": cid,
        "label": label,
        "desc": "desc",
        "emotes": emotes if emotes is not None else [{"name": "om", "weight": 2}],
        "templates": [],
    }


class _Base(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "emote_categories.json"
        self.path.write_text(json.dumps(_doc([_category()])), encoding="utf-8")
        self.storage = _FakeStorage()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    async def _store(self):
        store = CategoryStore(self.storage, self.path)
        await store.load()
        return store


class TestCategoryStore(_Base):
    async def test_load_seeds_once_and_is_idempotent(self):
        store = await self._store()
        first = self.storage.rows["categories"]["content"]
        self.assertEqual(self.storage.upserts, 1)
        self.assertEqual(store.category_ids(), ["funny"])

        # 二次 load 读已有行，不再播种
        again = CategoryStore(self.storage, self.path)
        await again.load()
        self.assertEqual(self.storage.upserts, 1)
        self.assertEqual(self.storage.rows["categories"]["content"], first)
        self.assertEqual(again.category_ids(), ["funny"])

    async def test_sample_is_deterministic_for_same_seed(self):
        store = await self._store()
        a = [store.sample("funny", random.Random(7)) for _ in range(10)]
        b = [store.sample("funny", random.Random(7)) for _ in range(10)]
        self.assertEqual(a, b)

    async def test_weight_skew_drives_frequency(self):
        self.path.write_text(
            json.dumps(_doc([
                _category(emotes=[
                    {"name": "heavy", "weight": 100},
                    {"name": "rare", "weight": 1},
                ])
            ])),
            encoding="utf-8",
        )
        store = await self._store()
        rng = random.Random(11)
        draws = [store.sample("funny", rng) for _ in range(5000)]
        self.assertGreater(draws.count("heavy"), draws.count("rare") * 10)

    async def test_sample_unknown_category_returns_none(self):
        store = await self._store()
        self.assertIsNone(store.sample("ghost", random.Random(1)))


class TestValidateContent(unittest.TestCase):
    def test_valid_doc_has_no_errors(self):
        self.assertEqual(validate_content(_doc([_category()])), [])

    def test_bad_id(self):
        errors = validate_content(_doc([_category(cid="Bad-Id")]))
        self.assertTrue(any("id" in e for e in errors), errors)

    def test_duplicate_id(self):
        errors = validate_content(_doc([_category("same"), _category("same")]))
        self.assertTrue(any("重复" in e for e in errors), errors)

    def test_non_positive_weight(self):
        errors = validate_content(_doc([_category(emotes=[
            {"name": "om", "weight": 0}
        ])]))
        self.assertTrue(any("weight" in e for e in errors), errors)

    def test_missing_member_name(self):
        errors = validate_content(_doc([_category(emotes=[
            {"weight": 3}
        ])]))
        self.assertTrue(any("name" in e for e in errors), errors)

    def test_version_and_empty_categories(self):
        self.assertTrue(validate_content(_doc([], version="1")))
        self.assertTrue(validate_content(_doc([])))


class TestBakedDefaults(unittest.TestCase):
    def test_baked_document_policy(self):
        doc = json.loads(BAKED.read_text(encoding="utf-8"))
        self.assertEqual(doc["version"], 1)
        self.assertEqual(len(doc["categories"]), 15)
        ids = [c["id"] for c in doc["categories"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(CATEGORY_ID.fullmatch(cid) for cid in ids))

        usage = json.loads(USAGE.read_text(encoding="utf-8"))["emotes"]
        whitelist = {"EvilJAM", "NeuroJAM", "ShouldiCelebrate"}
        for category in doc["categories"]:
            self.assertTrue(category["emotes"], category["id"])
            for member in category["emotes"]:
                name = member["name"]
                self.assertIn(name, usage, f"{category['id']}: {name}")
                self.assertIsInstance(member["weight"], int)
                self.assertGreaterEqual(member["weight"], 1)
                expected = max(1, int(usage[name]["by_tag"]["neuro_solo"]))
                self.assertEqual(member["weight"], expected)
                bucket = str(usage[name].get("primary_bucket") or "")
                if bucket.startswith("twitch_other"):
                    self.assertIn(name, whitelist, f"{name} is twitch_other")


class TestManageEndpoints(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "emote_categories.json"
        self.path.write_text(json.dumps(_doc([_category()])), encoding="utf-8")
        self.store = CategoryStore(_FakeStorage(), self.path)
        await self.store.load()
        self.app = stream_server.create_app()
        self.app.state.emote_categories = self.store
        self.client = TestClient(self.app)

    async def asyncTearDown(self):
        self.tmp.cleanup()

    def test_get_overview_shape(self):
        body = self.client.get("/manage/emote-categories").json()
        self.assertEqual(body["total_members"], 1)
        self.assertEqual(body["categories"][0]["id"], "funny")
        self.assertEqual(body["categories"][0]["emote_count"], 1)
        self.assertEqual(body["categories"][0]["preview"], ["om"])

    def test_put_valid_returns_full_document(self):
        doc = _doc([_category("other", emotes=[{"name": "Pog", "weight": 4}])])
        resp = self.client.put(
            "/manage/emote-categories", json={"content": json.dumps(doc)}
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["content"]["categories"][0]["id"], "other")
        self.assertEqual(body["total_members"], 1)
        self.assertEqual(self.store.category_ids(), ["other"])

    def test_put_bad_document_returns_400_and_keeps_old(self):
        bad = _doc([_category(cid="Bad-Id")])
        resp = self.client.put("/manage/emote-categories", json={"content": bad})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("id", resp.json()["detail"])
        self.assertEqual(self.store.category_ids(), ["funny"])

    def test_reset_restores_defaults(self):
        doc = _doc([_category("changed")])
        self.client.put("/manage/emote-categories", json={"content": doc})
        resp = self.client.post("/manage/emote-categories/reset")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["content"]["categories"][0]["id"], "funny")


if __name__ == "__main__":
    unittest.main()
