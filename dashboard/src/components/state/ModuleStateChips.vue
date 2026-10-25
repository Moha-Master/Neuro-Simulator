<template>
  <div v-if="entry" class="module-state">
    <template v-if="entry.reachable && entry.status">
      <!-- 不可达时整行只留一个 chip + error tooltip，不渲染空 chip 群 -->
      <template v-if="module === 'stream' && streamSections">
        <v-chip
          size="small"
          :color="loopColor"
          variant="tonal"
          :prepend-icon="loopIcon"
          :title="streamSections.loop.current?.state ?? 'unknown'"
        >
          {{ streamSections.loop.current?.state ?? '—' }}
        </v-chip>

        <v-tooltip
          v-if="streamSections.scene.current"
          :text="streamSections.scene.current.desc || ''"
          :disabled="!streamSections.scene.current.desc"
          location="top"
        >
          <template #activator="{ props: tip }">
            <v-chip v-bind="tip" size="small" variant="outlined" prepend-icon="mdi-movie-open">
              {{ streamSections.scene.current.label }}
            </v-chip>
          </template>
        </v-tooltip>

        <v-chip
          v-if="streamSections.games.current?.active"
          size="small"
          variant="outlined"
          prepend-icon="mdi-gamepad-variant"
        >
          {{ streamSections.games.current.active }}
        </v-chip>

        <v-chip size="small" variant="outlined" prepend-icon="mdi-account">
          Viewers {{ formatCount(streamSections.audience.current?.viewers) }}
        </v-chip>

        <v-chip size="small" variant="outlined" prepend-icon="mdi-message-text">
          Queue {{ pendingText }}
        </v-chip>

        <v-chip
          v-if="streamSections.loop.current?.highlight_paused"
          size="small"
          color="warning"
          variant="tonal"
          prepend-icon="mdi-pause-circle"
          :title="`高亮队列暂停（来源：${streamSections.loop.current.highlight_source ?? '—'}）`"
        >
          高亮暂停
        </v-chip>

        <v-chip
          v-if="streamSections.chatbot.current"
          size="small"
          variant="outlined"
          prepend-icon="mdi-robot"
        >
          Chatbot {{ streamSections.chatbot.current.in_flight }}
        </v-chip>

        <v-tooltip
          v-if="streamSections.neuro_link.current && !streamSections.neuro_link.current.reachable"
          :text="streamSections.neuro_link.current.error || 'neuro is unreachable'"
          location="top"
        >
          <template #activator="{ props: tip }">
            <v-chip
              v-bind="tip"
              size="small"
              color="error"
              variant="tonal"
              prepend-icon="mdi-link-variant-off"
            >
              neuro link down
            </v-chip>
          </template>
        </v-tooltip>

        <v-tooltip
          v-if="lastError"
          :text="lastError.message"
          location="top"
        >
          <template #activator="{ props: tip }">
            <v-chip
              v-bind="tip"
              size="small"
              color="error"
              variant="tonal"
              prepend-icon="mdi-alert"
            >
              {{ truncate(lastError.message) }}
            </v-chip>
          </template>
        </v-tooltip>
      </template>

      <template v-else-if="module === 'neuro_sama' && neuroSections">
        <v-tooltip
          v-if="neuroSections.scene.current"
          :text="neuroSections.scene.current.desc || ''"
          :disabled="!neuroSections.scene.current.desc"
          location="top"
        >
          <template #activator="{ props: tip }">
            <v-chip v-bind="tip" size="small" variant="outlined" prepend-icon="mdi-movie-open">
              {{ neuroSections.scene.current.label }}
            </v-chip>
          </template>
        </v-tooltip>

        <v-chip
          v-if="neuroSections.turn.current?.generating"
          size="small"
          color="info"
          variant="tonal"
          prepend-icon="mdi-brain"
        >
          Generating
        </v-chip>
        <v-chip
          v-if="neuroSections.turn.current?.speaking"
          size="small"
          color="primary"
          variant="tonal"
          prepend-icon="mdi-volume-high"
        >
          Speaking {{ neuroSections.turn.current.remaining_s.toFixed(1) }}s
        </v-chip>

        <v-tooltip
          :text="ttsTooltip"
          :disabled="!neuroSections.tts.current?.degraded"
          location="top"
        >
          <template #activator="{ props: tip }">
            <v-chip
              v-bind="tip"
              size="small"
              :color="neuroSections.tts.current?.degraded ? 'warning' : undefined"
              variant="tonal"
              prepend-icon="mdi-microphone"
            >
              {{ neuroSections.tts.current?.effective_provider ?? '—' }}
            </v-chip>
          </template>
        </v-tooltip>

        <v-chip
          v-if="neuroSections.model.current"
          size="small"
          :color="neuroSections.model.current.service_ok ? 'success' : 'error'"
          variant="tonal"
          prepend-icon="mdi-robot"
        >
          {{ neuroSections.model.current.service_ok ? 'LLM OK' : 'LLM down' }}
        </v-chip>
      </template>
    </template>

    <v-tooltip v-else-if="!entry.reachable" :text="entry.error || 'module unreachable'" location="top">
      <template #activator="{ props: tip }">
        <v-chip v-bind="tip" size="small" color="error" variant="tonal">unreachable</v-chip>
      </template>
    </v-tooltip>

    <div v-if="entry.status" class="freshness">
      <v-icon v-if="changedFlag" size="x-small" color="success" class="mr-1">mdi-circle</v-icon>
      <span :class="{ 'text-warning': entry.elapsed_ms > 1500 }">rev {{ entry.status.rev }}</span>
      <span class="mx-1">·</span>
      <span :class="{ 'text-warning': entry.elapsed_ms > 1500 }">
        {{ relativeTime(entry.observed_at) }} · {{ entry.elapsed_ms }}ms
      </span>
    </div>
  </div>
