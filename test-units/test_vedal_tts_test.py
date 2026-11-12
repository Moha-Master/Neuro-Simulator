"""vedal `POST /manage/tts/test`（azure TTS 凭据自检）的契约。

钉住三件事：

1. **region 会被拼进 URL host**（`https://{region}.tts.speech.microsoft.com/...`），
   所以格式校验是主机劫持/SSRF 的唯一防线——`.` `/` `@` `#` 空格一类必须 400，
   这条永远不许松；
2. 端点**不读不写配置**：测试全程不碰 config.json（成功/失败路径都只回 JSON）；
3. 合成文本固定在服务端常量里——body 传 `text` 也不生效（不是自由合成入口）。

httpx 被 mock 掉：单测不打真实 Azure。
"""

import base64
import unittest
from unittest import mock

import httpx
from fastapi.testclient import TestClient

from neuro_simulator.vedal import server as vedal_server


class FakeCtx:
    """替身 httpx.AsyncClient：`async with` 后记录请求、返回预置响应/抛预置异常。"""

    def __init__(self, *, response=None, error=None):
        self.response = response
        self.error = error
        self.url = None
        self.kwargs = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, **kwargs):
        self.url = url
        self.kwargs = kwargs
        if self.error is not None:
            raise self.error
        return self.response


class FakeResponse:
    def __init__(self, status_code=200, content=b"", text=""):
        self.status_code = status_code
        self.content = content
        self.text = text


class TTSTestEndpoint(unittest.TestCase):
    def setUp(self):
        self.app = vedal_server.create_app()
        self.client = TestClient(self.app)

    def _post(self, body, *, response=None, error=None):
        ctx = FakeCtx(response=response, error=error)
        with mock.patch.object(httpx, "AsyncClient", return_value=ctx):
            resp = self.client.post("/manage/tts/test", json=body)
        return resp, ctx

    # ---------- 1. region/key 校验（安全线） ----------

    def test_rejects_host_hijacking_regions(self):
        bad = [
            "evil.com/", "eastasia/../x", "a b", "", "southeastasia#evil",
            "x@evil", "eastasia:8080", "..", "east asia", "East Asia!",
        ]
        for region in bad:
            resp, ctx = self._post({"region": region, "key": "k"})
            self.assertEqual(resp.status_code, 400, f"region={region!r} 必须被拒")
            self.assertIsNone(ctx.url, "校验失败时不应发出任何请求")

    def test_rejects_empty_key(self):
        resp, ctx = self._post({"region": "eastasia", "key": "   "})
        self.assertEqual(resp.status_code, 400)
        self.assertIsNone(ctx.url)

    # ---------- 2. 成功/失败路径 ----------

    def test_success_returns_audio_base64_and_normalizes_region(self):
        resp, ctx = self._post(
            {"region": "EastAsia", "key": "secret"},
            response=FakeResponse(200, content=b"FAKEMP3"),
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(base64.b64decode(data["audio"]), b"FAKEMP3")
        self.assertEqual(data["mime"], "audio/mpeg")
        # region 归一化小写并拼进官方 host；文本固定、key 走 header
        self.assertEqual(ctx.url, "https://eastasia.tts.speech.microsoft.com/cognitiveservices/v1")
        self.assertEqual(
            ctx.kwargs["headers"]["Ocp-Apim-Subscription-Key"], "secret")
        self.assertIn(vedal_server.TTS_TEST_TEXT, ctx.kwargs["content"].decode())
        # 命名空间必须是 SSML 1.0 的 synthesis——写成 W3C PR 的 `2001/10/voice`
        # 会被 Azure 以 BadRequest 拒掉（SDK 报 CancellationErrorCode.BadRequest，
        # REST 报 400 空响应体；两个症状都出现过，这里永久钉死）
        self.assertIn('xmlns="http://www.w3.org/2001/10/synthesis"', ctx.kwargs["content"].decode())

    def test_upstream_error_status_is_reported_not_raised(self):
        resp, _ = self._post(
            {"region": "westus2", "key": "k"},
            response=FakeResponse(401, text="Access denied"),
        )
        self.assertEqual(resp.status_code, 200)  # 自检失败不是 vedal 的失败
        data = resp.json()
        self.assertFalse(data["ok"])
        self.assertEqual(data["status"], 401)
        self.assertIn("401", data["detail"])

    def test_network_error_degrades_to_readable_detail(self):
        resp, _ = self._post(
            {"region": "eastasia", "key": "k"},
            error=httpx.ConnectError("boom"),
        )
        data = resp.json()
        self.assertFalse(data["ok"])
        self.assertIn("ConnectError", data["detail"])

    # ---------- 3. 端点形状 ----------

    def test_registered_route_and_documented(self):
        paths = [getattr(r, "path", "") for r in self.app.routes]
        self.assertIn("/manage/tts/test", paths)
        # 必须注册在通用代理 /manage/{module}/{path} 之前（否则会被当成模块请求 502）
        proxy = next(
            i for i, p in enumerate(paths)
            if p.startswith("/manage/{module}")
        )
        self.assertLess(paths.index("/manage/tts/test"), proxy)


if __name__ == "__main__":
    unittest.main()
