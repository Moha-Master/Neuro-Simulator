"""vedal 单 key 认证（auth_key）与匿名白名单的离线契约。

全部用 TestClient 走 ASGI 内存调用：**不打真实端口、不访问真实模块**。
默认（auth_key 为空）时整条中间件短路，既有行为必须逐字不变——这里也用两条
代表端点（`/health`、`/manage/modules`）钉住"关闭态 = 旧行为"。

启用态覆盖三件交付通道（Bearer / 会话 cookie / 匿名 GET 白名单）、
爆破限流，以及"白名单是显式常量列表、不能被悄悄删条目"的契约。
"""

import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from neuro_simulator.vedal import server as vedal_server

ROOT = Path(__file__).resolve().parents[1]

# 白名单的**完整**期望集合：新增公开 GET 必须同时改这里与 vedal/server.py。
EXPECTED_PUBLIC_GET_PATHS = {
    "/",
    "/index.html",
    "/assets/*",
    "/favicon.ico",
    "/banner.jpeg",
    "/avatar.webp",
    "/fonts/*",
    "/twitch/*",
    "/ui/*",
    "/public/modules_status",
    "/public/stream_view",
    "/manage/stream/view/*",
    "/manage/stream/queue/messages",
    "/manage/stream/ui/events",
    "/manage/stream/ui/manifest",
    "/manage/stream/ui/assets/*",
    "/manage/stream/emotes",
    "/manage/stream/emotes/images/*",
    "/manage/neuro_sama/ui/manifest",
    "/manage/neuro_sama/ui/assets/*",
    "/manage/neuro_sama/ui/media/*",
    "/manage/neuro_sama/chat/events",
    "/manage/neuro_sama/state/events",
}


class _FakeConfig:
    """最小 Config 替身：中间件只读 data，模块地址只读 module()。"""

    def __init__(self, auth_key: str = "") -> None:
        self.WORKDIR = Path(tempfile.gettempdir())
        self.data = {
            "server": {"llm_services": {}, "tts_services": {}},
            "vedal": {
                "host": "127.0.0.1",
                "port": 8100,
                "external_url": "",
                "auth_key": auth_key,
            },
            "neuro_sama": {"host": "127.0.0.1", "port": 8000, "external_url": ""},
            "stream": {"host": "127.0.0.1", "port": 8200, "external_url": ""},
        }

    def module(self, name: str) -> dict:
        value = self.data.get(name, {})
        return value if isinstance(value, dict) else {}


class VedalAuthTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = _FakeConfig("")
        # 所有模块都"未配置地址"：代理端点 503 而不是尝试真实连接（离线纪律）。
        patchers = (
            patch.object(vedal_server, "get_config", lambda: self.cfg),
            patch.object(vedal_server, "module_url", lambda name: None),
        )
        for item in patchers:
            item.start()
            self.addCleanup(item.stop)
        vedal_server._reset_login_rate_limits()
        self.addCleanup(vedal_server._reset_login_rate_limits)
        self.app = vedal_server.create_app()

    def _enable(self, key: str = "test-auth-key") -> str:
        self.cfg.data["vedal"]["auth_key"] = key
        return key

    # ---------- 关闭态：行为与未引入鉴权时逐字一致 ----------

    def test_disabled_keeps_health_and_modules_anonymous(self) -> None:
        with TestClient(self.app) as client:
            health = client.get("/health")
            modules = client.get("/manage/modules")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json()["module"], "vedal")
        self.assertEqual(modules.status_code, 200)
        self.assertIn("modules", modules.json())

    # ---------- 启用态：Bearer / 无凭证 ----------

    def test_management_requires_credentials_and_accepts_bearer(self) -> None:
        key = self._enable("bearer-key")
        with TestClient(self.app) as client:
            self.assertEqual(client.get("/manage/config").status_code, 401)
            ok = client.get(
                "/manage/config", headers={"Authorization": f"Bearer {key}"}
            )
            bad = client.get(
                "/manage/config", headers={"Authorization": "Bearer wrong-key"}
            )
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(bad.status_code, 401)

    def test_management_and_health_are_locked(self) -> None:
        self._enable()
        locked = (
            "/health",
            "/openapi.json",
            "/manage/config",
            "/manage/modules",
            "/manage/state",
            "/manage/logs/stream",
            "/manage/neuro_sama/status",
            "/manage/stream/stream/sessions",
            "/manage/stream/stream/status",
        )
        with TestClient(self.app) as client:
            for path in locked:
                with self.subTest(path=path):
                    self.assertEqual(client.get(path).status_code, 401)

    # ---------- 匿名 GET 白名单 ----------

    def test_public_get_allowlist_passes_the_middleware(self) -> None:
        self._enable()
        public = (
            "/ui/stream/",
            "/public/modules_status",
            "/manage/stream/ui/events",
            "/manage/neuro_sama/chat/events",
            "/manage/stream/emotes",
            "/manage/stream/view/",
            "/manage/stream/ui/manifest",
            "/manage/neuro_sama/ui/manifest",
            "/manage/neuro_sama/ui/media/neuro_start.mp4",
            "/public/stream_view",
            "/manage/stream/queue/messages",
        )
        with TestClient(self.app) as client:
            for path in public:
                with self.subTest(path=path):
                    # 404/503 都算穿透成功；只有 401 表示被中间件拦下。
                    self.assertNotEqual(client.get(path).status_code, 401)

    def test_unknown_non_manage_get_stays_public_for_spa(self) -> None:
        """dashboard 是 history 模式 SPA：任意无扩展名页面必须能匿名载入。

        否则启用鉴权后连"输入 key 的登录遮罩"都渲染不出来（先 401 后页面）。
        """
        self._enable()
        with TestClient(self.app) as client:
            for path in ("/random/page", "/config", "/neuro-sama-persona"):
                with self.subTest(path=path):
                    self.assertNotEqual(client.get(path).status_code, 401)

    # ---------- 会话 cookie ----------

    def test_login_sets_http_only_cookie_and_cookie_authorizes(self) -> None:
        key = self._enable("correct-horse")
        with TestClient(self.app) as client:
            resp = client.post("/login", json={"key": key})
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json(), {"status": "ok"})
            cookie = resp.headers["set-cookie"]
            self.assertIn(f"{vedal_server.AUTH_COOKIE}={key}", cookie)
            self.assertIn("HttpOnly", cookie)
            self.assertIn("SameSite=Lax", cookie)
            self.assertIn(f"Max-Age={vedal_server.AUTH_COOKIE_MAX_AGE}", cookie)
            self.assertNotIn("Secure", cookie)  # 本地 http 模式不加 Secure
            # TestClient 自动保存 Set-Cookie：后续受保护请求直接放行
            self.assertEqual(client.get("/manage/config").status_code, 200)

    def test_login_sets_secure_cookie_behind_https_proxy(self) -> None:
        key = self._enable()
        with TestClient(self.app) as client:
            resp = client.post(
                "/login", json={"key": key}, headers={"x-forwarded-proto": "https"}
            )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Secure", resp.headers["set-cookie"])

    def test_logout_requires_auth_and_clears_cookie(self) -> None:
        key = self._enable()
        with TestClient(self.app) as client:
            self.assertEqual(client.post("/logout").status_code, 401)
            client.post("/login", json={"key": key})
            resp = client.post("/logout")
            self.assertEqual(resp.status_code, 200)
            self.assertIn(f"{vedal_server.AUTH_COOKIE}=", resp.headers["set-cookie"])
            self.assertEqual(client.get("/manage/config").status_code, 401)

    # ---------- 爆破限流 ----------

    def test_login_failures_are_rate_limited_per_ip(self) -> None:
        self._enable()
        headers = {"x-forwarded-for": "203.0.113.9, 10.0.0.1"}
        with TestClient(self.app) as client:
            statuses = [
                client.post(
                    "/login", json={"key": f"wrong-{index}"}, headers=headers
                ).status_code
                for index in range(vedal_server.LOGIN_RATE_LIMIT + 1)
            ]
        self.assertEqual(
            statuses[: vedal_server.LOGIN_RATE_LIMIT],
            [401] * vedal_server.LOGIN_RATE_LIMIT,
        )
        self.assertEqual(statuses[-1], 429, "超限后必须返回 429")

    def test_wrong_bearer_also_counts_toward_the_ip_bucket(self) -> None:
        self._enable()
        headers = {
            "x-forwarded-for": "198.51.100.7",
            "Authorization": "Bearer definitely-wrong",
        }
        with TestClient(self.app) as client:
            statuses = [
                client.get("/manage/config", headers=headers).status_code
                for _ in range(vedal_server.LOGIN_RATE_LIMIT + 1)
            ]
        self.assertEqual(statuses[-1], 429)
        self.assertEqual(statuses[0], 401)

    def test_non_ascii_key_is_rejected_without_crashing(self) -> None:
        """`secrets.compare_digest` 的 str 版只接受 ASCII；key 无格式约束，不能 500。"""
        self._enable("ascii-only-key")
        with TestClient(self.app) as client:
            resp = client.post("/login", json={"key": "不是正确的钥"})
        self.assertEqual(resp.status_code, 401)

    # ---------- 匿名状态针眼 ----------

    def test_public_modules_status_exposes_only_booleans(self) -> None:
        self._enable()

        async def fake_reachable(url: str) -> bool:
            return url is not None and "neuro_sama" in url

        with patch.object(vedal_server, "_is_reachable", fake_reachable), patch.object(
            vedal_server,
            "module_url",
            lambda name: f"http://127.0.0.1/{name}",
        ):
            with TestClient(self.app) as client:
                body = client.get("/public/modules_status").json()

        self.assertEqual(set(body), {"modules"})
        self.assertEqual(set(body["modules"]), {"vedal", "neuro_sama", "stream"})
        self.assertIs(body["modules"]["neuro_sama"], True)
        self.assertIs(body["modules"]["stream"], False)
        self.assertIs(body["modules"]["vedal"], True)
        for value in body["modules"].values():
            self.assertIsInstance(value, bool)
        # 侦察字段一律不得出现（url / pid / managed / error）
        for forbidden in ("url", "pid", "managed", "error"):
            self.assertNotIn(forbidden, str(body))


