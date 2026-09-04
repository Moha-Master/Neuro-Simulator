export interface Memory {
  id: number
  content: string
  ts: string
}

export interface MemoryStats {
  used: number
  char_limit: number
  count: number
}
