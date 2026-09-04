import { ref } from 'vue'
import { api, describeError } from '@/stores/connection'
import type { Memory, MemoryStats } from '@/types/memory'

const MODULE = 'neuro_sama'

export function useMemories() {
  const memories = ref<Memory[]>([])
  const used = ref(0)
  const charLimit = ref(0)
  const count = ref(0)
  const isLoading = ref(false)
  const error = ref('')
  const busy = ref(false)

  function applyStats(stats: MemoryStats) {
    used.value = stats.used
    charLimit.value = stats.char_limit
    count.value = stats.count
  }

  async function load() {
    isLoading.value = true
    error.value = ''
    try {
      const data = await api<{ memories: Memory[] } & MemoryStats>(`/manage/${MODULE}/manage/memories`)
      memories.value = data.memories
      applyStats(data)
    } catch (e) {
      error.value = '加载记忆失败: ' + describeError(e)
    } finally {
      isLoading.value = false
    }
  }

  async function add(content: string): Promise<boolean> {
    error.value = ''
    busy.value = true
    try {
      const data = await api<{ memory: Memory } & MemoryStats>(`/manage/${MODULE}/manage/memories`, {
        method: 'POST',
        body: JSON.stringify({ content }),
      })
      memories.value.push(data.memory)
      applyStats(data)
      return true
    } catch (e) {
      error.value = '添加记忆失败: ' + describeError(e)
      return false
    } finally {
      busy.value = false
    }
  }

  async function update(id: number, content: string): Promise<boolean> {
    error.value = ''
    busy.value = true
    try {
      const data = await api<{ memory: Memory } & MemoryStats>(`/manage/${MODULE}/manage/memories/${id}`, {
        method: 'PUT',
        body: JSON.stringify({ content }),
      })
      const idx = memories.value.findIndex((m) => m.id === id)
      if (idx !== -1) memories.value[idx] = data.memory
      applyStats(data)
      return true
    } catch (e) {
      error.value = '修改记忆失败: ' + describeError(e)
      return false
    } finally {
      busy.value = false
    }
  }

  async function remove(id: number): Promise<boolean> {
    error.value = ''
    busy.value = true
    try {
      const data = await api<MemoryStats>(`/manage/${MODULE}/manage/memories/${id}`, { method: 'DELETE' })
      memories.value = memories.value.filter((m) => m.id !== id)
      applyStats(data)
      return true
    } catch (e) {
      error.value = '删除记忆失败: ' + describeError(e)
      return false
    } finally {
      busy.value = false
    }
  }

  return {
    memories,
    used,
    charLimit,
    count,
    isLoading,
    busy,
    error,
    load,
    add,
    update,
    remove,
  }
}
