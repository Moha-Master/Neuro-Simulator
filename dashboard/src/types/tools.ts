/**
 * Neuro 工具管理契约（与后端 `GET /manage/tools` 逐字对齐）。
 *
 * 这些类型是**契约守卫**：后端改字段名/改形状时 vue-tsc 会直接报错，
 * 而不是运行时读到 undefined 才发现。
 */

export type ToolOrigin = 'builtin' | 'game' | 'mcp'

export interface ToolParameters {
  type?: string
  properties?: Record<string, ToolParameters>
  required?: string[]
  description?: string
  [key: string]: unknown
}

export interface ToolInfo {
  name: string
  origin: ToolOrigin
  /** 游戏工具所属 game id；None = 通用工具 */
  game: string | null
  /** "all" | "start" | "active" */
  phase: string
  description: string
  parameters: ToolParameters
  /** false = 已禁用（只影响暴露给 LLM 的 schema/names，不影响手动运行） */
  enabled: boolean
}

export type McpTransport = 'stdio' | 'http'

export interface McpServerInfo {
  name: string
  transport: McpTransport
  command?: string
  args?: string[]
  env?: Record<string, string>
  url?: string
  enabled: boolean
  /** 最近一次启动的连接结果 */
  connected: boolean
  /** 该 server 注册进来的工具数（未连接/未启用为 0） */
  tool_count: number
  /** 连接失败原因（异常类名 + 文本，截断）；成功/未启用为 null */
  error: string | null
}

export interface ToolsResponse {
  tools: ToolInfo[]
  mcp_servers: McpServerInfo[]
  tool_timeout_s: number
}

/** 变更端点（PUT enabled / MCP CRUD）在 GET 载荷外多一个 status。 */
export interface ToolsMutationResponse extends ToolsResponse {
  status: string
}

export interface ToolRunResponse {
  ok: boolean
  result: string
  duration_ms: number
}

/** MCP server 创建/整条替换的请求体（与后端 MCPServerPayload 对齐）。 */
export interface McpServerInput {
  name: string
  transport: McpTransport
  command?: string
  args?: string[]
  env?: Record<string, string>
  url?: string
  enabled?: boolean
}
