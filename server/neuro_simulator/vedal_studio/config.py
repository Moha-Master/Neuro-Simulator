"""Configuration management for the Vedal Studio module."""

import os
import yaml
from pathlib import Path
from typing import Dict
import logging

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
        template_dir = Path(__file__).parent / "working_dir"  # 修正路径
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