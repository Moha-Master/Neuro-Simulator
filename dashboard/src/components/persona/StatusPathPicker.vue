<template>
  <v-card variant="tonal" class="mb-3">
    <v-card-text class="pb-2">
      <div class="d-flex align-center flex-wrap ga-2 mb-2">
        <div class="font-weight-medium">
          {{ mode === 'template' ? '插入状态路径' : '插入占位符' }}
        </div>
        <v-chip size="x-small" variant="outlined">
          {{ mode === 'template' ? 'Jinja · 点击插入光标处' : '字符串替换' }}
        </v-chip>
        <v-spacer />
        <v-btn
          v-if="mode === 'template'"
          size="x-small"
          variant="text"
          prepend-icon="mdi-refresh"
          :loading="state.loading.value"
          @click="state.refresh()"
        >
          刷新投影
        </v-btn>
      </div>

      <div v-if="mode === 'persona'" class="text-caption text-disabled mb-2">
        system prompt 仍是字符串替换，只认下面这些占位符；状态树只用于消息模板。
      </div>

      <div v-if="mode === 'template'" class="text-caption text-disabled mb-2">
        数据来自 <code>GET /manage/state</code> 的实况投影；未定义路径渲染为空串。
      </div>

      <v-text-field
        v-if="mode === 'template'"
        v-model="query"
        density="compact"
        variant="outlined"
        hide-details
        prepend-inner-icon="mdi-magnify"
        placeholder="按路径过滤，如 scene.desc"
        class="mb-2"
        clearable
      />

      <div class="d-flex flex-wrap ga-2 mb-2">
        <v-chip
          v-for="item in mode === 'template' ? fixedItems : []"
          :key="item.path"
          size="small"
          variant="flat"
          color="primary"
          class="font-weight-medium"
          :title="item.description"
          @click="insert(`{{ ${item.path} }}`)"
        >
          {{ item.path }}
        </v-chip>
        <v-chip
          v-for="p in personaPlaceholders"
          :key="p.key"
          size="small"
          variant="flat"
          color="primary"
          class="font-weight-medium"
          :title="p.description"
          @click="insert(p.key)"
        >
          {{ p.key }}
        </v-chip>
      </div>

      <div v-if="mode === 'template'" class="d-flex flex-wrap ga-2 mb-2">
        <v-btn size="x-small" variant="tonal" prepend-icon="mdi-code-braces" @click="insertIfSnippet">
          插入 if 片段
        </v-btn>
        <v-btn
          size="x-small"
          variant="tonal"
          color="warning"
          prepend-icon="mdi-alert-outline"
          title="整段投影可能很大，别直接塞进 prompt"
          @click="insert('{{ stream_status }}')"
        >
          插入整段 stream_status
        </v-btn>
      </div>

      <template v-if="mode === 'template'">
        <v-alert
          v-if="!available"
          type="info"
          variant="tonal"
          density="compact"
          class="mb-2 text-caption"
        >
          模块状态暂不可用（/manage/state 未返回 stream / neuro_sama）；仍可用上面的固定条目手动输入。
        </v-alert>

        <div v-else class="picker-tree">
          <div v-for="group in filteredGroups" :key="group.root" class="mb-1">
            <div
              class="picker-group d-flex align-center ga-2"
              role="button"
              tabindex="0"
              @click="toggle(group.root)"
              @keydown.enter="toggle(group.root)"
            >
              <v-icon size="16">
                {{ open[group.root] ? 'mdi-chevron-down' : 'mdi-chevron-right' }}
              </v-icon>
              <code class="text-body-2">{{ group.root }}</code>
              <v-chip size="x-small" variant="outlined">{{ group.items.length }}</v-chip>
            </div>
            <template v-if="open[group.root] || query.trim()">
              <div
                v-for="item in group.items"
                :key="item.path"
                class="picker-leaf d-flex align-center ga-2"
                :style="{ paddingLeft: `${12 + item.depth * 14}px` }"
                role="button"
                tabindex="0"
                @click="insert(`{{ ${item.path} }}`)"
                @keydown.enter="insert(`{{ ${item.path} }}`)"
              >
                <v-icon size="14" :color="item.big ? 'warning' : 'primary'">
                  {{ item.big ? 'mdi-alert-outline' : 'mdi-code-braces' }}
                </v-icon>
                <code class="picker-path">{{ item.path }}</code>
                <span class="picker-value text-disabled">{{ item.preview }}</span>
                <v-spacer />
                <v-btn
                  v-if="item.big"
                  icon="mdi-alert-outline"
                  size="x-small"
                  variant="text"
                  color="warning"
                  title="raw render snapshot, don't dump into a prompt"
                  @click.stop
                />
                <v-btn
                  icon="mdi-content-copy"
                  size="x-small"
                  variant="text"
                  title="复制"
                  @click.stop="copy(item.path)"
                />
              </div>
            </template>
          </div>
          <div v-if="!filteredGroups.length" class="text-caption text-disabled px-2 py-1">
            没有匹配的路径。
          </div>
        </div>
      </template>

      <v-snackbar v-model="snackbar" :timeout="1500" location="bottom">{{ snackbarText }}</v-snackbar>
    </v-card-text>
  </v-card>
</template>

<script lang="ts" setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useModuleState } from '@/composables/useModuleState'
import type { PlaceholderInfo } from '@/types/persona'
import type { ModuleState, UnknownSections } from '@/types/status'

