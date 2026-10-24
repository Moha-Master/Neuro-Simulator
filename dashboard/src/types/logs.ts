/**
 * 进程日志的类型契约（与后端字段逐字对齐）。
 *
 * 日志有两条来源，形状不同但**不能合并成一个宽松类型**：
 * - `memory` 只在进程活着时可读（模块自己的内存环形缓冲，经 vedal 代理）；
 * - `disk`   由 vedal 读文件，进程死了也能看。
 *
 * `source` 是判别字段：页面据此决定状态条、空态与"这不是实时输出"的提示。
 * 这里不用 `any` 兜底——后端改字段名时让 `vue-tsc` 直接报错。
 */

/** 日志窗口的来源：内存活体 / 磁盘死档。 */
export type LogSource = 'memory' | 'disk'

/** 三个可读日志的模块（vedal 自己由 `/manage/vedal/logs` 直接提供）。 */
export type LogModule = 'vedal' | 'neuro_sama' | 'stream'

export interface LogFileInfo {
  name: string
  size: number
  /** 文件 mtime（ISO 8601，带时区） */
  mtime: string
  /** true = 当前追加中的 `.log`；false = 轮转备份 `.log.N` */
  current: boolean
}

export interface LogDiskInfo {
  path: string
  /** 当前 `.log` 文件是否存在（不代表有内容） */
  exists: boolean
  /** 当前文件 + 备份的总占用 */
  size: number
  /** 轮转上限（总占用不会超过它） */
  limit: number
  /** 新 → 旧排列的文件清单 */
  files: LogFileInfo[]
}

/** `GET /manage/<module>/logs` 与 SSE 首帧 `snapshot` 的形状。 */
export interface MemoryLogSnapshot {
  module: LogModule
  source: 'memory'
  /** 本进程 PID（内存日志与进程同生共死） */
  pid: number
  /** 本次运行开始时间（ISO 8601，带时区） */
  started_at: string
  /** 最后一块的编号；SSE 续播的锚点（本页只读不续播） */
  seq: number
  /** 原文，可能含 ANSI 转义 */
  content: string
  /** 本次窗口返回的字节数 */
  bytes: number
  /** 内存里当前保留的总字节数 */
  retained_bytes: number
  /** 环形缓冲上限 */
  memory_limit: number
  /** true = 超出本次 `max_bytes` 窗口的更早内容没有返回 */
  truncated: boolean
  /** 因缓冲写满而被挤掉的字节数（这部分历史已不可回看） */
  dropped_bytes: number
  dropped_chunks: number
  /** 磁盘死档的位置与清单（用于说明"还能翻多少"） */
  disk: LogDiskInfo
}

/** `GET /manage/logs/<module>` 的形状（vedal 读磁盘，进程不在了也能读）。 */
export interface DiskLogSnapshot {
  module: LogModule
  source: 'disk'
  /** 当前 `.log` 文件的尾部窗口（可能含 ANSI） */
  content: string
  /** true = 文件尾部之外还有内容没给 */
  truncated: boolean
  disk: LogDiskInfo
  /** 读 PID 文件得到的存活 PID；没有则 null */
  pid: number | null
  /** `pid !== null` 即 true（只说明有进程，不说明可达） */
  running: boolean
}

export type LogSnapshot = MemoryLogSnapshot | DiskLogSnapshot

/** SSE `event: log` 的单帧。 */
export interface LogFrame {
  seq: number
  data: string
}

/** SSE `event: gap`：请求的续播点已被环形缓冲挤掉。 */
export interface LogGapFrame {
  requested_seq: number
  first_seq: number
  /** 累计被挤掉的字节数（不是本次缺口的精确大小） */
  dropped_bytes: number
}
