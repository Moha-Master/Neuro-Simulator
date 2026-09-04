import { ref, onMounted, onUnmounted } from 'vue'
import { api } from '@/stores/connection'

/**
 * 模块可达性轮询。
 * dashboard 只与 vedal 通信；vedal 的 GET /manage/modules 报告各模块可达性。
 * 用于在模块未运行时阻止进入其管理页面（如 Neuro Sama），并在模块停止后
 * （用户已在页面上）及时切换到离线态。
 */
export function useModuleStatus(moduleName: string, pollIntervalMs = 5000) {
  const reachable = ref(false)
  // 是否已完成首次检查（首次检查完成前展示 loading，而非误判为离线）
  const checked = ref(false)
  let timer: number | null = null

  async function check() {
    try {
      const data = await api<{ modules: Record<string, { reachable: boolean }> }>('/manage/modules')
      reachable.value = data.modules[moduleName]?.reachable ?? false
    } catch {
      // vedal 不可达或模块未列出 → 视为不可达
      reachable.value = false
    } finally {
      checked.value = true
    }
  }

  function start() {
    if (timer !== null) return
    void check()
    timer = window.setInterval(() => void check(), pollIntervalMs)
  }

  function stop() {
    if (timer !== null) {
      window.clearInterval(timer)
      timer = null
    }
  }

  onMounted(start)
  onUnmounted(stop)

  return { reachable, checked, check }
}
