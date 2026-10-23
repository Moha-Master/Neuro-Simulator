"""StateBoard 机制：形状、rev、want/drop、降级，以及"provider 必须同步"的纪律。"""

import unittest

from neuro_simulator.state_kit import StateBoard


class TestRegister(unittest.TestCase):
    def test_rejects_empty_and_duplicate_names(self):
        board = StateBoard("m")
        with self.assertRaises(ValueError):
            board.register("", lambda: {})
        board.register("a", lambda: {"x": 1})
        with self.assertRaises(ValueError):
            board.register("a", lambda: {})

    def test_rejects_coroutine_provider(self):
        board = StateBoard("m")

        async def provider():
            return {}

        with self.assertRaises(TypeError):
            board.register("a", provider)

    def test_rejects_bad_graph(self):
        board = StateBoard("m")
        with self.assertRaises(TypeError):
            board.register("a", lambda: {}, graph=[])  # type: ignore[arg-type]


class TestRead(unittest.TestCase):
    def setUp(self) -> None:
        self.board = StateBoard("demo")
        self.board.register("one", lambda: {"v": 1})
        self.board.register("two", lambda: {"v": 2}, graph={"states": [], "transitions": []})

    def test_envelope_and_section_shape(self):
        out = self.board.read()
        self.assertEqual(out["module"], "demo")
        self.assertEqual(out["rev"], 0)
        self.assertTrue(out["ts"])
        self.assertEqual(out["sections"]["one"], {"current": {"v": 1}})
        self.assertIn("graph", out["sections"]["two"])
        self.assertNotIn("graph", out["sections"]["one"])

    def test_note_change_bumps_rev(self):
        self.board.note_change("one")
        self.assertEqual(self.board.read()["rev"], 1)
        self.board.note_change("one", "two")
        self.assertEqual(self.board.read()["rev"], 2)

    def test_want_drop_and_star(self):
        self.assertEqual(list(self.board.read(want=["two"])["sections"]), ["two"])
        self.assertEqual(list(self.board.read(drop=["two"])["sections"]), ["one"])
        self.assertEqual(list(self.board.read(want=["*"], drop=["one"])["sections"]), ["two"])
        # 字符串形式（query 直接传 "one,two"）也接受
        self.assertEqual(list(self.board.read(want="one,two")["sections"]), ["one", "two"])
        self.assertEqual(self.board.read(drop="*")["sections"], {})
        # 未知分区忽略，不报错
        self.assertEqual(list(self.board.read(want=["nope"])["sections"]), [])

    def test_provider_failure_degrades_only_that_section(self):
        board = StateBoard("demo")

        def boom():
            raise RuntimeError("数据坏了")

        board.register("bad", boom)
        board.register("good", lambda: {"ok": True})
        out = board.read()
        self.assertEqual(out["sections"]["bad"]["current"], None)
        self.assertIn("RuntimeError: 数据坏了", out["sections"]["bad"]["error"])
        self.assertNotIn("graph", out["sections"]["bad"])
        self.assertEqual(out["sections"]["good"], {"current": {"ok": True}})


if __name__ == "__main__":
    unittest.main()
