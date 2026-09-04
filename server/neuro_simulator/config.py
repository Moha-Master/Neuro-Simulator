"""neuro_simulator 共享配置管理（独立于任何单一模块，供所有模块复用）。

config.yaml 结构（按模块分 schema）：
    server: {}            # 全局共用（预留，暂无内容）
    neuro_sama: {...}     # neuro_sama 模块的全部配置
    # 未来模块各自新增顶层 schema，共用 server schema 下的全局配置

惯例（与 Aliyun-Controller / Image-API-OpenAI 两个项目保持一致）：
- 工作目录默认 ~/.config/neuro-simulator，CLI --dir/-D 可覆盖，自动创建
- 配置文件为工作目录下的 config.yaml（YAML 格式）
- 包内携带 config.yaml.example，首次启动自动部署为 config.yaml
- 工作目录结构：
    <workdir>/
    ├── config.yaml          # 全局共享配置（各模块 schema 汇总）
    └── neuro-sama/          # 仅 neuro_sama 模块使用的数据（如 data.db）
- 优先级：
    服务参数 host/port：仅 config.yaml（无 CLI 覆盖；改地址需重启进程）
    业务参数：          config.yaml > 代码内联默认值

热重载：Config.reload() 重新读取文件并刷新字段；
parse_module_fields() 提供“不落地”的校验（/manage 上传配置时使用）。
"""

import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

DEFAULT_WORKDIR = os.path.expanduser("~/.config/neuro-simulator")
CONFIG_FILENAME = "config.yaml"
EXAMPLE_FILENAME = "config.yaml.example"
SERVER_SCHEMA = "server"

# 各模块的数据子目录：模块名 -> workdir 下的子目录名
MODULE_DATA_DIRS = {
    "neuro_sama": "neuro-sama",
}

DEFAULT_SYSTEM_PROMPT = "You are Neuro-Sama, an AI Vtuber streaming on Twitch."


def _as_int(ns: dict, key: str, default: int) -> int:
    value = ns.get(key, default)
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValueError(f"neuro_sama.{key} 必须是整数（当前: {value!r}）")


def _as_float(ns: dict, key: str, default: float) -> float:
    value = ns.get(key, default)
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValueError(f"neuro_sama.{key} 必须是数字（当前: {value!r}）")


def parse_module_fields(data: Dict[str, Any]) -> Dict[str, Any]:
    """从完整 config dict 中提取 neuro_sama 模块字段并做类型校验。

    成功返回字段 dict（UPPER_CASE 键）；类型不合法时抛 ValueError。
    供 Config 加载与 /manage 配置上传校验共用。
    """
    ns = data.get("neuro_sama", {})
    if not isinstance(ns, dict):
        raise ValueError("neuro_sama 配置必须是映射（key: value）")

    servers = ns.get("mcp_servers", [])
    if servers is None:
        servers = []
    if not isinstance(servers, list):
        raise ValueError("neuro_sama.mcp_servers 必须是列表")

    extra_body = ns.get("extra_body", {})
    if extra_body is None:
        extra_body = {}
    if not isinstance(extra_body, dict):
        raise ValueError("neuro_sama.extra_body 必须是映射（key: value）")

    return {
        "API_BASE_URL": str(ns.get("api_base_url", "")),
        "API_KEY": str(ns.get("api_key", "")),
        "MODEL": str(ns.get("model", "")),
        "EXTRA_BODY": extra_body,
        "SYSTEM_PROMPT_BASE": str(ns.get("system_prompt_base", DEFAULT_SYSTEM_PROMPT)),
        "MAX_STEPS": _as_int(ns, "max_steps", 10),
        "MAX_CONTEXT_MESSAGES": _as_int(ns, "max_context_messages", 60),
        "TOOL_TIMEOUT": _as_float(ns, "tool_timeout", 30.0),
        "MEMORY_CHAR_LIMIT": _as_int(ns, "memory_char_limit", 4000),
        "HOST": str(ns.get("host", "127.0.0.1")),
        "PORT": _as_int(ns, "port", 8000),
        "MCP_SERVERS": servers,
    }


def resolve_workdir(cli_dir: Optional[str] = None) -> Path:
    """解析并确保工作目录存在；config.yaml 缺失时自动部署。"""
    workdir = Path(os.path.expanduser(cli_dir or DEFAULT_WORKDIR))
    workdir.mkdir(parents=True, exist_ok=True)
    _deploy_config_if_missing(workdir)
    for sub in MODULE_DATA_DIRS.values():
        (workdir / sub).mkdir(parents=True, exist_ok=True)
    return workdir


def _deploy_config_if_missing(workdir: Path) -> None:
    target = workdir / CONFIG_FILENAME
    if target.exists():
        return
    example = Path(__file__).parent / EXAMPLE_FILENAME
    shutil.copyfile(example, target)
    print(f"[neuro-simulator] 已创建初始配置: {target}")
    print("[neuro-simulator] 请编辑该文件（至少填写 neuro_sama.api_key）后再使用服务。")


class Config:
    """工作目录 + config.yaml 加载（UPPER_CASE 属性 + 内联默认值兜底）。

    当前字段全部来自 neuro_sama schema；未来新模块的字段应迁移到各自模块内
    的 typed view（通过 module() 取原始 dict），保持本文件只承载共享逻辑。
    """

    def __init__(self, workdir: Path) -> None:
        self.WORKDIR: Path = workdir
        self.DATA_DIR: Path = workdir / MODULE_DATA_DIRS["neuro_sama"]
        self.DB_PATH: Path = self.DATA_DIR / "data.db"
        self.reload()

    def reload(self) -> None:
        """重新读取 config.yaml 并刷新全部字段（热重载入口）。"""
        path = self.WORKDIR / CONFIG_FILENAME
        if not path.exists():
            raise FileNotFoundError(
                f"config.yaml not found in {self.WORKDIR}; "
                f"expected it to be deployed from {EXAMPLE_FILENAME} on first start."
            )
        with path.open("r", encoding="utf-8") as f:
            self.data: Dict[str, Any] = yaml.safe_load(f) or {}
        fields = parse_module_fields(self.data)
        for key, value in fields.items():
            setattr(self, key, value)

    def module(self, name: str) -> Dict[str, Any]:
        """取某模块 schema 的原始 dict（不存在时返回空 dict）。"""
        value = self.data.get(name, {})
        return value if isinstance(value, dict) else {}

    def server(self) -> Dict[str, Any]:
        """取全局共用 server schema 的原始 dict。"""
        value = self.data.get(SERVER_SCHEMA, {})
        return value if isinstance(value, dict) else {}


_config: Optional[Config] = None


def init_config(cli_dir: Optional[str] = None) -> Config:
    """入口处调用一次：解析工作目录、加载配置。

    监听地址等全部参数只认 config.yaml（无 CLI 覆盖）——避免 reload()
    重读文件后抹掉 CLI 值造成的"内存配置与文件不一致"。
    运行期修改 host/port 需重启进程生效（uvicorn 监听地址不可热改）。
    """
    global _config
    workdir = resolve_workdir(cli_dir)
    _config = Config(workdir)
    return _config


def get_config() -> Config:
    """各模块获取配置的统一入口；未初始化时给出明确报错。"""
    if _config is None:
        raise RuntimeError(
            "Config not initialized: call init_config() at startup "
            "(the `neuro` entry point does this automatically)."
        )
    return _config
