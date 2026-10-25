import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api } from '@/stores/connection'
import type { QueueMessage } from '@/types/stream'

/** 聊天只保留最近 N 条（Twitch 同样只保留有限历史，避免 DOM 无限增长）。 */
const MAX_MESSAGES = 500

const EVENT_TYPES = [
  'snapshot',
  'state',
  'session_begin',
  'session_end',
  'stream_metadata',
  'chat_message',
  'highlight_message',
] as const

interface Snapshot {
  state?: string
  session?: { session_id?: string } | null
  metadata?: { title?: string; category?: string; tags?: string[] }
  chat?: QueueMessage[]
}

interface ChatEvent {
  id?: number
  username?: string
  content?: string
  created_at?: string
  highlight_type?: string | null
  value?: number
}

function nowStamp(): string {
  const d = new Date()
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

/**
 * 直播间聊天/状态流：SSE /manage/stream/ui/events（同一条画面事件通道）。
 *
 * - snapshot 带最近 30 条聊天记录，用于首屏/重连恢复
 * - chat_message / highlight_message 为实时入列事件
 * - 断线重连后按 after_id 走 REST 补齐（避免 EventSource 重连期间漏消息）
 */
export function useStreamChat() {
  const messages = ref<QueueMessage[]>([])
  const state = ref('')
  const metadata = ref<{ title: string; category: string; tags: string[] }>({
    title: '',
    category: '',
    tags: [],
  })
  const connected = ref(false)
  const sessionId = ref<string | null>(null)

  let lastId = 0
  let source: EventSource | null = null
  let disposed = false

  const active = computed(() => Boolean(sessionId.value) && state.value !== 'offline')

  function merge(list: QueueMessage[]): void {
    if (!list.length) return
    const byId = new Map<number, QueueMessage>()
    for (const m of messages.value) byId.set(m.id, m)
    for (const m of list) byId.set(m.id, m)
    const merged = [...byId.values()].sort((a, b) => a.id - b.id)
    messages.value = merged.length > MAX_MESSAGES ? merged.slice(-MAX_MESSAGES) : merged
    for (const m of list) lastId = Math.max(lastId, m.id)
  }

  function reset(): void {
    messages.value = []
    lastId = 0
  }

  async function gapFill(): Promise<void> {
    if (lastId <= 0) return
    try {
      const data = await api<{ messages: QueueMessage[] }>(
        `/manage/stream/queue/messages?after_id=${lastId}&limit=500`,
      )
      merge(data.messages ?? [])
    } catch {
      // 无场次 / 模块重启中：等待下一次 snapshot 或重连
    }
  }

  function appendEvent(ev: ChatEvent): void {
    const id = Number(ev.id)
    if (!Number.isFinite(id)) return
    merge([
      {
        id,
        username: String(ev.username ?? ''),
        content: String(ev.content ?? ''),
        // SSE 入列事件不带 created_at；直播场景下接收时间与发送时间同一秒内
        created_at: ev.created_at ?? nowStamp(),
        highlight_type: ev.highlight_type ?? null,
        value: Number(ev.value ?? 0),
      },
    ])
  }

  function handle(type: string, data: unknown): void {
    if (type === 'snapshot') {
      const snap = data as Snapshot
      state.value = snap.state ?? 'offline'
      if (snap.metadata) {
        metadata.value = {
          title: snap.metadata.title ?? '',
          category: snap.metadata.category ?? '',
          tags: Array.isArray(snap.metadata.tags) ? snap.metadata.tags : [],
        }
      }
      sessionId.value = snap.session?.session_id ?? null
      if (!snap.session) {
        reset()
      } else if (snap.chat?.length) {
        merge(snap.chat)
        void gapFill()
      }
      return
    }

    if (type === 'session_begin') {
      const ev = data as {
        session_id?: string
        title?: string
        category?: string
        tags?: string[]
      }
      // 新场次：清空上一场的聊天记录
      if (ev.session_id && ev.session_id !== sessionId.value) reset()
      if (ev.session_id) sessionId.value = ev.session_id
      if (state.value === 'offline' || state.value === '') state.value = 'starting'
      metadata.value = {
        title: ev.title ?? metadata.value.title,
        category: ev.category ?? metadata.value.category,
        tags: Array.isArray(ev.tags) ? ev.tags : metadata.value.tags,
      }
      return
    }

    if (type === 'session_end') {
      state.value = 'offline'
      sessionId.value = null
      return
    }

    if (type === 'state') {
      const ev = data as { state?: string }
      if (ev.state) state.value = ev.state
      return
    }

    if (type === 'stream_metadata') {
      const ev = data as { title?: string; category?: string; tags?: string[] }
      metadata.value = {
        title: ev.title ?? '',
        category: ev.category ?? '',
        tags: Array.isArray(ev.tags) ? ev.tags : [],
      }
      return
    }

    if (type === 'chat_message' || type === 'highlight_message') {
      appendEvent(data as ChatEvent)
    }
  }

  function open(): void {
    if (disposed) return
    source?.close()
    const es = new EventSource('/manage/stream/ui/events')
    source = es
    es.onopen = () => {
      connected.value = true
      void gapFill()
    }
    es.onerror = () => {
      // EventSource 会自动重连；这里只更新 UI 状态
      connected.value = false
    }
    for (const type of EVENT_TYPES) {
      es.addEventListener(type, (ev) => {
        try {
          handle(type, JSON.parse((ev as MessageEvent).data))
        } catch {
          // 单条坏帧不影响后续事件
        }
      })
    }
  }

  onMounted(open)
  onUnmounted(() => {
    disposed = true
    source?.close()
    source = null
  })

  return {
    messages,
    state,
    metadata,
    connected,
    sessionId,
    active,
    reconnect: open,
  }
}
