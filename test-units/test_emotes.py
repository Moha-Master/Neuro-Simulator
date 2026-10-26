"""表情服务：清单合并/搜索匹配/下载增量/端点形状（全部 fake 网络）。

不碰真实 CDN：`httpx.AsyncClient` 用内存假客户端替换，get/post 都按 URL 路由。
"""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from neuro_simulator.stream import emotes as emotes_module
from neuro_simulator.stream import server as stream_server
from neuro_simulator.stream.chatbot import EMOTE_CANON
from neuro_simulator.stream.emotes import EmoteStore

SEVEN = emotes_module.SEVEN_TV_GLOBAL
BTTV = emotes_module.BTTV_GLOBAL
FFZ = emotes_module.FFZ_GLOBAL
GQL = emotes_module.SEVEN_TV_GQL


class _FakeResponse:
    def __init__(self, payload=None, *, content: bytes = b"", status: int = 200, headers=None):
        self._payload = payload
        self.content = content
        self.status_code = status
        self.headers = headers or {}

    def json(self):
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeNet:
    def __init__(self) -> None:
        self.globals = {
            SEVEN: {"emotes": []},
            BTTV: [],
            FFZ: {"sets": {}},
        }
        self.search_items: dict[str, list] = {}
        self.image_bytes = b"IMAGE"
        self.image_failures = 0
        self.fail_urls: set[str] = set()
        self.posts: list[tuple[str, dict]] = []
        self.gets: list[str] = []

    def image_url(self, url: str) -> bool:
        # 图片走 CDN；三个 global 清单端点走 api 域，不能被误判成图片下载
        return "cdn." in url


class _FakeClient:
    net: _FakeNet

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url):
        net = type(self).net
        net.gets.append(url)
        if net.image_url(url):
            if url in net.fail_urls:
                raise RuntimeError("network down")
            if net.image_failures > 0:
                net.image_failures -= 1
                raise RuntimeError("network down")
            return _FakeResponse(content=net.image_bytes, headers={"content-type": "image/webp"})
        return _FakeResponse(net.globals[url])

    async def post(self, url, json=None):
        net = type(self).net
        assert url == GQL
        net.posts.append((url, json or {}))
        name = (json or {}).get("variables", {}).get("q")
        return _FakeResponse({"data": {"emotes": {"items": net.search_items.get(name, [])}}})


def _seven_emote(name: str, emote_id: str, *, animated: bool = False, files=None):
    return {
        "id": emote_id,
        "name": name,
        "animated": animated,
        "host": {
            "url": "//cdn.7tv.test/emote/{{id}}/{{file_name}}",
            "files": files
            or [
                {"name": "1x.webp"},
                {"name": "2x.webp"},
                {"name": "4x.webp"},
                *([{"name": "4x.gif"}] if animated else []),
            ],
        },
    }


def _bttv_emote(name: str, emote_id: str, *, animated: bool = False):
    return {
        "id": emote_id,
        "code": name,
        "animated": animated,
        "imageType": "gif" if animated else "webp",
    }


def _ffz_emote(name: str, emote_id: int, *, animated: bool = False):
    return {
        "id": emote_id,
        "name": name,
        "urls": {"1": f"https://cdn.ffz.test/{emote_id}/1", "2": f"https://cdn.ffz.test/{emote_id}/2"},
        **({"animated": {"2": f"https://cdn.ffz.test/{emote_id}/animated/2"}} if animated else {}),
    }


class _EmoteTestBase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "stream" / "emotes"
        self.store = EmoteStore(self.root)
        self.net = _FakeNet()
        _FakeClient.net = self.net

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def patch_network(self):
        return patch.object(emotes_module.httpx, "AsyncClient", _FakeClient)

    async def refresh(self):
        with self.patch_network(), patch.object(emotes_module, "_sleep", new=AsyncMock()):
            return await self.store.refresh("test")


class TestParsing(unittest.TestCase):
    def test_seven_tv_animated_picks_gif_and_url_template(self):
        payload = {"emotes": [_seven_emote("neuroSpin", "01ABC", animated=True)]}
        parsed = emotes_module._parse_seven_tv(payload, "7tv_global")
        emote = parsed["neuroSpin"]
        self.assertTrue(emote.animated)
        self.assertEqual(emote.ext, ".gif")
        self.assertEqual(emote.mime, "image/gif")
        self.assertEqual(emote.url, "https://cdn.7tv.test/emote/01ABC/4x.gif")

    def test_seven_tv_static_picks_2x_webp(self):
        payload = {"emotes": [_seven_emote("LUL", "01XYZ")]}
        parsed = emotes_module._parse_seven_tv(payload, "7tv_global")
        self.assertEqual(parsed["LUL"].ext, ".webp")
        self.assertEqual(parsed["LUL"].url, "https://cdn.7tv.test/emote/01XYZ/2x.webp")
        self.assertFalse(parsed["LUL"].animated)

    def test_bttv_and_ffz_urls(self):
        bttv = emotes_module._parse_bttv([_bttv_emote("KEKW", "b1", animated=True)])
        self.assertEqual(bttv["KEKW"].url, "https://cdn.betterttv.net/emote/b1/2x")
        self.assertEqual(bttv["KEKW"].ext, ".gif")
        ffz = emotes_module._parse_ffz(
            {"sets": {"3": {"emoticons": [_ffz_emote("catJAM", 42, animated=True)]}}}
        )
        self.assertEqual(ffz["catJAM"].url, "https://cdn.ffz.test/42/2")
        self.assertTrue(ffz["catJAM"].animated)

    def test_search_is_case_sensitive_and_uses_lowest_id(self):
        payload = {
            "data": {
                "emotes": {
                    "items": [
                        _seven_emote("NeuroSpin", "zzz"),
                        _seven_emote("neuroSpin", "ccc"),
                        _seven_emote("neuroSpin", "aaa"),
                    ]
                }
            }
        }
        matched = emotes_module._parse_search(payload, "neuroSpin")
        self.assertIsNotNone(matched)
        self.assertEqual(matched.emote_id, "aaa")
        self.assertIsNone(emotes_module._parse_search(payload, "neurospin"))