</template>

<script lang="ts" setup>
import { computed } from 'vue'
import { relativeTime, useModuleState } from '@/composables/useModuleState'
import { neuroModule, streamModule } from '@/types/status'

const props = defineProps<{
  module: string
}>()

const { state, changed } = useModuleState()

const changedFlag = computed(() => changed(props.module))

const streamEntry = computed(() => (props.module === 'stream' ? streamModule(state.value) : null))
const neuroEntry = computed(() =>
  props.module === 'neuro_sama' ? neuroModule(state.value) : null,
)
const entry = computed(() => streamEntry.value ?? neuroEntry.value)

const streamSections = computed(() => streamEntry.value?.status?.sections ?? null)
const neuroSections = computed(() => neuroEntry.value?.status?.sections ?? null)

const loopColor = computed(() => {
  const stateName = streamSections.value?.loop.current?.state
  return (
    {
      live: 'success',
      starting: 'warning',
      paused: 'info',
      offline: 'grey',
    } as Record<string, string>
  )[stateName ?? ''] ?? 'grey'
})
const loopIcon = computed(() =>
  streamSections.value?.loop.current?.state === 'live' ? 'mdi-record' : 'mdi-circle',
)

const pendingText = computed(() => {
  const chat = streamSections.value?.chat.current
  if (!chat || chat.normal_pending === null) return '—'
  return `${chat.normal_pending} / ${chat.highlight_pending ?? '—'}`
})

const lastError = computed(() => streamSections.value?.loop.current?.last_error ?? null)

const ttsTooltip = computed(() =>
  neuroSections.value?.tts.current?.degraded
    ? `TTS degraded: ${neuroSections.value.tts.current.effective_provider ?? 'unknown provider'}`
    : '',
)

function formatCount(value: number | null | undefined): string {
  return value === null || value === undefined ? '—' : value.toLocaleString('en-US')
}

function truncate(text: string, max = 36): string {
  return text.length > max ? `${text.slice(0, max)}…` : text
}
</script>

<style scoped>
.module-state {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  margin-top: 8px;
}
.freshness {
  display: flex;
  align-items: center;
  width: 100%;
  margin-top: 4px;
  font-size: 12px;
  color: rgb(var(--v-theme-on-surface-variant));
}
</style>
