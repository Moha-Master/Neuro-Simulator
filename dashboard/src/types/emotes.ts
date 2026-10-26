/**
 * 表情接口类型契约（与 stream 后端 `GET /emotes` 字段逐字对齐）。
 *
 * 这些类型是**契约守卫**：后端改字段名/改形状时 `vue-tsc` 会直接报错，
 * 而不是运行时读到 undefined 才发现（与 `types/status.ts` 同一做法）。
 */

export interface EmoteSummary {
  name: string
  /** 后端给相对路径 `/emotes/images/<name>`，调用方按模块基址拼绝对地址 */
  url: string
  source: string
  animated: boolean
}

export interface EmotesResponse {
  emotes: EmoteSummary[]
  updated_at: string | null
  counts: Record<string, number>
  last_error: string | null
}