class TestRefresh(_EmoteTestBase):
    async def test_priority_custom_then_7tv_bttv_ffz_then_search(self):
        # custom 目录投放同名与独占表情（最高优先级、免下载）
        custom = self.root / "custom"
        custom.mkdir(parents=True)
        (custom / "Shared.webp").write_bytes(b"CUSTOM")
        (custom / "CustomOnly.png").write_bytes(b"CUSTOM")

        self.net.globals[SEVEN] = {
            "emotes": [
                _seven_emote("Shared", "s1"),
                _seven_emote("SevenOnly", "s2"),
                _seven_emote("DupSevenBttv", "s3"),
            ]
        }
        self.net.globals[BTTV] = [
            _bttv_emote("Shared", "b1"),
            _bttv_emote("BttvOnly", "b2"),
            _bttv_emote("DupSevenBttv", "b3"),
        ]
        self.net.globals[FFZ] = {
            "sets": {"1": {"emoticons": [_ffz_emote("Shared", 1), _ffz_emote("FfzOnly", 2)]}}
        }
        self.net.search_items["neuroSpin"] = [_seven_emote("neuroSpin", "search1")]

        result = await self.refresh()
        self.assertEqual(result["status"], "ok")
        entries = {item["name"]: item for item in self.store.entries()}

        self.assertEqual(entries["Shared"]["source"], "custom")
        self.assertEqual(entries["CustomOnly"]["source"], "custom")
        self.assertEqual(entries["SevenOnly"]["source"], "7tv_global")
        self.assertEqual(entries["BttvOnly"]["source"], "bttv_global")
        self.assertEqual(entries["DupSevenBttv"]["source"], "7tv_global")
        self.assertEqual(entries["FfzOnly"]["source"], "ffz_global")
        self.assertEqual(entries["neuroSpin"]["source"], "7tv_search")

        snapshot = self.store.snapshot()
        self.assertEqual(snapshot["counts"]["custom"], 2)
        self.assertEqual(snapshot["counts"]["7tv_global"], 2)
        self.assertEqual(snapshot["counts"]["bttv_global"], 1)
        self.assertEqual(snapshot["counts"]["ffz_global"], 1)

        # manifest 不重复记录 custom（读取时现扫）
        manifest = json.loads((self.root / "manifest.json").read_text(encoding="utf-8"))
        self.assertNotIn("Shared", manifest["emotes"])
        self.assertNotIn("CustomOnly", manifest["emotes"])
        self.assertNotIn("custom", manifest["sources"])

    async def test_search_request_has_query_and_no_sort(self):
        await self.refresh()
        self.assertTrue(self.net.posts)
        _, body = self.net.posts[0]
        self.assertIn("emotes(query:$q", body["query"])
        self.assertNotIn("sort", json.dumps(body))
        self.assertIn("q", body["variables"])

    async def test_missing_search_names_are_not_failures(self):
        await self.refresh()
        manifest = json.loads((self.root / "manifest.json").read_text(encoding="utf-8"))
        search = manifest["sources"]["7tv_search"]
        self.assertIsNone(search.get("error"))
        self.assertIn("vedalSad", search["missing"])
        self.assertIsNone(manifest["last_error"])

    async def test_download_retries_then_succeeds(self):
        self.net.globals[SEVEN] = {"emotes": [_seven_emote("Flaky", "f1")]}
        self.net.image_failures = 2
        with patch.object(emotes_module, "_sleep", new=AsyncMock()) as sleep:
            with self.patch_network():
                await self.store.refresh("test")
        self.assertEqual(self.net.gets.count("https://cdn.7tv.test/emote/f1/2x.webp"), 3)
        self.assertEqual(sleep.await_count, 2)
        self.assertIn("Flaky", {item["name"] for item in self.store.entries()})

    async def test_single_image_failure_does_not_destroy_manifest(self):
        self.net.globals[SEVEN] = {
            "emotes": [_seven_emote("Good", "g1"), _seven_emote("Bad", "b1")]
        }
        self.net.fail_urls = {"https://cdn.7tv.test/emote/b1/2x.webp"}

        with patch.object(emotes_module, "_sleep", new=AsyncMock()):
            with self.patch_network():
                await self.store.refresh("test")

        names = {item["name"] for item in self.store.entries()}
        self.assertIn("Good", names)
        self.assertNotIn("Bad", names)
        manifest = json.loads((self.root / "manifest.json").read_text(encoding="utf-8"))
        self.assertIsNotNone(manifest["last_error"])
        self.assertIn("download", manifest["last_error"])

    async def test_existing_file_is_not_downloaded_again(self):
        self.net.globals[SEVEN] = {"emotes": [_seven_emote("LUL", "l1")]}
        await self.refresh()
        first = [url for url in self.net.gets if self.net.image_url(url)]
        self.assertEqual(len(first), 1)

        self.net.gets.clear()
        await self.refresh()
        second = [url for url in self.net.gets if self.net.image_url(url)]
        self.assertEqual(second, [])

    async def test_manifest_is_atomic_and_startup_reuses_fresh_manifest(self):
        self.net.globals[SEVEN] = {"emotes": [_seven_emote("LUL", "l1")]}
        await self.refresh()
        self.assertFalse((self.root / "manifest.json.tmp").exists())

        # 新实例：fresh manifest 直接加载，startup 不应发网络请求
        store = EmoteStore(self.root)
        self.net.gets.clear()
        with patch.object(
            emotes_module.httpx,
            "AsyncClient",
            side_effect=AssertionError("fresh manifest 不应刷新"),
        ):
            await store.startup()
            await asyncio.sleep(0)
            self.assertIn("LUL", {item["name"] for item in store.entries()})
            await store.shutdown()
        self.assertEqual(self.net.gets, [])

    async def test_source_failure_keeps_old_entries(self):
        self.net.globals[SEVEN] = {"emotes": [_seven_emote("LUL", "l1")]}
        await self.refresh()
        self.assertIn("LUL", {item["name"] for item in self.store.entries()})

        # 下一次 7TV global 挂掉：旧条目保留，error 记录在 sources 上
        class _FailingSeven(_FakeClient):
            async def get(self, url):
                if url == SEVEN:
                    raise RuntimeError("down")
                return await super().get(url)

        self.net.gets.clear()
        with patch.object(emotes_module.httpx, "AsyncClient", _FailingSeven):
            await self.store.refresh("test")
        entries = {item["name"]: item for item in self.store.entries()}
        self.assertEqual(entries["LUL"]["source"], "7tv_global")
        manifest = json.loads((self.root / "manifest.json").read_text(encoding="utf-8"))
        self.assertIn("down", manifest["sources"]["7tv_global"]["error"])


