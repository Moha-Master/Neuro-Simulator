"""进程日志：内存是活体，磁盘是死档——两份存储、两个语义。

    内存环形缓冲  本进程此刻的输出，随进程消失；`GET /logs` / `GET /logs/events`
    磁盘文件      追加 + 轮转，进程死后仍可读；vedal 的 `GET /manage/logs/<模块>`

    <workdir>/logs/<模块名>.log      最新的内容（跨运行追加）
    <workdir>/logs/<模块名>.log.1    更早
    <workdir>/logs/<模块名>.log.2

为什么是这个形状：

- **网页读内存**。读网页的前提是进程活着（端点自己就是那个进程在应答），所以内存
  "进程死了就读不到"不是缺陷，而是与消费者同生同死的一致性。换来的是：没有轮转跟读、
  没有 inode 判断、不可能读到别的运行的尾巴，实时且零磁盘 I/O。
- **磁盘读死档**。进程崩了（OOM、`kill -9`、import 期异常）以后内存无人可读，vedal
  还活着，它替人去看那个进程最后写了什么。这也是 vedal 启动失败 detail 的数据源。
- **磁盘只追加、绝不启动截断**。曾经的设计是"打开即截断，让当前文件必然属于本次运行"
  ——那是因为它当时兼任网页主源。降级成死档之后，截断等于销毁证据：你想查"它昨天
  为什么死",重启的那一下就把上一世的记录清空了。"哪段属于本次运行"改由**运行分隔行**
  （`RUN_MARKER`）标注，磁盘于是保留跨运行的完整历史（受轮转上限约束）。
- **进程自己写，不由 vedal 重定向子进程**。与 PID 文件同一条约定："任何入口启动的
  实例都能被管理"（命令行手动、vedal 托管、vedal 崩溃后遗留）。日志若只认 vedal
  托管就破坏了这条对称性，手动跑起来的实例既没有内存端点之外的日志，也没人替它写。

上限（`MEMORY_MAX_BYTES` / `MAX_BYTES` / `BACKUPS`）是运维事实而不是业务参数，不进
config——与 `stream/audience.py` 的模块常量同一先例：改它只影响"占多少资源、能往回
看多少",没有业务含义。

ANSI 色码**原样保留**，两端都不剥：neuro 的终端输出用颜色承载语义（品红=reasoning、
黄=工具调用、橙=工具结果），读取端（dashboard）把它映射成 CSS。副作用：子进程继承
vedal 的 tty，uvicorn 是否上色取决于 vedal 自己有没有 tty。

已知盲区：崩在 `attach()` 之前（import 期异常、可执行文件本身坏了）时，内存和磁盘都
还没有内容，traceback 只出现在父终端里。`read_run_tail()` 会把这种情况明确说出来，
而不是端出上一个运行的尾巴冒充本次崩溃原因。
"""

import asyncio
import atexit
import json
import logging
import os
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, Union

LOG_DIRNAME = "logs"

# 内存环形缓冲的上限（每进程）：够看清"刚才发生了什么",又不至于吃掉几百 MB。
MEMORY_MAX_BYTES = 2 * 1024 * 1024

# 磁盘：单文件上限与备份份数，每模块总占用 ≤ MAX_BYTES * (1 + BACKUPS)。
MAX_BYTES = 5 * 1024 * 1024
BACKUPS = 2

# REST 首屏与 SSE 首帧的默认窗口。远小于内存上限：默认就该是"最近几分钟",
# 整段历史留给调用方显式要大值或读磁盘。
TAIL_MAX_BYTES = 256 * 1024

# 运行分隔行前缀（磁盘里每个进程启动写一行）。读端靠它认出"本次运行的开始",
# vedal 的启动失败 detail 靠它判断"这个 pid 到底有没有写下过日志"。
RUN_MARKER = "[log_kit] run start"

# 可调的日志等级。`trace` 刻意不在取值域内：uvicorn 认它会挂 MessageLoggerMiddleware，
# 比 access 行更吵；本项目也没有任何代码用 trace。
LEVELS = ("debug", "info", "warning", "error")
DEFAULT_LEVEL = "info"

