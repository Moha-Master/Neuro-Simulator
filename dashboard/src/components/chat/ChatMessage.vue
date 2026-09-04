<template>
  <div :class="['chat-message', `chat-message--${msg.role}`]">
    <div class="chat-message__avatar">
      <v-avatar :color="msg.role === 'user' ? 'primary' : 'success'" size="32">
        <v-icon size="18" color="white">
          {{ msg.role === 'user' ? 'mdi-account' : 'mdi-robot' }}
        </v-icon>
      </v-avatar>
    </div>
    <div class="chat-message__body">
      <div class="chat-message__header">
        <span class="text-caption font-weight-medium">
          {{ msg.role === 'user' ? 'You' : 'Neuro Sama' }}
        </span>
        <span v-if="msg.createdAt" class="text-caption text-disabled ml-2">
          {{ formatTime(msg.createdAt) }}
        </span>
      </div>

      <ThinkingBlock
        v-if="msg.reasoning"
        :content="msg.reasoning"
        :is-streaming="!!msg.isStreaming && !msg.content"
      />

      <template v-if="msg.tool_calls?.length">
        <ToolCallBlock
          v-for="tc in msg.tool_calls"
          :key="tc.id"
          :tool="tc"
        />
      </template>

      <div
        v-if="msg.content"
        class="chat-message__content text-body-2"
        v-html="renderedContent"
      />

      <div v-if="msg.isStreaming && !msg.content && !msg.reasoning" class="chat-message__content">
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
  </div>
</template>

<script lang="ts" setup>
import { computed } from 'vue'
import MarkdownIt from 'markdown-it'
import ThinkingBlock from './ThinkingBlock.vue'
import ToolCallBlock from './ToolCallBlock.vue'
import type { DisplayMessage } from '@/types/chat'

const md = new MarkdownIt({
  html: false,
  breaks: true,
  linkify: true,
  typographer: true,
})

const props = defineProps<{
  msg: DisplayMessage
}>()

const renderedContent = computed(() => {
  if (!props.msg.content) return ''
  return md.render(props.msg.content)
})

function formatTime(ts: string): string {
  try {
    const d = new Date(ts)
    return d.toLocaleTimeString()
  } catch {
    return ts
  }
}
</script>

<style scoped>
.chat-message {
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
.chat-message__content {
  white-space: pre-wrap;
  word-break: break-word;
}
.chat-message__content :deep(pre) {
  background: rgba(0, 0, 0, 0.05);
  padding: 8px 12px;
  border-radius: 4px;
  overflow-x: auto;
  max-height: 400px;
  overflow-y: auto;
}
.chat-message__content :deep(code) {
  font-size: 0.875em;
}
.chat-message__content :deep(p) {
  margin-bottom: 0.5em;
}
.chat-message__content :deep(p:last-child) {
  margin-bottom: 0;
}
.chat-message--user .chat-message__content {
  background: rgb(var(--v-theme-primary));
  color: white;
  padding: 8px 14px;
  border-radius: 16px 16px 4px 16px;
  display: inline-block;
}
.chat-message--user .chat-message__content :deep(pre) {
  background: rgba(255, 255, 255, 0.15);
  color: white;
}
.chat-message--assistant .chat-message__content {
  background: rgb(var(--v-theme-surface));
  padding: 8px 14px;
  border-radius: 16px 16px 16px 4px;
  display: inline-block;
}
.chat-message__usage {
  display: flex;
  flex-wrap: wrap;
}
</style>