class TestEndpoints(_EmoteTestBase):
    def _app(self):
        app = stream_server.create_app()
        app.state.emotes = self.store
        return app

    async def test_list_and_image_and_404s(self):
        self.net.globals[SEVEN] = {"emotes": [_seven_emote("LUL", "l1")]}
        await self.refresh()
        app = self._app()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            listing = await client.get("/emotes")
            self.assertEqual(listing.status_code, 200)
            body = listing.json()
            self.assertEqual(body["emotes"], [
                {
                    "name": "LUL",
                    "url": "/emotes/images/LUL",
                    "source": "7tv_global",
                    "animated": False,
                }
            ])
            self.assertIn("updated_at", body)
            self.assertIn("counts", body)
            self.assertIn("last_error", body)

            image = await client.get("/emotes/images/LUL")
            self.assertEqual(image.status_code, 200)
            self.assertEqual(image.headers["content-type"], "image/webp")
            self.assertEqual(image.headers["cache-control"], "max-age=3600")
            self.assertEqual(image.content, b"IMAGE")

            missing = await client.get("/emotes/images/nope")
            self.assertEqual(missing.status_code, 404)

            traversal = await client.get("/emotes/images/..%2F..%2Fetc%2Fpasswd")
            self.assertEqual(traversal.status_code, 404)

    async def test_manual_refresh_and_concurrent_409(self):
        app = self._app()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            with self.patch_network():
                response = await client.post("/emotes/refresh")
            self.assertEqual(response.status_code, 200)
            body = response.json()
            self.assertEqual(body["status"], "ok")
            self.assertIn("sources", body)
            self.assertIn("emote_count", body)

            await self.store._lock.acquire()
            try:
                busy = await client.post("/emotes/refresh")
            finally:
                self.store._lock.release()
            self.assertEqual(busy.status_code, 409)
            self.assertIn("进行中", busy.json()["detail"])


class TestCanon(unittest.TestCase):
    def test_emote_canon_is_public_and_has_channel_names(self):
        self.assertIn("neurospin", EMOTE_CANON)
        self.assertEqual(EMOTE_CANON["neurospin"], "neuroSpin")
        self.assertEqual(EMOTE_CANON["vedalsad"], "vedalSad")


if __name__ == "__main__":
    unittest.main()
