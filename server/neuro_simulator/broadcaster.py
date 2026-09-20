"""事件广播总线（模块间共享基建）：服务端事件 → 多路 SSE 订阅者 fan-out。

用途：SSE 端点把"生成/直播事件"同时投递给 HTTP 响应之外的观察者
（如 stream 模块的 /ui 画面订阅）。定位是只读镜像出口——不承载任何
控制语义，不知道也不关心谁在看。

投递语义：尽力而为。订阅者队列满时丢弃该事件（镜像允许有损，
断线重连由订阅方的 snapshot 兜底），绝不阻塞发布方主循环。
"""

import asyncio


class EventBroadcaster:
    def __init__(self, maxsize: int = 256) -> None:
        self._maxsize = maxsize
        self._subscribers: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=self._maxsize)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def publish(self, event: dict) -> None:
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass  # 慢消费者丢事件，不阻塞

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)
