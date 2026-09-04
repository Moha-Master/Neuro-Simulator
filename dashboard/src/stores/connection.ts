import { defineStore } from 'pinia'

export interface ModuleHealth {
  url: string | null
  reachable: boolean
}

/**
 * 连接/健康检查 store。
 * dashboard 只与 vedal 模块通信（dashboard 由 vedal 静态托管，天然同域）；
 * 健康检查即 dashboard <-> vedal 的 GET /health，vedal 顺带报告各模块可达性。
 */
export const useConnectionStore = defineStore('connection', {
  state: () => ({
    isVedalConnected: false,
    modules: {} as Record<string, ModuleHealth>,
    lastCheckAt: null as string | null,
    error: '',
  }),

  actions: {
    async checkHealth() {
      try {
        const resp = await fetch('/health', { headers: { Accept: 'application/json' } })
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
        const data = await resp.json()
        this.isVedalConnected = data.status === 'ok'
        this.modules = data.modules || {}
        this.lastCheckAt = new Date().toLocaleString()
        this.error = ''
      } catch (e) {
        this.isVedalConnected = false
        this.error = e instanceof Error ? e.message : 'Unknown error'
      }
    },
  },
})

export class ApiError extends Error {
  status: number
  constructor(status: number, detail: string) {
    super(detail)
    this.status = status
  }
}

/**
 * 把 API/网络错误转成面向用户的友好文案。
 * 502 = vedal 代理无法到达目标模块（模块未运行/崩溃）；503 = 模块未配置地址。
 */
export function describeError(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 502) return '目标模块未运行或不可达（请先启动该模块）'
    if (e.status === 503) return '目标模块未配置 host/port 或 external_url'
    return e.message
  }
  if (e instanceof TypeError && /fetch|network/i.test(e.message)) {
    return '网络请求失败（与 vedal 的连接已断开）'
  }
  return e instanceof Error ? e.message : 'Unknown error'
}

/** 与 vedal /manage 通信的通用 fetch 封装（同域相对路径）。 */
export async function api<T = any>(path: string, options: RequestInit = {}): Promise<T> {
  const resp = await fetch(path, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
  })
  const text = await resp.text()
  let data: any = null
  try {
    data = text ? JSON.parse(text) : null
  } catch {
    data = text
  }
  if (!resp.ok) {
    const detail =
      data && typeof data === 'object' && 'detail' in data
        ? JSON.stringify((data as Record<string, any>).detail)
        : text || `HTTP ${resp.status}`
    throw new ApiError(resp.status, detail)
  }
  return data as T
}
