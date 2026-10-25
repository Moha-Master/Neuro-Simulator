"""neuro 模块 HTTP/SSE 客户端：stream 作为 /chat 的调用方消费完整响应流。

chat() 是 async generator：解析 SSE（event:/data: 帧），每个事件以 dict 产出；
chat_batch() 走 `stream:false`，生成结束后拿整轮文本/segments（直播循环用它，
不再自己拼 speech 文本）。轮次节奏另读 status() 的 `speech.ends_at`。

短请求：health() 开播守卫、intro() 触发 neuro 的开场场景（返回**要等她离开的场景
id**，不是时长）、status() 拉轻量状态投影（权威场景 / 语音还剩多久）。
**stream 不倒数 neuro 的任何秒数**——开场是否放完由轮询 status 判定。
"""

import json
from typing import AsyncIterator, Optional

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
                    json={
                        "message": message,
                        "channel": channel,
                        "conversation_id": conversation_id,
                        # 流式消费者不看音频（音频归 neuro 窗口），别让 base64 占带宽
                        "audio": False,
                    },
                ) as resp:
                    if resp.status_code != 200:
                        body = (await resp.aread()).decode("utf-8", errors="replace")
                        raise NeuroError(f"neuro /chat HTTP {resp.status_code}: {body[:300]}")
                    async for ev in _parse_sse(resp.aiter_lines()):
                        yield ev
            except httpx.HTTPError as e:
                raise NeuroError(f"neuro 请求失败: {type(e).__name__}: {e}") from e

    async def chat_batch(
        self,
        message: str,
        channel: str,
        conversation_id: str,
        audio: bool = False,
        highlights: Optional[list[dict]] = None,
    ) -> dict:
        """非流式一轮生成：返回 `POST /chat {stream:false}` 的批量 JSON。

        轮次节奏不再自己算——`text` 取自 neuro 的整轮全量文本，`ends_at` 由调用方
        另查 `/status`。超时用"连接快、读不限"：生成可能几十秒到几分钟，真正的
        上限由调用方的 round deadline 兜底（否则上游丢响应会永久挂起）。

        `highlights` 非空时随请求体一起发给 neuro（高亮消息的元数据；neuro 在
        `start` 之后发 `highlight` 内容事件，由 neuro 窗口弹浮层）。普通轮不带该字段。
        """
        timeout = httpx.Timeout(30.0, read=None)
        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                body: dict = {
                    "message": message,
                    "channel": channel,
                    "conversation_id": conversation_id,
                    "audio": audio,
                    "stream": False,
                }
                if highlights:
                    body["highlights"] = highlights
                r = await client.post(f"{self.base_url}/chat", json=body)
                if r.status_code != 200:
                    body = r.text[:300]
                    raise NeuroError(f"neuro /chat HTTP {r.status_code}: {body}")
                return r.json()
            except httpx.HTTPError as e:
                raise NeuroError(f"neuro 请求失败: {type(e).__name__}: {e}") from e
            except ValueError as e:
                raise NeuroError(f"neuro /chat 响应不是 JSON: {e}") from e

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
        """探测 neuro 是否可达（开播前守卫）。失败抛 NeuroError。"""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(f"{self.base_url}/health")
                r.raise_for_status()
                return r.json()
        except httpx.HTTPError as e:
            raise NeuroError(f"neuro 健康检查失败: {type(e).__name__}: {e}") from e

    async def intro(self) -> str:
        """触发 neuro 窗口的开场场景，返回**她切到的场景 id**。失败抛 NeuroError。

        刻意不返回时长：stream 不倒数任何秒数。开场是否放完由 neuro 自己的
        `state_script` 决定，stream 只轮询 `/status` 等她**离开这个场景**
        （neuro 改了场景 id、换了视频素材、调了 `after` 余量，都不需要同步改 stream）。
        """
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(f"{self.base_url}/ui/intro")
                if r.status_code != 200:
                    body = (await r.aread()).decode("utf-8", errors="replace")
                    raise NeuroError(f"neuro /ui/intro HTTP {r.status_code}: {body[:300]}")
                scene_id = str((r.json().get("scene") or {}).get("id") or "").strip()
                if not scene_id:
                    raise NeuroError("neuro /ui/intro 未返回场景 id")
                return scene_id
        except (httpx.HTTPError, ValueError, AttributeError) as e:
            raise NeuroError(f"neuro 开场启动失败: {type(e).__name__}: {e}") from e

    async def chat_events(
        self,
        channel: Optional[str] = None,
        want: Optional[list[str]] = None,
        drop: Optional[list[str]] = None,
        audio: bool = True,
    ) -> AsyncIterator[dict]:
        """订阅 neuro 的内容总线（`GET /chat/events`）。

        与 `state_events` 相同：只是一层 SSE 解析，断线/非 200 抛 `NeuroError`，
        由调用方决定是否重连。`audio=0` 只在**这一条连接**的序列化层剥离音频。
        """
        params: dict[str, str] = {}
        if channel:
            params["channel"] = channel
        if want:
            params["want"] = ",".join(want)
        if drop:
            params["drop"] = ",".join(drop)
        if not audio:
            params["audio"] = "0"
        timeout = httpx.Timeout(5.0, read=None)
        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                async with client.stream(
                    "GET", f"{self.base_url}/chat/events", params=params
                ) as resp:
                    if resp.status_code != 200:
                        body = (await resp.aread()).decode("utf-8", errors="replace")
                        raise NeuroError(
                            f"neuro /chat/events HTTP {resp.status_code}: {body[:300]}"
                        )
                    async for ev in _parse_sse(resp.aiter_lines()):
                        yield ev
            except httpx.HTTPError as e:
                raise NeuroError(
                    f"neuro /chat/events 失败: {type(e).__name__}: {e}"
                ) from e

    async def state_events(
        self, want: Optional[list[str]] = None, drop: Optional[list[str]] = None
    ) -> AsyncIterator[dict]:
        """订阅 neuro 的状态总线（`GET /state/events`）。

        只是一层 SSE 解析：断线/非 200 抛 NeuroError，由调用方决定是否退回轮询
        （幂等对账是轮询的强项，订阅只是省掉"没有变化时的空转"）。
        """
        params: dict[str, str] = {}
        if want:
            params["want"] = ",".join(want)
        if drop:
            params["drop"] = ",".join(drop)
        timeout = httpx.Timeout(5.0, read=None)
        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                async with client.stream(
                    "GET", f"{self.base_url}/state/events", params=params
                ) as resp:
                    if resp.status_code != 200:
                        body = (await resp.aread()).decode("utf-8", errors="replace")
                        raise NeuroError(
                            f"neuro /state/events HTTP {resp.status_code}: {body[:300]}"
                        )
                    async for ev in _parse_sse(resp.aiter_lines()):
                        yield ev
            except httpx.HTTPError as e:
                raise NeuroError(
                    f"neuro /state/events 失败: {type(e).__name__}: {e}"
                ) from e

    async def status(self, channel: Optional[str] = None) -> dict:
        """拉 neuro 的状态投影（权威场景 + 语音节奏 + 在途轮次）。失败抛 NeuroError。

        轻量端点（不含音频），适合每半秒一次的轮询；中途连接续播走
        `/chat/events` 的首帧 snapshot（带 `consumed` 与正在发声句的音频）。
        """
        params = {"channel": channel} if channel else None
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(f"{self.base_url}/status", params=params)
                r.raise_for_status()
                return r.json()
        except httpx.HTTPError as e:
            raise NeuroError(f"neuro /status 失败: {type(e).__name__}: {e}") from e