# 噪音 logger：uvicorn 的 access 行恒以 INFO 记录（uvicorn 0.46 的 h11_impl 里写死），
# 没有"降成 DEBUG"的原生参数，只能抬门槛。只有用户主动选 debug 时才放开。
_NOISY_LOGGERS = ("uvicorn.access", "httpx", "httpcore")
# uvicorn 自己的非 access logger 跟项目等级走。
_UVICORN_LOGGERS = ("uvicorn.error", "uvicorn", "uvicorn.asgi")

_root_handler_installed = False


def resolve_level(module_cfg: dict) -> str:
    """从模块原始 dict 读 `log_level`；非法/缺失回落 `DEFAULT_LEVEL`，不抛。

    配置保存路径（`config.validate_config`）已经把非法值挡在 400 上；这里兜的是
    绕过保存直写文件的老配置——运行期不能因为一个字符串把进程打死。
    """
    if not isinstance(module_cfg, dict):
        return DEFAULT_LEVEL
    value = module_cfg.get("log_level")
    if isinstance(value, str):
        value = value.strip().lower()
        if value in LEVELS:
            return value
    return DEFAULT_LEVEL


def apply_levels(level: str) -> None:
    """把等级应用到本进程的 logging；幂等，且必须在 `attach()` 之后调用。

    之前项目的 logger 全部没有 handler：INFO 直接丢，WARNING 走 lastResort 裸行，
    dashboard 的 `classifyLine` 因此认不出级别。所以这里不只调 `setLevel`，还要在
    首次调用时给 root 挂一个绑到**当前** `sys.stdout`（即 tee）的 handler；handler
    带 `%(levelname)s:` 前缀，级别过滤才有得判。

    uvicorn 侧必须传 `log_config=None`：它会 dictConfig `uvicorn.access` 并把等级
    覆盖回 INFO，在 `uvicorn.run()` 之前 setLevel 是无效的。
    """
    if level not in LEVELS:
        level = DEFAULT_LEVEL
    numeric = getattr(logging, level.upper())

    global _root_handler_installed
    root = logging.getLogger()
    if not _root_handler_installed:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
        root.addHandler(handler)
        _root_handler_installed = True
    # 项目自己的 logger 全部 NOTSET，继承 root；不逐一列名单。
    root.setLevel(numeric)

    noisy = logging.DEBUG if level == "debug" else logging.WARNING
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(noisy)
    for name in _UVICORN_LOGGERS:
        logging.getLogger(name).setLevel(numeric)


def log_dir(workdir: Union[str, Path]) -> Path:
    return Path(workdir) / LOG_DIRNAME


def log_path(workdir: Union[str, Path], module: str) -> Path:
    return Path(workdir) / LOG_DIRNAME / f"{module}.log"


def backup_path(path: Path, index: int) -> Path:
    """轮转备份的命名约定：`<模块>.log.<index>`，index 从 1 开始（越大越旧）。"""
    return Path(f"{path}.{index}")


def _iso(epoch: Optional[float] = None) -> str:
    dt = datetime.fromtimestamp(epoch) if epoch is not None else datetime.now()
    return dt.astimezone().isoformat(timespec="seconds")


# ---------- 内存：环形缓冲 ----------


