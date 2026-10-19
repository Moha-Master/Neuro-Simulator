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
}

export interface PreviewResult {
  prompt: string
  chars: {
    template: number
    core: number
    short_term: number
    total: number
  }
  estimated_tokens: number
}
