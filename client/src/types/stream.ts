export interface StreamSession {
  session_id: string
  queue_id: string
  channel: string
  conversation_id: string
  cursor: number
  highlight_cursor: number
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
  highlight_type: string | null
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
  /** 该场景是否允许高亮消息进入队列消费 */
  highlight_messages: boolean
  elements: SceneElement[]
}

/** vedal `GET /public/stream_view` 的匿名针眼投影：观看页真正消费的字段。
 *  全量 `/stream/status`（last_error / neuro_link.url / conversation_id /
 *  chatbot 配置）属锁内管理面；形状由 test_vedal_auth 契约钉死。 */
export interface ViewerStatus {
  /** loop.state：offline / starting / live / paused */
  state: string
  /** metadata.live——对外是否在播的唯一判据（record 恒 false） */
  live: boolean
  title: string | null
  category: string | null
  tags: string[]
  /** 是否存在本场（不再暴露 session/conversation 原始 id） */
  has_session: boolean
  /** 开场时刻（persisted 且未 offline 时非空，服务端已判好） */
  started_at: string | null
  audience: {
    viewers: number | null
    viewers_source: string | null
    updated_at: string | null
    /** 投影里恒 null：错误文本留在锁内的全量 status */
    error: string | null
  }
}