class MemoryLog:
    """本进程输出的环形缓冲：按字节上限从最旧一侧挤掉。

    给 SSE 的续播用的是 **seq**（块的单调编号）而不是字节偏移：缓冲被挤压时偏移会
    失去意义，而 seq 能诚实地说"你要的那段已经不在了"（`gap`），与 `/state/events`
    靠 `rev` 跳号提醒回读 `/status` 是同一条纪律。
    """

    def __init__(self, max_bytes: int = MEMORY_MAX_BYTES) -> None:
        self._max_bytes = max_bytes
        self._lock = threading.Lock()
        self._chunks: deque[tuple[int, str, int]] = deque()  # (seq, 文本, 字节数)
        self._total = 0
        self._next_seq = 1
        self._dropped_bytes = 0
        self._dropped_chunks = 0

    def append(self, text: str) -> None:
        if not text:
            return
        # 字节数在入块时算一次就存下来：snapshot/since 每次再 encode 一遍等于把
        # 整个缓冲重扫一次，而这两者都是"网页随时会拉"的热路径。
        size = len(text.encode("utf-8", errors="replace"))
        with self._lock:
            seq = self._next_seq
            self._next_seq += 1
            self._chunks.append((seq, text, size))
            self._total += size
            # 单块就超上限时不切分（宁可暂时超限，也不在一行中间断开缓冲）
            while self._total > self._max_bytes and len(self._chunks) > 1:
                _, _, old_size = self._chunks.popleft()
                self._total -= old_size
                self._dropped_bytes += old_size
                self._dropped_chunks += 1

    def snapshot(self, max_bytes: int = TAIL_MAX_BYTES) -> dict:
        """尾部若干字节 + 续播锚点（`seq` = 最后一块的编号）。"""
        with self._lock:
            parts: list[str] = []
            acc = 0
            truncated = False
            for _, text, size in reversed(self._chunks):
                if acc + size > max_bytes:
                    truncated = True
                    break
                parts.append(text)
                acc += size
            parts.reverse()
            return {
                "content": "".join(parts),
                "seq": self._next_seq - 1,
                "bytes": acc,
                "retained_bytes": self._total,
                "truncated": truncated,
                "dropped_bytes": self._dropped_bytes,
                "dropped_chunks": self._dropped_chunks,
            }

    def since(self, seq: int) -> dict:
        """`seq` 之后的内容。要的那段已被挤掉时 `gap=True`（客户端应回读 snapshot）。"""
        with self._lock:
            first_seq = self._chunks[0][0] if self._chunks else self._next_seq
            chunks = [(s, t) for s, t, _ in self._chunks if s > seq]
            return {
                "frames": [{"seq": s, "data": t} for s, t in chunks],
                "gap": seq < first_seq - 1 and seq >= 0,
                "first_seq": first_seq,
                "last_seq": self._next_seq - 1,
                "dropped_bytes": self._dropped_bytes,
            }

    @property
    def max_bytes(self) -> int:
        return self._max_bytes

    @property
    def last_seq(self) -> int:
        with self._lock:
            return self._next_seq - 1


# ---------- 磁盘：追加 + 重命名链轮转 ----------


class DiskSink:
    """日志文件写入端：本进程是该文件的 writer（跨进程可能并存旧 writer 已退出）。

    `backups=0` = 不轮转、一直追加（测试与"就要留全量"的场合用）。

    行缓冲落盘（遇到 `\\n` 才 flush）：既不必每个 token 一次 syscall（neuro 的逐字流
    很密），又保证"崩溃前的最后一行"大概率已经在文件里。
    """

    def __init__(
        self,
        path: Path,
        *,
        max_bytes: int = MAX_BYTES,
        backups: int = BACKUPS,
    ) -> None:
        self.path = path
        self._max_bytes = max_bytes
        self._backups = backups
        self._lock = threading.Lock()
        self._closed = False
        self._broken: Optional[str] = None
        path.parent.mkdir(parents=True, exist_ok=True)
        # 二进制 + 自己编码：字节数必须精确可算（轮转判据就是字节），且避开换行翻译。
        # **追加**：不截断——这个文件是进程死后的唯一证据来源。
        self._fh = open(path, "ab")
        self._size = self._fh.tell()
        atexit.register(self.close)

    @property
    def broken(self) -> Optional[str]:
        """写日志失败的原因（None=正常）。失败只降级，绝不拖垮宿主进程。"""
        return self._broken

    def write(self, text: str) -> None:
        if not text:
            return
        data = text.encode("utf-8", errors="replace")
        with self._lock:
            if self._broken is not None or self._closed:
                return
            try:
                if self._backups >= 1 and self._size + len(data) > self._max_bytes:
                    self._rotate()
                self._fh.write(data)
                self._size += len(data)
                if "\n" in text:
                    self._fh.flush()
            except OSError as e:
                self._broken = f"{type(e).__name__}: {e}"

    def flush(self) -> None:
        with self._lock:
            if self._broken is None and not self._closed:
                try:
                    self._fh.flush()
                except OSError as e:
                    self._broken = f"{type(e).__name__}: {e}"

    def _rotate(self) -> None:
        """重命名链：`.log.1`→`.log.2` … `.log`→`.log.1`，然后开新文件。

        调用方持锁。最旧的一份被挤出，所以磁盘占用有硬上限；被挤掉的是整段最旧的
        历史，不是某个文件的中途。
        """
        self._fh.flush()
        self._fh.close()
        for i in range(self._backups - 1, 0, -1):
            src = backup_path(self.path, i)
            if src.exists():
                os.replace(src, backup_path(self.path, i + 1))
        if self.path.exists():
            os.replace(self.path, backup_path(self.path, 1))
        self._fh = open(self.path, "ab")
        self._size = 0

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            for op in (self._fh.flush, self._fh.close):
                try:
                    op()
                except OSError:
                    pass


