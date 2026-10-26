"""log_kit：内存=活体、磁盘=死档；以及由此分出的两条日志端点。

钉住这几件事（都是本轮设计里明确选的取舍，后续改动不该悄悄破坏）：

- **一次写入三处落地**：原 stream（终端）、内存环形缓冲、磁盘文件；
- **磁盘跨运行只追加、绝不截断**——进程死后它是唯一证据，重启不许销毁上一世；
- **轮转有上限**：文件数 ≤ 1 + BACKUPS，最旧的整段挤出；
- **`read_run_tail` 按运行分隔行截取**：绝不把上一个运行的尾巴冒充本次崩溃原因；
- **内存的 seq/gap 语义**：被挤掉的那段要诚实报 gap + 回整份 snapshot，不静默丢帧；
- **端点分工**：活体读模块自己的 `/logs`（内存），死后读 vedal 的 `/manage/logs/<模块>`（磁盘），
  且后者不能被通用代理 `/manage/{module}/{path}` 抢走。

不打真实端口、不起服务（TestClient 只做 ASGI 内存调用）。
"""

import asyncio
import io
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from neuro_simulator import log_kit
from neuro_simulator.vedal import server as vedal_server


class _FakeTerminal(io.StringIO):
    """替身终端：带真实 stream 的能力（tee 必须把这些原样代理出去）。"""

    def fileno(self) -> int:
        return 42

    def isatty(self) -> bool:
        return True


class _AttachedMixin:
    """把 stdout/stderr 换成替身终端后 attach 到临时 workdir，用完还原。"""

    def _start_tmp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()  # type: ignore[attr-defined]
        self.workdir = Path(self.tmp.name)  # type: ignore[attr-defined]
        self.addCleanup(self.tmp.cleanup)  # type: ignore[attr-defined]
        self._saved = (sys.stdout, sys.stderr)
        self._term = _FakeTerminal()  # type: ignore[attr-defined]
        sys.stdout = self._term
        sys.stderr = self._term

    def _stop_tmp(self) -> None:
        for module in list(log_kit._attached):
            log_kit.detach(module)
        sys.stdout, sys.stderr = self._saved  # type: ignore[attr-defined]

    @property
    def terminal(self) -> str:
        return self._term.getvalue()  # type: ignore[attr-defined]


class TestTee(unittest.TestCase, _AttachedMixin):
    def setUp(self) -> None:
        self._start_tmp()
        self.addCleanup(self._stop_tmp)

    def test_one_write_reaches_terminal_memory_and_disk(self) -> None:
        log = log_kit.attach(self.workdir, "demo", memory_bytes=10_000, max_bytes=10_000)
        self.assertIsInstance(log, log_kit.ProcessLog)
        print("hello-tee")
        log.disk.flush()

        self.assertIn("hello-tee", self.terminal)  # 终端没被吞
        self.assertIn("hello-tee", log.snapshot(100_000)["content"])  # 内存
        disk_text = (self.workdir / "logs" / "demo.log").read_text(encoding="utf-8")
        self.assertIn("hello-tee", disk_text)  # 磁盘
        self.assertIn(log_kit.RUN_MARKER, disk_text)  # 运行分隔行

    def test_attach_twice_does_not_double_write(self) -> None:
        first = log_kit.attach(self.workdir, "demo", memory_bytes=10_000, max_bytes=10_000)
        self.assertIs(first, log_kit.attach(self.workdir, "demo"))
        print("once")
        self.assertEqual(self.terminal.count("once"), 1)
        self.assertEqual(first.snapshot(100_000)["content"].count("once"), 1)

    def test_tee_delegates_stream_capabilities(self) -> None:
        """isatty/fileno 必须代理给原 stream：uvicorn 靠它们决定要不要上色。"""
        log_kit.attach(self.workdir, "demo")
        self.assertTrue(sys.stdout.isatty())
        self.assertEqual(sys.stdout.fileno(), 42)

    def test_ansi_escapes_are_kept_verbatim(self) -> None:
        """色码是终端输出的一部分（neuro 用它区分 reasoning/工具），不许剥。"""
        log = log_kit.attach(self.workdir, "demo")
        sys.stdout.write("\x1b[35mthinking\x1b[0m\n")
        log.disk.flush()
        raw = (self.workdir / "logs" / "demo.log").read_text(encoding="utf-8")
        self.assertIn("\x1b[35mthinking\x1b[0m", raw)
        self.assertIn("\x1b[35mthinking\x1b[0m", log.snapshot(100_000)["content"])

    def test_attach_returns_none_when_disk_is_unwritable(self) -> None:
        """写不了日志不是停止服务的理由：告警 + None，绝不抛异常打死进程。"""
        (self.workdir / "logs").mkdir()
        (self.workdir / "logs" / "blocked.log").write_text("占位：父目录是文件\n")
        # 让 log_path 的父目录变成一个文件，open 必然 OSError
        blocker = self.workdir / "logs" / "blocked.log" / "x"
        with patch.object(log_kit, "log_path", lambda _w, _m: blocker):
            self.assertIsNone(log_kit.attach(self.workdir, "blocked"))
        self.assertIn("WARNING", self.terminal)


