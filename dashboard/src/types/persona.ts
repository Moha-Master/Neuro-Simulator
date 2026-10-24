import type { MemoryStats } from '@/types/memory'

export interface CoreMemoryBlock {
  id: number
  title: string
  content: string
  created_at: string
  updated_at: string
}

export interface PromptInfo {
  content: string
  updated_at: string
  is_default: boolean
}

export interface PlaceholderInfo {
  key: string
  description: string
}

export interface CoreInfo {
  blocks: CoreMemoryBlock[]
  count: number
  used: number
  is_default: boolean
}

export interface PersonaData {
  prompt: PromptInfo
  core: CoreInfo
  placeholders: PlaceholderInfo[]
  short_term: MemoryStats
  count?: number
}

/** 保存前的三道关卡结果（消息模板）。G3 只展示，不影响保存。 */
export interface TemplateGate {
  g1_ok: boolean
  g2_ok: boolean
  skeleton_rendered: boolean
}

export interface TemplateValidation {
  gate: TemplateGate
  /** 非阻塞：无法静态验证的路径（如运行时下标的 games[...].summary） */
  warnings: string[]
}

/** stream 消息模板保存的返回值；persona 页面仍返回 boolean。 */
export interface SaveResult {
  ok: boolean
  validation?: TemplateValidation
  error?: string
}

/** 编译预览：prompt 模板（含 core / 附加段落）或单条消息模板共用。 */
export interface PreviewResult {
  prompt: string
  /** 试渲染是否成功（失败不阻塞，只给提示） */
  rendered?: boolean
  error?: string | null
  /** G1 编译 / G2 最严苛骨架是否通过；G3 就是 rendered 本身。 */
  gate?: TemplateGate
  /** 被拒绝的原因（保存时会 400；preview 里只展示） */
  gate_errors?: Record<string, string>
  /** 非阻塞提示：无法静态验证的运行时下标路径 */
  warnings?: string[]
  chars: {
    template: number
    core?: number
    sections?: Record<string, number>
    total: number
  }
  /** 草稿里缺失的必需占位符（**仅提示**，不再拒绝保存）。 */
  missing_placeholders?: string[]
  /** 顶层变量 → 渲染展开后的字符数 */
  expanded?: Record<string, number>
  estimated_tokens?: number
}

export interface TemplateInfo {
  name: string
  label: string
  description: string
  content: string
  updated_at: string
  is_default: boolean
  required: string[]
  placeholders: PlaceholderInfo[]
}

export interface TemplateContext {
  title: string
  category: string
  live: boolean
  scene: string | null
  rounds: number
}

export interface TemplatesPayload {
  templates: TemplateInfo[]
  context: TemplateContext
}
