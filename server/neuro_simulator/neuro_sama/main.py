"""服务入口：neuro（pip install 后的可执行文件）/ python -m neuro_simulator.neuro_sama。

启动参数（监听地址等全部取自 config.yaml，无 CLI 覆盖）：
    --dir/-D   工作目录（默认 ~/.config/neuro-simulator）

启动时写入 <workdir>/neuro_sama.pid（防双开，供 vedal 管理任何入口启动的实例），
退出时删除。
"""

import argparse
import os

import uvicorn

from .. import pidfile
from ..config import DEFAULT_WORKDIR, init_config
from .server import create_app

MODULE = "neuro_sama"


def main() -> None:
    parser = argparse.ArgumentParser(description="Neuro-Sama AI Agent server")
    parser.add_argument(
        "--dir", "-D",
        default=DEFAULT_WORKDIR,
        help=f"Working directory to read config.yaml from (default: {DEFAULT_WORKDIR})",
    )
    args = parser.parse_args()

    cfg = init_config(cli_dir=args.dir)
    print(f"[neuro-sama] workdir: {cfg.WORKDIR}")
    svc = cfg.neuro_llm()
    if cfg.NEURO_MODEL_REF and (svc is None or not svc.api_key or not svc.model):
        print(f"[neuro-sama] WARNING: 模型服务 {cfg.NEURO_MODEL_REF!r} 未完整定义，"
              f"请在 dashboard Configuration 页填写 server.llm_services。")
    elif not cfg.NEURO_MODEL_REF:
        print("[neuro-sama] WARNING: neuro_sama.model 未配置（对话将直接报错）。")

    try:
        pidfile.write_pid_file(cfg.WORKDIR, MODULE)
    except RuntimeError as e:
        print(f"[neuro-sama] ERROR: {e}")
        raise SystemExit(1)
    print(f"[neuro-sama] pid {os.getpid()} -> {pidfile.pid_file_path(cfg.WORKDIR, MODULE)}")

    try:
        uvicorn.run(create_app(), host=cfg.HOST, port=cfg.PORT)
    finally:
        pidfile.remove_pid_file(cfg.WORKDIR, MODULE)


if __name__ == "__main__":
    main()
