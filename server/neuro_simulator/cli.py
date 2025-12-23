"""Central management framework for Neuro Simulator modules."""

import asyncio
import json
import os
import sys
import argparse
import yaml
import subprocess
import signal
import time
from pathlib import Path
from typing import Dict, List, Optional
import logging
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


class ConfigManager:
    """管理全局配置文件."""
    
    def __init__(self, working_dir: str):
        self.working_dir = Path(working_dir)
        self.config_path = self.working_dir / "config.yaml"
        self.config = self.load_config()
    
    def load_config(self) -> Dict:
        """加载配置文件."""
        if not self.config_path.exists():
            print(f"Error: Configuration file does not exist: {self.config_path}")
            print("The configuration template should have been created during working directory setup.")
            # 退出程序，因为没有配置文件
            raise SystemExit(f"Configuration file missing: {self.config_path}")

        with open(self.config_path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    
    def save_config(self, config: Dict):
        """保存配置文件."""
        with open(self.config_path, 'w', encoding='utf-8') as f:
            yaml.dump(config, f, default_flow_style=False, allow_unicode=True)
        # 更新内部配置
        self.config = config

    def get_module_config(self, module_name: str) -> Dict:
        """获取特定模块的配置."""
        return self.config.get(module_name, {})

    def reload_config(self):
        """重新加载配置文件."""
        self.config = self.load_config()
        return self.config


class WorkingDirManager:
    """管理工作目录."""
    
    def __init__(self, working_dir: str):
        self.working_dir = Path(working_dir)
        self.modules_dir = self.working_dir / "modules"
        self.logs_dir = self.working_dir / "logs"
        
    def setup_working_dir(self):
        """创建工作目录结构."""
        self.working_dir.mkdir(parents=True, exist_ok=True)
        self.modules_dir.mkdir(exist_ok=True)
        self.logs_dir.mkdir(exist_ok=True)

        # 复制整个工作目录模板
        import shutil
        template_dir = Path(__file__).parent / "working_dir"
        if template_dir.exists():
            # 复制所有内容，但跳过已存在的文件
            for item in template_dir.iterdir():
                dest_path = self.working_dir / item.name
                if not dest_path.exists():
                    if item.is_dir():
                        shutil.copytree(item, dest_path)
                    else:
                        shutil.copy2(item, dest_path)
        else:
            print(f"Warning: Template directory does not exist: {template_dir}")
            # 如果模板不存在，创建基本目录结构
            neuro_sama_dir = self.working_dir / "neuro_sama"
            neuro_sama_dir.mkdir(exist_ok=True)
            (neuro_sama_dir / "memory").mkdir(exist_ok=True)
            (neuro_sama_dir / "prompts").mkdir(exist_ok=True)


class ModuleManager:
    """管理各个模块的启动和停止."""
    
    def __init__(self, working_dir: str, config_manager: ConfigManager, reload: bool = False):
        self.working_dir = Path(working_dir)
        self.config_manager = config_manager
        self.reload = reload
        self.processes = {}
    
    def start_module(self, module_name: str) -> bool:
        """启动指定模块."""
        if module_name == "neuro_sama":
            return self._start_neuro_sama()
        else:
            print(f"Unknown module: {module_name}")
            return False
    
    def stop_module(self, module_name: str) -> bool:
        """停止指定模块."""
        if module_name in self.processes:
            process = self.processes[module_name]
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
            del self.processes[module_name]
            print(f"Stopped {module_name}")
            return True
        return False
    
    def _start_neuro_sama(self) -> bool:
        """启动 Neuro Sama 模块."""
        # 获取 Neuro Sama 模块配置
        module_config = self.config_manager.get_module_config("neuro_sama")

        # 获取服务器设置
        server_settings = module_config.get("server_settings", {})
        host = server_settings.get("host", "127.0.0.1")
        port = server_settings.get("port", 8001)

        # 启动模块
        cmd = [
            sys.executable, "-m", "neuro_simulator.neuro_sama.main"
        ]

        if self.reload:
            cmd.append("--reload")

        try:
            # 传递全局配置到模块
            env = os.environ.copy()
            # 使用环境变量来传递模块的工作目录
            neuro_sama_work_dir = self.working_dir / "neuro_sama"
            env["NEURO_WORKING_DIR"] = str(neuro_sama_work_dir)

            process = subprocess.Popen(cmd, env=env)
            self.processes["neuro_sama"] = process
            print(f"Started Neuro Sama module on {host}:{port}")
            return True
        except Exception as e:
            print(f"Failed to start Neuro Sama module: {e}")
            return False
    
    def start_all_modules(self) -> bool:
        """启动所有模块."""
        modules = ["neuro_sama"]  # 将来可以添加更多模块
        success = True
        for module in modules:
            if not self.start_module(module):
                success = False
        return success
    
    def stop_all_modules(self):
        """停止所有模块."""
        for module_name in list(self.processes.keys()):
            self.stop_module(module_name)


# Global variables to store app state
app_state = {
    'config_manager': None,
    'module_manager': None,
    'active_websockets': set()
}


def create_app(working_dir: str, config_manager: ConfigManager, module_manager: ModuleManager):
    """创建 FastAPI 应用."""
    app = FastAPI(title="Neuro Simulator Central Framework", version="1.0.0")

    # 挂载静态文件（dashboard）
    dashboard_path = Path(__file__).parent.parent.parent / "dashboard" / "dist"
    if dashboard_path.exists():
        app.mount("/dashboard", StaticFiles(directory=str(dashboard_path), html=True), name="dashboard")
    else:
        print(f"Warning: Dashboard directory not found at {dashboard_path}")

    # API 端点
    @app.get("/api/system/health")
    async def health_check():
        return {"status": "healthy", "message": "Neuro Simulator Central Framework is running"}

    # WebSocket 端点
    @app.websocket("/ws/admin")
    async def websocket_endpoint(websocket: WebSocket):
        await websocket.accept()
        app_state['active_websockets'].add(websocket)
        try:
            while True:
                data = await websocket.receive_text()
                message = json.loads(data)

                # 处理不同的管理消息
                action = message.get("action")
                payload = message.get("payload", {})

                if action == "get_config":
                    config = app_state['config_manager'].config
                    await websocket.send_text(json.dumps({
                        "type": "config_data",
                        "payload": config
                    }))
                elif action == "save_config":
                    new_config = payload.get("config")
                    if new_config:
                        try:
                            app_state['config_manager'].save_config(new_config)
                            # 重启相关模块以应用新配置
                            await restart_modules_for_config_change()

                            await websocket.send_text(json.dumps({
                                "type": "response",
                                "request_id": message.get("request_id"),
                                "payload": {"status": "success", "message": "Configuration saved and modules restarted"}
                            }))
                        except Exception as e:
                            await websocket.send_text(json.dumps({
                                "type": "response",
                                "request_id": message.get("request_id"),
                                "payload": {"status": "error", "message": str(e)}
                            }))
                else:
                    await websocket.send_text(json.dumps({
                        "type": "response",
                        "request_id": message.get("request_id"),
                        "payload": {"status": "error", "message": f"Unknown action: {action}"}
                    }))
        except WebSocketDisconnect:
            app_state['active_websockets'].remove(websocket)
        except Exception as e:
            print(f"WebSocket error: {e}")
            if websocket in app_state['active_websockets']:
                app_state['active_websockets'].remove(websocket)

    return app


async def restart_modules_for_config_change():
    """重启需要重新加载配置的模块."""
    # 停止所有模块
    app_state['module_manager'].stop_all_modules()

    # 等待一段时间确保模块完全停止
    await asyncio.sleep(1)

    # 重新启动所有模块
    app_state['module_manager'].start_all_modules()


def main():
    """主函数."""
    parser = argparse.ArgumentParser(description="Neuro Simulator Central Framework")
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

    # 创建模块管理器
    module_manager = ModuleManager(working_dir, config_manager, args.reload)

    # 保存到全局状态
    app_state['config_manager'] = config_manager
    app_state['module_manager'] = module_manager

    # 创建 FastAPI 应用
    app = create_app(working_dir, config_manager, module_manager)

    # 启动所有模块
    print("Starting Neuro Simulator modules...")
    if module_manager.start_all_modules():
        print("All modules started successfully!")
        print(f"Central framework running on http://{config_host}:{port}")
        print(f"Dashboard available at http://{config_host}:{port}/dashboard")
        print("Press Ctrl+C to stop all modules.")

        import uvicorn
        uvicorn.run(
            app,
            host=config_host,
            port=port,
            reload=args.reload
        )
    else:
        print("Failed to start some modules.")
        module_manager.stop_all_modules()


if __name__ == "__main__":
    main()