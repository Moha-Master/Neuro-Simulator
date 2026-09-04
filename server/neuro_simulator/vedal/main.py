"""vedal 模块独立入口：vedal（pip install 后可执行文件）/ python -m neuro_simulator.vedal。

监听地址取 config.yaml 中 vedal schema 的 host/port（无 CLI 覆盖）。
启动参数：
    --dir/-D   工作目录（默认 ~/.config/neuro-simulator）
启动时写入 <workdir>/vedal.pid（防双开），退出时删除。
"""

import argparse
import os

import uvicorn

from .. import pidfile
from ..config import DEFAULT_WORKDIR, init_config
from .server import create_app

MODULE = "vedal"


def main() -> None:
    parser = argparse.ArgumentParser(description="Vedal management service (dashboard host + module manager)")
    parser.add_argument(
        "--dir", "-D",
        default=DEFAULT_WORKDIR,
        help=f"Working directory to read config.yaml from (default: {DEFAULT_WORKDIR})",
    )
    args = parser.parse_args()

    cfg = init_config(cli_dir=args.dir)
    ns = cfg.module("vedal")
    host = str(ns.get("host", "127.0.0.1"))
    port = int(ns.get("port", 8100))
    print(f"[vedal] workdir: {cfg.WORKDIR}")

    try:
        pidfile.write_pid_file(cfg.WORKDIR, MODULE)
    except RuntimeError as e:
        print(f"[vedal] ERROR: {e}")
        raise SystemExit(1)
    print(f"[vedal] pid {os.getpid()} -> {pidfile.pid_file_path(cfg.WORKDIR, MODULE)}")

    try:
        uvicorn.run(create_app(), host=host, port=port)
    finally:
        pidfile.remove_pid_file(cfg.WORKDIR, MODULE)


if __name__ == "__main__":
    main()
