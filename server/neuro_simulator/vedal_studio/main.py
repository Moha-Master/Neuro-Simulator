"""Vedal Studio - Central management module for Neuro Simulator."""

import argparse
import json
import logging
import os
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from .api import router, app_state
from .config import ConfigManager, WorkingDirManager

logger = logging.getLogger(__name__)


def create_app(working_dir: str):
    """创建 FastAPI 应用."""
    app = FastAPI(title="Vedal Studio", version="1.0.0")
    
    # 首先添加 API 路由
    app.include_router(router)

    # 挂载静态文件（dashboard）到根路径，但让 API 路由优先
    # 尝试多种路径来查找dashboard文件，以支持不同的安装方式
    import neuro_simulator
    from pathlib import Path
    import sys

    # 首先尝试从包目录获取dashboard路径（适用于正常安装）
    package_dir = Path(neuro_simulator.__file__).parent
    dashboard_path = package_dir / "dashboard"

    # 如果在包目录下没找到，尝试在site-packages中查找（适用于-e安装）
    if not dashboard_path.exists():
        # 遍历sys.path查找包含neuro_simulator的site-packages路径
        for path in sys.path:
            if 'site-packages' in path:
                site_pkg_path = Path(path) / 'neuro_simulator' / 'dashboard'
                if site_pkg_path.exists():
                    dashboard_path = site_pkg_path
                    break

    # 备用路径：从当前文件位置查找
    if not dashboard_path.exists():
        dashboard_path = Path(__file__).parent.parent.parent / "dashboard" / "dist"

    if dashboard_path.exists():
        # 使用 html=True 选项，让 Vue Router 的 history 模式正常工作
        # 将dashboard挂载到根路径，但API路由会优先匹配
        app.mount("/", StaticFiles(directory=str(dashboard_path), html=True), name="dashboard")
    else:
        print(f"Warning: Dashboard directory not found at {dashboard_path}")
        # 如果 dashboard 不存在，至少提供一个简单的根路径
        @app.get("/")
        async def root():
            return {"message": "Vedal Studio is running", "status": "ok"}

    # Include API routes
    app.include_router(router)

    return app


async def notify_modules_config_change():
    """通知需要重载配置的模块."""
    # 在实际实现中，这里会通过WebSocket向各模块发送配置重载指令
    print("Notifying modules to reload configuration...")
    # TODO: 实现向各模块发送配置更新通知的逻辑


def create_and_run_app():
    """创建并运行应用，用于 uvicorn 直接调用."""
    # 展开用户目录
    working_dir = os.path.expanduser("~/.config/neuro-simulator")

    # 设置日志
    logging.basicConfig(level=logging.INFO)

    # 创建工作目录管理器
    working_dir_manager = WorkingDirManager(working_dir)
    working_dir_manager.setup_working_dir()

    # 创建配置管理器
    config_manager = ConfigManager(working_dir)

    # 从配置中获取服务器设置
    server_settings = config_manager.config.get("general", {}).get("server_settings", {})
    config_port = server_settings.get("port", 8000)
    config_host = server_settings.get("host", "127.0.0.1")

    # 保存到全局状态
    app_state['config_manager'] = config_manager

    # 创建 FastAPI 应用
    app = create_app(working_dir)

    return app


def get_app():
    """获取应用实例，用于 uvicorn reload 模式."""
    # 使用默认工作目录
    working_dir = os.path.expanduser("~/.config/neuro-simulator")

    # 设置日志
    logging.basicConfig(level=logging.INFO)

    # 创建工作目录管理器
    working_dir_manager = WorkingDirManager(working_dir)
    working_dir_manager.setup_working_dir()

    # 创建配置管理器
    config_manager = ConfigManager(working_dir)

    # 从配置中获取服务器设置
    server_settings = config_manager.config.get("general", {}).get("server_settings", {})
    config_port = server_settings.get("port", 8000)
    config_host = server_settings.get("host", "127.0.0.1")

    # 保存到全局状态
    app_state['config_manager'] = config_manager

    # 创建 FastAPI 应用
    app = create_app(working_dir)

    return app


def main():
    """主函数."""
    parser = argparse.ArgumentParser(description="Vedal Studio - Central management for Neuro Simulator")
    parser.add_argument("--dir", "-D", default="~/.config/neuro-simulator",
                        help="Working directory (default: ~/.config/neuro-simulator)")
    parser.add_argument("--reload", action="store_true",
                        help="Enable uvicorn reload mode for debug")
    parser.add_argument("--port", type=int,
                        help="Port for the central framework (default: from config)")

    args = parser.parse_args()

    # 展开用户目录
    working_dir = os.path.expanduser(args.dir)

    # 设置日志
    logging.basicConfig(level=logging.INFO)

    # 创建工作目录管理器
    working_dir_manager = WorkingDirManager(working_dir)
    working_dir_manager.setup_working_dir()

    # 创建配置管理器
    config_manager = ConfigManager(working_dir)

    # 从配置中获取服务器设置
    server_settings = config_manager.config.get("general", {}).get("server_settings", {})
    config_port = server_settings.get("port", 8000)
    config_host = server_settings.get("host", "127.0.0.1")

    # 如果命令行指定了端口，则使用命令行的值，否则使用配置文件的值
    port = args.port if args.port is not None else config_port

    # 保存到全局状态
    app_state['config_manager'] = config_manager

    # 创建 FastAPI 应用
    app = create_app(working_dir)

    print(f"Vedal Studio running on http://{config_host}:{port}")
    print(f"Dashboard available at http://{config_host}:{port}/")
    print("Press Ctrl+C to stop.")

    import uvicorn
    if args.reload:
        # For reload mode, we need to pass the module as a string
        uvicorn.run(
            "neuro_simulator.vedal_studio.main:get_app",  # Use function to get app
            host=config_host,
            port=port,
            reload=True
        )
    else:
        uvicorn.run(
            app,
            host=config_host,
            port=port,
            reload=False
        )


if __name__ == "__main__":
    main()