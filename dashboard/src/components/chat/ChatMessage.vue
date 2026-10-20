<template>
  <div :class="['chat-message', `chat-message--${msg.role}`]">
    <div class="chat-message__avatar">
      <v-avatar :color="msg.role === 'user' ? 'primary' : 'success'" size="32">
        <v-icon size="18" color="white">
          {{ msg.role === 'user' ? 'mdi-account' : 'mdi-robot' }}
        </v-icon>
      </v-avatar>
    </div>
    <div ref="bodyEl" class="chat-message__body">
      <div class="chat-message__header">
        <span class="text-caption font-weight-medium">
          {{ msg.role === 'user' ? 'You' : 'Neuro Sama' }}
        </span>
      </div>

      <!-- 合并后的 assistant turn：按原始顺序渲染正文 / 思考 / 工具调用 -->
      <template v-if="msg.segments?.length">
        <template v-for="(seg, i) in msg.segments" :key="i">
          <ThinkingBlock
            v-if="seg.kind === 'reasoning'"
            :content="seg.content"
            :is-streaming="!!msg.isStreaming && i === msg.segments.length - 1"
            :created-at="seg.createdAt"
          />
          <div
            v-else-if="seg.kind === 'tool'"
            class="chat-tool"
            data-seg-kind="tool"
          >
            <ToolCallBlock :tool="seg.tool" :created-at="seg.createdAt" />
          </div>
          <div
            v-else-if="seg.kind === 'content'"
            class="chat-segment"
            data-seg-kind="content"
          >
            <div class="chat-message__content text-body-2">{{ seg.content }}</div>
            <span
              v-if="seg.createdAt"
              class="chat-segment__time"
              :title="formatDateTime(seg.createdAt)"
            >
              {{ formatTime(seg.createdAt) }}
            </span>
          </div>
        </template>
      </template>

      <!-- 兼容没有 segments 的消息（例如旧数据） -->
      <template v-else>
        <ThinkingBlock
          v-if="msg.reasoning"
          :content="msg.reasoning"
          :is-streaming="!!msg.isStreaming && !msg.content"
          :created-at="msg.createdAt"
        />

        <template v-if="msg.tool_calls?.length">
          <div
            v-for="tc in msg.tool_calls"
            :key="tc.id"
            class="chat-tool"
            data-seg-kind="tool"
          >
            <ToolCallBlock
              :tool="tc"
              :created-at="tc.createdAt ?? msg.createdAt"
            />
          </div>
        </template>

        <div v-if="msg.content" class="chat-message__content text-body-2">
          {{ msg.content }}
        </div>
      </template>

      <div
        v-if="msg.isStreaming && !msg.content && !msg.reasoning && !msg.segments?.length"
        class="chat-message__content"
      >
        <v-progress-circular indeterminate size="16" width="2" color="success" />
      </div>

      <div v-if="msg.usage?.length" class="chat-message__usage mt-1">
        <v-chip
          v-for="u in msg.usage"
          :key="u.step"
          size="x-small"
          variant="outlined"
          class="mr-1"
        >
          Step {{ u.step }}: {{ u.input_tokens }}/{{ u.output_tokens }} tokens
        </v-chip>
      </div>
    </div>

    <!-- 消息外侧时间：user 在左、assistant 在右；title 里是完整日期 -->
    <span
      v-if="msg.createdAt && showRowTime"
      class="chat-message__time"
      :title="formatDateTime(msg.createdAt)"
    >
      {{ formatTime(msg.createdAt) }}
    </span>
  </div>
</template>

<script lang="ts" setup>
import { computed, nextTick, onBeforeUnmount, onMounted, onUpdated, ref } from 'vue'
import ThinkingBlock from './ThinkingBlock.vue'
import ToolCallBlock from './ToolCallBlock.vue'
import type { DisplayMessage } from '@/types/chat'
import { formatDateTime, formatTime } from '@/utils/time'

const props = defineProps<{
  msg: DisplayMessage
}>()

const showRowTime = computed(
  () =>
    props.msg.role === 'user' ||
    !props.msg.segments?.some((seg) => seg.kind === 'content'),
)

const MIN_TOOL_WIDTH = 320

