<template>
  <v-expansion-panels variant="accordion" class="mb-2">
    <v-expansion-panel>
      <v-expansion-panel-title class="py-1">
        <div class="d-flex align-center" style="gap: 8px;">
          <v-icon size="small" :color="statusColor">mdi-puzzle-outline</v-icon>
          <span class="text-caption font-weight-medium">{{ tool.name }}</span>
          <v-chip
            size="x-small"
            :color="statusColor"
            variant="tonal"
          >
            {{ statusLabel }}
          </v-chip>
          <v-chip
            v-if="tool.duration_ms !== undefined"
            size="x-small"
            variant="outlined"
          >
            {{ tool.duration_ms }}ms
          </v-chip>
          <v-progress-circular
            v-if="isPending"
            indeterminate
            size="14"
            width="2"
            color="orange"
            class="ml-1"
          />
        </div>
      </v-expansion-panel-title>
      <v-expansion-panel-text>
        <div v-if="tool.arguments" class="mb-2">
          <div class="text-caption text-disabled mb-1">Arguments</div>
          <pre class="tool-pre">{{ formattedArgs }}</pre>
        </div>
        <div v-if="tool.result !== undefined">
          <div class="text-caption text-disabled mb-1">Result</div>
          <pre class="tool-pre">{{ tool.result }}</pre>
        </div>
      </v-expansion-panel-text>
    </v-expansion-panel>
  </v-expansion-panels>
</template>

<script lang="ts" setup>
import { computed } from 'vue'
import type { ToolCallDisplay } from '@/types/chat'

const props = defineProps<{
  tool: ToolCallDisplay
}>()

const isPending = computed(() => props.tool.result === undefined)
const statusColor = computed(() => {
  if (isPending.value) return 'orange'
  return props.tool.ok ? 'success' : 'error'
})
const statusLabel = computed(() => {
  if (isPending.value) return 'running'
  return props.tool.ok ? 'ok' : 'error'
})
const formattedArgs = computed(() => {
  if (typeof props.tool.arguments === 'string') return props.tool.arguments
  return JSON.stringify(props.tool.arguments, null, 2)
})
</script>

<style scoped>
.tool-pre {
  background: rgba(0, 0, 0, 0.05);
  padding: 8px 12px;
  border-radius: 4px;
  overflow-x: auto;
  font-size: 0.8125rem;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 300px;
  overflow-y: auto;
}
</style>
