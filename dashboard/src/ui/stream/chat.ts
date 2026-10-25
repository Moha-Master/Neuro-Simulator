/** 直播画面聊天层。
 *
 * - ChatOverlay：左上角 Twitch 风格弹幕（jChat/ChatIS 风格：Segoe UI、
 *   800 字重、白字黑描边、Twitch 官方用户名色板、自动滚底）。
 *
 * 高亮消息的浮层归 neuro 窗口（`../neuro/highlight.ts`）：它在消息真正随
 * `POST /chat` 注入后才出现，属于 neuro 场景，不再是 stream 场景的元素。
 * 入列时这里只把消息当普通聊天行追加。
 */

import type { ChatMsg } from './feed'

/** Twitch 官方默认用户名色板（jChat/ChatIS 直接使用 IRC color tag；
 *  这里没有真实 color tag，用用户名哈希稳定映射，刷新/重连不换色）。 */
const USERNAME_COLORS = [
  '#FF0000',
  '#0000FF',
  '#00FF00',
  '#B22222',
  '#FF7F50',
  '#9ACD32',
  '#FF4500',
  '#2E8B57',
  '#DAA520',
  '#D2691E',
  '#5F9EA0',
  '#1E90FF',
  '#FF69B4',
  '#8A2BE2',
  '#00FF7F',
]

const MAX_LINES = 80
const MAX_SEEN = 1000

/** 稳定映射用户名 → Twitch 色板（小写后做 FNV-1a，Twitch 用户名不区分大小写）。 */
function colorFor (username: string): string {
  const key = username.toLowerCase()
  let hash = 0x81_1c_9d_c5
  for (let i = 0; i < key.length; i++) {
    hash ^= key.charCodeAt(i)
    hash = Math.imul(hash, 0x01_00_01_93)
  }
  // 取模必然落在数组内；`??` 只是为了满足 noUncheckedIndexedAccess
  return USERNAME_COLORS[(hash >>> 0) % USERNAME_COLORS.length] ?? '#FFFFFF'
}

export class ChatOverlay {
  private el: HTMLElement
  /** 内层承载字号：元素自身设 container-type，字号写在子节点上才能按本层宽度缩放 */
  private lines: HTMLElement
  /** 已渲染的消息 id（snapshot 与实时事件可能重叠，按 id 去重）。 */
  private seen = new Set<number>()

  constructor (el: HTMLElement) {
    this.el = el
    this.lines = document.createElement('div')
    this.lines.className = 'chat-lines'
    el.append(this.lines)
  }

  clear (): void {
    this.lines.replaceChildren()
    this.seen.clear()
  }

  /** snapshot 恢复：用最近 N 条整体替换（重连/刷新后聊天层不空）。 */
  replace (messages: ChatMsg[]): void {
    this.clear()
    for (const m of messages) {
      this.append(m)
    }
  }

  append (msg: ChatMsg): void {
    if (this.seen.has(msg.id)) {
      return
    }
    this.seen.add(msg.id)
    if (this.seen.size > MAX_SEEN) {
      this.seen.clear()
    }

    const line = document.createElement('div')
    line.className = 'chat-line__message'

    const userInfo = document.createElement('span')
    userInfo.className = 'chat-line__user-info'

    const user = document.createElement('span')
    user.className = 'chat-line__username'
    user.style.color = colorFor(msg.username || 'viewer')
    user.textContent = msg.username || 'viewer'

    const colon = document.createElement('span')
    colon.className = 'chat-line__colon'
    colon.textContent = ':'

    const text = document.createElement('span')
    text.className = 'text-fragment'
    text.textContent = msg.content

    userInfo.append(user, colon)
    line.append(userInfo, text)
    this.lines.append(line)

    // 只保留最近若干行，长时间直播不会无限堆 DOM
    while (this.lines.childElementCount > MAX_LINES) {
      this.lines.firstElementChild?.remove()
    }
    this.el.scrollTop = this.el.scrollHeight
  }
}
