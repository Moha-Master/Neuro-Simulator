import { computed, ref, watch } from 'vue'
import { api } from '@/stores/connection'
import type { VedalState } from '@/types/status'

/**
 * vedal `GET /manage/state` 的共享轮询（模块级单例）。
 *
 * - **一次请求供多页共享**：Home 与 State 页读同一份 `state`，不各自发 HTTP；
 * - 5s 节拍与 `useModuleStatus` 一致；`document.hidden` 时停表，回前台立刻刷一次；
 * - `changed(module)` 用 rev 前后值判断"这帧比上一帧新"（粗粒度，够 UI 闪一下）；
 * - 只缓存最近一次聚合结果，不写业务状态（聚合层不缓存是后端纪律，前端也不攒第二份）。
 */

const POLL_MS = 5000

const state = ref<VedalState | null>(null)
const loading = ref(false)
const error = ref('')
const auto = ref(true)
const changedModules = ref<Record<string, boolean>>({})

let timer: number | null = null
let consumers = 0
let inflight: Promise<void> | null = null
let visibilityBound = false
const lastRev: Record<string, number> = {}

function syncTimer() {
  const shouldRun = auto.value && consumers > 0 && !document.hidden
  if (shouldRun && timer === null) {
    timer = window.setInterval(() => void refresh(), POLL_MS)
  } else if (!shouldRun && timer !== null) {
    window.clearInterval(timer)
    timer = null
  }
}

function onVisibility() {
  if (document.hidden) {
    syncTimer()
    return
  }
  syncTimer()
  if (consumers > 0) void refresh()
}

watch(auto, (on) => {
  syncTimer()
  if (on && consumers > 0) void refresh()
})

function refresh(): Promise<void> {
  // 并发调用共享同一个请求：页面切换/手动刷新撞上定时器时不多打一份
  if (inflight) return inflight
  inflight = (async () => {
    loading.value = true
    try {
      // 注意是 /manage/state：根路径 `/state` 归 dashboard 的 State 页
      //（API 曾挤在根上，把那个页面遮蔽成 JSON，刷新就"变成一串 JSON"）。
      const data = await api<VedalState>('/manage/state')
      const changed: Record<string, boolean> = {}
      for (const [name, mod] of Object.entries(data.modules)) {
        const rev = mod.status?.rev
        if (typeof rev === 'number') {
          changed[name] = lastRev[name] !== undefined && lastRev[name] !== rev
          lastRev[name] = rev
        } else {
          changed[name] = false
        }
      }
      changedModules.value = changed
      state.value = data
      error.value = ''
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Unknown error'
    } finally {
      loading.value = false
    }
  })().finally(() => {
    inflight = null
  })
  return inflight
}

function start() {
  consumers += 1
  if (!visibilityBound) {
    document.addEventListener('visibilitychange', onVisibility)
    visibilityBound = true
  }
  if (state.value === null) void refresh()
  syncTimer()
}

function stop() {
  if (consumers === 0) return
  consumers -= 1
  if (consumers === 0) {
    syncTimer()
    if (visibilityBound) {
      document.removeEventListener('visibilitychange', onVisibility)
      visibilityBound = false
    }
  }
}

export function useModuleState() {
  const modules = computed(() => state.value?.modules ?? {})

  return {
    state,
    modules,
    loading,
    error,
    auto,
    refresh,
    start,
    stop,
    /** 该模块这一帧的 rev 是否比上一帧新 */
    changed: (module: string) => changedModules.value[module] === true,
    /** 与聚合读结果的 reachable 保持一致（不额外开定时器） */
    reachable: (module: string) => modules.value[module]?.reachable === true,
  }
}

/** `observed_at` / `ts` 的相对时间；解析失败显示 —，不抛错。 */
export function relativeTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const ms = new Date(iso).getTime()
  if (Number.isNaN(ms)) return '—'
  const seconds = Math.max(0, Math.round((Date.now() - ms) / 1000))
  if (seconds < 1) return 'now'
  if (seconds < 60) return `${seconds}s ago`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  return `${Math.floor(hours / 24)}d ago`
}
