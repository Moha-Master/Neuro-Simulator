"""直播信息工具：把工具调用转发到 stream 的 `PUT /stream/metadata`。

分工与 `games.py` 一致：**neuro 只发起调用并转述结果**；标题权威在 stream 的场次
记录里，改完由 stream 落库 + 广播 `stream_metadata` + 状态平面 metadata 分区。
所以这里不持有任何直播状态，stream 没开播 / 没配置时只返回一句人话，绝不抛异常。
"""

import httpx

from ..config import get_config, module_url
from .tools import Tool

STREAM_MODULE = "stream"
_TIMEOUT = 10.0  # 一次落库而已，不要沿用 games 的 120s（那是在等语音播完）
_MAX_TITLE = 140  # Twitch 上限；工具侧先挡，避免把必然被拒的超长标题甩给服务端


async def _set_title(title: str) -> str:
    """改本场标题；永远返回给模型读的文本，绝不 raise。"""
    text = str(title).strip() if title is not None else ""
    if not text:
        return "The stream title cannot be empty."
    if len(text) > _MAX_TITLE:
        return "The stream title is too long (max 140 characters)."

    base = module_url(get_config(), STREAM_MODULE)
    if not base:
        return "The stream service is not configured, so there is no stream title to change."

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.put(f"{base}/stream/metadata", json={"title": text})
    except httpx.HTTPError as e:
        return (
            f"The stream service is unreachable ({type(e).__name__}), "
            "so the title could not be changed."
        )

    if resp.status_code == 409:
        # 服务端 detail 是中文；这里给模型的是可直接转述的英文，不要原样透出。
        return (
            "You are not live right now, so there is no stream title to change. "
            "Start a stream first."
        )
    if resp.status_code >= 400:
        try:
            data = resp.json()
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        detail = data.get("detail")
        # 422 的 detail 是 list：str() 会输出 Python repr，只有字符串才值得拼进去。
        if isinstance(detail, str) and detail:
            return f"The stream rejected that title ({resp.status_code}): {detail}"
        return f"The stream rejected that title ({resp.status_code})."

    try:
        data = resp.json()
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    return f'Stream title updated to: "{data.get("title") or text}".'


def build_stream_tools() -> list[Tool]:
    async def stream_title_set(title: str) -> str:
        return await _set_title(title)

    return [
        Tool(
            "stream_title_set",
            "Change the title of the Twitch stream you are on right now (viewers see it at the "
            "top of the page). Use it when the topic of the stream changes. Only works while "
            "you are live; it changes the title of the current stream only.",
            {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "The new stream title, one short line.",
                    },
                },
                "required": ["title"],
            },
            stream_title_set,
        ),
    ]
