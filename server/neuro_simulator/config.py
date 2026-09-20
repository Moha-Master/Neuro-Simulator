"""neuro_simulator 共享配置管理（独立于任何单一模块，供所有模块复用）。

config.json 结构（按模块分 schema + 全局服务注册表）：
    server:                          # 全局共用
      llm_services: {<id>: {...}}    # LLM 服务注册表（id 即引用键）
      tts_services: {<id>: {...}}    # TTS 服务注册表
    neuro_sama: { ..., model: <llm_id>, tts: <tts_id|"null"|""> }
    vedal:    {...}
    stream:   { ..., chatbot: { ..., model: <llm_id> } }
    # 未来模块各自新增顶层 schema，照此惯例引用服务

服务引用语义：
- llm 引用（neuro_sama.model / stream.chatbot.model）：非空时必须是
  llm_services 中已定义的 id；未配置/悬空 → 相关功能明确报错或跳过。
- tts 引用（neuro_sama.tts）："null"=内置虚拟 TTS（无音频、估算时长驱动字幕）；
  ""=关闭；其余必须是 tts_services 中已定义的 id。

惯例：
- 工作目录默认 ~/.config/neuro-simulator，CLI --dir/-D 可覆盖，自动创建
- 配置文件为工作目录下的 config.json（JSON 格式，全程可经 dashboard 编辑）
- 包内携带 config.json.example，首次启动自动部署
- 热重载：Config.reload() 重读文件；validate_config() 供 vedal 保存时校验；
  sweep_dangling_refs() 实现"删除服务时自动清空引用它的地方"

监听地址 host/port 仅认配置文件（无 CLI 覆盖）；运行期修改需重启进程。
"""

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_WORKDIR = os.path.expanduser("~/.config/neuro-simulator")
CONFIG_FILENAME = "config.json"
EXAMPLE_FILENAME = "config.json.example"
SERVER_SCHEMA = "server"

# 各模块的数据子目录：模块名 -> workdir 下的子目录名
MODULE_DATA_DIRS = {
    "neuro_sama": "neuro-sama",
    "stream": "stream",
}

DEFAULT_SYSTEM_PROMPT = "You are Neuro-Sama, an AI Vtuber streaming on Twitch."

# 服务引用字段清单：(schema, 点分路径, 服务种类)。新增引用只需加一行。
REF_LLM = "llm"
REF_TTS = "tts"
REF_FIELDS: List[Tuple[str, str, str]] = [
    ("neuro_sama", "model", REF_LLM),
    ("stream", "chatbot.model", REF_LLM),
    ("neuro_sama", "tts", REF_TTS),
]
TTS_BUILTIN_IDS = ("null",)  # 保留字：内置虚拟 TTS（""=关闭 另行处理）

TTS_TYPES = ("azure_tts",)


@dataclass(frozen=True)
class LLMService:
    id: str
    display_name: str
    base_url: str
    api_key: str
    model: str
    extra_body: Dict[str, Any] = field(default_factory=dict)
    timeout: float = 60.0


@dataclass(frozen=True)
class TTSService:
    id: str
    display_name: str
    type: str
    azure_region: str = ""
    azure_key: str = ""
    timeout: float = 10.0


def _as_int(ns: dict, key: str, default: int) -> int:
    value = ns.get(key, default)
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{key} 必须是整数（当前: {value!r}）")


def _as_float(ns: dict, key: str, default: float) -> float:
    value = ns.get(key, default)
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{key} 必须是数字（当前: {value!r}）")


# ---------- 服务注册表读取 ----------


def _services_of(data: Dict[str, Any], kind: str) -> Dict[str, Any]:
    server = data.get(SERVER_SCHEMA)
    if not isinstance(server, dict):
        return {}
    key = "llm_services" if kind == REF_LLM else "tts_services"
    value = server.get(key)
    return value if isinstance(value, dict) else {}


def get_llm_service(data: Dict[str, Any], ref: str) -> Optional[LLMService]:
    """按 id 解析 LLM 服务；不存在/不完整返回 None。"""
    entry = _services_of(data, REF_LLM).get(ref)
    if not isinstance(entry, dict):
        return None
    return LLMService(
        id=ref,
        display_name=str(entry.get("display_name") or ref),
        base_url=str(entry.get("api_base_url") or "").strip(),
        api_key=str(entry.get("api_key") or ""),
        model=str(entry.get("api_model_name") or "").strip(),
        extra_body=dict(entry.get("api_extra_body") or {})
        if isinstance(entry.get("api_extra_body"), dict)
        else {},
        timeout=_opt_float(entry, "timeout", 60.0) or 60.0,
    )


def get_tts_service(data: Dict[str, Any], ref: str) -> Optional[TTSService]:
    entry = _services_of(data, REF_TTS).get(ref)
    if not isinstance(entry, dict):
        return None
    return TTSService(
        id=ref,
        display_name=str(entry.get("display_name") or ref),
        type=str(entry.get("type") or "azure_tts"),
        azure_region=str(entry.get("azure_tts_region") or ""),
        azure_key=str(entry.get("azure_tts_key") or ""),
        timeout=_opt_float(entry, "timeout", 10.0) or 10.0,
    )


