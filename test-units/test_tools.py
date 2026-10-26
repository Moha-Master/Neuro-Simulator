"""内置工具集：时间不再由工具提供（由 stream 等注入方在消息里带上），
画面动作工具 model_spin / model_zoom 必须存在，游戏工具以 game_<游戏>_<动作> 命名。"""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from neuro_simulator.neuro_sama.games import StreamGameProvider
from neuro_simulator.neuro_sama.tools import build_builtin_tools

GAME_TOOLS = {
    "game_wordle_start",
    "game_wordle_type",
    "game_wordle_delete",
    "game_wordle_submit",
    "game_wordle_state",
    "game_wordle_restart",
    "game_wordle_exit",
    "game_chess_start",
    "game_chess_move",
    "game_chess_state",
    "game_chess_resign",
    "game_chess_restart",
    "game_chess_exit",
}


class TestBuiltinTools(unittest.TestCase):
    def test_tool_names(self):
        # memory 只被 memory_add 的闭包引用，这里不会触发
        tools = build_builtin_tools(None)  # type: ignore[arg-type]
        names = {t.name for t in tools}
        self.assertEqual(names, {"memory_add", "model_spin", "model_zoom", "stream_title_set"} | GAME_TOOLS)
        self.assertNotIn("time_get_current", names)

    def test_avatar_tools_take_no_arguments(self):
        tools = {t.name: t for t in build_builtin_tools(None)}  # type: ignore[arg-type]
        for name in ("model_spin", "model_zoom", "game_wordle_start", "game_wordle_submit", "game_chess_start"):
            self.assertEqual(tools[name].parameters["properties"], {})

    def test_game_tools_document_arguments(self):
        tools = {t.name: t for t in build_builtin_tools(None)}  # type: ignore[arg-type]
        self.assertIn("letters", tools["game_wordle_type"].parameters["properties"])
        self.assertEqual(tools["game_wordle_type"].parameters["required"], ["letters"])
        self.assertIn("count", tools["game_wordle_delete"].parameters["properties"])
        self.assertEqual(tools["game_wordle_delete"].parameters["required"], [])
        self.assertIn("move", tools["game_chess_move"].parameters["properties"])
        self.assertEqual(tools["game_chess_move"].parameters["required"], ["move"])

    def test_dynamic_tool_filter(self):
        from neuro_simulator.neuro_sama.tools import ToolRegistry

        registry = ToolRegistry()
        for tool in build_builtin_tools(None):  # type: ignore[arg-type]
            registry.add(tool)

        idle = registry.names()
        self.assertIn("game_wordle_start", idle)
        self.assertIn("game_chess_start", idle)
        self.assertNotIn("game_wordle_submit", idle)
        self.assertNotIn("game_chess_move", idle)

        wordle = registry.names("wordle")
        self.assertIn("game_wordle_submit", wordle)
        self.assertNotIn("game_chess_move", wordle)

        chess = registry.names("chess")
        self.assertIn("game_chess_move", chess)
        self.assertNotIn("game_wordle_submit", chess)


class TestToolSwitch(unittest.IsolatedAsyncioTestCase):
    """registry 级开关：只过滤暴露给 LLM 的 schema/names，不挡 dispatch。"""

    def _registry(self):
        from neuro_simulator.neuro_sama.tools import Tool, ToolRegistry

        calls: list[dict] = []

        async def handler(**kwargs) -> str:
            calls.append(kwargs)
            return "executed"

        registry = ToolRegistry()
        registry.add(
            Tool(
                "memory_add",
                "fake",
                {"type": "object", "properties": {}},
                handler,
            )
        )
        registry.add(Tool("other_tool", "fake", {"type": "object", "properties": {}}, handler))
        return registry, calls

    async def test_disabled_is_hidden_from_schema_and_names_but_dispatch_still_runs(self):
        registry, calls = self._registry()
        registry.set_disabled({"memory_add"})
        self.assertEqual(registry.disabled, {"memory_add"})

        schema_names = {t["function"]["name"] for t in registry.openai_schema(None)}
        self.assertNotIn("memory_add", schema_names)
        self.assertIn("other_tool", schema_names)
        self.assertEqual(registry.names(None), ["other_tool"])
        # 全量名与手动运行不受开关影响
        self.assertEqual(registry.all_names(), ["memory_add", "other_tool"])
        with patch(
            "neuro_simulator.neuro_sama.tools.get_config",
            return_value=SimpleNamespace(TOOL_TIMEOUT=5),
        ):
            ok, text = await registry.dispatch("memory_add", {"content": "x"})
        self.assertTrue(ok)
        self.assertEqual(text, "executed")
        self.assertEqual(calls, [{"content": "x"}])

    async def test_set_disabled_replaces_the_whole_set(self):
        registry, _ = self._registry()
        registry.set_disabled({"memory_add", "other_tool"})
        registry.set_disabled({"other_tool"})
        self.assertEqual(registry.disabled, {"other_tool"})
        self.assertIn("memory_add", {t["function"]["name"] for t in registry.openai_schema(None)})


class _FakeResponse:
    def __init__(self, payload, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


class _FakeHttpClient:
    """只实现 provider 用到的 get()，用来验证缓存/失效逻辑。"""

    payload: dict = {"active": None}
    calls = 0

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args) -> bool:
        return False

    async def get(self, url):
        type(self).calls += 1
        return _FakeResponse(self.payload)


