import { ref, reactive, nextTick } from 'vue'
import { api, describeError } from '@/stores/connection'
import type {
  DisplayMessage,
  Conversation,
  ToolCallDisplay,
  UsageInfo,
  DoneEventData,
  SSEEvent,
} from '@/types/chat'

const MODULE = 'neuro_sama'

export function useChat() {
  const messages = ref<DisplayMessage[]>([])
  const isStreaming = ref(false)
  const currentReasoning = ref('')
  const currentContent = ref('')
  const currentToolCalls = ref<ToolCallDisplay[]>([])
  const currentUsage = ref<UsageInfo[]>([])
  const error = ref('')
  const activeConversationId = ref<string | null>(null)
  const activeChannel = ref<string>('web')
  const abortController = ref<AbortController | null>(null)

  function reset() {
    messages.value = []
    isStreaming.value = false
    currentReasoning.value = ''
    currentContent.value = ''
    currentToolCalls.value = []
    currentUsage.value = []
    error.value = ''
    activeConversationId.value = null
    activeChannel.value = 'web'
  }

  async function loadMessages(channel: string, conversationId: string) {
    try {
      const data = await api<{
        channel: string
        conversation_id: string
        messages: Array<{ id: number; role: string; content: string; created_at?: string; tool_calls?: unknown; tool_call_id?: string; reasoning?: string }>
      }>(`/manage/${MODULE}/manage/chats/${channel}/${conversationId}`)

      const displayMsgs: DisplayMessage[] = []
      const toolCallMap = new Map<string, ToolCallDisplay>()

      for (const m of data.messages) {
        if (m.role === 'user') {
          const text = typeof m.content === 'string' ? m.content : JSON.stringify(m.content)
          displayMsgs.push({
            key: `user-${m.id}`,
            role: 'user',
            content: text,
            createdAt: m.created_at,
          })
        } else if (m.role === 'assistant') {
          let content = ''
          if (m.content) {
            content = typeof m.content === 'string' ? m.content : JSON.stringify(m.content)
          }
          const toolCalls: ToolCallDisplay[] = []
          if (m.tool_calls && Array.isArray(m.tool_calls)) {
            for (const tc of m.tool_calls) {
              const fn = tc.function || {}
              const td: ToolCallDisplay = {
                id: tc.id,
                name: fn.name || 'unknown',
                arguments: fn.arguments || '{}',
              }
              toolCallMap.set(tc.id, td)
              toolCalls.push(td)
            }
          }
          displayMsgs.push({
            key: `assistant-${m.id}`,
            role: 'assistant',
            content,
            reasoning: m.reasoning || undefined,
            tool_calls: toolCalls.length ? toolCalls : undefined,
            createdAt: m.created_at,
          })
        } else if (m.role === 'tool' && m.tool_call_id) {
          const existing = toolCallMap.get(m.tool_call_id)
          if (existing) {
            existing.result = typeof m.content === 'string' ? m.content : JSON.stringify(m.content)
            existing.ok = true
          }
        }
      }

      messages.value = displayMsgs
    } catch (e) {
      error.value = '加载消息失败: ' + describeError(e)
    }
  }

  async function sendMessage(
    text: string,
    conversationId?: string,
    channel = 'web',
  ): Promise<void> {
    if (isStreaming.value) return
    if (!text.trim()) return

    error.value = ''
    isStreaming.value = true
    currentReasoning.value = ''
    currentContent.value = ''
    currentToolCalls.value = []
    currentUsage.value = []

    // 用 reactive() 创建：后续流式更新通过该代理写回才能触发视图重渲染
    // （普通对象持有原始引用改属性不会触发响应式）
    const userMsg: DisplayMessage = reactive({
      key: `user-${Date.now()}`,
      role: 'user',
      content: text.trim(),
    })
    messages.value.push(userMsg)

    const assistantMsg: DisplayMessage = reactive({
      key: `assistant-streaming-${Date.now()}`,
      role: 'assistant',
      content: '',
      isStreaming: true,
    })
    messages.value.push(assistantMsg)

    await nextTick()

    const ctrl = new AbortController()
    abortController.value = ctrl

    try {
      const body: Record<string, unknown> = { message: text.trim(), channel }
      if (conversationId) body.conversation_id = conversationId

      const resp = await fetch(`/manage/${MODULE}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal: ctrl.signal,
      })

      if (!resp.ok) {
        let detail = `HTTP ${resp.status}`
        try {
          const errBody = await resp.json()
          if (errBody && typeof errBody.detail === 'string') detail = errBody.detail
        } catch {
          // 非 JSON 错误体，保留 HTTP xxx
        }
        throw new Error(detail)
      }

      const reader = resp.body!.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const parts = buffer.split('\n\n')
        buffer = parts.pop() || ''

        for (const part of parts) {
          const lines = part.split('\n')
          let eventType = ''
          let eventData = ''

          for (const line of lines) {
            if (line.startsWith('event: ')) {
              eventType = line.slice(7)
            } else if (line.startsWith('data: ')) {
              eventData = line.slice(6)
            }
          }

          if (!eventType || !eventData) continue

          try {
            const data = JSON.parse(eventData) as SSEEvent
            handleSSEEvent(eventType, data, assistantMsg)
          } catch {
            // non-JSON data, ignore
          }
        }
      }
    } catch (e) {
      if (e instanceof DOMException && e.name === 'AbortError') {
        assistantMsg.content += '\n\n*[生成已中断]*'
      } else {
        error.value = '发送失败: ' + describeError(e)
        const idx = messages.value.indexOf(assistantMsg)
        if (idx !== -1) messages.value.splice(idx, 1)
      }
    } finally {
      assistantMsg.isStreaming = false
      isStreaming.value = false
      abortController.value = null
      currentReasoning.value = ''
      currentContent.value = ''
      currentToolCalls.value = []
      currentUsage.value = []
    }
  }

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  function handleSSEEvent(eventType: string, data: any, assistantMsg: DisplayMessage) {
    switch (eventType) {
      case 'start': {
        activeChannel.value = data.channel
        activeConversationId.value = data.conversation_id
        break
      }
      case 'reasoning': {
        currentReasoning.value += data.delta
        assistantMsg.reasoning = currentReasoning.value
        break
      }
      case 'content': {
        currentContent.value += data.delta
        assistantMsg.content = currentContent.value
        break
      }
      case 'tool_start': {
        const td: ToolCallDisplay = {
          id: data.tool_call_id,
          name: data.name,
          arguments: data.arguments,
        }
        currentToolCalls.value.push(td)
        assistantMsg.tool_calls = [...currentToolCalls.value]
        break
      }
      case 'tool_result': {
        const existing = currentToolCalls.value.find((t) => t.id === data.tool_call_id)
        if (existing) {
          existing.ok = data.ok
          existing.result = data.result
          existing.duration_ms = data.duration_ms
          assistantMsg.tool_calls = [...currentToolCalls.value]
        }
        break
      }
      case 'usage': {
        currentUsage.value.push(data)
        assistantMsg.usage = [...currentUsage.value]
        break
      }
      case 'done': {
        assistantMsg.content = data.content || currentContent.value
        assistantMsg.reasoning = data.reasoning || currentReasoning.value
        assistantMsg.tool_calls = data.tool_calls?.length
          ? data.tool_calls
          : currentToolCalls.value.length
            ? [...currentToolCalls.value]
            : undefined
        assistantMsg.isStreaming = false
        break
      }
      case 'cancelled': {
        assistantMsg.content = currentContent.value || assistantMsg.content || '*[生成已中断]*'
        assistantMsg.isStreaming = false
        break
      }
      case 'error': {
        error.value = data.message
        assistantMsg.content = currentContent.value || `*[错误: ${data.message}]*`
        assistantMsg.isStreaming = false
        break
      }
    }
  }

  async function stopGeneration() {
    if (!activeConversationId.value || !isStreaming.value) return
    try {
      // 先让服务端置 cancel_event：agent 落库半截内容并推送 cancelled 事件，流自然结束
      await api(`/manage/${MODULE}/manage/chats/${activeChannel.value}/${activeConversationId.value}/stop`, {
        method: 'POST',
      })
    } catch {
      // /stop 失败（如模块不可达）：直接断开本地流兜底
      abortController.value?.abort()
    }
  }

  return {
    messages,
    isStreaming,
    currentReasoning,
    currentContent,
    error,
    activeConversationId,
    activeChannel,
    reset,
    loadMessages,
    sendMessage,
    stopGeneration,
  }
}
