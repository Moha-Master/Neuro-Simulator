"""Configuration management for the Vedal Studio module."""

import os
import json
from pathlib import Path
from typing import Dict
import logging

logger = logging.getLogger(__name__)


class ConfigManager:
    """管理全局配置文件."""

    def __init__(self, working_dir: str):
        self.working_dir = Path(working_dir)
        self.config_path = self.working_dir / "config.json"
        self.config = self.load_config()

    def load_config(self) -> Dict:
        """加载配置文件."""
        if not self.config_path.exists():
            print(f"Configuration file does not exist: {self.config_path}")
            print("Creating from template...")
            # 从模板复制配置文件
            self._copy_config_from_template()
        else:
            # 配置文件存在，检查是否有效
            with open(self.config_path, 'r', encoding='utf-8') as f:
                try:
                    config = json.load(f)
                except json.JSONDecodeError as e:
                    print(f"Error: Configuration file is not valid JSON: {e}")
                    print("Backing up corrupted config and creating a new one from template...")
                    self._backup_and_replace_config()
                    with open(self.config_path, 'r', encoding='utf-8') as f:
                        return json.load(f)

            # 检查配置文件是否包含必需的结构
            if not self._is_config_valid(config):
                print(f"Warning: Configuration file is missing required sections: {self.config_path}")
                print("Backing up corrupted config and creating a new one from template...")
                self._backup_and_replace_config()
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    return json.load(f)

        # 重新加载新创建或已验证的配置文件
        with open(self.config_path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def _is_config_valid(self, config: Dict) -> bool:
        """检查配置是否包含必需的部分."""
        if not isinstance(config, dict):
            return False

        # 检查是否有 general 部分
        if "general" not in config:
            return False

        # 检查 general.server_settings 是否存在且包含必需字段
        general = config["general"]
        if not isinstance(general, dict):
            return False

        if "server_settings" not in general:
            return False

        server_settings = general["server_settings"]
        if not isinstance(server_settings, dict):
            return False

        # 检查 server_settings 是否包含必需字段
        if "host" not in server_settings or "port" not in server_settings:
            return False

        return True

    def _backup_and_replace_config(self):
        """备份损坏的配置文件并从模板复制新的."""
        import datetime
        import shutil

        # 备份当前配置文件
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = self.config_path.with_suffix(f".json.bak_{timestamp}")
        shutil.copy2(self.config_path, backup_path)
        print(f"Backed up corrupted config to: {backup_path}")

        # 从模板复制新的配置文件
        self._copy_config_from_template()

    def _copy_config_from_template(self):
        """从模板目录复制配置文件."""
        import shutil
        from pathlib import Path

        # 获取模板目录中的配置文件
        template_dir = Path(__file__).parent.parent / "working_dir"
        template_config_path = template_dir / "config.json"

        if template_config_path.exists():
            # 复制模板配置文件到工作目录
            shutil.copy2(template_config_path, self.config_path)
            print(f"Created config from template: {self.config_path}")
        else:
            print(f"Error: Template config does not exist: {template_config_path}")
            # 退出程序，因为没有模板配置文件
            raise SystemExit(f"Template configuration file missing: {template_config_path}")

    def save_config(self, config: Dict):
        """保存配置文件."""
        with open(self.config_path, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
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
        # 修正路径：working_dir 模板在包的根目录下，而不是在 vedal_studio 目录下
        template_dir = Path(__file__).parent.parent / "working_dir"
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

            # 创建默认的 JSON 配置文件
            default_config = {
                "general": {
                    "server_settings": {
                        "host": "127.0.0.1",
                        "port": 8000
                    },
                    "llm_services": [],
                    "tts_services": []
                },
                "neuro_sama": {
                    "llm_service_id": "",
                    "tts_service_id": "",
                    "server_settings": {
                        "host": "127.0.0.1",
                        "port": 8001
                    }
                },
                "stream": {}
            }

            config_path = self.working_dir / "config.json"
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(default_config, f, ensure_ascii=False, indent=2)
