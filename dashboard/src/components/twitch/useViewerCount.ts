import { computed, onMounted, onUnmounted, ref } from 'vue'

/**
 * 频道观众数：沿用旧 client 的 TwitchTracker 数据源。
 *
 * TwitchTracker 提供的是**平均观众**（不是实时在线数），但它是旧 client 里
 * `#avg-viewers` 的唯一来源；接口允许跨域，拿不到时显示 “—”，不编造数字。
 */
const ENDPOINT = 'https://twitchtracker.com/api/channels/summary/vedal987'
const REFRESH_MS = 5 * 60 * 60 * 1000
const RETRY_MS = 60 * 1000
const TIMEOUT_MS = 8000

export function useViewerCount() {
  const viewers = ref<number | null>(null)
  const available = ref(false)
  let timer: number | null = null
  let disposed = false

  function schedule(delay: number): void {
    if (disposed) return
    if (timer !== null) window.clearTimeout(timer)
    timer = window.setTimeout(() => void load(), delay)
  }

  async function load(): Promise<void> {
    const controller = new AbortController()
    const timeout = window.setTimeout(() => controller.abort(), TIMEOUT_MS)
    try {
      const resp = await fetch(ENDPOINT, { signal: controller.signal })
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
      const data = (await resp.json()) as { avg_viewers?: unknown }
      if (disposed) return
      if (typeof data.avg_viewers === 'number') {
        viewers.value = data.avg_viewers
        available.value = true
        schedule(REFRESH_MS)
      } else {
        throw new Error('bad payload')
      }
    } catch {
      if (disposed) return
      // 拿不到就保持 “—”，60s 后重试（与旧 client 的退避思路一致）
      available.value = false
      schedule(RETRY_MS)
    } finally {
      window.clearTimeout(timeout)
    }
  }

  onMounted(() => void load())

  onUnmounted(() => {
    disposed = true
    if (timer !== null) window.clearTimeout(timer)
  })

  return {
    viewers,
    available,
    text: computed(() => (viewers.value === null ? '—' : viewers.value.toLocaleString('en-US'))),
    title: computed(() =>
      available.value ? 'TwitchTracker 平均观众（非实时）' : '观众数暂不可用',
    ),
  }
}
