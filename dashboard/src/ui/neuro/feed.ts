/** neuro 窗口的两条只读连接（SSE，各一条语义，生命周期对称）。
 *
 * | 平面 | 地址 | 用途 |
 * |---|---|---|
 * | 内容 | `/chat/events?channel=<ch>&audio=1&drop=reasoning,content` | 字幕逐句推进、音频、`tool_start` 立绘动作、`highlight` 高亮消息浮层、跨轮清空、首帧 snapshot 续播 |
 * | 状态 | `/state/events?want=scene,caption,layers,manifest` | 场景（含 `elapsed` 恢复时间线）、字幕设置、层显隐基线、清单变更 |
 *
 * 内容事件永远是全量（音频不裁），`drop` 只影响这条连接收到的类型；状态事件
 * 只推"离散变化"（`elapsed`/`remaining_s` 这类时间流逝不推，前端自己推演）。
 * `highlight` 是瞬时浮层事件（`start` 之后、正文之前），snapshot 不保存它——
 * 重连不续播已弹过的浮层。
 * 两条连接都靠 EventSource 原生重连，`close()` 一起关。
 */

import type { NeuroSections, Section } from '../../types/status'
import { apiUrl } from '../engine/base'

export interface FeedEvent {
  type: string
  [key: string]: unknown
}

export interface TurnSeg {
  step?: number
  index: number
  text: string
  duration: number
  consumed: number
  audio?: string
  mime?: string
}

/** 内容订阅的首帧（场景/字幕设置已移到状态平面，这里不再出现）。 */
export interface ContentSnapshot {
  type: string
  channel: string | null
  turn: { id: number, active: boolean, speech: TurnSeg[] } | null
}

export interface SceneInfo {
  id: string
  /** 场景有效时长（后端可能按视频轨道探测）；`at_end` 靠它换算 */
  duration: number
  /** 已进入场景的秒数（中途连接从这里续播） */
  elapsed: number
}

/** 状态订阅的一帧：`sections` 只含这一帧变化的分区（首帧是完整信封）。 */
export interface StateFrame {
  rev: number
  ts: string
  sections: Partial<NeuroSections>
}

export interface StateSnapshot extends StateFrame {
  module: string
}

export type SectionOf<K extends keyof NeuroSections> = NeuroSections[K] extends Section<infer T>
  ? T
  : never

const CONTENT_EVENT_NAMES = [
  'snapshot',
  'start',
  'reasoning',
  'content',
  'highlight',
  'speech',
  'tool_start',
  'tool_result',
  'usage',
  'done',
  'cancelled',
  'error',
]

const STATE_EVENT_NAMES = [
  'snapshot',
  'state',
]

type Handler = (ev: FeedEvent) => void

export class NeuroFeed {
  private contentEs: EventSource
  private stateEs: EventSource
  private contentHandlers = new Map<string, Set<Handler>>()
  private stateHandlers = new Map<string, Set<Handler>>()
  onStatus?: (plane: 'content' | 'state', connected: boolean) => void

  constructor () {
    this.contentEs = new EventSource(this.contentEndpoint())
    this.stateEs = new EventSource(this.stateEndpoint())

    this.contentEs.addEventListener('open', () => this.onStatus?.('content', true))
    this.contentEs.onerror = () => this.onStatus?.('content', false) // 原生自动重连
    this.stateEs.addEventListener('open', () => this.onStatus?.('state', true))
    this.stateEs.onerror = () => this.onStatus?.('state', false)

    this.listen(this.contentEs, this.contentHandlers, CONTENT_EVENT_NAMES, '内容')
    this.listen(this.stateEs, this.stateHandlers, STATE_EVENT_NAMES, '状态')
  }

  /** 页面 URL 可带 ?channel=stream；内容连接按它做服务端过滤，状态连接不过滤。 */
  private contentEndpoint (): string {
    const channel = new URLSearchParams(window.location.search).get('channel')
    const query = ['audio=1', 'drop=reasoning,content']
    if (channel) {
      query.unshift(`channel=${encodeURIComponent(channel)}`)
    }
    // 事件流是**模块 API**：走模块基址（/manage/neuro_sama/ 或直挂时的 /）
    return apiUrl(`chat/events?${query.join('&')}`)
  }

  private stateEndpoint (): string {
    return apiUrl('state/events?want=scene,caption,layers,manifest')
  }

  private listen (
    source: EventSource,
    handlers: Map<string, Set<Handler>>,
    names: string[],
    plane: string,
  ): void {
    for (const name of names) {
      source.addEventListener(name, (e: Event) => {
        try {
          const data = JSON.parse((e as MessageEvent).data) as FeedEvent
          this.dispatch(name, data, handlers)
        } catch (error) {
          console.warn(`neuro ${plane}事件帧解析失败`, error)
        }
      })
    }
  }

  onContent (type: string, fn: Handler): () => void {
    return this.register(this.contentHandlers, type, fn)
  }

  onState (type: string, fn: Handler): () => void {
    return this.register(this.stateHandlers, type, fn)
  }

  private register (handlers: Map<string, Set<Handler>>, type: string, fn: Handler): () => void {
    if (!handlers.has(type)) {
      handlers.set(type, new Set())
    }
    handlers.get(type)!.add(fn)
    return () => handlers.get(type)?.delete(fn)
  }

  private dispatch (type: string, ev: FeedEvent, handlers: Map<string, Set<Handler>>): void {
    ev.type = ev.type || type
    for (const fn of handlers.get(type) ?? []) {
      try {
        fn(ev)
      } catch (error) {
        console.warn(`neuro 事件 ${type} 处理异常`, error)
      }
    }
  }

  close (): void {
    this.contentEs.close()
    this.stateEs.close()
  }
}