# ---------- tee：同一份输出分给内存与磁盘 ----------


class _TeeStream:
    """把写进来的文本原样交给原 stream，同时送内存与磁盘。

    除 `write`/`flush` 外的属性（`isatty`、`fileno`、`encoding`……）都代理给原
    stream：uvicorn 的彩色日志靠 `isatty()` 判断，判断错了就变成"终端上没颜色"。
    """

    def __init__(self, original, log: "ProcessLog") -> None:
        self._original = original
        self._log = log

    def write(self, text: str) -> int:
        # 先记账再透传：进程可能在 print 中途死掉，顺序反了会丢现场。
        self._log.record(text)
        return self._original.write(text)

    def writelines(self, lines) -> None:
        for line in lines:
            self.write(line)

    def flush(self) -> None:
        self._log.disk.flush()
        self._original.flush()

    def __getattr__(self, name):  # 其余能力全部交给被包的 stream
        return getattr(self._original, name)


@dataclass
class ProcessLog:
    """一个进程的日志（内存 + 磁盘 + 元信息），模块自己的 `/logs` 端点读它。"""

    module: str
    path: Path
    memory: MemoryLog
    disk: DiskSink
    pid: int = field(default_factory=os.getpid)
    started_at: str = field(default_factory=_iso)

    def record(self, text: str) -> None:
        self.memory.append(text)
        self.disk.write(text)

    def snapshot(self, max_bytes: int = TAIL_MAX_BYTES) -> dict:
        """给 `GET /logs` 的完整信封：内存尾部 + 元信息 + 磁盘位置（让人知道死档在哪）。"""
        mem = self.memory.snapshot(max_bytes)
        return {
            "module": self.module,
            "source": "memory",
            "pid": self.pid,
            "started_at": self.started_at,
            "seq": mem["seq"],
            "content": mem["content"],
            "bytes": mem["bytes"],
            "retained_bytes": mem["retained_bytes"],
            "memory_limit": self.memory.max_bytes,
            "truncated": mem["truncated"],
            "dropped_bytes": mem["dropped_bytes"],
            "dropped_chunks": mem["dropped_chunks"],
            "disk": disk_status(self.path),
        }


# attach 过的 module -> ProcessLog（本进程只装一个；测试里可多个）
_attached: dict[str, ProcessLog] = {}
_saved_streams: dict[str, tuple[object, object]] = {}


def attach(
    workdir: Union[str, Path],
    module: str,
    *,
    memory_bytes: int = MEMORY_MAX_BYTES,
    max_bytes: int = MAX_BYTES,
    backups: int = BACKUPS,
) -> Optional[ProcessLog]:
    """把本进程的 stdout/stderr 套一层 tee，同时写内存环形缓冲与磁盘文件。

    在每个 `main()` 里、`init_config()` 之后尽早调用。重复调用（同一 module）只装
    一次——装两次会让终端双写、内存里出现两份相同内容。

    打不开磁盘文件（只读文件系统、权限）时返回 None 并在终端提示一次，**不抛异常**：
    写不了日志不是停止服务的理由。但这种情况内存缓冲也就没了（tee 不装），
    所以它是个必须看见的告警，不是可以忽略的降级。
    """
    if module in _attached:
        return _attached[module]

    path = log_path(workdir, module)
    try:
        disk = DiskSink(path, max_bytes=max_bytes, backups=backups)
    except OSError as e:
        print(f"[log_kit] WARNING: 无法写日志文件 {path}: {type(e).__name__}: {e}", flush=True)
        return None

    log = ProcessLog(module=module, path=path, memory=MemoryLog(memory_bytes), disk=disk)
    out_tee = _TeeStream(sys.stdout, log)
    err_tee = _TeeStream(sys.stderr, log)
    _attached[module] = log
    _saved_streams[module] = (sys.stdout, sys.stderr)
    sys.stdout = out_tee  # type: ignore[assignment]
    sys.stderr = err_tee  # type: ignore[assignment]
    # 运行分隔行：磁盘跨运行追加，没有它就无法回答"这段属于哪一次"。
    marker = f"{RUN_MARKER} module={module} pid={log.pid} at={log.started_at}\n"
    log.record(marker)
    disk.flush()  # 分隔行必须马上到磁盘：它下面可能就是崩溃 traceback
    return log


