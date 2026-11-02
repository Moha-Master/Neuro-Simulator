"""模块资产 `/ui/assets`（第 5 期）：白名单、落盘命名、路径穿越与 SSRF 防护。

机制在 `neuro_simulator.asset_kit`；stream / neuro 只是注入各自的目录。
这里全部走临时目录 + 本地构造，不打真实外网（SSRF 只校验"拒绝"的一侧：
内网目标本来就该拒，公网抓取逻辑不在此测）。
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from neuro_simulator import asset_kit

PNG_MAGIC = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG_MAGIC = b"\xff\xd8\xff\xe0" + b"\x00" * 32
HTML_DOC = b"<!doctype html><html><body><script>document.title='ok'</script></body></html>"


def make_store(root: Path) -> asset_kit.AssetStore:
    store = asset_kit.AssetStore(Path(root) / "package", Path(root) / "work")
    store.deploy()
    return store


def make_client(store: asset_kit.AssetStore) -> TestClient:
    app = FastAPI()
    asset_kit.register_asset_routes(app, lambda: store)
    return TestClient(app)


class TestAssetStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = make_store(Path(self.tmp.name))

    def test_save_uses_magic_and_sha_name(self):
        info = self.store.save(PNG_MAGIC, ".png")
        self.assertEqual(info["kind"], "image")
        self.assertTrue(info["name"].startswith("images/"))
        self.assertTrue(info["name"].endswith(".png"))
        self.assertEqual(len(Path(info["name"]).stem), 12)
        self.assertTrue((self.store.root / info["name"]).is_file())
        self.assertEqual(info["mime"], "image/png")

    def test_save_rejects_unknown_magic(self):
        with self.assertRaises(ValueError):
            self.store.save(b"plain text, no magic", ".png")

    def test_save_rejects_disallowed_source_ext(self):
        with self.assertRaises(ValueError):
            self.store.save(PNG_MAGIC, ".exe")

    def test_magic_wins_over_extension(self):
        # 扩展名 .png + JPEG 魔数：魔数说了算（落到 jpg），双白名单只拦"白名单外"
        info = self.store.save(JPEG_MAGIC, ".png")
        self.assertTrue(info["name"].endswith(".jpg"))

    def test_html_upload_goes_to_html_dir(self):
        info = self.store.save(HTML_DOC, None)
        self.assertEqual(info["kind"], "html")
        self.assertTrue(info["name"].startswith("html/"))

    def test_resolve_blocks_traversal(self):
        self.assertIsNone(self.store.resolve("../secret.txt"))
        self.assertIsNone(self.store.resolve("images/../../secret.txt"))
        self.assertIsNone(self.store.resolve("/etc/passwd"))
        self.assertIsNone(self.store.resolve("."))
        self.assertIsNone(self.store.resolve("missing.webp"))

    def test_resolve_bare_name_falls_back_to_subdirs(self):
        info = self.store.save(PNG_MAGIC, ".png")
        bare = Path(info["name"]).name
        found = self.store.resolve(bare)
        self.assertIsNotNone(found)
        self.assertEqual(found.name, bare)

    def test_deploy_copies_package_template_and_keeps_existing(self):
        pkg = Path(self.tmp.name) / "package"
        (pkg / "images").mkdir(parents=True)
        (pkg / "images" / "builtin.webp").write_bytes(PNG_MAGIC)
        store = asset_kit.AssetStore(pkg, Path(self.tmp.name) / "work2")
        store.deploy()
        self.assertTrue((store.root / "images" / "builtin.webp").is_file())
        # 只补缺失：改写工作目录文件后再 deploy 不被覆盖
        (store.root / "images" / "builtin.webp").write_bytes(b"changed")
        store.deploy()
        self.assertEqual((store.root / "images" / "builtin.webp").read_bytes(), b"changed")


class TestAssetRoutes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = make_store(Path(self.tmp.name))
        self.client = make_client(self.store)

    def test_upload_and_fetch_roundtrip(self):
        resp = self.client.post(
            "/ui/assets",
            files={"file": ("pic.png", PNG_MAGIC, "image/png")},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        info = resp.json()
        self.assertTrue(info["src"].startswith("images/"))
        got = self.client.get(info["url"])
        self.assertEqual(got.status_code, 200)
        self.assertEqual(got.headers["content-type"], "image/png")
        self.assertEqual(got.content, PNG_MAGIC)

    def test_upload_url_import_with_mocked_fetch(self):
        with mock.patch.object(
            asset_kit, "fetch_url", return_value=(HTML_DOC, "https://example.com/page.html")
        ):
            resp = self.client.post("/ui/assets", data={"url": "https://example.com/page.html"})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertTrue(resp.json()["name"].startswith("html/"))

    def test_upload_requires_file_or_url(self):
        self.assertEqual(self.client.post("/ui/assets").status_code, 400)

    def test_upload_bad_magic_is_400(self):
        resp = self.client.post(
            "/ui/assets",
            files={"file": ("junk.png", b"not an asset", "image/png")},
        )
        self.assertEqual(resp.status_code, 400)

    def test_get_missing_is_404_and_traversal_blocked(self):
        self.assertEqual(self.client.get("/ui/assets/nope.webp").status_code, 404)
        self.assertEqual(self.client.get("/ui/assets/../config.json").status_code, 404)

    def test_list_assets(self):
        self.store.save(PNG_MAGIC, ".png")
        data = self.client.get("/ui/assets").json()
        names = [a["name"] for a in data["assets"]]
        self.assertTrue(any(n.startswith("images/") for n in names))
        for a in data["assets"]:
            self.assertIn("kind", a)
            self.assertIn("size", a)


class TestSsrfGuard(unittest.TestCase):
    def test_rejects_non_http_schemes(self):
        for url in ("ftp://example.com/a.png", "file:///etc/passwd"):
            with self.assertRaises(ValueError):
                asset_kit.validate_public_url(url)

    def test_rejects_loopback_and_metadata(self):
        for host in ("127.0.0.1", "169.254.169.254", "[::1]", "0.0.0.0"):
            with self.assertRaises(ValueError):
                asset_kit.validate_public_url(f"http://{host}/x.png")

    def test_rejects_private_and_link_local(self):
        for host in ("10.1.2.3", "192.168.1.5", "172.16.0.9", "169.254.10.10"):
            with self.assertRaises(ValueError):
                asset_kit.validate_public_url(f"http://{host}/x.png")

    def test_rejects_unresolvable_host(self):
        with self.assertRaises(ValueError):
            asset_kit.validate_public_url("http://this-host-does-not-exist.invalid/x.png")


if __name__ == "__main__":
    unittest.main()