/** 消息模板 / system prompt 共用的占位符选择器。
 *
 * - `template`：数据源是 vedal `GET /manage/state`（stream + neuro_sama 的
 *   `/status` 投影），树形展示分区 → 路径；点击在光标处插入 `{{ path }}`。
 * - `persona`：system prompt 仍用字符串替换，只列真实占位符，不显示状态树。
 * 模块离线时降级为固定条目 + 提示，不报错、不挡编辑。
 */
const props = withDefaults(
  defineProps<{
    mode?: 'template' | 'persona'
    placeholders?: PlaceholderInfo[]
  }>(),
  {
    mode: 'persona',
    placeholders: () => [],
  },
)

const emit = defineEmits<{ (e: 'insert', text: string): void }>()

const state = useModuleState()
const query = ref('')
const open = reactive<Record<string, boolean>>({ stream_status: true, neuro_status: true })
const snackbar = ref(false)
const snackbarText = ref('')

const fixedItems = [
  { path: 'messages', description: '观众消息块（空队列时是 placeholder_message）' },
  { path: 'neuro_said', description: '本轮 neuro 正文；本场没说过话为空串' },
  { path: 'datetime', description: '当前本地时间字符串' },
]

const personaPlaceholders = computed(() => props.placeholders || [])

interface Row {
  path: string
  depth: number
  preview: string
  big: boolean
}

interface Group {
  root: string
  items: Row[]
}

function sectionsOf(name: string): Record<string, { current?: unknown }> | null {
  const entry = state.modules.value[name] as ModuleState<UnknownSections> | undefined
  const sections = entry?.status?.sections
  return (sections as Record<string, { current?: unknown }> | undefined) ?? null
}

const available = computed(
  () => state.reachable('stream') || state.reachable('neuro_sama'),
)

function previewOf(value: unknown): string {
  if (value === null || value === undefined) return 'null'
  if (Array.isArray(value)) return `[…] ${value.length} items`
  if (typeof value === 'object') {
    const keys = Object.keys(value as Record<string, unknown>).length
    return `{…} ${keys} keys`
  }
  const text = JSON.stringify(value)
  return text.length > 42 ? text.slice(0, 42) + '…' : text
}

function isBig(path: string, value: unknown): boolean {
  if (path.endsWith('.element')) return true
  if (Array.isArray(value)) return value.length > 8
  if (value && typeof value === 'object') return Object.keys(value).length > 8
  return false
}

function walk(prefix: string, value: unknown, depth: number, rows: Row[], maxDepth = 3) {
  rows.push({ path: prefix, depth, preview: previewOf(value), big: isBig(prefix, value) })
  if (depth >= maxDepth || value === null || typeof value !== 'object') return
  if (Array.isArray(value)) return
  for (const [key, child] of Object.entries(value as Record<string, unknown>)) {
    walk(`${prefix}.${key}`, child, depth + 1, rows)
  }
}

function buildGroups(root: string, sections: Record<string, { current?: unknown }> | null): Group[] {
  if (!sections) return []
  return Object.entries(sections).map(([name, section]) => {
    const rows: Row[] = []
    walk(`${root}.${name}`, section?.current, 0, rows)
    return { root: `${root}.${name}`, items: rows }
  })
}

const groups = computed<Group[]>(() => [
  ...buildGroups('stream_status', sectionsOf('stream')),
  ...buildGroups('neuro_status', sectionsOf('neuro_sama')),
])

const filteredGroups = computed<Group[]>(() => {
  const needle = query.value.trim().toLowerCase()
  if (!needle) return groups.value
  return groups.value
    .map((group) => ({
      ...group,
      items: group.items.filter((item) => item.path.toLowerCase().includes(needle)),
    }))
    .filter((group) => group.items.length > 0)
})

function toggle(root: string) {
  open[root] = !open[root]
}

function insert(text: string) {
  emit('insert', text)
}

function insertIfSnippet() {
  insert('{% if stream_status.games.active %}Game: {{ stream_status.games[stream_status.games.active].summary }}{% endif %}')
}

async function copy(path: string) {
  try {
    await navigator.clipboard.writeText(`{{ ${path} }}`)
    snackbarText.value = '已复制到剪贴板'
    snackbar.value = true
  } catch {
    snackbarText.value = '复制失败（浏览器未授权剪贴板）'
    snackbar.value = true
  }
}

onMounted(() => {
  if (props.mode !== 'template') return
  state.start()
  void state.refresh()
})

onBeforeUnmount(() => {
  if (props.mode !== 'template') return
  state.stop()
})
</script>

<style scoped>
.picker-tree {
  max-height: 320px;
  overflow: auto;
  border-radius: 6px;
  background: rgba(var(--v-theme-surface), 0.55);
  padding: 4px 6px;
}
.picker-group {
  cursor: pointer;
  padding: 3px 4px;
  border-radius: 4px;
}
.picker-group:hover,
.picker-leaf:hover {
  background: rgba(var(--v-theme-primary), 0.08);
}
.picker-leaf {
  cursor: pointer;
  padding: 2px 4px;
  border-radius: 4px;
  min-width: 0;
}
.picker-path {
  font-size: 12px;
  white-space: nowrap;
}
.picker-value {
  font-size: 11px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
