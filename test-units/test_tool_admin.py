"""工具管理：DB 存储/播种 + /manage/tools 与 /manage/mcp-servers 端点。

端点测试沿用 neuro 其他测试的做法：不跑 lifespan（会碰真实 config/DB），
用 `create_app()` 后手工装 app.state——端点闭包只读这些状态。
"""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from neuro_simulator.neuro_sama import server as ns_server
from neuro_simulator.neuro_sama import tools as tools_module
from neuro_simulator.neuro_sama.mcp_client import MCPManager
from neuro_simulator.neuro_sama.storage import Storage, seed_mcp_servers
from neuro_simulator.neuro_sama.tools import ToolRegistry, build_builtin_tools


class _FakeMemory:
    count = 0
    used = 0
    char_limit = 1000

    async def add(self, content: str) -> str:
        return f"remembered: {content}"

    def render(self) -> str:
        return ""


class TestToolStorage(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = Storage(Path(self.tmp.name) / "test.db")
        await self.storage.init()

    async def asyncTearDown(self):
        await self.storage.close()
        self.tmp.cleanup()

    async def test_tool_settings_upsert_and_read_back(self):
        self.assertEqual(await self.storage.get_tool_settings(), {})
        await self.storage.set_tool_enabled("model_spin", False)
        await self.storage.set_tool_enabled("memory_add", True)
        self.assertEqual(
            await self.storage.get_tool_settings(),
            {"model_spin": False, "memory_add": True},
        )
        # upsert，不新增重复行
        await self.storage.set_tool_enabled("model_spin", True)
        self.assertEqual(
            await self.storage.get_tool_settings(),
            {"model_spin": True, "memory_add": True},
        )

    async def test_mcp_document_roundtrip_and_none_before_seed(self):
        self.assertIsNone(await self.storage.get_mcp_servers())
        servers = [{"name": "a", "transport": "http", "url": "http://x/mcp", "enabled": True}]
        await self.storage.upsert_mcp_servers(servers)
        self.assertEqual(await self.storage.get_mcp_servers(), servers)
        await self.storage.upsert_mcp_servers([])
        self.assertEqual(await self.storage.get_mcp_servers(), [])

    async def test_seed_from_config_is_one_shot(self):
        wrote = await seed_mcp_servers(
            self.storage, [{"name": "cfg", "command": "npx", "args": ["-y", "x"]}]
        )
        self.assertTrue(wrote)
        servers = await self.storage.get_mcp_servers()
        self.assertEqual(len(servers), 1)
        self.assertEqual(servers[0]["name"], "cfg")
        self.assertTrue(servers[0]["enabled"], "旧 config 没有 enabled 时播种为 true")
        self.assertEqual(await self.storage.get_meta("mcp_seeded"), "1")

        # 第二次即便 config 变了也不覆盖 DB
        wrote_again = await seed_mcp_servers(self.storage, [{"name": "other", "url": "http://y"}])
        self.assertFalse(wrote_again)
        self.assertEqual([s["name"] for s in await self.storage.get_mcp_servers()], ["cfg"])


class _ToolAdminBase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = Storage(Path(self.tmp.name) / "test.db")
        await self.storage.init()
        self.memory = _FakeMemory()
        self.registry = ToolRegistry()
        for tool in build_builtin_tools(self.memory):  # type: ignore[arg-type]
            self.registry.add(tool)
        self.mcp = MCPManager([])
        self.app = ns_server.create_app()
        self.app.state.agent = SimpleNamespace(registry=self.registry)
        self.app.state.storage = self.storage
        self.app.state.memory = self.memory
        self.app.state.mcp = self.mcp
        self.cfg = SimpleNamespace(TOOL_TIMEOUT=5.0)
        self._patchers = [
            patch.object(ns_server, "get_config", return_value=self.cfg),
            patch.object(tools_module, "get_config", return_value=self.cfg),
        ]
        for p in self._patchers:
            p.start()

    async def asyncTearDown(self):
        for p in self._patchers:
            p.stop()
        await self.app.state.mcp.close()
        await self.storage.close()
        self.tmp.cleanup()

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://test"
        )


