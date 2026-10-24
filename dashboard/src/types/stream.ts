export interface StreamSession {
  session_id: string
  queue_id: string
  channel: string
  conversation_id: string
  cursor: number
  priority_cursor: number
  /** 本场标题 / 分区 / 标签（开播时可填、直播中可改） */
  title?: string | null
  category?: string | null
  tags?: string[]
}

/**
 * stream 模块的非状态类型。
 *
 * 状态机投影（`/stream/status` 与 vedal `/manage/state`）的类型统一在
 * `@/types/status.ts`——那边是契约守卫，别在这里再抄一份。
 */

export interface QueueMessage {
  id: number
  username: string
  content: string
  created_at: string
  /** bits / points；null = 普通观众消息 */
  priority_type: string | null
  /** 货币量（bits / points 数）；普通消息为 0 */
  value: number
}

export interface SessionRow {
  title?: string | null
  category?: string | null
  session_id: string
  queue_id: string
  channel: string
  conversation_id: string
  state: string
  started_at: string
  ended_at: string | null
  rounds: number
  tags?: string[]
}

/** 场景里的一个元素（清单来自画面侧 scenes.json，服务端只做概览转发）。 */
export interface SceneElement {
  id: string
  name: string
  /** color / image / video / text / component */
  content: string | null
  /** content=component 时的组件名，如 wordle.game */
  component: string | null
  z: number
  visible: boolean
}

export interface SceneSummary {
  id: string
  label: string
  icon: string
  /** 面向机器/LLM 的英文状态自述 */
  desc: string
  prompt_context: string
  elements: SceneElement[]
}
