/** 独立聊天页 `/ui/chat/`（dashboard 构建第五入口，vanilla TS）。
 *
 * 渲染器复用 `stream/chat.ts` 的 ChatOverlay；数据由画布（chat.frame 组件）
 * 转发——ns `stream-chat`，本页**不建 SSE**（连接账：合成器 1 条，本页 0 条）。
 *
 * 协议见 `stream/chat-frame.ts` 头注。本页与画布**同源**（vedal 静态托管，第一方
 * 页面、不加 sandbox——opaque origin 连自己的 module 脚本都加载不了）：方向校验
 * `event.source === window.parent` 且 `event.origin === location.origin`。
 *
 * settings：字号 = 舞台宽（画布转发 stageW）× font；颜色/方向同协议下发。
 * 方向变化需要重建 ChatOverlay（构造时固定），重建后用本地消息恢复。
 */

import type { ChatMsg } from '../stream/feed'
import { ChatOverlay } from '../stream/chat'
import '../stream/styles/stream.css'
import './chat.css'

interface ChatSettings {
  font?: number
  color?: string
  direction?: 'top' | 'bottom'
  stageW?: number
  emotesUrl?: string
}

const root = document.querySelector<HTMLElement>('#chat-root')

let overlay: ChatOverlay | null = null
let messages: ChatMsg[] = []
let settings: ChatSettings = {}

function applySettings (): void {
  if (!root) {
    return
  }
  // 字号基准是**画布舞台**的宽度（不是 iframe 自己的宽）：由画布随舞台 resize 转发
  root.style.setProperty('--ns-stage-w', `${settings.stageW ?? window.innerWidth}px`)
  if (settings.font && settings.font > 0) {
    root.style.setProperty('--chat-font', String(settings.font))
  } else {
    root.style.removeProperty('--chat-font')
  }
  if (settings.color) {
    root.style.color = settings.color
  }
}

function buildOverlay (): void {
  overlay = null
  root?.replaceChildren()
  if (!root) {
    return
  }
  overlay = new ChatOverlay(root, {
    direction: settings.direction,
    font: settings.font,
    color: settings.color,
    emotesUrl: settings.emotesUrl,
  })
  overlay.replace(messages)
}

function applySnapshot (list: ChatMsg[]): void {
  messages = Array.isArray(list) ? [...list] : []
  overlay?.replace(messages)
}

window.addEventListener('message', (event: MessageEvent) => {
  // 同源父页面（chat.frame 是本构建的组件）
  if (window.parent === window || event.source !== window.parent) {
    return
  }
  if (event.origin !== window.location.origin) {
    return
  }
  const data = event.data as
    | {
      ns?: string
      type?: string
      settings?: ChatSettings
      messages?: ChatMsg[]
      message?: ChatMsg
    }
    | undefined
  if (data?.ns !== 'stream-chat') {
    return
  }
  if (data.type === 'settings') {
    const prev = { ...settings }
    settings = { ...settings, ...data.settings }
    applySettings()
    // 方向/表情端点变化需要重建 ChatOverlay（构造时固定）
    if (
      overlay
      && ((settings.direction ?? 'bottom') !== (prev.direction ?? 'bottom')
        || (settings.emotesUrl ?? '') !== (prev.emotesUrl ?? ''))
    ) {
      buildOverlay()
    }
    return
  }
  if (data.type === 'snapshot') {
    applySnapshot(data.messages ?? [])
    return
  }
  if (data.type === 'chat_message' || data.type === 'highlight_message') {
    if (!data.message) {
      return
    }
    messages.push(data.message)
    overlay?.append(data.message)
    return
  }
  if (data.type === 'clear') {
    messages = []
    overlay?.clear()
  }
})

applySettings()
buildOverlay()
// 通知画布（chat.frame）：可以补发 settings + snapshot 了
window.parent.postMessage({ ns: 'stream-chat', type: 'ready' }, '*')
