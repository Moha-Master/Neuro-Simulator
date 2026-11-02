/** chat.frame：渲染指向独立聊天页 `/ui/chat/` 的 iframe 并转发聊天管线。
 *
 * 与 ChatOverlay 的关系（设计定稿 §6）：
 * - `chat.overlay` 保留（内置场景继续用，直接渲染在合成器画布里）；
 * - `chat.frame` 是用户可自由摆放的**第二形态**：同一个构建里的第五入口
 *   `/ui/chat/`，数据由本组件经 postMessage 转发（ns `stream-chat`），
 *   **不新增 SSE**（连接账：合成器 1 条，iframe 页 0 条）。
 *
 * 协议（iframe → 画布）：`{ns:"stream-chat", type:"ready"}`；
 * （画布 → iframe）：`settings{font,color,direction,stageW}`、
 * `snapshot{messages}`、`chat_message{message}`、`highlight_message{message}`、
 * `clear`。本页与画布同源（vedal 静态托管）：方向校验 `event.source` +
 * `event.origin === location.origin`，发送用同源 targetOrigin。
 */

import type { ChatMsg } from './feed'
import { apiUrl } from '../engine/base'

interface ChatFrameProps {
  font?: number
  color?: string
  direction?: 'top' | 'bottom'
  /** stream 模块的表情索引端点（/ui/chat/ 页自己解析不到模块基址）。 */
  emotesUrl?: string
}

interface ChatSettings {
  font?: number
  color?: string
  direction?: 'top' | 'bottom'
  stageW?: number
  emotesUrl?: string
}

export class ChatFrame {
  private el: HTMLElement
  private iframe: HTMLIFrameElement
  private stage: HTMLElement
  private props: ChatFrameProps
  private observer: ResizeObserver | null = null
  private ready = false
  /** iframe 就绪前的消息缓冲：ready 后作为 snapshot 一次性补发。 */
  private pending: ChatMsg[] = []
  private lastSettings: ChatSettings = {}

  constructor (el: HTMLElement, props: Record<string, unknown>, stage: HTMLElement) {
    this.el = el
    this.stage = stage
    this.props = {
      font: Number(props.font) > 0 ? Number(props.font) : undefined,
      color: typeof props.color === 'string' ? props.color : undefined,
      direction: props.direction === 'top' ? 'top' : 'bottom',
      emotesUrl: apiUrl('emotes'),
    }
    this.iframe = document.createElement('iframe')
    // 兄弟入口：/ui/stream/ → /ui/chat/（同一构建、同一 origin、vedal 静态托管）。
    // **不加 sandbox**：/ui/chat/ 是第一方页，sandbox 的 opaque origin 会让它连
    // 自己的 module 脚本都加载不了（CORS）；同源代码沿用 neuro 窗口先例。
    // 用户上传的第三方 html（iframe kind）默认 sandbox="allow-scripts"，两者不同。
    this.iframe.src = new URL('../chat/', window.location.href).href
    this.iframe.title = 'chat'
    this.iframe.style.width = '100%'
    this.iframe.style.height = '100%'
    this.iframe.style.border = '0'
    el.classList.add('ns-chat-frame')
    el.append(this.iframe)
    window.addEventListener('message', this.onMessage)
    if (typeof ResizeObserver !== 'undefined') {
      // 舞台宽度变了 → 聊天页的字号基准跟着变（字号与元素尺寸解耦的另一半）
      this.observer = new ResizeObserver(() => this.sendSettings())
      this.observer.observe(stage)
    }
  }

  /** 元素引擎的通用状态通道之外，main.ts 直接调用的聊天管线方法。 */
  append (msg: ChatMsg): void {
    if (!this.ready) {
      this.pending.push(msg)
      return
    }
    this.post({ type: 'chat_message', message: msg })
  }

  replace (messages: ChatMsg[]): void {
    if (!this.ready) {
      this.pending = [...messages]
      return
    }
    this.post({ type: 'snapshot', messages })
  }

  clear (): void {
    this.pending = []
    if (this.ready) {
      this.post({ type: 'clear' })
    }
  }

  dispose (): void {
    window.removeEventListener('message', this.onMessage)
    this.observer?.disconnect()
    this.observer = null
    this.iframe.remove()
  }

  private onMessage = (event: MessageEvent): void => {
    if (event.source !== this.iframe.contentWindow) {
      return
    }
    if (event.origin !== window.location.origin) {
      return
    }
    const data = event.data as { ns?: string, type?: string } | undefined
    if (data?.ns !== 'stream-chat') {
      return
    }
    if (data.type === 'ready') {
      this.ready = true
      this.sendSettings()
      if (this.pending.length) {
        const buffered = this.pending
        this.pending = []
        this.post({ type: 'snapshot', messages: buffered })
      } else {
        this.post({ type: 'snapshot', messages: [] })
      }
    }
  }

  private sendSettings (): void {
    const settings: ChatSettings = {
      font: this.props.font,
      color: this.props.color,
      direction: this.props.direction,
      stageW: this.stage.clientWidth,
      emotesUrl: this.props.emotesUrl,
    }
    this.lastSettings = settings
    this.post({ type: 'settings', settings })
  }

  private post (payload: Record<string, unknown>): void {
    this.iframe.contentWindow?.postMessage({ ns: 'stream-chat', ...payload }, window.location.origin)
  }

  /** 供 E2E 观察 */
  get currentSettings (): ChatSettings {
    return this.lastSettings
  }
}
