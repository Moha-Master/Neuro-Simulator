"""vedal `GET /state`：全景扇出聚合的纪律测试。

这里用假的 httpx.AsyncClient 替代真实模块，重点钉住四件事：

- **原样透传**：模块 status envelope 一字不差，聚合层只加观测字段；
- **失败隔离**：超时/连接失败/500/坏 JSON 都只降级该模块，HTTP 永远 200；
- **并发扇出**：总耗时 ≈ 最慢的模块，而不是各模块之和；
- **参数透传**：want/drop 原样带给每个模块，modules 只聚合指定模块。

不打真实端口、不启动服务（TestClient 只走 ASGI 内存调用）。
"""

import asyncio
import json
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from neuro_simulator.vedal import server as vedal_server

URLS = {
    "neuro_sama": "http://127.0.0.1:8000",
    "stream": "http://127.0.0.1:8101",
}

NEURO_STATUS = {
    "module": "neuro_sama",
    "rev": 7,
    "ts": "2026-10-23T14:03:11+08:00",
    "sections": {
        "scene": {
            "current": {
                "id": "normal",
                "label": "Normal",
                "desc": "Neuro is idling in her room.",
                "icon": "mdi-account",
                "duration": None,
                "elapsed": 3.25,
                "auto_returns": False,
            },
            "graph": {
                "states": [{"id": "normal", "label": "Normal", "desc": "Room."}],
                "transitions": [],
            },
        },
        "turn": {
            "current": {
                "turn_id": "t-1",
                "channel": "web",
                "generating": False,
                "speaking": True,
                "remaining_s": 1.5,
                "ends_at": 1761200000.0,
                "segments": 2,
                "segments_done": 1,
                "in_flight": 0,
            }
        },
    },
}

STREAM_STATUS = {
    "module": "stream",
    "rev": 17,
    "ts": "2026-10-23T14:03:11+08:00",
    "sections": {
        "loop": {
            "current": {
                "state": "live",
                "session_id": "s-1",
                "queue_id": "q-1",
                "channel": "twitch",
                "conversation_id": "c-1",
                "round": 4,
                "cursor": 10,
                "priority_cursor": 2,
                "intro": None,
                "last_error": None,
                "started_at": "2026-10-23T14:00:00+08:00",
                "persisted": {"session_id": "s-1", "rounds": 4},
            },
            "graph": {
                "states": [{"id": "live", "label": "Live", "desc": "On air."}],
                "transitions": [{"from": "live", "to": "paused", "by": "POST /stream/pause"}],
            },
        },
        "audience": {
            "current": {
                "viewers": 1234,
                "viewers_source": "twitchtracker",
                "updated_at": "2026-10-23T14:03:00+08:00",
                "error": None,
            }
        },
        "neuro_link": {
            "current": {
                "reachable": True,
                "observed_at": "2026-10-23T14:03:10+08:00",
                "url": URLS["neuro_sama"],
                "error": None,
            }
        },
    },
}


class _Response:
    def __init__(
        self,
        *,
        status_code: int = 200,
        payload=None,
        text: str = "",
        bad_json: bool = False,
    ) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self._bad_json = bad_json

    def json(self):
        if self._bad_json:
            raise json.JSONDecodeError("Expecting value", "", 0)
        return self._payload


class _FakeAsyncClient:
    """按 URL 配置行为（payload/会合钩子/延迟/抛异常/状态码/坏 JSON）。"""

    behavior: dict[str, dict] = {}
    calls: list[tuple[str, dict]] = []

    def __init__(self, timeout=None) -> None:
        self.timeout = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url: str, params=None):
        type(self).calls.append((url, dict(params or {})))
        cfg = type(self).behavior.get(url)
        if cfg is None:
            raise AssertionError(f"测试未配置的 URL: {url}")
        hook = cfg.get("hook")
        if hook is not None:
            await hook(url)
        delay = cfg.get("delay", 0)
        if delay:
            await asyncio.sleep(delay)
        if "raise" in cfg:
            raise cfg["raise"]
        return _Response(
            status_code=cfg.get("status_code", 200),
            payload=cfg.get("payload"),
            text=cfg.get("text", ""),
            bad_json=cfg.get("bad_json", False),
        )


class VedalStateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.names = ["neuro_sama", "stream"]
        _FakeAsyncClient.behavior = {}
        _FakeAsyncClient.calls = []
        for patcher in (
            patch.object(vedal_server, "_module_names", side_effect=lambda: list(self.names)),
            patch.object(vedal_server, "module_url", side_effect=lambda n: URLS.get(n)),
            patch.object(vedal_server.httpx, "AsyncClient", _FakeAsyncClient),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _client(self) -> TestClient:
        return TestClient(vedal_server.create_app())

    def _ok_both(self) -> None:
        _FakeAsyncClient.behavior = {
            f"{URLS['neuro_sama']}/status": {"payload": NEURO_STATUS},
            f"{URLS['stream']}/stream/status": {"payload": STREAM_STATUS},
        }

    def test_status_is_passed_through_verbatim(self) -> None:
        self._ok_both()
        with self._client() as client:
            resp = client.get("/state")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()

        # 字段一字不差（聚合层不许改名/摊平/重算）
        self.assertEqual(body["modules"]["neuro_sama"]["status"], NEURO_STATUS)
        self.assertEqual(body["modules"]["stream"]["status"], STREAM_STATUS)
        self.assertEqual(body["vedal"]["module"], "vedal")
        self.assertIsInstance(body["vedal"]["ts"], str)

        # 聚合层只允许加这六个观测字段
        for name in ("neuro_sama", "stream"):
            self.assertEqual(
                set(body["modules"][name]),
                {"url", "reachable", "observed_at", "elapsed_ms", "status", "error"},
            )
            self.assertTrue(body["modules"][name]["reachable"])
            self.assertIsNone(body["modules"][name]["error"])
            self.assertIsInstance(body["modules"][name]["observed_at"], str)
            elapsed = body["modules"][name]["elapsed_ms"]
            self.assertIs(type(elapsed), int)
            self.assertGreaterEqual(elapsed, 0)

        # vedal 自身没有状态机投影
        self.assertNotIn("vedal", body["modules"])

    def test_failures_only_degrade_the_broken_module(self) -> None:
        cases = {
            "timeout": {"raise": httpx.ReadTimeout("timed out")},
            "connect": {"raise": httpx.ConnectError("refused")},
            "http_500": {"status_code": 500, "text": "boom"},
            "bad_json": {"bad_json": True},
        }
        expected_type = {
            "timeout": "ReadTimeout",
            "connect": "ConnectError",
            "http_500": "RuntimeError",
            "bad_json": "JSONDecodeError",
        }
        for label, broken in cases.items():
            with self.subTest(label=label):
                _FakeAsyncClient.calls = []
                _FakeAsyncClient.behavior = {
                    f"{URLS['neuro_sama']}/status": broken,
                    f"{URLS['stream']}/stream/status": {"payload": STREAM_STATUS},
                }
                with self._client() as client:
                    resp = client.get("/state")

                self.assertEqual(resp.status_code, 200, "任何模块失败都不能让 /state 变 500")
                body = resp.json()
                neuro = body["modules"]["neuro_sama"]
                self.assertFalse(neuro["reachable"])
                self.assertIsNone(neuro["status"])
                self.assertTrue(neuro["error"])
                self.assertIn(expected_type[label], neuro["error"])

                # 另一个模块不受影响
                stream = body["modules"]["stream"]
                self.assertTrue(stream["reachable"])
                self.assertIsNone(stream["error"])
                self.assertEqual(stream["status"], STREAM_STATUS)

    def test_module_without_url_is_unreachable(self) -> None:
        self.names = ["orphan"]
        with patch.object(vedal_server, "module_url", return_value=None):
            with self._client() as client:
                resp = client.get("/state")
        self.assertEqual(resp.status_code, 200)
        entry = resp.json()["modules"]["orphan"]
        self.assertFalse(entry["reachable"])
        self.assertIsNone(entry["status"])
        self.assertTrue(entry["error"])
        self.assertEqual(_FakeAsyncClient.calls, [])

    def test_want_drop_are_forwarded_to_every_module(self) -> None:
        self._ok_both()
        with self._client() as client:
            client.get("/state", params={"want": "scene,loop", "drop": "layers"})

        sent = dict(_FakeAsyncClient.calls)
        self.assertEqual(
            sent[f"{URLS['neuro_sama']}/status"],
            {"want": "scene,loop", "drop": "layers"},
        )
        self.assertEqual(
            sent[f"{URLS['stream']}/stream/status"],
            {"want": "scene,loop", "drop": "layers"},
        )

    def test_modules_filter_limits_fanout(self) -> None:
        self._ok_both()
        with self._client() as client:
            body = client.get("/state", params={"modules": "stream"}).json()

        self.assertEqual(list(body["modules"]), ["stream"])
        self.assertEqual([url for url, _ in _FakeAsyncClient.calls], [f"{URLS['stream']}/stream/status"])

    def test_fanout_is_concurrent(self) -> None:
        """并发扇出用「会合」证明，不用秒表（时间断言在负载下会假失败）。

        两个 fake 各自登记"我进来了"，再等另一个也进来：串行执行时第一个请求
        永远等不到同伴 → 超时 → 记进 `missed` → 断言失败。并发时两边互相会合，
        `missed` 为空，且不需要对耗时做任何猜测。
        """
        started: dict[str, bool] = {}
        missed: list[str] = []
        both = asyncio.Event()

        async def rendezvous(url: str) -> None:
            started[url] = True
            if len(started) == 2:
                both.set()
                return
            try:
                await asyncio.wait_for(both.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                missed.append(url)

        _FakeAsyncClient.behavior = {
            f"{URLS['neuro_sama']}/status": {"payload": NEURO_STATUS, "hook": rendezvous},
            f"{URLS['stream']}/stream/status": {"payload": STREAM_STATUS, "hook": rendezvous},
        }
        with self._client() as client:
            resp = client.get("/state")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(started), 2, "两个模块都没被请求")
        self.assertEqual(missed, [], "扇出是串行的：某个请求没能与另一个同时在飞")

    def test_vedal_itself_never_appears_in_modules(self) -> None:
        self.names = ["vedal", "neuro_sama"]
        _FakeAsyncClient.behavior = {
            f"{URLS['neuro_sama']}/status": {"payload": NEURO_STATUS},
        }
        with self._client() as client:
            body = client.get("/state").json()

        self.assertEqual(list(body["modules"]), ["neuro_sama"])
        self.assertEqual(len(_FakeAsyncClient.calls), 1)


if __name__ == "__main__":
    unittest.main()
