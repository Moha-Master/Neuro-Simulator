<template>
  <v-expansion-panels variant="accordion" multiple>
    <v-expansion-panel v-for="entry in entries" :key="entry.name" :value="entry.name">
      <v-expansion-panel-title>
        <span class="text-subtitle-2">{{ entry.name }}</span>
        <v-chip v-if="entry.section.error" size="x-small" color="error" class="ml-2">error</v-chip>
        <v-chip
          v-else-if="entry.section.current === null"
          size="x-small"
          color="grey"
          class="ml-2"
        >
          current=null
        </v-chip>
      </v-expansion-panel-title>

      <v-expansion-panel-text>
        <v-alert
          v-if="entry.section.error"
          type="error"
          variant="tonal"
          density="compact"
          class="mb-2"
        >
          {{ entry.section.error }}
        </v-alert>

        <div v-else-if="entry.name === 'loop' && loop" class="kv">
          <template v-for="row in loopRows" :key="row.label">
            <div class="kv-key">{{ row.label }}</div>
            <div class="kv-val">{{ row.value }}</div>
          </template>
        </div>

        <div v-else-if="entry.name === 'scene' && scene" class="kv">
          <template v-for="row in sceneRows" :key="row.label">
            <div class="kv-key">{{ row.label }}</div>
            <div class="kv-val">{{ row.value }}</div>
          </template>
        </div>

        <div v-else-if="entry.name === 'turn' && turn" class="kv">
          <template v-for="row in turnRows" :key="row.label">
            <div class="kv-key">{{ row.label }}</div>
            <div class="kv-val">{{ row.value }}</div>
          </template>
        </div>

        <div v-else-if="entry.name === 'audience' && audience" class="kv">
          <template v-for="row in audienceRows" :key="row.label">
            <div class="kv-key">{{ row.label }}</div>
            <div class="kv-val">{{ row.value }}</div>
          </template>
        </div>

        <div v-else-if="entry.name === 'chat' && chat" class="kv">
          <template v-for="row in chatRows" :key="row.label">
            <div class="kv-key">{{ row.label }}</div>
            <div class="kv-val">{{ row.value }}</div>
          </template>
        </div>

        <div v-else-if="entry.name === 'chatbot' && chatbot" class="kv">
          <template v-for="row in chatbotRows" :key="row.label">
            <div class="kv-key">{{ row.label }}</div>
            <div class="kv-val">{{ row.value }}</div>
          </template>
        </div>

        <div v-else-if="entry.name === 'neuro_link' && neuroLink" class="kv">
          <template v-for="row in neuroLinkRows" :key="row.label">
            <div class="kv-key">{{ row.label }}</div>
            <div class="kv-val">{{ row.value }}</div>
          </template>
        </div>

        <!-- 未知分区 / games / metadata / layers 等：原样 JSON，不猜字段 -->
        <pre v-else class="json">{{ pretty(entry.section.current) }}</pre>
      </v-expansion-panel-text>
    </v-expansion-panel>
  </v-expansion-panels>
</template>

<script lang="ts" setup>
import { computed } from 'vue'
import type {
  AudienceStatus,
  ChatStatus,
  ChatbotRuntime,
  LoopStatus,
  NeuroLinkStatus,
  NeuroSceneStatus,
  NeuroSections,
  Section,
  StreamSceneStatus,
  StreamSections,
  TurnStatus,
} from '@/types/status'

const props = defineProps<{
  sections: StreamSections | NeuroSections
}>()

/** 两个模块的分区名不同，这里只做"名字 -> 分区"的统一遍历。 */
const allSections = computed(
  () => props.sections as unknown as Record<string, Section<unknown>>,
)
const entries = computed(() =>
  Object.entries(allSections.value).map(([name, section]) => ({ name, section })),
)

const loop = computed(() => (allSections.value.loop?.current ?? null) as LoopStatus | null)
const scene = computed(
  () =>
    (allSections.value.scene?.current ?? null) as StreamSceneStatus | NeuroSceneStatus | null,
)
const turn = computed(() => (allSections.value.turn?.current ?? null) as TurnStatus | null)
const audience = computed(
  () => (allSections.value.audience?.current ?? null) as AudienceStatus | null,
)
const chat = computed(() => (allSections.value.chat?.current ?? null) as ChatStatus | null)
const chatbot = computed(
  () => (allSections.value.chatbot?.current ?? null) as ChatbotRuntime | null,
)
const neuroLink = computed(
  () => (allSections.value.neuro_link?.current ?? null) as NeuroLinkStatus | null,
)

interface Row {
  label: string
  value: string
}

function fmt(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'boolean') return value ? 'true' : 'false'
  if (typeof value === 'number') return Number.isFinite(value) ? String(value) : '—'
  if (typeof value === 'string') return value
  return JSON.stringify(value, null, 2)
}

function pretty(value: unknown): string {
  if (value === undefined) return '—'
  return JSON.stringify(value, null, 2)
}

