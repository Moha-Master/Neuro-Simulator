"""两份元素引擎的一致性（stream/ui 与 neuro_sama/ui）。

两个前端是各自独立的 Vite 工程（各有 package.json / tsconfig，`hatch_build.py`
逐目录构建，wheel 只带 dist），所以引擎采用"两份逐字相同的拷贝"而不是共享包。
这个测试就是那根防漂移的桩：内容一旦分叉，直接把差异行打出来。

要改引擎：先改一份，把整份文件复制到另一边，再跑 `npm run build-only` 两边各一次。
"""

import difflib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STREAM_ENGINE = ROOT / "server/neuro_simulator/stream/ui/src/core/elements.ts"
NEURO_ENGINE = ROOT / "server/neuro_simulator/neuro_sama/ui/src/core/elements.ts"


class TestEngineParity(unittest.TestCase):
    def test_two_engine_copies_are_byte_identical(self):
        a = STREAM_ENGINE.read_text(encoding="utf-8")
        b = NEURO_ENGINE.read_text(encoding="utf-8")
        if a == b:
            return
        diff = list(
            difflib.unified_diff(
                a.splitlines(),
                b.splitlines(),
                fromfile="stream/ui/src/core/elements.ts",
                tofile="neuro_sama/ui/src/core/elements.ts",
                lineterm="",
                n=2,
            )
        )
        self.fail("元素引擎的两份拷贝出现漂移：\n" + "\n".join(diff[:120]))

    def test_engine_dependencies_exist_on_both_sides(self):
        """引擎 import 的相对路径必须在两边都存在，否则"内容相同"跑不起来。"""
        for engine in (STREAM_ENGINE, NEURO_ENGINE):
            core = engine.parent
            for name in ("assets.ts", "action.ts", "components.ts"):
                self.assertTrue((core / name).is_file(), f"{core.name} 缺 {name}")
            self.assertTrue((core.parent / "scenes" / "schema.ts").is_file(), engine)

    def test_schema_declares_the_shared_contract(self):
        """两边 schema 都要有引擎依赖的字段（at_end / duration / state_script）。"""
        for side in (STREAM_ENGINE, NEURO_ENGINE):
            schema = (side.parent.parent / "scenes" / "schema.ts").read_text(encoding="utf-8")
            for token in ("at_end", "state_script", "duration?:", "SceneTimeline"):
                self.assertIn(token, schema, f"{side.parent.name}: schema 缺 {token}")


if __name__ == "__main__":
    unittest.main()
