/** 直播画面聊天层。
 *
 * - ChatOverlay：Twitch 风格弹幕（jChat/ChatIS 风格：Segoe UI、
 *   800 字重、白字黑描边、Twitch 官方用户名色板）。
 * - 表情名按 tokenize 渲染成同源图片（`<img>`，绝不 innerHTML）；索引来自
 *   `GET /emotes`，到达后重渲染已有行。
 * - 滚动方向按元素 props 配置：
 *   - `bottom`（默认）：新消息追加到末尾、淘汰头行、单条平滑滚底；
 *   - `top`：新消息插到开头、淘汰尾行、始终保持 scrollTop=0
 *     （play_chess 左下框新消息浮顶）。
 *
 * 高亮消息的浮层归 neuro 窗口（`../neuro/highlight.ts`）：它在消息真正随
 * `POST /chat` 注入后才出现，属于 neuro 场景，不再是 stream 场景的元素。
 * 入列时这里只把消息当普通聊天行追加。
 */

import type { EmotesResponse } from '../../types/emotes'
import { tokenize } from '../../utils/chatEmotes'
import { apiUrl } from '../engine/base'
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
  /** 原始消息（按时间顺序），表情索引到达后据此重渲染。 */
  private messages: ChatMsg[] = []
  private direction: 'top' | 'bottom'
  /** name → 已拼好模块基址的同源 URL */
  private emoteIndex = new Map<string, string>()

  constructor (el: HTMLElement, props?: Record<string, unknown>) {
    this.el = el
    this.direction = props?.direction === 'top' ? 'top' : 'bottom'
    this.lines = document.createElement('div')
    this.lines.className = 'chat-lines'
    el.append(this.lines)
    void this.loadEmotes()
  }

  private async loadEmotes (): Promise<void> {
    try {
      const response = await fetch(apiUrl('emotes'))
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`)
      }
      const data = (await response.json()) as EmotesResponse
      const index = new Map<string, string>()
      for (const emote of data.emotes) {
        index.set(emote.name, apiUrl(emote.url.replace(/^\/+/, '')))
      }
      this.emoteIndex = index
      // 索引到达后把已有行从纯文本重渲染成图片（snapshot 可能先到）
      this.replace(this.messages)
    } catch (error) {
      // 失败静默：聊天仍然可用，只是表情保持纯文本
      console.warn('[chat] 表情索引不可用，按纯文本渲染:', error)
    }
  }

  clear (): void {
    this.lines.replaceChildren()
    this.seen.clear()
    this.messages = []
  }

  /** snapshot 恢复：用最近 N 条整体替换（重连/刷新后聊天层不空）。 */
  replace (messages: ChatMsg[]): void {
    this.lines.replaceChildren()
    this.seen.clear()
    this.messages = [...messages]
    for (const message of this.messages) {
      this.renderLine(message)
    }
    this.evict()
    this.settleScroll()
  }

  append (msg: ChatMsg): void {
    if (this.seen.has(msg.id)) {
      return
    }
    this.messages.push(msg)
    // 与 DOM 同步裁剪（无论哪个方向，最旧的都是 messages[0]），防长时间直播堆积
    if (this.messages.length > MAX_LINES) {
      this.messages.splice(0, this.messages.length - MAX_LINES)
    }
    this.renderLine(msg)
    this.evict()
    if (this.direction === 'top') {
      this.el.scrollTop = 0
    } else {
      this.el.scrollTo({ top: this.el.scrollHeight, behavior: 'smooth' })
    }
  }

  private renderLine (msg: ChatMsg): void {
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
    this.appendTokens(text, msg.content)

    userInfo.append(user, colon)
    line.append(userInfo, text)
    if (this.direction === 'top') {
      this.lines.prepend(line)
    } else {
      this.lines.append(line)
    }
  }

  /** 消息内容不可信：文本走 createTextNode，表情只建 img 节点，绝不 innerHTML。 */
  private appendTokens (target: HTMLElement, content: string): void {
    for (const token of tokenize(content, this.emoteIndex)) {
      if (token.type === 'text') {
        target.append(document.createTextNode(token.text))
        continue
      }
      const image = document.createElement('img')
      image.className = 'chat-emote'
      image.src = token.url
      image.alt = token.name
      image.title = token.name
      image.draggable = false
      target.append(image)
    }
  }

  private evict (): void {
    while (this.lines.childElementCount > MAX_LINES) {
      if (this.direction === 'top') {
        this.lines.lastElementChild?.remove()
      } else {
        this.lines.firstElementChild?.remove()
      }
    }
  }

  /** replace/snapshot 恢复用瞬时定位，不播滚动动画。 */
  private settleScroll (): void {
    if (this.direction === 'top') {
      this.el.scrollTop = 0
    } else {
      this.el.scrollTop = this.el.scrollHeight
    }
  }
}