/** unix 秒 → 相对现在；绝对时刻仍保留在 value 里。 */
function endsAtRelative(seconds: number | null): string {
  if (seconds === null) return ''
  const delta = seconds - Date.now() / 1000
  return delta >= 0 ? `剩 ${delta.toFixed(1)}s` : `已过 ${Math.abs(delta).toFixed(1)}s`
}

const loopRows = computed<Row[]>(() => {
  const v = loop.value
  if (!v) return []
  return [
    { label: 'state', value: fmt(v.state) },
    { label: 'session_id', value: fmt(v.session_id) },
    { label: 'queue_id', value: fmt(v.queue_id) },
    { label: 'channel', value: fmt(v.channel) },
    { label: 'conversation_id', value: fmt(v.conversation_id) },
    { label: 'round', value: fmt(v.round) },
    { label: 'cursor', value: fmt(v.cursor) },
    { label: 'priority_cursor', value: fmt(v.priority_cursor) },
    { label: 'started_at', value: fmt(v.started_at) },
    { label: 'persisted', value: fmt(v.persisted) },
    { label: 'intro', value: fmt(v.intro) },
    { label: 'last_error', value: fmt(v.last_error) },
  ]
})

const sceneRows = computed<Row[]>(() => {
  const v = scene.value
  if (!v) return []
  const rows: Row[] = [
    { label: 'id', value: fmt(v.id) },
    { label: 'label', value: fmt(v.label) },
    { label: 'desc', value: fmt(v.desc) },
    { label: 'icon', value: fmt(v.icon) },
    { label: 'duration', value: fmt(v.duration) },
    { label: 'elapsed', value: fmt(v.elapsed) },
  ]
  // auto_returns 只有 neuro 的场景分区有；stream 没有就不显示
  if ('auto_returns' in v) {
    rows.push({ label: 'auto_returns', value: fmt(v.auto_returns) })
  }
  return rows
})

const turnRows = computed<Row[]>(() => {
  const v = turn.value
  if (!v) return []
  return [
    { label: 'turn_id', value: fmt(v.turn_id) },
    { label: 'channel', value: fmt(v.channel) },
    { label: 'generating', value: fmt(v.generating) },
    { label: 'speaking', value: fmt(v.speaking) },
    { label: 'remaining_s', value: fmt(v.remaining_s) },
    {
      label: 'ends_at',
      value: v.ends_at === null ? '—' : `${v.ends_at} (${endsAtRelative(v.ends_at)})`,
    },
    { label: 'segments', value: fmt(v.segments) },
    { label: 'segments_done', value: fmt(v.segments_done) },
    { label: 'in_flight', value: fmt(v.in_flight) },
  ]
})

const audienceRows = computed<Row[]>(() => {
  const v = audience.value
  if (!v) return []
  return [
    { label: 'viewers', value: fmt(v.viewers) },
    { label: 'viewers_source', value: fmt(v.viewers_source) },
    { label: 'updated_at', value: fmt(v.updated_at) },
    { label: 'error', value: fmt(v.error) },
  ]
})

const chatRows = computed<Row[]>(() => {
  const v = chat.value
  if (!v) return []
  return [
    { label: 'normal_pending', value: fmt(v.normal_pending) },
    { label: 'priority_pending', value: fmt(v.priority_pending) },
    { label: 'last_message_id', value: fmt(v.last_message_id) },
  ]
})

const chatbotRows = computed<Row[]>(() => {
  const v = chatbot.value
  if (!v) return []
  return [
    { label: 'enabled', value: fmt(v.enabled) },
    { label: 'model', value: fmt(v.model) },
    { label: 'model_ref', value: fmt(v.model_ref) },
    { label: 'interval_s', value: fmt(v.interval_s) },
    { label: 'count', value: fmt(v.count) },
    { label: 'max_concurrent', value: fmt(v.max_concurrent) },
    { label: 'interval_jitter', value: fmt(v.interval_jitter) },
    { label: 'in_flight', value: fmt(v.in_flight) },
  ]
})

const neuroLinkRows = computed<Row[]>(() => {
  const v = neuroLink.value
  if (!v) return []
  return [
    { label: 'reachable', value: fmt(v.reachable) },
    { label: 'observed_at', value: fmt(v.observed_at) },
    { label: 'url', value: fmt(v.url) },
    { label: 'error', value: fmt(v.error) },
  ]
})
</script>

<style scoped>
.kv {
  display: grid;
  grid-template-columns: minmax(120px, 180px) 1fr;
  gap: 4px 12px;
  font-size: 13px;
}
.kv-key {
  color: rgb(var(--v-theme-on-surface-variant));
}
.kv-val {
  white-space: pre-wrap;
  word-break: break-word;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}
.json {
  margin: 0;
  padding: 8px;
  border-radius: 4px;
  background: rgba(var(--v-theme-surface-variant), 0.4);
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
