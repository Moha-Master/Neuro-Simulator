import { computed, onUnmounted, ref } from 'vue'
import { api, describeError } from '@/stores/connection'
import type {
  DiskLogSnapshot,
  LogFrame,
  LogGapFrame,
  LogModule,
  MemoryLogSnapshot,
} from '@/types/logs'
import { createAnsiStyle, parseAnsiLine } from '@/utils/ansi'
import type { AnsiLine, AnsiStyle } from '@/utils/ansi'

export type LogMode = 'live' | 'disk' | 'error'

/**
 * 一个模块的日志状态机（每个页面各建一份，**不做模块级单例**）。
 *
 * 语义：
 * 1. 打开就挂 EventSource，首帧 `snapshot` 整屏初始化；
 * 2. `onerror` 拿不到状态码，用普通 fetch 试磁盘端点：
 *    成功 → disk 模式（明确标注"不是实时输出"）；失败 → error；
 * 3. 连不上时自己每 5s 重建一次 EventSource：模块恢复后自动切回 live 并整屏重来。
 *    **不能只靠浏览器的原生重连**——EventSource 规范规定响应状态非 200 就
 *    "fail the connection" 并且不再尝试，而模块不可达时 vedal 代理恰好返回 502
 *    （最需要自动恢复的那条路径）。网络层错误（连接被拒绝）浏览器才会重连。
 * 4. `gap` 帧提示"有多少字节已不可回看"，随后那帧 snapshot 会重置内容。
 *
 * 解析状态（当前颜色/未完成行）跨 chunk 保留；只把已完成的完整行放进
 * `completed`，最后一行可能是半行（逐 token 流式输出），单独放在 `partial`。
 */
