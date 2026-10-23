/** neuro 窗口层：把 neuro 自己的 `/ui` 当作一块"窗口捕获"放进合成器。
 *
 * 与 OBS 的对应关系：
 * - iframe 固定 1920×1080 渲染（neuro 内部坐标不变），只对整窗做 translate+scale；
 * - 场景的 `neuro` 块声明窗口在舞台上的位置/大小/层级，以及要隐藏哪些 neuro 层；
 * - 父页面永远不进入 iframe 改内部元素——只发 visibility 开关（与 dashboard
 *   Neuro View 同一套 postMessage 协议）。
 *
 * 关键点：iframe **跨场景复用**，只改 wrapper 的几何/显隐，所以字幕、音频、
 * zoom、开场视频都不会因为切场景重新加载。
 */

import type { NeuroLayerConfig } from '../engine/schema'

export interface NeuroSettings {
  uiUrl: string
  channel: string
}

const pct = (v: number): string => `${(v * 100).toFixed(4)}%`

export class NeuroLayer {
  private wrap: HTMLElement
  private frame: HTMLIFrameElement
  private config: NeuroLayerConfig | null = null
  private settings: NeuroSettings
  private ready = false
  private available = true
  private observer: ResizeObserver | null = null

  constructor (stage: HTMLElement, settings: NeuroSettings) {
    this.settings = settings

    this.wrap = document.createElement('div')
    this.wrap.className = 'neuro-window'
    this.wrap.style.position = 'absolute'
    this.wrap.style.overflow = 'hidden'
    this.wrap.style.pointerEvents = 'none'
    this.wrap.style.display = 'none'

    this.frame = document.createElement('iframe')
    this.frame.className = 'neuro-window__frame'
    this.frame.title = 'Neuro window'
    this.frame.setAttribute('allow', 'autoplay; fullscreen')
    this.frame.style.width = '1920px'
    this.frame.style.height = '1080px'
    this.frame.style.border = '0'
    this.frame.style.background = 'transparent'
    this.frame.style.colorScheme = 'dark'
    this.frame.style.transformOrigin = '0 0'
    this.frame.style.display = 'block'

    this.wrap.append(this.frame)
    stage.append(this.wrap)

    window.addEventListener('message', this.onMessage)
    if (typeof ResizeObserver !== 'undefined') {
      this.observer = new ResizeObserver(() => this.fit())
      this.observer.observe(this.wrap)
    }
    this.load()
  }

  /** 更新 neuro UI 地址/channel；变化时只重载 iframe（罕见：配置修改）。 */
  setSettings (settings: NeuroSettings): void {
    const changed
      = settings.uiUrl !== this.settings.uiUrl || settings.channel !== this.settings.channel
    this.settings = settings
    if (changed) {
      this.load()
    }
  }

  /** 应用场景的 neuro 配置；undefined = 本场景不显示 neuro。 */
  setScene (config: NeuroLayerConfig | undefined): void {
    this.config = config ?? null
    if (this.config) {
      this.applyBox()
      this.applyZ()
    }
    this.applyVisible()
  }

  /** 健康检查结果：neuro 掉线时合成器显示 no_source，窗口层先隐藏。 */
  setAvailable (available: boolean): void {
    if (this.available === available) {
      return
    }
    this.available = available
    this.applyVisible()
  }

  destroy (): void {
    window.removeEventListener('message', this.onMessage)
    this.observer?.disconnect()
    this.observer = null
    this.wrap.remove()
  }

  // ---------- 内部 ----------

  private load (): void {
    this.ready = false
    if (!this.settings.uiUrl) {
      this.frame.removeAttribute('src')
      return
    }
    const url = new URL(this.settings.uiUrl, window.location.href)
    if (this.settings.channel) {
      url.searchParams.set('channel', this.settings.channel)
    }
    this.frame.src = url.toString()
  }

  private onMessage = (event: MessageEvent): void => {
    if (event.source !== this.frame.contentWindow) {
      return
    }
    const data = event.data as { ns?: string, type?: string } | undefined
    if (data?.ns !== 'neuro-window' || data.type !== 'ready') {
      return
    }
    this.ready = true
    this.applyVisibility()
  }

  private applyBox (): void {
    const c = this.config
    if (!c) {
      return
    }
    if (c.edge?.x === 'right') {
      this.wrap.style.right = pct(c.x)
      this.wrap.style.left = 'auto'
    } else {
      this.wrap.style.left = pct(c.x)
      this.wrap.style.right = 'auto'
    }
    if (c.edge?.y === 'bottom') {
      this.wrap.style.bottom = pct(c.y)
      this.wrap.style.top = 'auto'
    } else {
      this.wrap.style.top = pct(c.y)
      this.wrap.style.bottom = 'auto'
    }
    // 舞台 16:9，所以宽 60% 时高也正好是 60%（窗口本身 16:9）
    this.wrap.style.width = pct(c.w)
    this.wrap.style.height = pct(c.w)
    this.fit()
  }

  private applyZ (): void {
    this.wrap.style.zIndex = String(this.config?.z ?? 10)
  }

  private fit (): void {
    const width = this.wrap.clientWidth
    if (width <= 0) {
      return
    }
    this.frame.style.transform = `scale(${width / 1920})`
  }

  private applyVisible (): void {
    const visible = !!this.config && !this.config.hidden && this.available
    this.wrap.style.display = visible ? 'block' : 'none'
    if (visible) {
      this.fit()
      this.applyVisibility()
    }
  }

  private applyVisibility (): void {
    if (!this.ready || !this.config || this.config.hidden) {
      return
    }
    // 只发"要隐藏哪些层 id"：neuro 窗口对它自己场景里的每一层应用这个集合，
    // 所以新增一层（贴纸/告警横幅…）不必回头改这里的枚举。
    this.frame.contentWindow?.postMessage(
      {
        ns: 'neuro-window',
        type: 'visibility',
        hidden: this.config.hide ?? [],
      },
      '*',
    )
  }
}