def current(module: str) -> Optional[ProcessLog]:
    """本进程 attach 过的日志（模块的 `/logs` 路由用它；没装则 None）。"""
    return _attached.get(module)


def detach(module: str) -> None:
    """还原 stdout/stderr 并关闭磁盘（测试收尾用，避免污染同进程的其它应用）。"""
    log = _attached.pop(module, None)
    if log is None:
        return
    saved = _saved_streams.pop(module, (None, None))
    old_out, old_err = saved
    if old_out is not None and sys.stdout is not old_out:
        sys.stdout = old_out
    if old_err is not None and sys.stderr is not old_err:
        sys.stderr = old_err
    log.disk.close()


# ---------- 磁盘读端（进程死后仍可读） ----------


def disk_status(path: Union[str, Path]) -> dict:
    """磁盘上这个模块的日志文件清单（当前 + 备份，新→旧）与总占用。"""
    path = Path(path)
    files = []
    total = 0
    for candidate, current_flag in (
        (path, True),
        *((backup_path(path, i), False) for i in range(1, BACKUPS + 1)),
    ):
        try:
            st = candidate.stat()
        except OSError:
            continue
        total += st.st_size
        files.append(
            {
                "name": candidate.name,
                "size": st.st_size,
                "mtime": _iso(st.st_mtime),
                "current": current_flag,
            }
        )
    return {
        "path": str(path),
        "exists": path.exists(),
        "size": total,
        "limit": MAX_BYTES * (1 + BACKUPS),
        "files": files,
    }


def read_disk_tail(
    workdir: Union[str, Path],
    module: str,
    *,
    max_bytes: int = TAIL_MAX_BYTES,
) -> dict:
    """读当前文件的尾部（seek 读，不把整个文件吸进内存）。

    `truncated=True` 表示尾部之外还有内容没给——调用方必须能看出来"这不是全部",
    不能静默给一段尾巴让人以为日志就这么长。
    """
    path = log_path(workdir, module)
    info = {
        "module": module,
        "source": "disk",
        "content": "",
        "truncated": False,
        "disk": disk_status(path),
    }
    try:
        st = path.stat()
    except OSError:
        return info
    if st.st_size == 0:
        return info
    try:
        with open(path, "rb") as fh:
            start = max(0, st.st_size - max_bytes)
            info["truncated"] = start > 0
            fh.seek(start)
            raw = fh.read(st.st_size - start)
    except OSError as e:
        info["error"] = f"{type(e).__name__}: {e}"
        return info
    # 起点可能落在多字节字符中间：丢掉开头的替换符，别造出假乱码。
    text = raw.decode("utf-8", errors="replace")
    if info["truncated"]:
        text = text.lstrip("\ufffd")
    info["content"] = text
    return info


def read_run_tail(
    workdir: Union[str, Path],
    module: str,
    pid: int,
    *,
    max_bytes: int = 64 * 1024,
    lines: int = 5,
) -> str:
    """某次运行（按 pid）在磁盘上留下的输出末尾几行——vedal 启动失败 detail 用。

    截到**该 pid 的运行分隔行**为止，所以不会把上一个运行的尾巴冒充本次的崩溃原因
    （旧实现用追加模式读整个文件，进程秒崩、一行没写时端出来的就是旧日志）。
    找不到分隔行就明确说"本次没写下任何日志",而不是返回别人的内容。
    """
    tail = read_disk_tail(workdir, module, max_bytes=max_bytes)
    content = (tail.get("content") or "").strip()
    if not content:
        return "日志为空（进程可能在装 tee 之前就崩溃，traceback 只在父终端里）"
    marker = f"{RUN_MARKER} module={module} pid={pid}"
    lines_list = content.splitlines()
    for index in range(len(lines_list) - 1, -1, -1):
        if lines_list[index].startswith(marker):
            body = lines_list[index + 1 :]
            return " | ".join(body[-lines:])[:500] if body else "（该进程启动后未输出任何内容）"
    return (
        f"未找到 pid={pid} 的运行分隔行（本次输出超出读取窗口，或进程在装 tee 前退出）："
        f"{' | '.join(lines_list[-lines:])[:400]}"
    )