class TestApplyLevels(unittest.TestCase):
    """`apply_levels` 动的是进程级 logging——本类**受控地**碰全局状态。

    隔离方式：setUp 快照 root 的 level/handlers、五个 uvicorn/httpx logger 的 level
    与 `log_kit._root_handler_installed`；addCleanup 原样恢复。这样既验证了真实机制
    （root handler/等级），又不会把 handler 或等级泄漏给后面的测试。
    """

    _LOGGERS = (
        "uvicorn.access",
        "uvicorn.error",
        "uvicorn",
        "uvicorn.asgi",
        "httpx",
        "httpcore",
    )

    def setUp(self) -> None:
        import logging

        root = logging.getLogger()
        self._saved_stdout = sys.stdout
        self._saved_root_level = root.level
        self._saved_root_handlers = list(root.handlers)
        self._saved_installed = log_kit._root_handler_installed
        self._saved_logger_levels = {
            name: logging.getLogger(name).level for name in self._LOGGERS
        }
        self.out = io.StringIO()
        sys.stdout = self.out
        self.addCleanup(self._restore_logging)

    def _restore_logging(self) -> None:
        import logging

        sys.stdout = self._saved_stdout
        root = logging.getLogger()
        root.handlers[:] = self._saved_root_handlers
        root.setLevel(self._saved_root_level)
        for name, level in self._saved_logger_levels.items():
            logging.getLogger(name).setLevel(level)
        log_kit._root_handler_installed = self._saved_installed

    def test_level_matrix(self) -> None:
        import logging

        for level in ("debug", "info", "warning", "error"):
            log_kit.apply_levels(level)
            numeric = getattr(logging, level.upper())
            self.assertEqual(logging.getLogger().level, numeric)
            for name in ("uvicorn.error", "uvicorn", "uvicorn.asgi"):
                self.assertEqual(logging.getLogger(name).level, numeric, level)
            noisy = logging.DEBUG if level == "debug" else logging.WARNING
            for name in ("uvicorn.access", "httpx", "httpcore"):
                self.assertEqual(logging.getLogger(name).level, noisy, level)

    def test_handler_is_installed_once_even_across_reloads(self) -> None:
        import logging

        before = len(logging.getLogger().handlers)
        log_kit.apply_levels("info")
        after_first = len(logging.getLogger().handlers)
        log_kit.apply_levels("debug")
        log_kit.apply_levels("warning")
        self.assertEqual(after_first, before + 1)
        self.assertEqual(len(logging.getLogger().handlers), after_first)

    def test_root_handler_writes_levelname_prefix_to_current_stdout(self) -> None:
        import logging

        log_kit.apply_levels("info")
        logging.getLogger("ns-log-kit-level-probe").warning("probe-message")
        self.assertIn("WARNING: probe-message", self.out.getvalue())

    def test_resolve_level_falls_back_for_missing_and_invalid_values(self) -> None:
        self.assertEqual(log_kit.resolve_level({"log_level": "debug"}), "debug")
        self.assertEqual(log_kit.resolve_level({}), "info")
        self.assertEqual(log_kit.resolve_level({"log_level": "trace"}), "info")
        self.assertEqual(log_kit.resolve_level({"log_level": ""}), "info")
        self.assertEqual(log_kit.resolve_level({"log_level": 3}), "info")
        self.assertEqual(log_kit.resolve_level(None), "info")  # type: ignore[arg-type]


