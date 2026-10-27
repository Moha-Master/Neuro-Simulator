/**
 * 与 vedal `/manage` 通信的通用 fetch 封装（同源**绝对**路径）。
 *
 * 观看页经 vedal 访问时（`http://<vedal>:8100/manage/stream/view/`）天然同源；
 * 直连 stream 的 `/view/` 时这些路径不可达，页面会退化为 ModuleOffline /
 * describeError 提示（不额外做路径探测）。
 *
 * 路径常量与 dashboard 保持一致：`/manage/stream/...`、`/manage/modules`。
 */

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

/** 与 vedal /manage 通信的通用 fetch 封装（同域绝对路径）。 */
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
