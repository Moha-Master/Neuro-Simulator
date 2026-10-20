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
        <div v-if="createdAt" class="text-caption text-disabled mb-2">
          Time: {{ formatDateTime(createdAt) }}
        </div>
        <div v-if="content" class="thinking-content text-body-2">{{ content }}</div>
        <div v-else class="thinking-content text-body-2"><em>No thinking recorded</em></div>
      </v-expansion-panel-text>
    </v-expansion-panel>
  </v-expansion-panels>
</template>

<script lang="ts" setup>
import { formatDateTime } from '@/utils/time'

defineProps<{
  content: string
  isStreaming: boolean
  duration?: number
  createdAt?: string
}>()
</script>

<style scoped>
.thinking-content {
  max-height: 40vh;
  overflow-y: auto;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
