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

export interface StreamMetadata {
  title: string
  category: string
  /** Twitch 流标签（最多 10 个） */
  tags: string[]
  /** false = 无场次，此时值是 config 的默认值（作为下一场初值） */
  live: boolean
}

export interface ChatbotRuntime {
  enabled: boolean
  model: string | null
  model_ref: string | null
  interval_s: number
  count: number
  max_concurrent: number
  interval_jitter: number
  in_flight: number
}

export interface PersistedSession {
  session_id: string
  queue_id?: string
  channel: string
  conversation_id: string
  state: string
  started_at: string
  ended_at: string | null
  cursor_msg?: number
  rounds: number
  tags?: string[]
}

export interface StreamStatus {
  state: string
  /** 当前画面场景 id（starting 中为 intro） */
  scene: string | null
  /** 当前活跃游戏 id（无则 null） */
  game: string | null
  session: StreamSession | null
  metadata: StreamMetadata
  queue_depth_unconsumed: number | null
  priority_queue_depth_unconsumed: number | null
  chatbot: ChatbotRuntime
  persisted: PersistedSession | null
}

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
  prompt_context: string
  elements: SceneElement[]
}
