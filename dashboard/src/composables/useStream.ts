import { ref } from 'vue'
import { ApiError, api, describeError } from '@/stores/connection'
import type { QueueMessage, SessionRow, StreamStatus } from '@/types/stream'

const MODULE = 'stream'

export function useStream() {
  const status = ref<StreamStatus | null>(null)
  const sessions = ref<SessionRow[]>([])
  const error = ref('')
  const busy = ref(false)
  // 队列：增量尾读游标 + 已加载消息（只加不减的队列，前端也只是追加）
  const messages = ref<QueueMessage[]>([])
  const queueActive = ref(false)
  let lastMsgId = 0
  let isPolling = false

  async function refreshStatus() {
    try {
      status.value = await api<StreamStatus>(`/manage/${MODULE}/stream/status`)
    } catch (e) {
      error.value = describeError(e)
    }
  }

  async function refreshSessions() {
    try {
      const data = await api<{ sessions: SessionRow[] }>(`/manage/${MODULE}/stream/sessions?limit=10`)
      sessions.value = data.sessions
    } catch (e) {
      error.value = describeError(e)
    }
  }

  async function action(kind: 'start' | 'pause' | 'resume' | 'stop') {
    busy.value = true
    error.value = ''
    try {
      await api(`/manage/${MODULE}/stream/${kind}`, { method: 'POST' })
      await Promise.all([refreshStatus(), refreshSessions()])
      return true
    } catch (e) {
      error.value = describeError(e)
      return false
    } finally {
      busy.value = false
    }
  }

  async function setScene(id: string) {
    error.value = ''
    try {
      await api(`/manage/${MODULE}/scene`, { method: 'POST', body: JSON.stringify({ id }) })
    } catch (e) {
      error.value = describeError(e)
    }
  }

  /** 队列增量轮询：无进行中场次（409）时静默清空并标记不可用。 */
  async function pollQueue() {
    if (isPolling) return
    isPolling = true
    try {
      const data = await api<{ queue_id: string; messages: QueueMessage[] }>(
        `/manage/${MODULE}/queue/messages?after_id=${lastMsgId}`,
      )
      queueActive.value = true
      if (data.messages.length) {
        // 简单去重过滤，防止意外重复添加相同 id 的消息
        const existingIds = new Set(messages.value.map(m => m.id))
        const newMsgs = data.messages.filter(m => !existingIds.has(m.id))
        if (newMsgs.length) {
          messages.value = [...messages.value, ...newMsgs]
        }
        const last = data.messages[data.messages.length - 1]
        if (last) {
          lastMsgId = last.id
        }
      }
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        resetQueue()
      } else {
        error.value = describeError(e)
      }
    } finally {
      isPolling = false
    }
  }

  /** 主动清空队列状态（无场次时调用，避免向服务端发注定 409 的请求）。 */
  function resetQueue() {
    queueActive.value = false
    if (messages.value.length) {
      messages.value = []
    }
    lastMsgId = 0
  }

  async function postMessage(username: string, content: string): Promise<boolean> {
    error.value = ''
    try {
      await api(`/manage/${MODULE}/queue/message`, {
        method: 'POST',
        body: JSON.stringify({ username, content }),
      })
      void pollQueue()
      return true
    } catch (e) {
      error.value = describeError(e)
      return false
    }
  }

  async function deleteSession(sessionId: string): Promise<boolean> {
    error.value = ''
    try {
      await api(`/manage/${MODULE}/stream/sessions/${sessionId}`, { method: 'DELETE' })
      await Promise.all([refreshStatus(), refreshSessions()])
      return true
    } catch (e) {
      error.value = describeError(e)
      return false
    }
  }

  return {
    status,
    sessions,
    error,
    busy,
    messages,
    queueActive,
    refreshStatus,
    refreshSessions,
    action,
    setScene,
    pollQueue,
    resetQueue,
    postMessage,
    deleteSession,
  }
}
