"""neuro 模块 HTTP/SSE 客户端：stream 作为 /chat 的调用方消费完整响应流。

chat() 是 async generator：解析 SSE（event:/data: 帧），每个事件以 dict 产出
（type 字段即事件名；speech 事件含 audio/duration，供循环计时与画面转发）。
"""

import json
from typing import AsyncIterator

import httpx


class NeuroError(Exception):
    pass


async def _parse_sse(lines) -> AsyncIterator[dict]:
    event_name = "message"
    data_lines: list[str] = []
    async for line in lines:
        if isinstance(line, bytes):
            line = line.decode("utf-8", errors="replace")
        line = line.rstrip("\r")
        if line.startswith("event: "):
            event_name = line[7:].strip()
        elif line.startswith("data: "):
            data_lines.append(line[6:])
        elif line == "":
            if data_lines:
                try:
                    payload = json.loads("\n".join(data_lines))
                except json.JSONDecodeError:
                    payload = {"type": event_name, "raw": "\n".join(data_lines)}
                payload.setdefault("type", event_name)
                yield payload
            event_name = "message"
            data_lines = []


class NeuroClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    async def chat(
        self, channel: str, conversation_id: str, message: str
    ) -> AsyncIterator[dict]:
        """发起一轮生成并流式消费事件。HTTP 非 200 抛 NeuroError。"""
        # read=None：LLM 生成全程可能长时间无字节
        timeout = httpx.Timeout(30.0, read=None)
        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                async with client.stream(
                    "POST",
                    f"{self.base_url}/chat",
                    json={"message": message, "channel": channel, "conversation_id": conversation_id},
                ) as resp:
                    if resp.status_code != 200:
                        body = (await resp.aread()).decode("utf-8", errors="replace")
                        raise NeuroError(f"neuro /chat HTTP {resp.status_code}: {body[:300]}")
                    async for ev in _parse_sse(resp.aiter_lines()):
                        yield ev
            except httpx.HTTPError as e:
                raise NeuroError(f"neuro 请求失败: {type(e).__name__}: {e}") from e

    async def stop(self, channel: str, conversation_id: str) -> None:
        """请求 neuro 中断该会话进行中的生成（尽力而为，失败不抛）。"""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                await client.post(f"{self.base_url}/manage/chats/{channel}/{conversation_id}/stop")
        except httpx.HTTPError:
            pass

    async def delete_session(self, channel: str, conversation_id: str) -> bool:
        """删除 neuro 侧的会话及消息。"""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.delete(f"{self.base_url}/manage/sessions/{channel}/{conversation_id}")
                return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def health(self) -> dict:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{self.base_url}/health")
            r.raise_for_status()
            return r.json()
