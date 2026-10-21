"""内置工具集：时间不再由工具提供（由 stream 等注入方在消息里带上），
画面动作工具 model_spin / model_zoom 必须存在，游戏工具以 game_<游戏>_<动作> 命名。"""

import unittest
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
        self.assertEqual(names, {"memory_add", "model_spin", "model_zoom"} | GAME_TOOLS)
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


if __name__ == "__main__":
    unittest.main()
