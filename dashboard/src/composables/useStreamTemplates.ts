import { ref } from 'vue'
import { api, describeError } from '@/stores/connection'
import type { PreviewResult, TemplateContext, TemplateInfo } from '@/types/persona'

const BASE = '/manage/stream/manage/templates'

/** stream 拼给 neuro / chatbot 的 user message 模板（存 stream 数据库，人工可编辑）。 */
export function useStreamTemplates() {
  const templates = ref<TemplateInfo[]>([])
  const context = ref<TemplateContext>({
    title: '',
    category: '',
    live: false,
    scene: null,
    rounds: 0,
  })
  const isLoading = ref(false)
  const busy = ref(false)
  const error = ref('')

  function apply(list: TemplateInfo[]) {
    templates.value = list
  }

  async function load() {
    isLoading.value = true
    error.value = ''
    try {
      const data = await api<{ templates: TemplateInfo[]; context: TemplateContext }>(BASE)
      apply(data.templates)
      context.value = data.context
    } catch (e) {
      error.value = '加载消息模板失败: ' + describeError(e)
    } finally {
      isLoading.value = false
    }
  }

  async function save(name: string, content: string): Promise<boolean> {
    error.value = ''
    busy.value = true
    try {
      const data = await api<{ templates: TemplateInfo[] }>(`${BASE}/${name}`, {
        method: 'PUT',
        body: JSON.stringify({ content }),
      })
      apply(data.templates)
      return true
    } catch (e) {
      error.value = '保存模板失败: ' + describeError(e)
      return false
    } finally {
      busy.value = false
    }
  }

  async function reset(name: string): Promise<boolean> {
    error.value = ''
    busy.value = true
    try {
      const data = await api<{ templates: TemplateInfo[] }>(`${BASE}/${name}/reset`, {
        method: 'POST',
      })
      apply(data.templates)
      return true
    } catch (e) {
      error.value = '恢复默认模板失败: ' + describeError(e)
      return false
    } finally {
      busy.value = false
    }
  }

  async function preview(name: string, content?: string): Promise<PreviewResult | null> {
    error.value = ''
    try {
      return await api<PreviewResult>(`${BASE}/${name}/preview`, {
        method: 'POST',
        body: JSON.stringify(content === undefined ? {} : { content }),
      })
    } catch (e) {
      error.value = '生成预览失败: ' + describeError(e)
      return null
    }
  }

  return { templates, context, isLoading, busy, error, load, save, reset, preview }
}
