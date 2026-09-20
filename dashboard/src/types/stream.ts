export interface StreamSession {
  session_id: string
  queue_id: string
  channel: string
  conversation_id: string
  cursor: number
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
}

export interface StreamStatus {
  state: string
  session: StreamSession | null
  queue_depth_unconsumed: number | null
  persisted: PersistedSession | null
}

export interface QueueMessage {
  id: number
  username: string
  content: string
  created_at: string
}

export interface SessionRow {
  session_id: string
  queue_id: string
  channel: string
  conversation_id: string
  state: string
  started_at: string
  ended_at: string | null
  rounds: number
}
