/** 路径解析：前端资产相对**构建根**，模块 API 相对**托管这个页面的那个模块**。
 *
 * 三种东西、三套地址：
 * 1. **前端资产**（图片/字体）：dashboard 构建的 `public/` 根 → 绝对路径 `/x.png`。
 *    三个入口（dashboard 首页、合成器、neuro 窗口）都在同一个构建里，资产在根上，
 *    所以用绝对路径最省事，也不受页面路径影响。
 * 2. **模块 API**（事件流、场景清单）：属于**模块**，不随页面位置变化。页面可能由
 *    多种方式提供，对应多种基址：
 *    - 经 vedal 代理：页面自己的 `data-module-base`（如 `/manage/stream/`）；
 *    - 直挂模块：页面在 `/ui/` → 基址 `/`；
 *    - vite dev：页面在 `/` → 基址 `/`（靠 dev 代理）。
 * 3. **模块媒体**（开场视频）：文件在模块包里（后端要探测它的时长）→ 模块的
 *    `ui/media/{name}` 路由。
 *
 * 显式基址：页面元素上的 `data-module-base` 优先，其次 URL 参数 `?module-base=`。
 */

import type { AssetUrl } from './types'

/** 前端资产（构建根的 public/）。 */
export function rootAsset (path: string): string {
  const clean = path.replace(/^\.?\//, '')
  return new URL(`/${clean}`, window.location.origin).href
}

/** 显式基址：优先取元素上的 data-module-base，其次 URL 参数 ?module-base=。 */
function explicitModuleBase (): string | null {
  const el = document.querySelector<HTMLElement>('[data-module-base]')
  const attr = el?.dataset.moduleBase
  if (attr) {
    return attr
  }
  try {
    const q = new URLSearchParams(window.location.search).get('module-base')
    if (q) {
      return q
    }
  } catch {
    /* 忽略：URL 异常时按推导处理 */
  }
  return null
}

/** 模块 API 基址（末尾带 "/"）。 */
export function moduleBase (): string {
  const explicit = explicitModuleBase()
  if (explicit) {
    return explicit.endsWith('/') ? explicit : `${explicit}/`
  }
  const m = window.location.pathname.match(/^(\/manage\/[^/]+)\/ui(?:\/|$)/)
  return m ? `${m[1]}/` : '/'
}

/** 模块 API 的绝对地址：`apiUrl("ui/manifest")` → "<基址>ui/manifest"。 */
export function apiUrl (path: string): string {
  const clean = path.replace(/^\/+/, '')
  return new URL(moduleBase() + clean, window.location.href).href
}

/** 另一个模块的 API 基址（合成器要问 neuro 的健康状态）。 */
export function foreignModuleBase (attr: string, fallback: string): string {
  const el = document.querySelector(`[${attr}]`)
  const value = el?.getAttribute(attr)
  const base = value || fallback
  return base.endsWith('/') ? base : `${base}/`
}

/** 模块媒体（neuro 开场视频：后端要探测它的时长，文件归 ui/media）。 */
export function mediaUrl (name: string): string {
  return apiUrl(`ui/media/${name.replace(/^\.?\//, '')}`)
}

/** 模块资产（图片/视频/html 上传与内置素材；第 5 期起的统一寻址）。 */
export function moduleAssetUrl (name: string): string {
  return apiUrl(`ui/assets/${name.replace(/^\.?\//, '')}`)
}

/** 引擎的资产寻址（**按页面归属**区分模块，设计定稿 §5）：
 *
 * - `module: 'stream'`（合成器页）→ 图片/视频/html 全走本模块 `ui/assets`；
 *   `module: 'neuro'`（neuro 窗口页）→ 图片走 `ui/assets`，**视频保持
 *   `ui/media`**（开场视频有 mtime/轨道探测依赖，链路不动）。
 * - 外链 http(s)：素材类**渲染期拒绝**（console.warn + 不加载）——外链素材只允许
 *   经 `POST /ui/assets` 的 URL 导入转存后以本地名引用（"全部后端中转"原则）；
 *   唯一例外是 iframe kind 的 `src`（语义 = OBS 浏览器源，活页面直连，见第 6 期）。
 * - 以 `/`、`./`、`../` 开头的路径 = 前端构建产物（public/），保持 rootAsset。
 */
export function makeAssetUrl (module: 'stream' | 'neuro' = 'stream'): AssetUrl {
  return (src, kind) => {
    const clean = src.trim()
    if (/^https?:\/\//i.test(clean)) {
      if (kind === 'iframe') {
        return clean
      }
      console.warn(`[assets] 外链素材已停用，请用 URL 导入转存后引用: ${clean}`)
      return ''
    }
    if (clean.startsWith('/') || clean.startsWith('./') || clean.startsWith('../')) {
      return rootAsset(clean)
    }
    if (module === 'neuro' && kind === 'video') {
      return mediaUrl(clean)
    }
    return moduleAssetUrl(clean)
  }
}
