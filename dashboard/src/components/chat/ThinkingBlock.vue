<template>
  <v-expansion-panels variant="accordion" class="mb-2">
    <v-expansion-panel>
      <v-expansion-panel-title class="py-1">
        <div class="d-flex align-center" style="gap: 8px;">
          <v-icon size="small" color="purple">mdi-brain</v-icon>
          <span class="text-caption text-purple">Thinking</span>
          <v-chip
            v-if="!isStreaming && duration"
            size="x-small"
            variant="outlined"
            color="purple"
          >
            {{ duration }}ms
          </v-chip>
          <v-progress-circular
            v-if="isStreaming"
            indeterminate
            size="14"
            width="2"
            color="purple"
            class="ml-1"
          />
        </div>
      </v-expansion-panel-title>
      <v-expansion-panel-text>
        <div class="thinking-content text-body-2" v-html="renderedContent" />
      </v-expansion-panel-text>
    </v-expansion-panel>
  </v-expansion-panels>
</template>

<script lang="ts" setup>
import { computed } from 'vue'
import MarkdownIt from 'markdown-it'

const md = new MarkdownIt({ html: false, breaks: true })

const props = defineProps<{
  content: string
  isStreaming: boolean
  duration?: number
}>()

const renderedContent = computed(() => {
  if (!props.content) return '<em>No thinking recorded</em>'
  return md.render(props.content)
})
</script>

<style scoped>
.thinking-content {
  max-height: 40vh;
  overflow-y: auto;
  white-space: pre-wrap;
}
.thinking-content :deep(pre) {
  background: rgba(0, 0, 0, 0.05);
  padding: 8px 12px;
  border-radius: 4px;
  overflow-x: auto;
}
</style>