class TestLoggingOrdering(unittest.TestCase, _AttachedMixin):
    """**attach 必须先于 logging 配置**：uvicorn 的 handler 在 dictConfig 时绑定
    `ext://sys.stdout`，取的是"当时"的 sys.stdout。若 main() 里把 attach 放到
    uvicorn.run() 之后，handler 早就绑住了裸流，日志三处一处都不会有——而且
    服务看起来完全正常，只是 logs 页空着。这条测试钉住那个时机。
    """

    def setUp(self) -> None:
        self._start_tmp()
        self.addCleanup(self._stop_tmp)

    def test_handlers_created_after_attach_still_reach_all_three_places(self) -> None:
        """本测试只建本地 logger，不 dictConfig（那会 disable_existing_loggers，毒掉
        别的应用的 `assertLogs`）。全局 logging 的唯一受控触碰在 `TestApplyLevels`，
        那里用 setUp 快照 + addCleanup 原样恢复。
        """
        import logging

        log = log_kit.attach(self.workdir, "order", memory_bytes=100_000, max_bytes=100_000)

        handler = logging.StreamHandler(sys.stdout)  # 关键：绑到当前 sys.stdout（已是 tee）
        name = "ns-log-kit-order-probe"
        probe = logging.getLogger(name)
        probe.handlers = [handler]
        probe.propagate = False
        self.addCleanup(probe.handlers.clear)

        probe.warning("uvicorn-style record")

        memory = log.snapshot(100_000)["content"]
        log_kit.detach("order")
        disk = (self.workdir / "logs" / "order.log").read_text(encoding="utf-8")

        self.assertIn("uvicorn-style record", self.terminal)
        self.assertIn("uvicorn-style record", memory)
        self.assertIn("uvicorn-style record", disk)


