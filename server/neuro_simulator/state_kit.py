"""状态机投影机制（neuro / stream 共用）：**只放机制，不放策略**。

投影形状（两个模块的 `/status` 完全一致）：

    {"module": "stream", "rev": 17, "ts": "...+08:00",
     "sections": {"loop": {"current": {...}, "graph": {...}}, ...}}

几条纪律钉在代码里：

- **分区值是派生的，不是第二份存储**：provider 只能现读归属者已经持有的数据，
  不得自己攒副本、不得二次计算规则（上一轮 stream 自己重算 neuro 时间轴就是
  反例）。所以 provider 必须同步、廉价、无 I/O、不 await——这里用
  `inspect.iscoroutinefunction` 直接拒绝协程，把纪律变成启动期错误。
- **状态平面永不含音频/正文**：provider 返回什么由各模块决定，这条靠各模块
  的 provider 守住（`/status` 会被高频拉取）。
- **provider 抛异常不拖垮模块**：该分区降级成 `{"current": None, "error": ...}`，
  画面/循环照常跑；与"场景数据坏了不拖垮窗口"的既有纪律一致。
- **rev 供消费者做 gap 检测**：归属者改东西时 `note_change()` 喊一声就 +1
  （粗粒度，允许"喊了就加"）。`note_change` 同时把"哪些分区变了"报给可选的
  listener（`GET /state/events` 的推送源）；listener 同步、不得 await。
- **want/drop 是分区级过滤**：先取 want 再减 drop，`*` = 全部；过滤只影响输出，
  不是业务分支。本轮不做深层路径过滤。
"""

import inspect
import logging
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)


class StateBoard:
    """一个模块的状态分区注册表（每个模块建一个实例）。"""

    def __init__(self, module: str) -> None:
        name = str(module or "").strip()
        if not name:
            raise ValueError("StateBoard 的 module 不能为空")
        self._module = name
        self._rev = 0
        # dict 保序：输出顺序 = 注册顺序，方便人工对比两份 /status
        self._providers: Dict[str, Callable[[], dict]] = {}
        self._graphs: Dict[str, dict] = {}
        # 状态推送的接线点：note_change 发生时把 (rev, 分区名) 交给它。
        # listener 由 server 在启动时注册（组装 EventBroadcaster），机制层不知道
        # 推送怎么发；没注册时 note_change 只加 rev（单测/脚本直接用的情形）。
        self._listener: Optional[Callable[[int, tuple], None]] = None

    # ---------- 注册 ----------

    def register(
        self,
        name: str,
        provider: Callable[[], dict],
        *,
        graph: Optional[dict] = None,
    ) -> None:
        section = str(name or "").strip()
        if not section:
            raise ValueError("分区名不能为空")
        if section in self._providers:
            raise ValueError(f"分区重复注册: {section}")
        if not callable(provider):
            raise TypeError(f"分区 {section} 的 provider 必须可调用")
        if inspect.iscoroutinefunction(provider):
            # 见模块头：provider 无 I/O，绝不能 await
            raise TypeError(
                f"分区 {section} 的 provider 不能是协程函数（provider 必须同步、无 I/O）"
            )
        if graph is not None and not isinstance(graph, dict):
            raise TypeError(f"分区 {section} 的 graph 必须是对象")
        self._providers[section] = provider
        if graph is not None:
            self._graphs[section] = graph

    # ---------- 变更通知 ----------

    def set_listener(self, listener: Optional[Callable[[int, tuple], None]]) -> None:
        """注册变更监听（每个 board 至多一个：一个模块一条状态总线）。"""
        self._listener = listener

    def section_names(self) -> tuple:
        """已注册的分区名（注册顺序）。"""
        return tuple(self._providers)

    def note_change(self, *names: str) -> None:
        """归属者改了状态：rev +1（粗粒度，不按分区分别计数）。

        `names` 既作为"哪些分区受影响"的注释，也告诉推送层这一帧该带哪些分区；
        不传名字 = 让推送层带上全部分区（宁多勿缺）。
        """
        self._rev += 1
        if self._listener is not None:
            self._listener(self._rev, tuple(n for n in names if n in self._providers))

    # ---------- 读 ----------

    def read(
        self,
        want: Optional[Sequence[str]] = None,
        drop: Optional[Sequence[str]] = None,
    ) -> dict:
        sections: Dict[str, Any] = {}
        for name in self._select(want, drop):
            provider = self._providers[name]
            try:
                current = provider()
            except Exception as e:  # 状态投影坏了不能连累模块主流程
                logger.exception("状态分区 %s 的 provider 失败", name)
                sections[name] = {
                    "current": None,
                    "error": f"{type(e).__name__}: {e}",
                }
                continue
            entry: Dict[str, Any] = {"current": current}
            graph = self._graphs.get(name)
            if graph is not None:
                entry["graph"] = graph
            sections[name] = entry
        return {
            "module": self._module,
            "rev": self._rev,
            "ts": _local_iso(),
            "sections": sections,
        }

    def _select(
        self,
        want: Optional[Sequence[str]],
        drop: Optional[Sequence[str]],
    ) -> List[str]:
        names = list(self._providers)
        wanted = _names(want)
        dropped = _names(drop)
        if wanted is not None and "*" not in wanted:
            names = [n for n in names if n in wanted]
        if dropped is not None:
            if "*" in dropped:
                return []
            names = [n for n in names if n not in dropped]
        return names


def _names(raw: Optional[Sequence[str]]) -> Optional[set]:
    """把 query 里的 `a,b` / 列表归一成集合；None 表示"未指定"。"""
    if raw is None:
        return None
    items: List[str] = []
    # 方便直接传 "loop,scene"（query 参数解析在端点做，这里兼容字符串）
    groups = [raw] if isinstance(raw, str) else raw
    for item in groups:
        items.extend(str(item).split(","))
    return {p.strip() for p in items if p.strip()} or None


def _local_iso() -> str:
    """本地 ISO 时刻（带时区偏移）：运行机器在哪个时区就写哪个。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")
