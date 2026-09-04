import { ref } from 'vue'
import { api, describeError } from '@/stores/connection'
import type { Conversation } from '@/types/chat'

const MODULE = 'neuro_sama'

export function useConversations() {
  const sessions = ref<Conversation[]>([])
  const isLoading = ref(false)
  const error = ref('')

  async function loadSessions() {
    try {
      isLoading.value = true
      error.value = ''
      const data = await api<{ sessions: Conversation[] }>(`/manage/${MODULE}/manage/sessions`)
      sessions.value = data.sessions
    } catch (e) {
      error.value = '获取会话列表失败: ' + describeError(e)
    } finally {
      isLoading.value = false
    }
  }

  async function deleteSession(channel: string, conversationId: string) {
    try {
      await api(`/manage/${MODULE}/manage/sessions/${channel}/${conversationId}`, { method: 'DELETE' })
      await loadSessions()
    } catch (e) {
      error.value = '删除会话失败: ' + describeError(e)
    }
  }

  async function renameSession(channel: string, conversationId: string, title: string) {
    try {
      await api(`/manage/${MODULE}/manage/sessions/${channel}/${conversationId}`, {
        method: 'PUT',
        body: JSON.stringify({ title }),
      })
      await loadSessions()
    } catch (e) {
      error.value = '重命名失败: ' + describeError(e)
    }
  }

  return {
    sessions,
    isLoading,
    error,
    loadSessions,
    deleteSession,
    renameSession,
  }
}