class TestAuthContracts(unittest.TestCase):
    """白名单纪律与前端 URL 的静态契约（不请求、不打端口）。"""

    def test_client_polls_the_public_needle(self) -> None:
        source = (
            ROOT / "client" / "src" / "composables" / "useModuleStatus.ts"
        ).read_text(encoding="utf-8")
        self.assertIn("/public/modules_status", source)
        # 旧路径是登录态管理接口；观看页匿名，不能再用它
        self.assertNotIn("'/manage/modules'", source)

    def test_allowlist_is_an_explicit_pinned_constant(self) -> None:
        source = (ROOT / "server" / "neuro_simulator" / "vedal" / "server.py").read_text(
            encoding="utf-8"
        )
        match = re.search(
            r"PUBLIC_GET_PATHS\s*:[^=]*=\s*\((.*?)\n\)",
            source,
            re.DOTALL,
        )
        self.assertIsNotNone(match, "vedal/server.py 里找不到 PUBLIC_GET_PATHS 常量")
        entries = re.findall(r'"([^"]+)"', match.group(1))
        self.assertEqual(
            set(entries),
            EXPECTED_PUBLIC_GET_PATHS,
            "白名单条目变了：新增公开 GET 必须同时更新本测试与消费方注释",
        )
        self.assertEqual(len(entries), len(set(entries)), "白名单有重复条目")
        # 管理数据类 GET 绝不进白名单
        for locked in (
            "/health",
            "/manage/config",
            "/manage/modules",
            "/manage/state",
            "/manage/logs/*",
            "/manage/neuro_sama/status",
            "/manage/stream/stream/sessions",
            "/manage/stream/stream/status",
            "/manage/stream/queue/message",
        ):
            self.assertNotIn(locked, entries)

    def test_docstring_lists_the_new_endpoints_and_locks_health(self) -> None:
        doc = vedal_server.__doc__ or ""
        for method, path in (
            ("POST", "/login"),
            ("POST", "/logout"),
            ("GET", "/public/modules_status"),
            ("GET", "/public/stream_view"),
        ):
            self.assertRegex(
                doc,
                rf"(?m)^-\s+{method}\s+{re.escape(path)}\b",
                f"vedal 模块 docstring 缺少 {method} {path} 的端点清单行",
            )
        self.assertRegex(
            doc,
            r"(?m)^-\s+GET\s+/health\b.*锁内",
            "/health 的 docstring 必须标注已移入锁内",
        )

    def test_new_endpoints_are_registered(self) -> None:
        app = vedal_server.create_app()
        registered = {
            (method, getattr(route, "path", ""))
            for route in app.routes
            for method in (getattr(route, "methods", None) or [])
        }
        for method, path in (
            ("POST", "/login"),
            ("POST", "/logout"),
            ("GET", "/public/modules_status"),
            ("GET", "/public/stream_view"),
        ):
            self.assertIn((method, path), registered)


