"""Main entry point for the Neuro Sama module."""

import asyncio
import json
import sys
import argparse
from contextlib import asynccontextmanager

from fastapi import FastAPI
from openai import AsyncOpenAI

from .config import Config
from .api import router
from .banner import display_banner


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    # Startup
    # Get working directory from environment
    import os
    working_dir = os.getenv("NEURO_WORKING_DIR")
    print(f"DEBUG: NEURO_WORKING_DIR = {working_dir}")

    # Read global config from file
    import json
    from pathlib import Path

    if working_dir:
        # The config.json should be in the parent directory of the module's working directory
        working_path = Path(working_dir)
        config_file = working_path.parent / "config.json"
        print(f"DEBUG: Working dir = {working_path}")
        print(f"DEBUG: Looking for config at: {config_file}")
        print(f"DEBUG: Config file exists: {config_file.exists()}")
        if config_file.exists():
            with open(config_file, 'r', encoding='utf-8') as f:
                global_config = json.load(f)
        else:
            raise ValueError(f"Global configuration file not found: {config_file}. Please run Vedal Studio to initialize the working directory.")
    else:
        # 检查默认工作目录是否存在
        default_working_dir = Path.home() / ".config" / "neuro-simulator" / "neuro_sama"
        config_file = default_working_dir.parent / "config.json"
        if config_file.exists():
            # 使用默认工作目录
            working_dir = str(default_working_dir)
            working_path = Path(working_dir)
            with open(config_file, 'r', encoding='utf-8') as f:
                global_config = json.load(f)
            print(f"Using default working directory: {working_dir}")
        else:
            raise ValueError(f"Global configuration file not found: {config_file}. Please run Vedal Studio to initialize the working directory.")

    app.state.config = Config(global_config, working_dir)
    app.state.openai_client = AsyncOpenAI(
        api_key=app.state.config.OPENAI_API_KEY,
        base_url=app.state.config.OPENAI_BASE_URL
    )

    # Display the banner
    display_banner()

    print(f"Neuro Sama module started on {app.state.config.HOST}:{app.state.config.PORT}")
    print(f"Using model: {app.state.config.OPENAI_MODEL}")

    yield

    # Shutdown
    print("Neuro Sama module shutting down...")


# Create FastAPI app
app = FastAPI(
    title="Neuro Sama Module",
    description="LLM service for Neuro Sama AI vtuber",
    version="0.1.0",
    lifespan=lifespan
)

# Include API routes
app.include_router(router)

@app.get("/")
async def root():
    """Root endpoint to check if the service is running."""
    return {"message": "Neuro Sama module is running", "status": "ok"}


def main():
    """Main entry point for the neuro command."""
    import uvicorn
    import argparse
    from pathlib import Path
    import json

    parser = argparse.ArgumentParser(description="Neuro Sama - LLM service for Neuro Sama AI vtuber")
    parser.add_argument("--dir", "-D", dest="working_dir",
                        default=str(Path.home() / ".config" / "neuro-simulator" / "neuro_sama"),
                        help="Working directory for the module (default: ~/.config/neuro-simulator/neuro_sama)")
    parser.add_argument("--reload", action="store_true",
                        help="Enable uvicorn reload mode for debug")
    parser.add_argument("--port", type=int,
                        help="Port for the module (default: from config)")

    args = parser.parse_args()

    # 在启动 uvicorn 之前检查配置
    # 从工作目录推断配置文件位置
    working_path = Path(args.working_dir)
    config_file = working_path.parent / "config.json"

    print(f"DEBUG: Working dir = {working_path}")
    print(f"DEBUG: Looking for config at: {config_file}")
    print(f"DEBUG: Config file exists: {config_file.exists()}")

    if not config_file.exists():
        raise ValueError(f"Global configuration file not found: {config_file}. Please run Vedal Studio to initialize the working directory.")

    with open(config_file, 'r', encoding='utf-8') as f:
        global_config = json.load(f)

    print(f"Using working directory: {args.working_dir}")

    # 验证配置是否包含必需的 neuro_sama 部分
    from .config import Config

    # 获取配置中的服务器设置，如果命令行提供了端口则使用命令行的值
    neuro_sama_config = global_config.get("neuro_sama", {})
    server_settings = neuro_sama_config.get("server_settings", {})
    config_port = server_settings.get("port", 8001)
    config_host = server_settings.get("host", "127.0.0.1")

    # 确保端口是整数
    try:
        config_port = int(config_port)
    except (ValueError, TypeError):
        raise ValueError(f"Invalid port value in configuration: {config_port}. Port must be a number.")

    # 如果命令行指定了端口，则使用命令行的值，否则使用配置文件的值
    port = args.port if args.port is not None else config_port

    Config(global_config, args.working_dir)  # 这会执行配置验证

    # 如果配置验证通过，启动 uvicorn
    uvicorn.run(
        "neuro_simulator.neuro_sama.main:app",
        host=config_host,  # 使用配置中的主机地址
        port=port,         # 使用命令行或配置中的端口
        reload=args.reload
    )


if __name__ == "__main__":
    main()