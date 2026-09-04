export interface Conversation {
  channel: string
  conversation_id: string
  title: string | null
  created_at: string
  updated_at: string
  /** 对话消息数（user + assistant 行，不含 tool 结果行） */
  message_count: number
  /** 发起工具调用的 assistant 消息数（"calls"） */
  tool_calls: number
  /** 实际执行的工具调用数（"used"，= tool 结果行数） */
  tool_uses: number
}

export interface ChatMessage {
  id: number
  role: 'user' | 'assistant' | 'tool' | 'system'
  content: string | null
  tool_calls?: ToolCallInfo[]
  tool_call_id?: string
  created_at?: string
}

export interface ToolCallInfo {
  id: string
  type: 'function'
  function: {
    name: string
    arguments: string
  }
}

export interface ToolCallDisplay {
  id: string
  name: string
  arguments: Record<string, unknown> | string
  result?: string
  ok?: boolean
  duration_ms?: number
}

export interface UsageInfo {
  step: number
  input_tokens: number
  output_tokens: number
  total_tokens: number
}

export interface DisplayMessage {
  key: string
  role: 'user' | 'assistant'
  content: string
  reasoning?: string
  tool_calls?: ToolCallDisplay[]
  usage?: UsageInfo[]
  createdAt?: string
  isStreaming?: boolean
}

export type SSEEventType =
  | 'start'
  | 'reasoning'
  | 'content'
  | 'tool_start'
  | 'tool_result'
  | 'usage'
  | 'done'
  | 'error'
  | 'cancelled'

export interface SSEEvent {
  type: SSEEventType
  [key: string]: unknown
}

export interface StartEventData {
  channel: string
  conversation_id: string
  model: string
}

export interface DoneEventData {
  content: string
  reasoning: string
  tool_calls: ToolCallDisplay[]
  total_usage: {
    input_tokens: number
    output_tokens: number
    total_tokens: number
  }
  _memory?: {
    count: number
    used: number
    char_limit: number
  }
}
