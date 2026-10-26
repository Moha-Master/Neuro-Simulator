"""`validate_config`：每模块 log_level 的取值域（缺省合法，显式非法 400）。"""

import copy
import json
import unittest
from pathlib import Path

from neuro_simulator import config as config_module
from neuro_simulator.config import validate_config

EXAMPLE = Path(config_module.__file__).with_name("config.json.example")


class TestLogLevelValidation(unittest.TestCase):
    def setUp(self) -> None:
        self.base = json.loads(EXAMPLE.read_text(encoding="utf-8"))

    def _doc(self) -> dict:
        return copy.deepcopy(self.base)

    def test_all_legal_levels_are_accepted_per_module(self) -> None:
        for schema in ("neuro_sama", "stream", "vedal"):
            for level in ("debug", "info", "warning", "error"):
                doc = self._doc()
                doc[schema]["log_level"] = level
                validate_config(doc)  # 不抛即通过

    def test_missing_level_is_legal(self) -> None:
        doc = self._doc()
        for schema in ("neuro_sama", "stream", "vedal"):
            doc[schema].pop("log_level", None)
        validate_config(doc)

    def test_invalid_and_trace_levels_are_rejected(self) -> None:
        for bad in ("verbose", "trace", "", 3, None):
            doc = self._doc()
            doc["stream"]["log_level"] = bad
            with self.assertRaises(ValueError) as ctx:
                validate_config(doc)
            self.assertIn("stream.log_level", str(ctx.exception))

    def test_server_schema_level_is_not_part_of_the_contract(self) -> None:
        # log_level 只放模块 schema；server 上多一个键不影响校验（也永远不会被读）。
        doc = self._doc()
        doc["server"]["log_level"] = "trace"
        validate_config(doc)


if __name__ == "__main__":
    unittest.main()
