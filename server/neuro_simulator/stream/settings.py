"""stream 模块私有配置的 typed view（读 config.yaml 的 stream schema）。

每次调用 load() 现取现算，天然支持热重载（reload 后无需重建对象）。
neuro 地址解析优先级：stream.neuro_url > module_url(cfg, "neuro_sama")。
"""

from dataclasses import dataclass

from ..config import get_config, module_url

# Twitch 的流标签限制：最多 10 个、每个最多 25 字符、大小写不敏感去重。
MAX_TAGS = 10
MAX_TAG_LENGTH = 25


def normalize_tags(tags: list[str]) -> list[str]:
    """清洗标签列表：去空白、去重（大小写不敏感）、校验数量/长度。

    空字符串直接忽略；超过限制抛 ValueError（由 API 层转 400）。
    """
    out: list[str] = []
    seen: set[str] = set()
    for raw in tags:
        tag = str(raw).strip()
        if not tag:
            continue
        if len(tag) > MAX_TAG_LENGTH:
            raise ValueError(f"标签最长 {MAX_TAG_LENGTH} 个字符：{tag!r}")
        key = tag.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(tag)
    if len(out) > MAX_TAGS:
        raise ValueError(f"最多只能有 {MAX_TAGS} 个标签")
    return out


@dataclass(frozen=True)
class ChatbotSettings:
    enabled: bool
    model_ref: str          # server.llm_services 中的服务 id（解析失败则三字段皆空）
    api_base_url: str
    api_key: str
    model: str              # 解析后的 api_model_name
    extra_body: dict
    timeout: float
    interval_s: float
    count: int
    neuro_sleep_timeout_s: float
    max_concurrent: int     # 并发生成上限（到上限的 tick 直接跳过）
    interval_jitter: float  # 间隔的 ±比例抖动，避免弹幕像节拍器


@dataclass(frozen=True)
class StreamSettings:
    neuro_url: str
    channel: str
    scene_context: str
    messages_per_round: int
    speech_cooldown_ms: int  # 语音播完后字幕停留 / 下一轮请求等待
    game_tool_wait_speech: bool  # game 工具执行前等当前语音播完
    empty_queue_behavior: str  # "placeholder" | "silent"
    placeholder_message: str
    default_title: str
    default_category: str
    default_tags: list[str]
    chatbot: ChatbotSettings


def load() -> StreamSettings:
    cfg = get_config()
    ns = cfg.module("stream")
    neuro_url = str(ns.get("neuro_url") or "").strip().rstrip("/")
    if not neuro_url:
        neuro_url = module_url(cfg, "neuro_sama") or ""
    if not neuro_url:
        raise RuntimeError(
            "stream.neuro_url 未配置，且无法从 neuro_sama schema 推断地址"
        )
    behavior = str(ns.get("empty_queue_behavior") or "placeholder")
    if behavior not in ("placeholder", "silent"):
        raise RuntimeError('stream.empty_queue_behavior 必须是 "placeholder" 或 "silent"')

    cb = ns.get("chatbot") if isinstance(ns.get("chatbot"), dict) else {}
    cb_enabled = bool(cb.get("enabled", True))
    cb_ref = str(cb.get("model") or "").strip()
    svc = cfg.llm_service(cb_ref)
    cb_base = svc.base_url if svc else ""
    cb_key = svc.api_key if svc else ""
    cb_model = svc.model if svc else ""
    cb_interval = float(cb.get("interval_s") if cb.get("interval_s") is not None else 10.0)
    cb_count = int(cb.get("count") if cb.get("count") is not None else 3)
    cb_sleep = float(cb.get("neuro_sleep_timeout_s") if cb.get("neuro_sleep_timeout_s") is not None else 45.0)
    # 并发上限：固定节拍调度器允许同时在途的生成任务数（1~10）
    cb_max_concurrent = int(cb.get("max_concurrent") if cb.get("max_concurrent") is not None else 3)
    cb_max_concurrent = max(1, min(cb_max_concurrent, 10))
    cb_jitter = float(cb.get("interval_jitter") if cb.get("interval_jitter") is not None else 0.2)
    cb_jitter = max(0.0, min(cb_jitter, 1.0))

    chatbot_settings = ChatbotSettings(
        enabled=cb_enabled,
        model_ref=cb_ref,
        api_base_url=cb_base,
        api_key=cb_key,
        model=cb_model,
        extra_body=dict(svc.extra_body) if svc else {},
        timeout=svc.timeout if svc else 60.0,
        interval_s=cb_interval,
        count=cb_count,
        neuro_sleep_timeout_s=cb_sleep,
        max_concurrent=cb_max_concurrent,
        interval_jitter=cb_jitter,
    )

    raw_tags = ns.get("default_tags")
    try:
        default_tags = normalize_tags(raw_tags if isinstance(raw_tags, list) else [])
    except ValueError as e:
        raise RuntimeError(f"stream.default_tags 不合法：{e}") from e

    raw_cooldown = ns.get("speech_cooldown_ms")
    speech_cooldown_ms = int(raw_cooldown if raw_cooldown is not None else 1000)
    speech_cooldown_ms = max(0, min(speech_cooldown_ms, 60_000))
    raw_game_wait = ns.get("game_tool_wait_speech", True)
    game_tool_wait_speech = raw_game_wait if isinstance(raw_game_wait, bool) else True

    return StreamSettings(
        neuro_url=neuro_url,
        channel=str(ns.get("channel") or "stream"),
        scene_context=str(ns.get("scene_context") or "You are streaming on Twitch now."),
        messages_per_round=int(ns.get("messages_per_round") or 10),
        speech_cooldown_ms=speech_cooldown_ms,
        game_tool_wait_speech=game_tool_wait_speech,
        empty_queue_behavior=behavior,
        placeholder_message=str(ns.get("placeholder_message") or "No new messages. Say something to your viewers!"),
        default_title=str(ns.get("default_title") or ""),
        default_category=str(ns.get("default_category") or ""),
        default_tags=default_tags,
        chatbot=chatbot_settings,
    )
