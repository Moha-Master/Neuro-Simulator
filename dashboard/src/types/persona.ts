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
  chars: {
    template: number
    core?: number
    sections?: Record<string, number>
    total: number
  }
  /** 草稿里缺失的必需占位符（保存会被服务端拒绝）。 */
  missing_placeholders?: string[]
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
