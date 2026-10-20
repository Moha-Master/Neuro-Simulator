"""内置工具集：时间不再由工具提供（由 stream 等注入方在消息里带上），
画面动作工具 model_spin / model_zoom 必须存在，游戏工具以 game_<游戏>_<动作> 命名。"""

import unittest

from neuro_simulator.neuro_sama.tools import build_builtin_tools

GAME_TOOLS = {
    "game_wordle_start",
    "game_wordle_type",
    "game_wordle_delete",
    "game_wordle_submit",
    "game_wordle_state",
    "game_wordle_restart",
    "game_wordle_exit",
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
        for name in ("model_spin", "model_zoom", "game_wordle_start", "game_wordle_submit"):
            self.assertEqual(tools[name].parameters["properties"], {})

    def test_game_tools_document_arguments(self):
        tools = {t.name: t for t in build_builtin_tools(None)}  # type: ignore[arg-type]
        self.assertIn("letters", tools["game_wordle_type"].parameters["properties"])
        self.assertEqual(tools["game_wordle_type"].parameters["required"], ["letters"])
        self.assertIn("count", tools["game_wordle_delete"].parameters["properties"])
        self.assertEqual(tools["game_wordle_delete"].parameters["required"], [])


if __name__ == "__main__":
    unittest.main()
