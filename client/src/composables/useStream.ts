import { ref } from 'vue'
import { ApiError, api, describeError } from '@/api'
import type { QueueMessage, SceneSummary, SessionRow } from '@/types/stream'
import type { StreamStatus } from '@/types/status'

const MODULE = 'stream'

export function useStream() {
  const status = ref<StreamStatus | null>(null)
  const sessions = ref<SessionRow[]>([])
  const error = ref('')
  const busy = ref(false)
  // 队列：增量尾读游标 + 已加载消息（只加不减的队列，前端也只是追加）
  const messages = ref<QueueMessage[]>([])
  const queueActive = ref(false)
  /** 场景清单（单一来源 = 画面侧 scenes.json；前端重新构建后自动多出/少掉按钮） */
  const scenes = ref<SceneSummary[]>([])
  let lastMsgId = 0
  let isPolling = false

  async function refreshStatus() {
    try {
      status.value = await api<StreamStatus>(`/manage/${MODULE}/stream/status`)
    } catch (e) {
      error.value = describeError(e)
    }
  }

  async function refreshScenes() {
    try {
      const data = await api<{ scenes: SceneSummary[] }>(`/manage/${MODULE}/stream/scenes`)
      scenes.value = data.scenes
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

  async function action(
    kind: 'start' | 'pause' | 'resume' | 'stop',
    meta?: { title?: string; category?: string; tags?: string[] },
  ) {
    busy.value = true
    error.value = ''
    try {
      await api(`/manage/${MODULE}/stream/${kind}`, {
        method: 'POST',
        // 只有 start 需要本场信息；其余保持无 body
        ...(kind === 'start' ? { body: JSON.stringify(meta ?? {}) } : {}),
      })
      await Promise.all([refreshStatus(), refreshSessions()])
      return true
    } catch (e) {
      error.value = describeError(e)
      return false
    } finally {
      busy.value = false
    }
  }

  /** 直播中修改本场标题 / 分区（→ 广播给画面，下一轮消息即生效）。 */
  async function setMetadata(title: string, category: string, tags: string[]): Promise<boolean> {
    error.value = ''
    busy.value = true
    try {
      await api(`/manage/${MODULE}/stream/metadata`, {
        method: 'PUT',
        body: JSON.stringify({ title, category, tags }),
      })
      await refreshStatus()
      return true
    } catch (e) {
      error.value = describeError(e)
      return false
    } finally {
      busy.value = false
    }
  }

  /** 手动切场景：dashboard 暂不提供按钮（避免与循环/游戏的自动切换打架），
   *  端点仍然可用，需要时把按钮加回 Control 页即可。 */
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

  async function postMessage(
    username: string,
    content: string,
    highlight?: { type: 'bits' | 'points'; value: number },
  ): Promise<boolean> {
    error.value = ''
    try {
      await api(`/manage/${MODULE}/queue/message`, {
        method: 'POST',
        body: JSON.stringify({
          username,
          content,
          ...(highlight ? { type: highlight.type, value: highlight.value } : {}),
        }),
      })
      void pollQueue()
      return true
    } catch (e) {
      error.value = describeError(e)
      return false
    }
  }

  /** 暂停/恢复高亮消息队列消费（PUT /stream/highlight，来源记为 manual）。 */
  async function setHighlightPaused(paused: boolean): Promise<boolean> {
    error.value = ''
    try {
      await api(`/manage/${MODULE}/stream/highlight`, {
        method: 'PUT',
        body: JSON.stringify({ paused }),
      })
      await refreshStatus()
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
    scenes,
    refreshStatus,
    refreshScenes,
    refreshSessions,
    action,
    setMetadata,
    setScene,
    pollQueue,
    resetQueue,
    postMessage,
    setHighlightPaused,
    deleteSession,
  }
}
