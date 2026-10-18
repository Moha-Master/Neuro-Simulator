"""内置工具集：时间不再由工具提供（由 stream 等注入方在消息里带上），
画面动作工具 model_spin / model_zoom 必须存在。"""

import unittest

from neuro_simulator.neuro_sama.tools import build_builtin_tools


class TestBuiltinTools(unittest.TestCase):
    def test_tool_names(self):
        # memory 只被 memory_add 的闭包引用，这里不会触发
        tools = build_builtin_tools(None)  # type: ignore[arg-type]
        names = {t.name for t in tools}
        self.assertEqual(names, {"memory_add", "model_spin", "model_zoom"})
        self.assertNotIn("time_get_current", names)

    def test_avatar_tools_take_no_arguments(self):
        tools = {t.name: t for t in build_builtin_tools(None)}  # type: ignore[arg-type]
        for name in ("model_spin", "model_zoom"):
            self.assertEqual(tools[name].parameters["properties"], {})


if __name__ == "__main__":
    unittest.main()