const bodyEl = ref<HTMLElement | null>(null)
let resizeObserver: ResizeObserver | null = null
let syncRaf = 0

/** 工具卡宽度：至少 MIN_TOOL_WIDTH，并尽量对齐前一个正文气泡的宽度。 */
function syncToolWidths(): void {
  const body = bodyEl.value
  if (!body) return
  const available = body.clientWidth
  if (available <= 0) return

  let lastContentWidth = 0
  const nodes = body.querySelectorAll<HTMLElement>('[data-seg-kind]')
  nodes.forEach((el) => {
    if (el.dataset.segKind === 'content') {
      const bubble = el.querySelector<HTMLElement>('.chat-message__content')
      lastContentWidth = bubble?.offsetWidth ?? 0
      return
    }
    if (el.dataset.segKind !== 'tool') return
    const target = Math.min(
      available,
      Math.max(MIN_TOOL_WIDTH, lastContentWidth || MIN_TOOL_WIDTH),
    )
    el.style.width = target > 0 ? `${Math.round(target)}px` : ''
  })
}

function scheduleSync(): void {
  if (syncRaf) cancelAnimationFrame(syncRaf)
  syncRaf = requestAnimationFrame(() => {
    syncRaf = 0
    syncToolWidths()
  })
}

onMounted(() => {
  void nextTick(scheduleSync)
  if (typeof ResizeObserver !== 'undefined') {
    resizeObserver = new ResizeObserver(scheduleSync)
    if (bodyEl.value) resizeObserver.observe(bodyEl.value)
  }
  window.addEventListener('resize', scheduleSync)
})

onUpdated(() => {
  void nextTick(scheduleSync)
})

onBeforeUnmount(() => {
  resizeObserver?.disconnect()
  resizeObserver = null
  window.removeEventListener('resize', scheduleSync)
  if (syncRaf) cancelAnimationFrame(syncRaf)
})
</script>

<style scoped>
.chat-message {
  position: relative;
  display: flex;
  gap: 12px;
  padding: 12px 16px;
}
.chat-message--user {
  flex-direction: row-reverse;
}
.chat-message__body {
  max-width: 80%;
  min-width: 0;
}
.chat-message--user .chat-message__body {
  text-align: right;
}
.chat-message__header {
  margin-bottom: 4px;
}
.chat-message--user .chat-message__header {
  text-align: right;
}
.chat-message__time {
  position: absolute;
  top: 14px;
  right: 12px;
  font-size: 11px;
  line-height: 1;
  color: rgba(var(--v-theme-on-surface), 0.5);
  font-variant-numeric: tabular-nums;
  transition: color 0.15s ease;
}
.chat-message--user .chat-message__time {
  right: auto;
  left: 12px;
}
.chat-message:hover .chat-message__time {
  color: rgba(var(--v-theme-on-surface), 0.78);
}
.chat-segment {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  margin-bottom: 4px;
}
.chat-segment:last-child {
  margin-bottom: 0;
}
.chat-segment__time {
  flex: 0 0 auto;
  padding-top: 9px;
  font-size: 11px;
  line-height: 1;
  color: rgba(var(--v-theme-on-surface), 0.5);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
  user-select: none;
}
.chat-segment .chat-message__content {
  min-width: 0;
}
.chat-tool {
  /* 宽度由脚本按“上一段正文气泡”计算（带 320px 下限）；这里不要撑满 */
  min-width: 0;
  max-width: 100%;
  margin-bottom: 4px;
}
.chat-tool :deep(.v-expansion-panels) {
  width: 100%;
}
.chat-message__content {
  white-space: pre-wrap;
  word-break: break-word;
}
.chat-message--user .chat-message__content {
  background: rgb(var(--v-theme-primary));
  color: white;
  padding: 8px 14px;
  border-radius: 16px 4px 16px 16px;
  display: inline-block;
}
.chat-message--assistant .chat-message__content {
  background: rgb(var(--v-theme-surface));
  padding: 8px 14px;
  border-radius: 4px 16px 16px 16px;
  display: inline-block;
}
.chat-message__usage {
  display: flex;
  flex-wrap: wrap;
}
</style>