class TestStreamGameProvider(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        _FakeHttpClient.payload = {"active": None}
        _FakeHttpClient.calls = 0

    async def test_caches_and_invalidates(self):
        with patch(
            "neuro_simulator.neuro_sama.games.get_config", return_value={}
        ), patch(
            "neuro_simulator.neuro_sama.games.module_url", return_value="http://fake.test:8200"
        ), patch("neuro_simulator.neuro_sama.games.httpx.AsyncClient", _FakeHttpClient):
            provider = StreamGameProvider(ttl=10.0, negative_ttl=10.0)
            self.assertIsNone(await provider.active_game())
            self.assertIsNone(await provider.active_game())
            self.assertEqual(_FakeHttpClient.calls, 1)  # 第二次命中缓存

            # stream 侧刚开了一局，但缓存还没失效 -> 仍然是旧的 None
            _FakeHttpClient.payload = {"active": "chess"}
            self.assertIsNone(await provider.active_game())

            # 执行完 game_* 工具后必须 invalidate，下一次立刻看到新状态
            provider.invalidate()
            self.assertEqual(await provider.active_game(), "chess")
            self.assertEqual(_FakeHttpClient.calls, 2)


class TestStreamTitleTool(unittest.IsolatedAsyncioTestCase):
    """stream_title_set：只转发到 stream 的 PUT /stream/metadata，失败也返回人话。"""

    def setUp(self) -> None:
        _FakePutClient.response = _FakeResponse({"status": "ok", "title": "new title"})
        _FakePutClient.error = None
        _FakePutClient.calls = []

    async def _call(self, title: str, *, base="http://stream.test:8200", error=None):
        _FakePutClient.error = error
        with patch(
            "neuro_simulator.neuro_sama.stream_tools.get_config", return_value={}
        ), patch(
            "neuro_simulator.neuro_sama.stream_tools.module_url", return_value=base
        ), patch(
            "neuro_simulator.neuro_sama.stream_tools.httpx.AsyncClient", _FakePutClient
        ):
            from neuro_simulator.neuro_sama.stream_tools import _set_title

            return await _set_title(title)

    async def test_success_puts_only_title_and_returns_new_one(self):
        text = await self._call("  A new topic  ")
        self.assertEqual(text, 'Stream title updated to: "new title".')
        self.assertEqual(len(_FakePutClient.calls), 1)
        url, body = _FakePutClient.calls[0]
        self.assertEqual(url, "http://stream.test:8200/stream/metadata")
        self.assertEqual(body, {"title": "A new topic"})

    async def test_conflict_is_english_and_does_not_leak_chinese_detail(self):
        _FakePutClient.response = _FakeResponse(
            {"detail": "当前没有进行中的直播，无法修改本场信息"}, status_code=409
        )
        text = await self._call("title")
        self.assertIn("not live", text)
        self.assertNotIn("当前", text)
        self.assertNotIn("Error:", text)

    async def test_unreachable_is_a_normal_tool_result(self):
        import httpx

        from neuro_simulator.neuro_sama.tools import ToolRegistry, build_builtin_tools

        _FakePutClient.error = httpx.ConnectError("boom")
        with patch(
            "neuro_simulator.neuro_sama.stream_tools.get_config", return_value={}
        ), patch(
            "neuro_simulator.neuro_sama.stream_tools.module_url",
            return_value="http://stream.test:8200",
        ), patch(
            "neuro_simulator.neuro_sama.stream_tools.httpx.AsyncClient", _FakePutClient
        ), patch(
            "neuro_simulator.neuro_sama.tools.get_config",
            return_value=SimpleNamespace(TOOL_TIMEOUT=5),
        ):
            registry = ToolRegistry()
            for tool in build_builtin_tools(None):  # type: ignore[arg-type]
                registry.add(tool)
            ok, text = await registry.dispatch("stream_title_set", {"title": "title"})

        self.assertTrue(ok)
        self.assertIn("unreachable", text)
        self.assertIn("ConnectError", text)

    async def test_unconfigured_stream_never_builds_a_client(self):
        text = await self._call("title", base=None)
        self.assertIn("not configured", text)
        self.assertEqual(_FakePutClient.calls, [])

    async def test_empty_and_overlong_titles_are_rejected_before_request(self):
        self.assertEqual(await self._call("   "), "The stream title cannot be empty.")
        text = await self._call("x" * 141)
        self.assertIn("too long", text)
        self.assertEqual(_FakePutClient.calls, [])

    async def test_tool_is_visible_with_and_without_active_game(self):
        from neuro_simulator.neuro_sama.tools import ToolRegistry

        registry = ToolRegistry()
        for tool in build_builtin_tools(None):  # type: ignore[arg-type]
            registry.add(tool)

        idle = {t["function"]["name"] for t in registry.openai_schema(None)}
        chess = {t["function"]["name"] for t in registry.openai_schema("chess")}
        self.assertIn("stream_title_set", idle)
        self.assertIn("stream_title_set", chess)


class _FakePutClient:
    """stream_tools 用到的 put()；与只实现 get() 的 _FakeHttpClient 分开。"""

    response: "_FakeResponse" = None  # type: ignore[assignment]
    error: Exception | None = None
    calls: list = []

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args) -> bool:
        return False

    async def put(self, url, json=None):
        type(self).calls.append((url, json))
        if type(self).error is not None:
            raise type(self).error
        return type(self).response


if __name__ == "__main__":
    unittest.main()
