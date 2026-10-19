import { ref } from 'vue'
import { api, describeError } from '@/stores/connection'
import type {
  CoreInfo,
  CoreMemoryBlock,
  PersonaData,
  PlaceholderInfo,
  PreviewResult,
  PromptInfo,
} from '@/types/persona'

const MODULE = 'neuro_sama'
const BASE = `/manage/${MODULE}/manage/persona`

export function usePersona() {
  const prompt = ref<PromptInfo>({ content: '', updated_at: '', is_default: false })
  const blocks = ref<CoreMemoryBlock[]>([])
  const placeholders = ref<PlaceholderInfo[]>([])
  const coreCount = ref(0)
  const coreUsed = ref(0)
  const coreIsDefault = ref(false)
  const isLoading = ref(false)
  const busy = ref(false)
  const error = ref('')

  function applyCore(core: CoreInfo) {
    coreCount.value = core.count
    coreUsed.value = core.used
    coreIsDefault.value = core.is_default
    if (core.blocks) blocks.value = core.blocks
  }

  async function load() {
    isLoading.value = true
    error.value = ''
    try {
      const data = await api<PersonaData>(BASE)
      prompt.value = data.prompt
      applyCore(data.core)
      placeholders.value = data.placeholders
    } catch (e) {
      error.value = '加载 Persona 失败: ' + describeError(e)
    } finally {
      isLoading.value = false
    }
  }

  async function savePrompt(content: string): Promise<boolean> {
    error.value = ''
    busy.value = true
    try {
      const data = await api<{ prompt: PromptInfo }>(`${BASE}/prompt`, {
        method: 'PUT',
        body: JSON.stringify({ content }),
      })
      prompt.value = data.prompt
      return true
    } catch (e) {
      error.value = '保存 Prompt 失败: ' + describeError(e)
      return false
    } finally {
      busy.value = false
    }
  }

  async function resetPrompt(): Promise<boolean> {
    error.value = ''
    busy.value = true
    try {
      const data = await api<{ prompt: PromptInfo }>(`${BASE}/prompt/reset`, {
        method: 'POST',
      })
      prompt.value = data.prompt
      return true
    } catch (e) {
      error.value = '恢复默认 Prompt 失败: ' + describeError(e)
      return false
    } finally {
      busy.value = false
    }
  }

  async function resetCore(): Promise<boolean> {
    error.value = ''
    busy.value = true
    try {
      const data = await api<{ core: CoreInfo }>(`${BASE}/core-memories/reset`, {
        method: 'POST',
      })
      applyCore(data.core)
      return true
    } catch (e) {
      error.value = '恢复默认核心记忆失败: ' + describeError(e)
      return false
    } finally {
      busy.value = false
    }
  }

  async function resetAll(): Promise<boolean> {
    error.value = ''
    busy.value = true
    try {
      const data = await api<{ prompt: PromptInfo; core: CoreInfo }>(`${BASE}/reset`, {
        method: 'POST',
      })
      prompt.value = data.prompt
      applyCore(data.core)
      return true
    } catch (e) {
      error.value = '恢复默认 Persona 失败: ' + describeError(e)
      return false
    } finally {
      busy.value = false
    }
  }

  async function preview(content?: string): Promise<PreviewResult | null> {
    error.value = ''
    try {
      return await api<PreviewResult>(`${BASE}/preview`, {
        method: 'POST',
        body: JSON.stringify(content === undefined ? {} : { content }),
      })
    } catch (e) {
      error.value = '生成预览失败: ' + describeError(e)
      return null
    }
  }

  async function addCore(title: string, content: string): Promise<boolean> {
    error.value = ''
    busy.value = true
    try {
      const data = await api<{ block: CoreMemoryBlock; core: CoreInfo }>(`${BASE}/core-memories`, {
        method: 'POST',
        body: JSON.stringify({ title, content }),
      })
      applyCore(data.core)
      return true
    } catch (e) {
      error.value = '新增核心记忆失败: ' + describeError(e)
      return false
    } finally {
      busy.value = false
    }
  }

  async function updateCore(id: number, title: string, content: string): Promise<boolean> {
    error.value = ''
    busy.value = true
    try {
      const data = await api<{ block: CoreMemoryBlock; core: CoreInfo }>(
        `${BASE}/core-memories/${id}`,
        { method: 'PUT', body: JSON.stringify({ title, content }) },
      )
      applyCore(data.core)
      return true
    } catch (e) {
      error.value = '修改核心记忆失败: ' + describeError(e)
      return false
    } finally {
      busy.value = false
    }
  }

  async function removeCore(id: number): Promise<boolean> {
    error.value = ''
    busy.value = true
    try {
      const data = await api<{ core: CoreInfo }>(`${BASE}/core-memories/${id}`, {
        method: 'DELETE',
      })
      applyCore(data.core)
      return true
    } catch (e) {
      error.value = '删除核心记忆失败: ' + describeError(e)
      return false
    } finally {
      busy.value = false
    }
  }

  return {
    prompt,
    blocks,
    placeholders,
    coreCount,
    coreUsed,
    coreIsDefault,
    isLoading,
    busy,
    error,
    load,
    savePrompt,
    resetPrompt,
    resetCore,
    resetAll,
    preview,
    addCore,
    updateCore,
    removeCore,
  }
}