class TestPublicStreamView(unittest.TestCase):
    """`/public/stream_view` 字段契约：投影映射、降级 offline 形状、侦察字段不外泄。"""

    def setUp(self) -> None:
        self.cfg = _FakeConfig("")  # 鉴权未启用：该端点本就匿名
        patchers = (
            patch.object(vedal_server, "get_config", lambda: self.cfg),
            patch.object(vedal_server, "module_url", lambda name: None),
        )
        for item in patchers:
            item.start()
            self.addCleanup(item.stop)
        self.app = vedal_server.create_app()

    def _get(self, fetch_result=None, *, real=False):
        if real:  # module_url=None → 真 _fetch_module_state 走 "no url" 降级
            with TestClient(self.app) as client:
                return client.get("/public/stream_view").json()

        async def fake(name, *, want=None, drop=None):
            return fetch_result

        with patch.object(vedal_server, "_fetch_module_state", fake):
            with TestClient(self.app) as client:
                return client.get("/public/stream_view").json()

    def test_maps_only_consumed_fields(self):
        body = self._get({
            "url": "http://127.0.0.1:8200",
            "reachable": True,
            "observed_at": "x",
            "elapsed_ms": 2,
            "error": None,
            "status": {"sections": {
                "loop": {"current": {
                    "state": "live",
                    "session_id": "s-1",
                    "conversation_id": "c-secret",
                    "last_error": {"detail": "boom"},
                    "persisted": {"state": "online", "started_at": "2026-11-14 10:00:00"},
                }},
                "metadata": {"current": {
                    "live": True, "title": "T", "category": "C", "tags": ["a"],
                }},
                "audience": {"current": {
                    "viewers": 1234,
                    "viewers_source": "twitchtracker_avg",
                    "updated_at": "x",
                    "error": "TwitchTracker 403",
                }},
            }},
        })
        self.assertEqual(
            set(body),
            {"state", "live", "title", "category", "tags",
             "has_session", "started_at", "audience"},
        )
        self.assertEqual(body["state"], "live")
        self.assertTrue(body["live"])
        self.assertTrue(body["has_session"])
        self.assertEqual(body["started_at"], "2026-11-14 10:00:00")
        self.assertEqual(body["audience"]["viewers"], 1234)
        # 侦察面字段绝不外泄：会话/对话原始 id、错误文本、内网地址
        blob = str(body)
        for forbidden in ("c-secret", "s-1", "boom", "TwitchTracker 403", "127.0.0.1"):
            self.assertNotIn(forbidden, blob)

    def test_degrades_to_offline_shape(self):
        body = self._get(real=True)
        self.assertEqual(body["state"], "offline")
        self.assertFalse(body["live"])
        self.assertFalse(body["has_session"])
        self.assertIsNone(body["started_at"])
        self.assertIsNone(body["audience"]["viewers"])
        self.assertIsNone(body["audience"]["error"])
        self.assertNotIn("no url configured", str(body))

    def test_started_at_null_when_persisted_offline(self):
        body = self._get({
            "status": {"sections": {"loop": {"current": {
                "state": "offline",
                "persisted": {"state": "offline", "started_at": "2026-11-13 20:00:00"},
            }}}},
        })
        self.assertIsNone(body["started_at"])


if __name__ == "__main__":
    unittest.main()
