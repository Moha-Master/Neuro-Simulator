/** 高亮消息浮层（neuro 窗口的场景元素）。
 *
 * 高亮消息（bits / points）在真正随 `POST /chat` 注入后才由 neuro 的内容事件
 * `highlight` 驱动：额外弹一个 sc_purple / sc_pink 背景的高亮框，时序同旧
 * client：滑入 1s → 停留 8s → 滑出 1s。多条排队逐条展示，避免快速到达时互相
 * 覆盖。`setEnabled(false)`（intro 场景）会清空队列并忽略后续事件。
 */

import { asset } from './assets'

/** 高亮事件里的一条消息（与 stream 的 ChatMsg 同形，但不依赖它）。 */
export interface HighlightMessage {
  id?: number
  username?: string
  content: string
  highlight_type?: string | null
  value?: number
}

const SC_HOLD_MS = 8000
const SC_FADE_MS = 1000

export class HighlightOverlay {
  private el: HTMLElement
  private queue: HighlightMessage[] = []
  private showing = false
  private enabled = true
  private hideTimer: number | null = null
  private removalTimer: number | null = null

  constructor (el: HTMLElement) {
    this.el = el
    this.el.classList.add('hidden')
  }

  /** 场景是否显示高亮浮层（`scene.highlight_messages`）。 */
  setEnabled (enabled: boolean): void {
    this.enabled = enabled
    if (!enabled) {
      this.clear()
    }
  }

  show (msg: HighlightMessage): void {
    if (!this.enabled) {
      return
    }
    const type = msg.highlight_type
    if (type !== 'bits' && type !== 'points') {
      return
    }
    this.queue.push(msg)
    this.next()
  }

  clear (): void {
    this.queue = []
    this.clearTimers()
    this.showing = false
    this.el.classList.remove('is-visible')
    this.el.classList.add('hidden')
    this.el.replaceChildren()
  }

  private next (): void {
    if (this.showing) {
      return
    }
    const msg = this.queue.shift()
    if (!msg) {
      return
    }
    this.showing = true

    const bits = msg.highlight_type === 'bits'
    const value = Number(msg.value ?? 0)
    const label = `HIGHLIGHTED MESSAGE${
      value > 0 ? ` (${value} ${bits ? 'BITS' : 'POINTS'})` : ''
    }`

    this.el.innerHTML = `
      <img src="${asset(bits ? 'sc_purple.png' : 'sc_pink.png')}" class="sc-background-image" alt="">
      <div class="sc-content">
        <div class="sc-user"></div>
        <div class="sc-label">${label}</div>
        <div class="sc-message"></div>
      </div>`;

    // 用户输入一律走 textContent（不拼进 innerHTML）
    (this.el.querySelector('.sc-user') as HTMLElement).textContent = msg.username || 'viewer'
    const messageEl = this.el.querySelector('.sc-message') as HTMLElement
    messageEl.textContent = msg.content
    messageEl.style.color = bits
      ? 'var(--sc-purple-bg-color)'
      : 'var(--sc-pink-bg-color)'

    // 先移除 hidden 让初态（屏幕外）生效，强制 reflow 后再加 is-visible 触发 transition
    this.el.classList.remove('hidden')
    void this.el.offsetHeight
    this.el.classList.add('is-visible')

    this.hideTimer = window.setTimeout(() => {
      this.el.classList.remove('is-visible') // 滑出
      this.removalTimer = window.setTimeout(() => {
        this.el.classList.add('hidden')
        this.el.replaceChildren()
        this.showing = false
        this.next()
      }, SC_FADE_MS)
    }, SC_HOLD_MS + SC_FADE_MS)
  }

  private clearTimers (): void {
    if (this.hideTimer !== null) {
      window.clearTimeout(this.hideTimer)
      this.hideTimer = null
    }
    if (this.removalTimer !== null) {
      window.clearTimeout(this.removalTimer)
      this.removalTimer = null
    }
  }
}
