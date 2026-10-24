"""服务入口：stream（pip install 后的可执行文件）/ python -m neuro_simulator.stream.main。

启动参数（监听地址等全部取自 config.yaml 的 stream schema，无 CLI 覆盖）：
    --dir/-D   工作目录（默认 ~/.config/neuro-simulator）

启动时写入 <workdir>/stream.pid（防双开，供 vedal 管理任何入口启动的实例），
退出时删除。
"""

import argparse
import os

import uvicorn

from .. import log_kit, pidfile
from ..config import DEFAULT_WORKDIR, init_config
from .server import create_app

MODULE = "stream"


def main() -> None:
    parser = argparse.ArgumentParser(description="Stream manager (viewer queue + streaming loop + UI host)")
    parser.add_argument(
        "--dir", "-D",
        default=DEFAULT_WORKDIR,
        help=f"Working directory to read config.yaml from (default: {DEFAULT_WORKDIR})",
    )
    args = parser.parse_args()

    cfg = init_config(cli_dir=args.dir)
    # 尽早装 tee：本进程之后的所有输出（含 uvicorn 与 logging）都进
    # <workdir>/logs/<模块名>.log，与启动入口无关（手动跑也有日志）。
    log_kit.attach(cfg.WORKDIR, MODULE)
    ns = cfg.module(MODULE)
    host = str(ns.get("host", "127.0.0.1"))
    port = int(ns.get("port", 8200))
    print(f"[stream] workdir: {cfg.WORKDIR}")

    try:
        pidfile.write_pid_file(cfg.WORKDIR, MODULE)
    except RuntimeError as e:
        print(f"[stream] ERROR: {e}")
        raise SystemExit(1)
    print(f"[stream] pid {os.getpid()} -> {pidfile.pid_file_path(cfg.WORKDIR, MODULE)}")

    app = create_app()
    # pid 文件的删除必须挂进 lifespan shutdown：uvicorn 优雅关闭后会把 SIGTERM
    # 重发给自己（capture_signals），进程在 uvicorn.run 的 finally 之前就被杀死。
    # 详见 pidfile.install_cleanup_on_shutdown 的说明。
    pidfile.install_cleanup_on_shutdown(app, cfg.WORKDIR, MODULE)

    try:
        uvicorn.run(app, host=host, port=port)
    finally:
        pidfile.remove_pid_file(cfg.WORKDIR, MODULE)


if __name__ == "__main__":
    main()