def _opt_float(entry: dict, key: str, default: float) -> Optional[float]:
    value = entry.get(key, default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _get_path(obj: dict, dotted: str) -> Any:
    cur: Any = obj
    for part in dotted.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _set_path(obj: dict, dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    cur = obj
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value


def service_usage(data: Dict[str, Any]) -> Dict[str, Dict[str, List[str]]]:
    """反查每个服务被哪些配置项使用：{kind: {service_id: [字段路径...]}}。

    供 vedal GET /manage/config 返回给 dashboard（删除前警告"谁在用"）。
    """
    usage: Dict[str, Dict[str, List[str]]] = {REF_LLM: {}, REF_TTS: {}}
    for schema, dotted, kind in REF_FIELDS:
        value = _get_path(data.get(schema) or {}, dotted)
        if isinstance(value, str) and value:
            usage[kind].setdefault(value, []).append(f"{schema}.{dotted}")
    return usage


def sweep_dangling_refs(data: Dict[str, Any]) -> List[str]:
    """把指向不存在服务的引用清空（"删除服务时自动清空调用处"）。

    原地修改并返回被清空的字段路径列表。tts 引用允许保留内置 "null"。
    """
    cleared: List[str] = []
    for schema, dotted, kind in REF_FIELDS:
        ns = data.get(schema)
        if not isinstance(ns, dict):
            continue
        value = _get_path(ns, dotted)
        if not isinstance(value, str) or not value:
            continue
        if kind == REF_TTS and value in TTS_BUILTIN_IDS:
            continue
        if value not in _services_of(data, kind):
            _set_path(ns, dotted, "")
            cleared.append(f"{schema}.{dotted}")
    return cleared


# ---------- 校验（不落地；vedal 保存配置时使用） ----------


def validate_config(data: Dict[str, Any]) -> None:
    """全量结构/类型/引用校验；不合法抛 ValueError（消息面向用户）。"""
    if not isinstance(data, dict):
        raise ValueError("配置根必须是 JSON 对象")

    server = data.get(SERVER_SCHEMA, {})
    if not isinstance(server, dict):
        raise ValueError("server 必须是对象")
    for key, kind in (("llm_services", REF_LLM), ("tts_services", REF_TTS)):
        reg = server.get(key, {})
        if reg is None:
            continue
        if not isinstance(reg, dict):
            raise ValueError(f"server.{key} 必须是以服务 id 为键的对象")
        for sid, entry in reg.items():
            _validate_service(kind, sid, entry)

    ns = data.get("neuro_sama", {})
    if ns and not isinstance(ns, dict):
        raise ValueError("neuro_sama 配置必须是对象")
    _check_ref(data, "neuro_sama", "model", REF_LLM)
    _check_ref(data, "neuro_sama", "tts", REF_TTS)
    for f in ("max_steps", "max_context_messages", "memory_char_limit"):
        if f in ns:
            _as_int(ns, f, 0)
    if "tool_timeout" in ns:
        _as_float(ns, "tool_timeout", 0)
    if not isinstance(ns.get("mcp_servers", []), list):
        raise ValueError("neuro_sama.mcp_servers 必须是列表")

    cb = _get_path(data.get("stream") or {}, "chatbot")
    _check_ref(data, "stream", "chatbot.model", REF_LLM)
    if cb is not None and not isinstance(cb, dict):
        raise ValueError("stream.chatbot 必须是对象")

    for name, schema in data.items():
        if name != SERVER_SCHEMA and not isinstance(schema, dict):
            raise ValueError(f"顶层 schema {name} 必须是对象")


def _validate_service(kind: str, sid: str, entry: Any) -> None:
    where = f"server.{'llm_services' if kind == REF_LLM else 'tts_services'}.{sid}"
    if not isinstance(entry, dict):
        raise ValueError(f"{where} 必须是对象")
    if kind == REF_LLM:
        for f in ("api_base_url", "api_key", "api_model_name", "display_name"):
            if f in entry and not isinstance(entry[f], str):
                raise ValueError(f"{where}.{f} 必须是字符串")
        if "api_extra_body" in entry and not isinstance(entry["api_extra_body"], dict):
            raise ValueError(f"{where}.api_extra_body 必须是 JSON 对象")
        if "timeout" in entry:
            _as_float(entry, "timeout", 0)
    else:
        t = str(entry.get("type") or "azure_tts")
        if t not in TTS_TYPES:
            raise ValueError(f"{where}.type 必须是 {' / '.join(TTS_TYPES)}（当前: {t!r}）")
        for f in ("azure_tts_region", "azure_tts_key", "display_name"):
            if f in entry and not isinstance(entry[f], str):
                raise ValueError(f"{where}.{f} 必须是字符串")
        if "timeout" in entry:
            _as_float(entry, "timeout", 0)


def _check_ref(data: Dict[str, Any], schema: str, dotted: str, kind: str) -> None:
    value = _get_path(data.get(schema) or {}, dotted)
    if value is None or value == "":
        return
    if not isinstance(value, str):
        raise ValueError(f"{schema}.{dotted} 必须是服务 id 字符串")
    if kind == REF_TTS and value in TTS_BUILTIN_IDS:
        return
    if value not in _services_of(data, kind):
        reg = "llm_services" if kind == REF_LLM else "tts_services"
        raise ValueError(f"{schema}.{dotted} 引用了不存在的服务 {value!r}（server.{reg}）")


# ---------- 加载 ----------


def resolve_workdir(cli_dir: Optional[str] = None) -> Path:
    """解析并确保工作目录存在；config.json 缺失时自动部署。"""
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
    print("[neuro-simulator] 请打开 dashboard 的服务配置页填写 LLM/TTS 服务与模型引用。")


class Config:
    """工作目录 + config.json 加载；服务注册表与模块 schema 的解析入口。"""

    def __init__(self, workdir: Path) -> None:
        self.WORKDIR: Path = workdir
        self.DATA_DIR: Path = workdir / MODULE_DATA_DIRS["neuro_sama"]
        self.DB_PATH: Path = self.DATA_DIR / "data.db"
        self.reload()

    def reload(self) -> None:
        """重新读取 config.json，校验并刷新全部派生字段（热重载入口）。"""
        path = self.WORKDIR / CONFIG_FILENAME
        if not path.exists():
            raise FileNotFoundError(
                f"{CONFIG_FILENAME} not found in {self.WORKDIR}; "
                f"expected it to be deployed from {EXAMPLE_FILENAME} on first start."
            )
        with path.open("r", encoding="utf-8") as f:
            try:
                self.data: Dict[str, Any] = json.load(f) or {}
            except json.JSONDecodeError as e:
                raise ValueError(f"{CONFIG_FILENAME} 不是合法 JSON: {e}") from e
        validate_config(self.data)

        ns = self.module("neuro_sama")
        self.HOST: str = str(ns.get("host", "127.0.0.1"))
        self.PORT: int = _as_int(ns, "port", 8000)
        self.SYSTEM_PROMPT_BASE: str = str(ns.get("system_prompt_base", DEFAULT_SYSTEM_PROMPT))
        self.MAX_STEPS: int = _as_int(ns, "max_steps", 10)
        self.MAX_CONTEXT_MESSAGES: int = _as_int(ns, "max_context_messages", 60)
        self.TOOL_TIMEOUT: float = _as_float(ns, "tool_timeout", 30.0)
        self.MEMORY_CHAR_LIMIT: int = _as_int(ns, "memory_char_limit", 4000)
        self.MCP_SERVERS: list = ns.get("mcp_servers", []) or []
        self.NEURO_MODEL_REF: str = str(ns.get("model") or "")
        self.NEURO_TTS_REF: str = str(ns.get("tts") or "")

    # ---- 通用访问 ----

    def module(self, name: str) -> Dict[str, Any]:
        """取某模块 schema 的原始 dict（不存在时返回空 dict）。"""
        value = self.data.get(name, {})
        return value if isinstance(value, dict) else {}

    def server(self) -> Dict[str, Any]:
        value = self.data.get(SERVER_SCHEMA, {})
        return value if isinstance(value, dict) else {}

    # ---- 服务解析（引用键 -> 运行时对象） ----

    def llm_service(self, ref: str) -> Optional[LLMService]:
        return get_llm_service(self.data, ref) if ref else None

    def tts_service(self, ref: str) -> Optional[TTSService]:
        return get_tts_service(self.data, ref) if ref else None

    def neuro_llm(self) -> Optional[LLMService]:
        """neuro_sama.model 引用的 LLM 服务；引用缺失/悬空返回 None。"""
        return self.llm_service(self.NEURO_MODEL_REF)

    def neuro_tts(self) -> Optional[TTSService]:
        """neuro_sama.tts 引用的 TTS 服务；""=关闭、"null"=内置虚拟、其余返回服务或 None。"""
        ref = self.NEURO_TTS_REF
        if not ref or ref == "null":
            return None
        return self.tts_service(ref)

    def service_usage(self) -> Dict[str, Dict[str, List[str]]]:
        return service_usage(self.data)


def module_url(cfg: Config, name: str) -> Optional[str]:
    """模块访问 URL：schema 的 external_url 优先，否则 http://{host}:{port}；未配置返回 None。

    vedal 的代理/健康检查与各模块间互访（如 stream 调 neuro）共用同一约定。
    """
    ns = cfg.module(name)
    ext = str(ns.get("external_url") or "").strip()
    if ext:
        return ext.rstrip("/")
    host, port = ns.get("host"), ns.get("port")
    if host and port is not None:
        return f"http://{host}:{port}"
    return None


_config: Optional[Config] = None


def init_config(cli_dir: Optional[str] = None) -> Config:
    """入口处调用一次：解析工作目录、加载配置。

    监听地址等全部参数只认 config.json（无 CLI 覆盖）——避免 reload()
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
