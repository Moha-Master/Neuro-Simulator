/** neuro 窗口的远程事件源（被 stream 合成器 iframe 捕获时使用）。
 *
 * 父页面（合成器）把 stream 后端聚合好的 `{plane, event, data}` 帧通过
 * `postMessage({ns:'neuro-window', type:'feed', event})` 送进来；本类只做两件事：
 *
 * 1. 按 `plane` 把帧分发给 `onContent` / `onState` 注册的 handler（与本地
 *    `NeuroFeed` 的签名一致，`main.ts` 的注册逻辑无需分叉）；
 * 2. 把首帧 snapshot 包成 neuro 直连时的形状——内容 `{type:'snapshot', turn}`、
 *    状态 `{type:'snapshot', sections:{<name>:{current}}}`。父页面缓存的是
 *    `mirror.snapshot()` 的 `sections.<name>.current`，这里补回 `current` 外壳。
 *
 * 没有 EventSource：一条 SSE 只由合成器持有。
 */

import type { FeedEvent } from './feed'

type Handler = (ev: FeedEvent) => void

interface FeedPayload {
  ns?: string
  type?: string
  event?: {
    plane?: string
    event?: string
    data?: unknown
  }
}

function objectData (raw: unknown): Record<string, unknown> {
  return raw && typeof raw === 'object' ? raw as Record<string, unknown> : {}
}

/** `{scene: current}` → `{scene: {current}}`；已经是 section 形状时原样保留。 */
function sectionEnvelope (raw: unknown): Record<string, unknown> {
  const sections = objectData(raw)
  const out: Record<string, unknown> = {}
  for (const [name, value] of Object.entries(sections)) {
    const hasCurrent = value !== null && typeof value === 'object' && 'current' in value
    out[name] = hasCurrent ? value : { current: value }
  }
  return out
}

export class RemoteFeed {
  private contentHandlers = new Map<string, Set<Handler>>()
  private stateHandlers = new Map<string, Set<Handler>>()

  constructor () {
    window.addEventListener('message', this.onMessage)
  }

  onContent (type: string, fn: Handler): () => void {
    return this.register(this.contentHandlers, type, fn)
  }

  onState (type: string, fn: Handler): () => void {
    return this.register(this.stateHandlers, type, fn)
  }

  close (): void {
    window.removeEventListener('message', this.onMessage)
    this.contentHandlers.clear()
    this.stateHandlers.clear()
  }

  // ---------- 内部 ----------

  private register (
    handlers: Map<string, Set<Handler>>,
    type: string,
    fn: Handler,
  ): () => void {
    if (!handlers.has(type)) {
      handlers.set(type, new Set())
    }
    handlers.get(type)!.add(fn)
    return () => handlers.get(type)?.delete(fn)
  }

  private onMessage = (event: MessageEvent): void => {
    const message = event.data as FeedPayload | undefined
    if (message?.ns !== 'neuro-window' || message.type !== 'feed') {
      return
    }
    const frame = message.event
    const plane = frame?.plane
    const name = typeof frame?.event === 'string' ? frame.event : ''
    if ((plane !== 'content' && plane !== 'state') || !name) {
      return
    }
    const data = objectData(frame?.data)
    let payload: FeedEvent
    if (plane === 'content') {
      payload = { ...data, type: name }
    } else if (name === 'snapshot') {
      // 父页面缓存的是 `sections.<name>.current`，补回 section 外壳
      payload = { type: 'snapshot', sections: sectionEnvelope(data.sections) }
    } else {
      payload = { ...data, type: name }
    }
    this.dispatch(name, payload, plane === 'content' ? this.contentHandlers : this.stateHandlers)
  }

  private dispatch (
    type: string,
    ev: FeedEvent,
    handlers: Map<string, Set<Handler>>,
  ): void {
    ev.type = ev.type || type
    for (const fn of handlers.get(type) ?? []) {
      try {
        fn(ev)
      } catch (error) {
        console.warn(`neuro 远程事件 ${type} 处理异常`, error)
      }
    }
  }
}
