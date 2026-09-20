"""stream 模块私有配置的 typed view（读 config.yaml 的 stream schema）。

每次调用 load() 现取现算，天然支持热重载（reload 后无需重建对象）。
neuro 地址解析优先级：stream.neuro_url > module_url(cfg, "neuro_sama")。
"""

from dataclasses import dataclass

from ..config import get_config, module_url


@dataclass(frozen=True)
class ChatbotSettings:
    enabled: bool
    model_ref: str          # server.llm_services 中的服务 id（解析失败则三字段皆空）
    api_base_url: str
    api_key: str
    model: str              # 解析后的 api_model_name
    extra_body: dict
    timeout: float
    system_prompt: str
    interval_s: float
    count: int
    neuro_sleep_timeout_s: float


@dataclass(frozen=True)
class StreamSettings:
    neuro_url: str
    channel: str
    scene_context: str
    messages_per_round: int
    round_gap_ms: int
    empty_queue_behavior: str  # "placeholder" | "silent"
    placeholder_message: str
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
    default_prompt = (
        "You are a simulator for Twitch chatters watching an AI Vtuber stream. "
        "Generate {count} short, realistic Twitch viewer comments based on Neuro's status. "
        "Each comment should be on a new line in the format 'username: message'. "
        "Use typical Twitch slang, emotes (e.g. Pog, LUL, Kappa), or short reactions."
    )
    cb_prompt = str(cb.get("system_prompt") or default_prompt).strip()
    cb_interval = float(cb.get("interval_s") if cb.get("interval_s") is not None else 10.0)
    cb_count = int(cb.get("count") if cb.get("count") is not None else 3)
    cb_sleep = float(cb.get("neuro_sleep_timeout_s") if cb.get("neuro_sleep_timeout_s") is not None else 45.0)

    chatbot_settings = ChatbotSettings(
        enabled=cb_enabled,
        model_ref=cb_ref,
        api_base_url=cb_base,
        api_key=cb_key,
        model=cb_model,
        extra_body=dict(svc.extra_body) if svc else {},
        timeout=svc.timeout if svc else 60.0,
        system_prompt=cb_prompt,
        interval_s=cb_interval,
        count=cb_count,
        neuro_sleep_timeout_s=cb_sleep,
    )

    return StreamSettings(
        neuro_url=neuro_url,
        channel=str(ns.get("channel") or "stream"),
        scene_context=str(ns.get("scene_context") or "You are streaming on Twitch now."),
        messages_per_round=int(ns.get("messages_per_round") or 10),
        round_gap_ms=int(ns.get("round_gap_ms") or 1500),
        empty_queue_behavior=behavior,
        placeholder_message=str(ns.get("placeholder_message") or "No new messages. Say something to your viewers!"),
        chatbot=chatbot_settings,
    )
