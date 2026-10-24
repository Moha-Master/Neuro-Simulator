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

/** 编译预览：prompt 模板（含 core / 附加段落）或单条消息模板共用。 */
export interface PreviewResult {
  prompt: string
  /** 试渲染是否成功（失败不阻塞，只给提示） */
  rendered?: boolean
  error?: string | null
  chars: {
    template: number
    core?: number
    sections?: Record<string, number>
    total: number
  }
  /** 草稿里缺失的必需占位符（**仅提示**，不再拒绝保存）。 */
  missing_placeholders?: string[]
  /** 模板引用了、但实况投影里不存在的状态路径（仅提示） */
  unresolved_paths?: string[]
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