export function useModuleLogs(module: LogModule) {
  const mode = ref<LogMode>('live')
  const completed = ref<AnsiLine[]>([])
  const partial = ref<AnsiLine | null>(null)
  const seq = ref(0)
  const meta = ref<MemoryLogSnapshot | DiskLogSnapshot | null>(null)
  /** 是否自动滚到底部（用户向上滚动时由界面关掉） */
  const follow = ref(true)
  const notice = ref('')
  const error = ref('')
  /** 首次结果（snapshot / disk / error）到达前为 true */
  const loading = ref(true)

  let source: EventSource | null = null
  let closed = false
  /** 自建重连的定时器（EventSource 对 502 会彻底放弃重连，见文件头注释） */
  let retryTimer: number | null = null

  /** 重建间隔：与磁盘回落的限流同节拍，避免两条循环各跑各的频率 */
  const RECONNECT_MS = 5000

  // 滚动解析状态：style 是"下一行开头"的样式，pendingStyle 是本行开头的样式
  let style: AnsiStyle = createAnsiStyle()
  let pendingStyle: AnsiStyle = createAnsiStyle()
  let pendingRaw = ''

  // 磁盘回落去重：模块一直不启动时 EventSource 会反复 onerror，
  // 内容没变就不要重置整屏（否则滚动位置会被反复抢走）。
  let diskContent: string | null = null
  let fallbackAt = 0
  let fallingBack = false
  /** 每收到一帧 snapshot 递增；用于丢弃"回落请求发出后 live 已恢复"的过期响应 */
  let liveEpoch = 0

  const lines = computed<AnsiLine[]>(() =>
    partial.value ? [...completed.value, partial.value] : [...completed.value],
  )

  function appendChunk(chunk: string): void {
    if (!chunk) return
    const buffer = pendingRaw + chunk
    const parts = buffer.split('\n')
    pendingRaw = parts.pop() ?? ''

    for (const part of parts) {
      const parsed = parseAnsiLine(part, style)
      style = parsed.style
      completed.value.push(parsed.line)
    }

    // 下一个 chunk 会从这一行的开头重新解析；样式状态不能在这里推进。
    pendingStyle = style
    partial.value = pendingRaw ? parseAnsiLine(pendingRaw, pendingStyle).line : null
  }

  function resetContent(content: string): void {
    completed.value = []
    partial.value = null
    style = createAnsiStyle()
    pendingStyle = createAnsiStyle()
    pendingRaw = ''
    appendChunk(content)
  }

  function applySnapshot(snapshot: MemoryLogSnapshot): void {
    liveEpoch += 1
    fallbackAt = 0
    diskContent = null
    mode.value = 'live'
    meta.value = snapshot
    seq.value = snapshot.seq
    error.value = ''
    loading.value = false
    cancelReconnect()
    resetContent(snapshot.content)
  }

  function applyLogFrame(frame: LogFrame): void {
    // 只在 live 模式追加：disk 模式下的半帧来自另一个真相，等 snapshot 整屏重来。
    if (mode.value !== 'live') return
    appendChunk(frame.data)
    seq.value = frame.seq
  }

  async function fallbackToDisk(): Promise<void> {
    if (closed || fallingBack) return
    const now = Date.now()
    if (now - fallbackAt < 5000) return
    fallbackAt = now
    fallingBack = true
    const epoch = liveEpoch

    try {
      const snapshot = await api<DiskLogSnapshot>(`/manage/logs/${module}`)
      if (closed || epoch !== liveEpoch) return

      mode.value = 'disk'
      meta.value = snapshot
      seq.value = 0
      error.value = ''
      loading.value = false

      if (snapshot.content !== diskContent) {
        diskContent = snapshot.content
        resetContent(snapshot.content)
      }
    } catch (e) {
      if (closed || epoch !== liveEpoch) return
      mode.value = 'error'
      loading.value = false
      error.value = describeError(e)
    } finally {
      fallingBack = false
    }
  }

  function parseEvent<T>(event: Event): T | null {
    const data = (event as MessageEvent<string>).data
    try {
      return JSON.parse(data) as T
    } catch {
      // 坏帧不拖垮页面；EventSource 后续帧仍然照常处理。
      return null
    }
  }

  function cancelReconnect(): void {
    if (retryTimer !== null) {
      window.clearTimeout(retryTimer)
      retryTimer = null
    }
  }

  /**
   * 安排一次自建重连。
   *
   * 只在"还没回到 live"时生效：浏览器原生重连成功时 snapshot 会先到、
   * `mode` 变成 live，这里的定时器就自己作废了（见 applySnapshot）。
   */
  function scheduleReconnect(): void {
    if (closed || retryTimer !== null) return
    retryTimer = window.setTimeout(() => {
      retryTimer = null
      if (closed || mode.value === 'live') return
      source?.close()
      source = null
      connect()
    }, RECONNECT_MS)
  }

  function connect(): void {
    if (source) return
    closed = false
    source = new EventSource(`/manage/${module}/logs/events`)
    source.addEventListener('snapshot', (event: Event) => {
      if (closed) return
      const snapshot = parseEvent<MemoryLogSnapshot>(event)
      if (snapshot) applySnapshot(snapshot)
    })

    source.addEventListener('log', (event: Event) => {
      if (closed) return
      const frame = parseEvent<LogFrame>(event)
      if (frame) applyLogFrame(frame)
    })

    source.addEventListener('gap', (event: Event) => {
      if (closed) return
      const gap = parseEvent<LogGapFrame>(event)
      if (!gap) return
      notice.value =
        `有 ${gap.dropped_bytes.toLocaleString('en-US')} 字节已经超出内存缓冲窗口，` +
        '更早的请看磁盘。'
    })

    // EventSource 只有 onerror、拿不到状态码：这时才去试磁盘端点。
    // 注意不要 close()——网络层错误时浏览器仍会自动重连；但 502 之类
    // 非 200 响应会让它彻底放弃，所以另外安排自建重连（scheduleReconnect）。
    source.onerror = () => {
      void fallbackToDisk()
      scheduleReconnect()
    }
  }

  function close(): void {
    closed = true
    cancelReconnect()
    source?.close()
    source = null
  }

  function clearNotice(): void {
    notice.value = ''
  }

  // 组件卸载时统一断开；connect/close 仍对外暴露，便于未来手动重连。
  onUnmounted(close)

  return {
    mode,
    lines,
    seq,
    meta,
    follow,
    notice,
    error,
    loading,
    connect,
    close,
    clearNotice,
  }
}