class TestManageTools(_ToolAdminBase):
    async def test_get_tools_shape_uses_full_registry(self):
        async with self._client() as client:
            resp = await client.get("/manage/tools")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        names = {t["name"] for t in body["tools"]}
        self.assertEqual(
            names,
            {
                "memory_add",
                "model_spin",
                "model_zoom",
                "stream_title_set",
                *{f"game_wordle_{a}" for a in ("start", "type", "delete", "submit", "state", "restart", "exit")},
                *{f"game_chess_{a}" for a in ("start", "move", "state", "resign", "restart", "exit")},
            },
        )
        self.assertTrue(all(t["enabled"] for t in body["tools"]))
        self.assertEqual(body["mcp_servers"], [])
        self.assertEqual(body["tool_timeout_s"], 5.0)
        spin = next(t for t in body["tools"] if t["name"] == "model_spin")
        self.assertEqual(spin["origin"], "builtin")
        self.assertIn("parameters", spin)
        self.assertIn("description", spin)
        wordle = next(t for t in body["tools"] if t["name"] == "game_wordle_start")
        self.assertEqual(wordle["origin"], "game")
        self.assertEqual(wordle["game"], "wordle")

    async def test_disable_and_reenable_take_effect_immediately(self):
        async with self._client() as client:
            resp = await client.put(
                "/manage/tools/model_spin/enabled", json={"enabled": False}
            )
            self.assertEqual(resp.status_code, 200)
            body = resp.json()
            self.assertEqual(body["status"], "ok")
            spin = next(t for t in body["tools"] if t["name"] == "model_spin")
            self.assertFalse(spin["enabled"])
            self.assertNotIn(
                "model_spin", {t["function"]["name"] for t in self.registry.openai_schema(None)}
            )
            self.assertIn("model_spin", self.registry.all_names(), "/health 仍看全量")
            self.assertEqual(
                (await self.storage.get_tool_settings()), {"model_spin": False}
            )

            resp = await client.put(
                "/manage/tools/model_spin/enabled", json={"enabled": True}
            )
            self.assertEqual(resp.status_code, 200)
            self.assertIn(
                "model_spin", {t["function"]["name"] for t in self.registry.openai_schema(None)}
            )

    async def test_unknown_tool_is_404(self):
        async with self._client() as client:
            resp = await client.put("/manage/tools/nope/enabled", json={"enabled": False})
        self.assertEqual(resp.status_code, 404)

    async def test_manual_run(self):
        async with self._client() as client:
            resp = await client.post("/manage/tools/model_zoom/run", json={"arguments": {}})
            self.assertEqual(resp.status_code, 200)
            body = resp.json()
            self.assertTrue(body["ok"])
            self.assertEqual(body["result"], "Zoom command sent.")
            self.assertIsInstance(body["duration_ms"], int)

            # 参数非法（非对象）→ 400（Pydantic 默认 422，这里按契约手检）
            resp = await client.post(
                "/manage/tools/model_spin/run", json={"arguments": ["not", "an", "object"]}
            )
            self.assertEqual(resp.status_code, 400)

            # 参数不匹配由 dispatch 的 TypeError 路径转成可读结果（不 500）
            resp = await client.post(
                "/manage/tools/model_spin/run", json={"arguments": {"unexpected": 1}}
            )
            self.assertEqual(resp.status_code, 200)
            body = resp.json()
            self.assertFalse(body["ok"])
            self.assertIn("invalid arguments", body["result"])

            resp = await client.post("/manage/tools/nope/run", json={"arguments": {}})
            self.assertEqual(resp.status_code, 404)


class TestManageMcpServers(_ToolAdminBase):
    async def test_crud_and_unreachable_status(self):
        async with self._client() as client:
            resp = await client.post(
                "/manage/mcp-servers",
                json={"name": "fake", "transport": "http", "url": "http://127.0.0.1:1/mcp"},
            )
            self.assertEqual(resp.status_code, 200, resp.text)
            body = resp.json()
            row = next(s for s in body["mcp_servers"] if s["name"] == "fake")
            self.assertEqual(row["transport"], "http")
            self.assertTrue(row["enabled"])
            self.assertFalse(row["connected"])
            self.assertTrue(row["error"], "连不通必须带 error")

            # 重名 409
            resp = await client.post(
                "/manage/mcp-servers",
                json={"name": "fake", "transport": "http", "url": "http://127.0.0.1:1/mcp"},
            )
            self.assertEqual(resp.status_code, 409)

            # 缺 command/url 400
            resp = await client.post("/manage/mcp-servers", json={"name": "empty"})
            self.assertEqual(resp.status_code, 400)

            # 改名不允许（409），整条替换同名的 enabled 字段也要遵守 body
            resp = await client.put(
                "/manage/mcp-servers/fake",
                json={"name": "renamed", "transport": "http", "url": "http://127.0.0.1:1/mcp"},
            )
            self.assertEqual(resp.status_code, 409)

            resp = await client.put(
                "/manage/mcp-servers/missing",
                json={"name": "missing", "transport": "http", "url": "http://127.0.0.1:1/mcp"},
            )
            self.assertEqual(resp.status_code, 404)

            # 启停
            resp = await client.put(
                "/manage/mcp-servers/fake/enabled", json={"enabled": False}
            )
            self.assertEqual(resp.status_code, 200)
            row = next(s for s in resp.json()["mcp_servers"] if s["name"] == "fake")
            self.assertFalse(row["enabled"])
            self.assertFalse(row["connected"])
            self.assertEqual(self.app.state.mcp.servers, [])

            resp = await client.put(
                "/manage/mcp-servers/fake/enabled", json={"enabled": True}
            )
            self.assertEqual(resp.status_code, 200)
            self.assertEqual([s["name"] for s in self.app.state.mcp.servers], ["fake"])

            # 删除
            resp = await client.delete("/manage/mcp-servers/fake")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json()["mcp_servers"], [])
            self.assertEqual(await self.storage.get_mcp_servers(), [])

            resp = await client.delete("/manage/mcp-servers/fake")
            self.assertEqual(resp.status_code, 404)

    async def test_stdio_validation_without_spawning(self):
        async with self._client() as client:
            # 缺 command（transport 显式 stdio）→ 业务校验 400
            resp = await client.post(
                "/manage/mcp-servers",
                json={"name": "bad", "transport": "stdio", "args": ["x"]},
            )
            self.assertEqual(resp.status_code, 400)

            # 形状不合法的 args/env 在 Pydantic 层就被挡住（422），不会 spawn
            resp = await client.post(
                "/manage/mcp-servers",
                json={
                    "name": "bad-env",
                    "transport": "stdio",
                    "command": "npx",
                    "env": {"A": 1},
                },
            )
            self.assertEqual(resp.status_code, 422)


if __name__ == "__main__":
    unittest.main()