def clamp_bytes(log: "ProcessLog", max_bytes: int) -> int:
    """把请求的读取窗口夹进 `[1, 内存上限]`。

    单独拿出来是因为三个模块的 `/logs` 都要做这件事，而"上限就是环形缓冲的大小"
    属于本件的机制，不该由调用方各抄一份常量。
    """
    try:
        value = int(max_bytes)
    except (TypeError, ValueError):
        value = TAIL_MAX_BYTES
    return max(1, min(value, log.memory.max_bytes))


# ---------- SSE 帧（机制：内存跟读与降级判断；策略：端点形状在各模块 server） ----------


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def follow_frames(
    log: ProcessLog, since_seq: int, max_bytes: int = TAIL_MAX_BYTES
) -> tuple[list[str], int]:
    """返回 (要发出的 SSE 帧, 新的 seq 锚点)。

    `max_bytes` 只作用于**首帧 snapshot**（增量帧是"自上次以来的全部",不该被窗口切掉,
    否则会丢行）；调用方应先用 `clamp_bytes` 夹好。

    - 首连（`since_seq < 0`）：发完整 `snapshot` 帧，客户端拿 `seq` 当续播锚点。
    - 断线重连：只发 `seq` 之后的块；那段已被环形缓冲挤掉时发 `gap` + 重新 `snapshot`,
      客户端据此整屏重来（与 `/state/events` 的 `rev` 跳号→回读 `/status` 同一条纪律）。
    """
    if since_seq < 0:
        snapshot = log.snapshot(max_bytes)
        return [_sse("snapshot", snapshot)], snapshot["seq"]

    result = log.memory.since(since_seq)
    if result["gap"]:
        frames = [
            _sse(
                "gap",
                {
                    "requested_seq": since_seq,
                    "first_seq": result["first_seq"],
                    "dropped_bytes": result["dropped_bytes"],
                },
            ),
            _sse("snapshot", log.snapshot(max_bytes)),
        ]
        return frames, log.memory.last_seq

    frames = [_sse("log", frame) for frame in result["frames"]]
    return frames, result["last_seq"] if result["frames"] else since_seq


# ---------- SSE 跟随（机制：帧与节拍；策略：端点名在各模块 server） ----------

# 轮询节拍的取舍：内存跟读没有 I/O，0.5s 只是"多久醒一次"，不是磁盘 poll；
# 再快对人眼没有意义（日志是给人读的），再慢会让人觉得"程序卡了"。
LOG_POLL_S = 0.5
LOG_KEEPALIVE_S = 15.0


async def sse_stream(
    log: ProcessLog,
    *,
    since: int = -1,
    max_bytes: int = TAIL_MAX_BYTES,
    poll_s: float = LOG_POLL_S,
    keepalive_s: float = LOG_KEEPALIVE_S,
):
    """产出 SSE 帧文本：首帧 `snapshot`,之后 `event: log` 增量，空闲发 `: ping` 保活。

    与 `/state/events` 一样是"尽力而为"的总线：客户端断线后用 `?since=<seq>` 续播,
    那段已被环形缓冲挤掉则收到 `gap` + 完整 `snapshot`（整屏重来），不会静默丢帧。
    """
    frames, anchor = follow_frames(log, since, max_bytes)
    for frame in frames:
        yield frame
    last_beat = time.monotonic()
    while True:
        frames, anchor = follow_frames(log, anchor)
        if frames:
            for frame in frames:
                yield frame
            last_beat = time.monotonic()
        elif time.monotonic() - last_beat >= keepalive_s:
            yield ": ping\n\n"
            last_beat = time.monotonic()
        await asyncio.sleep(poll_s)
