import { ref, onMounted, onUnmounted } from 'vue'
import { api } from '@/api'

/**
 * 模块可达性轮询。
 * 观看页只与 vedal 通信；vedal 的匿名针眼 GET /public/modules_status 返回
 * `{modules: {<name>: bool}}`（只有布尔，没有 url/pid/error）。用公开端点而不是
 * /manage/modules：观看页是公网匿名页面，启用鉴权后 /manage/* 需要登录。
 */
export function useModuleStatus(moduleName: string, pollIntervalMs = 5000) {
  const reachable = ref(false)
  // 是否已完成首次检查（首次检查完成前展示 loading，而非误判为离线）
  const checked = ref(false)
  let timer: number | null = null

  async function check() {
    try {
      const data = await api<{ modules: Record<string, boolean> }>('/public/modules_status')
      reachable.value = data.modules[moduleName] === true
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
