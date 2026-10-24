"""前端日志页的 URL 必须真的存在，且没被通用代理抢走。

这一族测试不做 UI 断言，只守**契约**：dashboard 里写死的日志 URL 形状、以及 vedal
路由表里这些路径的**注册顺序**。两类漂移都会让它失败：

- 后端改路径名（`/manage/logs/<模块>` 改名、`/logs` 挪走）→ 前端页面静默 404/502，
  用户只看到"读不到日志"，看不出是谁改的；
- 日志路由被移到通用代理 `/manage/{module}/{path:path}` **之后** → FastAPI 按注册顺序
  匹配，`/manage/logs/stream` 会被代理当成"模块 logs、路径 stream"处理，于是变成 502，
  而且看起来像是"模块挂了"而不是"路由写错了"。
"""

import re
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from neuro_simulator.vedal import server as vedal_server

DASHBOARD = Path(__file__).resolve().parents[1] / "dashboard" / "src"


class TestFrontendUrls(unittest.TestCase):
    def test_composable_uses_the_two_documented_shapes(self) -> None:
        source = (DASHBOARD / "composables" / "useModuleLogs.ts").read_text(encoding="utf-8")

        # 活体：模块自己的内存端点（vedal 代理到 /logs；vedal 自己注册了同形路径）
        self.assertRegex(source, r"/manage/\$\{module\}/logs/events")
        # 死档：vedal 读磁盘
        self.assertRegex(source, r"/manage/logs/\$\{module\}")

        # 这两个形状必须在 types/logs.ts 的注释里也有对应说明（改端点时两边一起动）
        types = (DASHBOARD / "types" / "logs.ts").read_text(encoding="utf-8")
        self.assertIn("/manage/<module>/logs", types)
        self.assertIn("/manage/logs/<module>", types)

    def test_no_log_url_uses_a_query_param_as_source_switch(self) -> None:
        """一条路径一个语义：不许用 `?source=memory|disk` 在一个端点上分两种行为。"""
        source = (DASHBOARD / "composables" / "useModuleLogs.ts").read_text(encoding="utf-8")
        self.assertNotIn("source=", source)

    def test_run_boundary_regex_matches_what_log_kit_writes(self) -> None:
        """磁盘模式的运行边界分隔线：前端正则必须认得出后端真正写下的那一行。

        磁盘日志跨运行追加（绝不启动截断），边界线是唯一能说明"下面是另一个
        进程"的东西。后端改格式而前端没跟着改的话，边界会静默消失——用户会
        把上一个进程的 traceback 当成本次运行的原因，这比没有日志更误导。
        """
        from neuro_simulator import log_kit

        viewer = (DASHBOARD / "components" / "logs" / "LogViewer.vue").read_text(
            encoding="utf-8"
        )
        match = re.search(r"const RUN_START = /(.+?)/(?=\s*$)", viewer, re.MULTILINE)
        self.assertIsNotNone(match, "LogViewer.vue 里找不到 RUN_START 正则")
        pattern = re.compile(match.group(1))

        # 与 log_kit 里 attach() 实际拼出来的行逐字一致
        line = (
            f"{log_kit.RUN_MARKER} module=stream pid=2214035 "
            "at=2026-10-24T15:58:30+08:00"
        )
        self.assertIsNotNone(pattern.match(line), f"前端正则认不出运行分隔行: {line}")
        # 普通日志行不能被误认成分隔线
        self.assertIsNone(pattern.match("INFO:     Started server process [2145625]"))
        self.assertIsNone(pattern.match("[stream] ready"))


class TestVedalRoutes(unittest.TestCase):
    def setUp(self) -> None:
        self.app = vedal_server.create_app()
        self.paths = [
            getattr(route, "path", "")
            for route in self.app.routes
            if getattr(route, "path", "")
        ]

    def _index(self, path: str) -> int:
        self.assertIn(path, self.paths, f"路由 {path} 不存在")
        return self.paths.index(path)

    def test_log_routes_are_registered_before_the_generic_proxy(self) -> None:
        proxy = self._index("/manage/{module}/{path:path}")
        for path in (
            "/manage/vedal/logs",
            "/manage/vedal/logs/events",
            "/manage/logs/{module_name}",
        ):
            self.assertLess(
                self._index(path),
                proxy,
                f"{path} 注册在通用代理之后，会被 /manage/{{module}}/{{path}} 抢走",
            )

    def test_vedal_registers_its_own_memory_routes_in_the_proxied_shape(self) -> None:
        """前端一套 URL 模式吃三个模块：vedal 的路径形状必须与代理出去的一致。"""
        self.assertIn("/manage/vedal/logs", self.paths)
        self.assertIn("/manage/vedal/logs/events", self.paths)
        # 磁盘命名空间只按模块给一份快照，没有 SSE（实时只在模块自己那条路上）
        self.assertEqual([p for p in self.paths if p.startswith("/manage/logs/")], [
            "/manage/logs/{module_name}"
        ])

    def test_modules_own_app_exposes_logs(self) -> None:
        """两个模块各自的 app 也得有 `/logs` 与 `/logs/events`，代理才有东西可转。"""
        from neuro_simulator.neuro_sama import server as ns_server
        from neuro_simulator.stream import server as stream_server

        for create in (ns_server.create_app, stream_server.create_app):
            paths = [getattr(r, "path", "") for r in create().routes]
            self.assertIn("/logs", paths)
            self.assertIn("/logs/events", paths)

    def test_disk_route_answers_instead_of_the_proxy(self) -> None:
        """端到端钉住顺序的效果：磁盘路由真的返回 source=disk，而不是代理的 502。"""
        import tempfile
        from types import SimpleNamespace
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            sink = log_kit_disk_sink(workdir)
            sink.write("route-order-check\n")
            sink.close()
            with patch.object(
                vedal_server,
                "get_config",
                lambda: SimpleNamespace(
                    WORKDIR=workdir, data={"server": {}, "vedal": {}, "stream": {}}
                ),
            ):
                body = TestClient(self.app).get("/manage/logs/stream").json()
        self.assertEqual(body["source"], "disk")
        self.assertIn("route-order-check", body["content"])


def log_kit_disk_sink(workdir: Path):
    from neuro_simulator import log_kit

    return log_kit.DiskSink(log_kit.log_path(workdir, "stream"), max_bytes=10_000, backups=1)


if __name__ == "__main__":
    unittest.main()