class TestDiskIsDeadManArchive(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def _sink(self, **kw) -> log_kit.DiskSink:
        return log_kit.DiskSink(log_kit.log_path(self.workdir, "demo"), **kw)

    def test_restart_appends_instead_of_truncating(self) -> None:
        """**证据不能被销毁**：重启把上一世的记录清空是错的（本轮推翻旧设计的核心）。"""
        first = self._sink(max_bytes=10_000, backups=2)
        first.write("run-one-crash-reason\n")
        first.close()

        second = self._sink(max_bytes=10_000, backups=2)
        second.write("run-two\n")
        second.close()

        text = (self.workdir / "logs" / "demo.log").read_text(encoding="utf-8")
        self.assertIn("run-one-crash-reason", text)
        self.assertIn("run-two", text)

    def test_rotation_bounds_file_count_and_keeps_newest(self) -> None:
        sink = self._sink(max_bytes=200, backups=2)
        for i in range(80):
            sink.write(f"line-{i}\n")
        sink.close()

        logs = self.workdir / "logs"
        self.assertEqual(
            sorted(p.name for p in logs.glob("demo.log*")),
            ["demo.log", "demo.log.1", "demo.log.2"],
        )
        self.assertIn("line-79", (logs / "demo.log").read_text(encoding="utf-8"))
        # 当前文件不会因为"裁尾"而出现半行：轮转只在写满时换文件
        self.assertTrue((logs / "demo.log").read_text(encoding="utf-8").endswith("\n"))

    def test_disk_status_lists_chain_with_total(self) -> None:
        sink = self._sink(max_bytes=200, backups=2)
        for i in range(80):
            sink.write(f"line-{i}\n")
        sink.close()

        status = log_kit.disk_status(log_kit.log_path(self.workdir, "demo"))
        self.assertTrue(status["exists"])
        self.assertEqual(status["files"][0]["name"], "demo.log")
        self.assertTrue(status["files"][0]["current"])
        self.assertEqual(status["size"], sum(f["size"] for f in status["files"]))
        self.assertEqual(status["limit"], log_kit.MAX_BYTES * (1 + log_kit.BACKUPS))

    def test_write_failure_degrades_instead_of_raising(self) -> None:
        class _Broken:
            def write(self, _data):
                raise OSError("disk gone")

            def flush(self):
                pass

            def close(self):
                pass

        sink = self._sink(max_bytes=10_000, backups=1)
        self.addCleanup(sink.close)
        sink._fh.close()  # 先关掉真文件句柄再换成坏的一份，别漏 fd
        sink._fh = _Broken()  # type: ignore[assignment]
        sink.write("boom\n")
        self.assertIsNotNone(sink.broken)
        sink.write("still-alive\n")  # 第二次也不抛：日志坏了要继续活着


class TestMemoryRing(unittest.TestCase):
    def test_evicts_oldest_and_accounts_drops(self) -> None:
        mem = log_kit.MemoryLog(max_bytes=300)
        for i in range(60):
            mem.append(f"line-{i}\n")
        snap = mem.snapshot(10_000)
        self.assertLessEqual(snap["retained_bytes"], 300)
        self.assertIn("line-59", snap["content"])
        self.assertNotIn("line-0\n", snap["content"])
        self.assertGreater(snap["dropped_bytes"], 0)

    def test_snapshot_window_is_bounded_by_requested_bytes(self) -> None:
        mem = log_kit.MemoryLog(max_bytes=10_000)
        for i in range(100):
            mem.append(f"y{i}\n")
        small = mem.snapshot(50)
        self.assertTrue(small["truncated"])
        self.assertLessEqual(small["bytes"], 50)
        self.assertIn("y99", small["content"])

    def test_since_returns_increment_and_reports_gap(self) -> None:
        mem = log_kit.MemoryLog(max_bytes=200)
        for i in range(10):
            mem.append(f"a{i}\n")
        head = mem.last_seq
        mem.append("newest\n")
        result = mem.since(head)
        self.assertFalse(result["gap"])
        self.assertEqual([f["data"] for f in result["frames"]], ["newest\n"])

        for i in range(40):
            mem.append(f"{'x' * 20}{i}\n")  # 每块 22 字节，40 块必然挤掉前面的 a*
        gone = mem.since(1)
        self.assertTrue(gone["gap"], "被挤掉的段必须报 gap，不能假装没有")
        self.assertGreater(gone["first_seq"], 2)

    def test_clamp_bytes_keeps_window_inside_the_ring(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        sink = log_kit.DiskSink(log_kit.log_path(Path(tmp.name), "demo"))
        self.addCleanup(sink.close)
        log = log_kit.ProcessLog(
            module="demo", path=sink.path, memory=log_kit.MemoryLog(max_bytes=4096), disk=sink
        )
        self.assertEqual(log_kit.clamp_bytes(log, 10_000_000), 4096)
        self.assertEqual(log_kit.clamp_bytes(log, 0), 1)
        # 非数字退回默认窗口，再夹到环形缓冲大小
        self.assertEqual(log_kit.clamp_bytes(log, "abc"), 4096)


class TestRunTail(unittest.TestCase):
    """启动失败 detail：只准说本次运行的事。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.path = log_kit.log_path(self.workdir, "stream")
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _marker(self, pid: int) -> str:
        return f"{log_kit.RUN_MARKER} module=stream pid={pid} at=2026-10-24T00:00:00+08:00\n"

    def _write(self, text: str) -> None:
        self.path.write_text(text, encoding="utf-8")

    def test_only_lines_after_this_runs_marker(self) -> None:
        self._write(
            "PREVIOUS-RUN-NOISE\n"
            + self._marker(111)
            + "old failure\n"
            + self._marker(222)
            + "boom: config broken\n"
        )
        tail = log_kit.read_run_tail(self.workdir, "stream", 222)
        self.assertIn("boom: config broken", tail)
        self.assertNotIn("PREVIOUS-RUN-NOISE", tail)
        self.assertNotIn("old failure", tail)

    def test_missing_marker_is_stated_not_disguised(self) -> None:
        self._write("ONLY-OLD-RUN\n")
        self.assertIn("未找到", log_kit.read_run_tail(self.workdir, "stream", 999))

    def test_empty_file_says_so(self) -> None:
        self._write("")
        self.assertIn("日志为空", log_kit.read_run_tail(self.workdir, "stream", 999))

    def test_run_with_marker_but_no_output(self) -> None:
        self._write(self._marker(7))
        self.assertIn("未输出任何内容", log_kit.read_run_tail(self.workdir, "stream", 7))


class TestFollowFrames(unittest.TestCase, _AttachedMixin):
    def setUp(self) -> None:
        self._start_tmp()
        self.addCleanup(self._stop_tmp)
        self.log = log_kit.attach(self.workdir, "demo", memory_bytes=400, max_bytes=100_000)

    def test_first_connect_gets_full_snapshot(self) -> None:
        frames, seq = log_kit.follow_frames(self.log, -1)
        self.assertEqual(len(frames), 1)
        self.assertTrue(frames[0].startswith("event: snapshot\ndata: "))
        self.assertEqual(seq, self.log.memory.last_seq)

    def test_increment_frames_only_new_text(self) -> None:
        _, seq = log_kit.follow_frames(self.log, -1)
        print("after-connect")
        frames, seq2 = log_kit.follow_frames(self.log, seq)
        self.assertTrue(all(f.startswith("event: log") for f in frames))
        self.assertIn("after-connect", "".join(frames))
        self.assertGreater(seq2, seq)

    def test_gap_is_followed_by_a_fresh_snapshot(self) -> None:
        for _ in range(80):
            print("flood")
        frames, _ = log_kit.follow_frames(self.log, 1)
        self.assertEqual([f.split("\n", 1)[0] for f in frames], ["event: gap", "event: snapshot"])

    def test_sse_stream_pings_when_idle(self) -> None:
        async def pull() -> list[str]:
            stream = log_kit.sse_stream(self.log, poll_s=0.01, keepalive_s=0.02)
            out = [await stream.__anext__(), await stream.__anext__()]
            await stream.aclose()
            return out

        frames = asyncio.run(pull())
        self.assertTrue(frames[0].startswith("event: snapshot"))
        self.assertTrue(frames[1].startswith(": ping"), frames[1])


class TestVedalEndpoints(unittest.TestCase, _AttachedMixin):
    def setUp(self) -> None:
        self._start_tmp()
        self.addCleanup(self._stop_tmp)
        patcher = patch.object(
            vedal_server,
            "get_config",
            lambda: SimpleNamespace(
                WORKDIR=self.workdir,
                data={"server": {}, "vedal": {}, "stream": {}, "neuro_sama": {}},
            ),
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(vedal_server.create_app())

    def test_vedal_own_logs_reads_its_memory(self) -> None:
        log = log_kit.attach(self.workdir, "vedal")
        print("vedal-live-line")
        data = self.client.get("/manage/vedal/logs").json()
        self.assertEqual(data["module"], "vedal")
        self.assertEqual(data["source"], "memory")
        self.assertIn("vedal-live-line", data["content"])
        self.assertEqual(data["pid"], log.pid)

    def test_vedal_own_logs_503_when_not_attached(self) -> None:
        resp = self.client.get("/manage/vedal/logs")
        self.assertEqual(resp.status_code, 503)
        self.assertIn("日志未启用", resp.json()["detail"])

    def test_disk_endpoint_reads_file_and_is_not_swallowed_by_proxy(self) -> None:
        """`/manage/logs/<模块>` 必须命中磁盘路由，不能被 `/manage/{module}/{path}` 抢走。

        被抢走的表征是 502（代理去连一个没在跑的模块），所以这里断言 200 + source=disk。
        """
        sink = log_kit.DiskSink(
            log_kit.log_path(self.workdir, "stream"), max_bytes=10_000, backups=2
        )
        sink.write("dead-process-output\n")
        sink.close()

        resp = self.client.get("/manage/logs/stream")
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertEqual(data["source"], "disk")
        self.assertIn("dead-process-output", data["content"])
        self.assertFalse(data["running"], "临时 workdir 里没有 stream.pid，不该报成在跑")
        self.assertEqual(data["disk"]["files"][0]["name"], "stream.log")

    def test_disk_endpoint_on_missing_file_is_empty_not_error(self) -> None:
        data = self.client.get("/manage/logs/neuro_sama").json()
        self.assertEqual(data["source"], "disk")
        self.assertFalse(data["disk"]["exists"])
        self.assertEqual(data["content"], "")

    def test_disk_endpoint_rejects_unknown_module(self) -> None:
        self.assertEqual(self.client.get("/manage/logs/nope").status_code, 404)

    def test_disk_endpoint_window_is_bounded(self) -> None:
        """窗口两处夹：请求的小窗口要报 truncated，超过磁盘读取上限的要被夹住。"""
        sink = log_kit.DiskSink(
            log_kit.log_path(self.workdir, "stream"), max_bytes=10_000_000, backups=0
        )
        for i in range(3000):
            sink.write(f"long-{i}\n")
        sink.write("x" * (vedal_server.DISK_READ_MAX_BYTES + 1024) + "\n")
        sink.close()

        small = self.client.get("/manage/logs/stream?max_bytes=1000").json()
        self.assertTrue(small["truncated"])
        self.assertLessEqual(len(small["content"].encode("utf-8")), 1000)

        huge = self.client.get("/manage/logs/stream?max_bytes=999999999").json()
        self.assertTrue(huge["truncated"], "超大窗口必须被夹住并明说还有没给的")
        self.assertLessEqual(len(huge["content"].encode("utf-8")), vedal_server.DISK_READ_MAX_BYTES)


class TestModuleEndpoints(unittest.TestCase, _AttachedMixin):
    """模块自己的 `/logs`：读内存、只含本次运行。"""

    def setUp(self) -> None:
        self._start_tmp()
        self.addCleanup(self._stop_tmp)

    def test_stream_logs_endpoint_reads_memory_not_disk(self) -> None:
        from neuro_simulator.stream import server as stream_server

        log_kit.attach(self.workdir, "stream")
        print("stream-live-line")
        # 磁盘上写一段"上一次运行"的内容：内存端点绝不能读到它
        sink = log_kit.DiskSink(log_kit.log_path(self.workdir, "stream"), backups=1)
        sink.write("previous-run\n")
        sink.close()

        data = TestClient(stream_server.create_app()).get("/logs").json()
        self.assertEqual(data["source"], "memory")
        self.assertIn("stream-live-line", data["content"])
        self.assertNotIn("previous-run", data["content"])
        self.assertEqual(data["module"], "stream")

    def test_stream_logs_endpoint_503_when_not_attached(self) -> None:
        from neuro_simulator.stream import server as stream_server

        resp = TestClient(stream_server.create_app()).get("/logs")
        self.assertEqual(resp.status_code, 503)

    def test_neuro_logs_endpoint_reads_memory(self) -> None:
        from neuro_simulator.neuro_sama import server as ns_server

        log = log_kit.attach(self.workdir, "neuro_sama")
        sys.stdout.write("\x1b[33m[tool] game_chess_start\x1b[0m\n")
        data = TestClient(ns_server.create_app()).get("/logs").json()
        self.assertEqual(data["source"], "memory")
        self.assertIn("\x1b[33m[tool] game_chess_start\x1b[0m", data["content"])
        self.assertEqual(data["memory_limit"], log.memory.max_bytes)

    def test_neuro_logs_window_is_clamped(self) -> None:
        from neuro_simulator.neuro_sama import server as ns_server

        log_kit.attach(self.workdir, "neuro_sama", memory_bytes=1000)
        data = TestClient(ns_server.create_app()).get("/logs?max_bytes=999999").json()
        self.assertLessEqual(data["retained_bytes"], 1000)
        self.assertEqual(data["memory_limit"], 1000)


if __name__ == "__main__":
    unittest.main()
