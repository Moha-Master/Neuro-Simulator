/** stream 模块 /ui/events 合成器事件通道（SSE，只读）。
 *
 * 合成器只关心"场景/背景/游戏/聊天"这些 stream 自己的层：
 *   snapshot{state,scene{id,duration,elapsed},session,settings,chat?,metadata?,elements?,neuro?}
 *   state / session_begin / session_end / scene_set{id,duration,elapsed} /
 *   settings{neuro_*} /
 *   element_state{id,payload} / stream_metadata{title,category} /
 *   chat_message / highlight_message /
 *   neuro{plane:"content"|"state", event, data}（后端 NeuroMirror 转发的原始帧）
 *
 * 字幕/音频/立绘动作/高亮消息浮层**仍然只由 neuro 窗口消费**，但事件不再由 iframe
 * 直连：stream 后端订阅 neuro 的两条总线，经 `neuro` 事件原样转发；合成器再把
 * `neuro` 帧喂给 iframe（见 neuro.ts / main.ts）。这样浏览器只占一条 SSE。
 */

import { apiUrl } from '../engine/base'

export interface FeedEvent {
  type: string
  [key: string]: unknown
}

export interface ChatMsg {
  id: number
  username: string
  content: string
  created_at?: string
  highlight_type?: string | null
  value?: number
}

/** 场景状态（与 neuro 窗口的 scene_set 同一套契约：id + 时长 + 已过秒数）。 */
export interface SceneInfo {
  id: string
  duration?: number
  elapsed?: number
}

export interface NeuroSettings {
  neuro_ui_url?: string
  neuro_health_url?: string
  neuro_channel?: string
}

/** 后端 NeuroMirror 转发的一帧（`data` 与 neuro 直连时逐字段一致）。 */
export interface NeuroMirrorEvent extends FeedEvent {
  plane: 'content' | 'state'
  event: string
  data: Record<string, unknown>
}

export interface Snapshot {
  state: string // offline | starting | live | paused
  scene: SceneInfo | null
  session: {
    session_id: string
    queue_id: string
    channel: string
    conversation_id: string
  } | null
  settings?: NeuroSettings // neuro 窗口地址 / 健康检查地址 / channel
  chat?: ChatMsg[] // 最近聊天记录（重连时恢复聊天层）
  metadata?: { title?: string, category?: string } // 本场标题/分区（dashboard 观看页消费；画面本身不使用）
  elements?: Record<string, unknown> // 各元素层的全量状态（key = 元素 id，如 game_wordle）
  neuro?: {
    // 最近一轮 turn（与 neuro /chat/events 首帧同形；无在途轮次时 null）
    turn?: { id: number, active: boolean, speech: Array<Record<string, unknown>> } | null
    // 状态分区：key → /state/events snapshot 里 sections.<name>.current 的原样值
    state?: Record<string, unknown>
  }
}

const EVENT_NAMES = [
  'snapshot',
  'state',
  'session_begin',
  'session_end',
  'stream_metadata',
  'settings',
  'scene_set',
  'element_state',
  'chat_message',
  'highlight_message',
  'manifest_changed',
  'neuro',
]

/** 相对当前页面解析 API 端点（见 base.ts）。 */
function resolveEndpoint (name: string): string {
  // 事件流是**模块 API**：走模块基址（/manage/stream/ 或直挂时的 /）
  return apiUrl(`ui/${name}`)
}

type Handler = (ev: FeedEvent) => void

export class StreamFeed {
  private es: EventSource
  private handlers = new Map<string, Set<Handler>>()
  onStatus?: (connected: boolean) => void
  /** 所有事件的旁路回调（含 snapshot）：合成器用它把帧转发给父页面。 */
  onAny?: (type: string, ev: FeedEvent) => void

  constructor (url?: string) {
    this.es = new EventSource(url ?? resolveEndpoint('events'))
    this.es.addEventListener('open', () => this.onStatus?.(true))
    this.es.onerror = () => this.onStatus?.(false) // EventSource 原生自动重连
    for (const name of EVENT_NAMES) {
      this.es.addEventListener(name, (e: Event) => {
        try {
          const data = JSON.parse((e as MessageEvent).data) as FeedEvent
          this.dispatch(name, data)
        } catch (error) {
          console.warn('events 帧解析失败', error)
        }
      })
    }
  }

  on (type: string, fn: Handler): () => void {
    if (!this.handlers.has(type)) {
      this.handlers.set(type, new Set())
    }
    this.handlers.get(type)!.add(fn)
    return () => this.handlers.get(type)?.delete(fn)
  }

  private dispatch (type: string, ev: FeedEvent): void {
    ev.type = ev.type || type
    try {
      this.onAny?.(type, ev)
    } catch (error) {
      console.warn(`事件 ${type} 旁路转发异常`, error)
    }
    for (const fn of this.handlers.get(type) ?? []) {
      try {
        fn(ev)
      } catch (error) {
        console.warn(`事件 ${type} 处理异常`, error)
      }
    }
  }

  close (): void {
    this.es.close()
  }
}
