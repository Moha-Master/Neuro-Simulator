"""TTS 模块：句级语音合成（provider: azure | null）。

- azure：微软 Azure Speech，音色/音高与正版 Neuro 一致（en-US-AshleyNeural，
  pitch ×1.25 即 SSML +25%），输出 mp3（16kHz 32kbps mono）base64。
  认证信息来源：neuro_sama.tts 引用的 server.tts_services 条目（type=azure_tts）。
- null（虚拟 TTS）：不产音频，按文本量估算时长（常量近似 Ashley 语速），
  用于无真实订阅时跑通全链路（字幕/伪流式节奏不依赖真实音频）。

neuro_sama.tts 三态：""=关闭（不产 speech 事件）；"null"=内置虚拟 TTS；
其余为 tts_services 中的服务 id。音色、音高、语速等"声音本身"的参数写死在本模块。

synthesize() 永不抛异常：azure 失败/超时自动降级 null 估算并锁存
（避免每句都撞一次坏订阅）；热重载时 reset_latch() 重新探测。
"""

import asyncio
import base64
import html
import logging
import re
from typing import Optional, Tuple

from ..config import TTSService, get_config

logger = logging.getLogger(__name__)

# --- 声音规格（固定，不进配置） ---
VOICE_NAME = "en-US-AshleyNeural"
PITCH_MULTIPLIER = 1.25

# 虚拟 TTS 估算常量（近似 Ashley 语速：英文约 150-170 wpm）
NULL_SEC_PER_CHAR = 0.055
NULL_PAUSE_MINOR = 0.25   # , ; : ， 、 等
NULL_PAUSE_MAJOR = 0.5    # . ! ? 。 ！ ？ … 等
NULL_MIN_DURATION = 0.35

# azure 硬失败锁存（订阅失效场景：只撞一次）
_azure_broken = False

_PLACEHOLDER_KEYS = ("", "your-azure-tts-key-here", "your-azure-region-here")


def reset_latch() -> None:
    """配置热重载后重新允许 azure（可能补好了 key）。"""
    global _azure_broken
    _azure_broken = False


def _resolve() -> Tuple[bool, Optional[TTSService]]:
    """(是否启用, azure 服务或 None)。"null"/悬空服务 → 虚拟 TTS。"""
    cfg = get_config()
    ref = cfg.NEURO_TTS_REF
    if not ref:
        return False, None
    if ref == "null":
        return True, None
    return True, cfg.tts_service(ref)


def tts_enabled() -> bool:
    return _resolve()[0]


def _azure_usable(svc: Optional[TTSService]) -> bool:
    return bool(
        svc
        and svc.type == "azure_tts"
        and svc.azure_key not in _PLACEHOLDER_KEYS
        and svc.azure_region not in _PLACEHOLDER_KEYS
    )


def tts_state() -> dict:
    """供 /health 展示：配置引用与生效的 provider（含降级状态）。"""
    cfg = get_config()
    enabled, svc = _resolve()
    if not enabled:
        effective = "disabled"
    elif _azure_usable(svc) and not _azure_broken:
        effective = "azure"
    else:
        effective = "null"
    return {
        "configured_ref": cfg.NEURO_TTS_REF or None,
        "service": svc.id if svc else None,
        "effective_provider": effective,
        "degraded": bool(svc and _azure_usable(svc) and _azure_broken),
    }


def remove_emoji(text: str) -> str:
    """剔除 emoji（TTS 念不出来，也不该进字幕时长计算）。"""
    if not text:
        return ""
    emoji_pattern = re.compile(
        "["
        "\U0001f300-\U0001f5ff"  # symbols & pictographs
        "\U0001f600-\U0001f64f"  # emoticons
        "\U0001f680-\U0001f6ff"  # transport & map symbols
        "\U0001f900-\U0001f9ff"  # supplemental symbols & pictographs
        "\U0001fa70-\U0001faff"  # symbols & pictographs extended-A
        "\U00002600-\U000027bf"  # misc symbols + dingbats
        "\U0001f1e6-\U0001f1ff"  # regional indicators（国旗）
        "]+",
        flags=re.UNICODE,
    )
    # 注意：旧实现的 \U000024c2-\U0001f251 区间会误吞整个 CJK 汉字区，已剔除
    return emoji_pattern.sub("", text).strip()


def _estimate_duration(text: str) -> float:
    duration = NULL_SEC_PER_CHAR * len(text)
    duration += sum(NULL_PAUSE_MINOR for ch in text if ch in ",;:，、；：")
    duration += sum(NULL_PAUSE_MAJOR for ch in text if ch in ".!?。！？…")
    return max(NULL_MIN_DURATION, round(duration, 2))


def synthesize_null(text: str) -> dict:
    """虚拟 TTS：无音频，时长按文本估算（接口与 azure 对齐）。"""
    return {"audio": "", "mime": "", "duration": _estimate_duration(text)}


async def synthesize_azure(text: str, svc: TTSService) -> dict:
    import azure.cognitiveservices.speech as speechsdk

    speech_config = speechsdk.SpeechConfig(
        subscription=svc.azure_key, region=svc.azure_region
    )
    speech_config.set_speech_synthesis_output_format(
        speechsdk.SpeechSynthesisOutputFormat.Audio16Khz32KBitRateMonoMp3
    )
    pitch_percent = int((PITCH_MULTIPLIER - 1.0) * 100)
    pitch_ssml = f"+{pitch_percent}%" if pitch_percent >= 0 else f"{pitch_percent}%"
    ssml = f"""
    <speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="en-US">
        <voice name="{VOICE_NAME}">
            <prosody pitch="{pitch_ssml}">
                {html.escape(text)}
            </prosody>
        </voice>
    </speak>
    """
    synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=None)

    def _blocking_call() -> dict:
        return synthesizer.speak_ssml_async(ssml).get()

    result = await asyncio.wait_for(_blocking_call_in_thread(_blocking_call), timeout=svc.timeout)
    if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
        return {
            "audio": base64.b64encode(result.audio_data).decode("utf-8"),
            "mime": "audio/mpeg",
            "duration": round(result.audio_duration.total_seconds(), 3),
        }
    details = result.cancellation_details
    msg = f"Azure TTS 失败 (reason={details.reason})"
    if details.error_details:
        msg += f": {details.error_details}"
    raise RuntimeError(msg)


async def _blocking_call_in_thread(fn):
    return await asyncio.to_thread(fn)


async def synthesize(text: str) -> dict:
    """合成一句语音。永不抛异常，失败自动降级虚拟 TTS。"""
    global _azure_broken
    text = remove_emoji(text)
    if not text:
        return {"audio": "", "mime": "", "duration": 0.0}

    _enabled, svc = _resolve()
    if _azure_usable(svc) and not _azure_broken:
        try:
            return await synthesize_azure(text, svc)
        except asyncio.TimeoutError:
            logger.warning("Azure TTS 超时（%.1fs），本句降级虚拟 TTS：%s", svc.timeout, text[:30])
            return synthesize_null(text)
        except Exception as e:
            _azure_broken = True
            logger.warning("Azure TTS 异常，锁存降级为虚拟 TTS（reload 后重试）：%s", e)

    return synthesize_null(text)
