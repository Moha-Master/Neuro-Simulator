"""PID 文件的清理必须真的发生——包括被 SIGTERM 停掉的时候。

背景（2026-10-24 实机发现）：三个入口都写着

    try:
        uvicorn.run(app, ...)
    finally:
        pidfile.remove_pid_file(...)

而 `finally` **在信号路径上从不执行**：uvicorn 优雅关闭后会在 `capture_signals`
的退出阶段 `signal.raise_signal(captured_signal)`，把捕获到的 SIGTERM 重新发给
自己（有意恢复"信号原本该有的默认行为"），进程当场死在 `finally` 之前。
strace 证据：`--- SIGTERM` 后紧跟 `+++ killed by SIGTERM +++`，pid 文件每次都残留。

后果不只是文件系统上多个壳：残留文件里写着死 pid，而 pid 随时可能被系统复用给
**另一个无关进程**，那时防双开就会报"已有实例在运行"，模块再也起不来。

清理因此挂在 **lifespan shutdown**（它在那个 raise_signal 之前执行），机制在
`pidfile.install_cleanup_on_shutdown`。这里既测机制（TestClient 跑 lifespan），
也测真实进程（真的 SIGTERM 一个 vedal），后者才是能抓到原 bug 的那一条。
"""

import json
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from neuro_simulator import pidfile


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class TestInstallCleanupOnShutdown(unittest.TestCase):
    def test_pid_file_removed_when_lifespan_shuts_down(self) -> None:
        from contextlib import asynccontextmanager

        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            seen: list[str] = []

            @asynccontextmanager
            async def inner(app):
                seen.append("startup")
                try:
                    yield
                finally:
                    seen.append("shutdown")

            app = FastAPI(lifespan=inner)
            pidfile.install_cleanup_on_shutdown(app, workdir, "demo")
            path = workdir / "demo.pid"
            path.write_text("424242\n", encoding="utf-8")

            with TestClient(app):
                self.assertTrue(path.exists(), "lifespan startup 阶段不该动 pid 文件")
            self.assertFalse(path.exists(), "lifespan shutdown 必须删掉 pid 文件")
            # 原有的 lifespan 语义不能被替换掉（模块的启动/关闭逻辑还在里面跑）
            self.assertEqual(seen, ["startup", "shutdown"])

    def test_shutdown_still_runs_when_startup_fails(self) -> None:
        """原 lifespan 的 startup 抛错时也要清理——否则半启动的残留最难排查。"""
        from contextlib import asynccontextmanager

        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)

            @asynccontextmanager
            async def inner(app):
                raise RuntimeError("startup boom")
                yield  # pragma: no cover

            app = FastAPI(lifespan=inner)
            pidfile.install_cleanup_on_shutdown(app, workdir, "demo")
            path = workdir / "demo.pid"
            path.write_text("424242\n", encoding="utf-8")

            with self.assertRaises(RuntimeError):
                with TestClient(app):
                    pass  # pragma: no cover
            self.assertFalse(path.exists())


class TestRealProcessPidCleanup(unittest.TestCase):
    """真的把 vedal 起起来、SIGTERM 掉，pid 文件必须消失。

    这条才是能抓到原 bug 的用例：TestClient 跑的是 lifespan，绕过了 uvicorn
    那段"把信号重发给自己"的代码，而那个动作正是 `finally` 失效的原因。
    """

    MODULE = "vedal"
    ENTRY = "-m"
    MODULE_PATH = "neuro_simulator.vedal.main"

    def test_sigterm_removes_pid_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            example = Path(__file__).resolve().parents[1] / "server" / "neuro_simulator" / "config.json.example"
            shutil.copy(example, workdir / "config.json")

            config_path = workdir / "config.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config.setdefault(self.MODULE, {})
            config[self.MODULE]["host"] = "127.0.0.1"
            config[self.MODULE]["port"] = _free_port()
            config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

            log_path = workdir / "child.log"
            with log_path.open("wb") as log_f:
                proc = subprocess.Popen(
                    [sys.executable, self.ENTRY, self.MODULE_PATH, "--dir", str(workdir)],
                    stdout=log_f,
                    stderr=subprocess.STDOUT,
                )
                try:
                    pid_path = workdir / f"{self.MODULE}.pid"
                    self._await_ready(proc, config[self.MODULE]["port"], log_path)
                    self.assertTrue(pid_path.exists(), "端口就绪后 pid 文件应该已经在了")

                    proc.terminate()  # SIGTERM = vedal 的正常停止方式（module_stop）
                    proc.wait(timeout=30)
                finally:
                    if proc.poll() is None:
                        proc.kill()
                        proc.wait(timeout=10)

            self.assertFalse(
                pid_path.exists(),
                "SIGTERM 后 pid 文件残留：清理是不是又被挪回 uvicorn.run 的 finally 里了？"
                "（那个 finally 在信号路径上永远不会执行，见 pidfile.install_cleanup_on_shutdown）",
            )

    def _await_ready(self, proc: "subprocess.Popen[bytes]", port: int, log_path: Path) -> None:
        """等 **端口就绪**，不是等 pid 文件。

        pid 文件在 uvicorn.run 之前就写好了；那时 lifespan 还没启动，
        此刻发 SIGTERM 会落在启动途中（uvicorn 直接放弃 lifespan），测的就不是
        "正常停止"了。/health 200 才说明服务真的起来了。
        """
        from urllib.error import URLError
        from urllib.request import urlopen

        url = f"http://127.0.0.1:{port}/health"
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                self.fail(
                    f"vedal 启动即退出（exit={proc.returncode}）：\n"
                    f"{log_path.read_text(encoding='utf-8', errors='replace')[-1500:]}"
                )
            try:
                with urlopen(url, timeout=2) as resp:
                    if resp.status == 200:
                        return
            except (URLError, OSError, TimeoutError):
                time.sleep(0.2)
        self.fail(f"40 秒内 /health 未就绪：\n{log_path.read_text(encoding='utf-8', errors='replace')[-1500:]}")


if __name__ == "__main__":
    unittest.main()
