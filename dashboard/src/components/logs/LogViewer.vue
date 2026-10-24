<template>
  <div class="logs-page">
    <!-- 状态条：来源 / PID / 启动时间 / 磁盘占用 / 内存缓冲 -->
    <div class="d-flex align-center flex-wrap ga-2 mb-1">
      <v-icon color="primary">mdi-file-document-outline</v-icon>
      <div class="text-h5">{{ title }}</div>
      <v-spacer />

      <v-chip
        v-if="loading"
        size="small"
        variant="tonal"
        color="grey"
        prepend-icon="mdi-access-point-network"
      >
        connecting
      </v-chip>
      <v-chip
        v-else
        size="small"
        variant="tonal"
        :color="sourceColor"
        :prepend-icon="sourceIcon"
      >
        {{ sourceLabel }}
      </v-chip>

      <v-chip size="small" variant="outlined" prepend-icon="mdi-identifier">
        pid {{ pid ?? '—' }}
      </v-chip>

      <v-tooltip v-if="liveMeta" :text="liveMeta.started_at" location="top">
        <template #activator="{ props: tip }">
          <v-chip v-bind="tip" size="small" variant="outlined" prepend-icon="mdi-clock-outline">
            started {{ relativeTime(liveMeta.started_at) }}
          </v-chip>
        </template>
      </v-tooltip>

      <v-chip
        v-if="diskUsage"
        size="small"
        variant="tonal"
        :color="diskColor"
        :title="diskInfo?.path"
        prepend-icon="mdi-harddisk"
      >
        {{ formatBytes(diskUsage.used) }} / {{ formatBytes(diskUsage.limit) }}
      </v-chip>

      <v-chip v-if="liveMeta" size="small" variant="outlined" prepend-icon="mdi-memory">
        buffer {{ formatBytes(liveMeta.retained_bytes) }} /
        {{ formatBytes(liveMeta.memory_limit) }}
      </v-chip>
    </div>

    <!-- 控件：级别过滤 / 关键字 / 自动滚底 -->
    <div class="d-flex align-center flex-wrap ga-3 mb-2">
      <v-chip-group v-model="level" mandatory selected-class="text-primary">
        <v-chip value="all" filter size="small" variant="outlined">ALL</v-chip>
        <v-chip value="info" filter size="small" variant="outlined">INFO</v-chip>
        <v-chip value="warn" filter size="small" variant="outlined">WARN</v-chip>
        <v-chip value="error" filter size="small" variant="outlined">ERROR</v-chip>
      </v-chip-group>

      <v-text-field
        v-model="search"
        class="log-search"
        density="compact"
        variant="outlined"
        hide-details
        clearable
        prepend-inner-icon="mdi-magnify"
        label="Search"
      />

      <v-spacer />

      <v-switch
        v-model="follow"
        label="Follow"
        color="primary"
        density="compact"
        hide-details
      />
    </div>

    <!-- 诚实标注：读不到实时/磁盘、磁盘模式、窗口截断、不可回看的丢弃 -->
    <v-alert v-if="mode === 'error'" type="error" variant="tonal" density="compact" class="mb-2">
      实时日志与磁盘日志都读不到：{{ error || '与 vedal 的连接已断开' }}
    </v-alert>

    <v-alert v-if="mode === 'disk'" type="warning" variant="tonal" density="compact" class="mb-2">
      {{ diskBanner }}
    </v-alert>

    <v-alert
      v-if="liveMeta?.truncated"
      type="info"
      variant="tonal"
      density="compact"
      class="mb-2"
    >
      实时窗口只返回了最近 {{ formatBytes(liveMeta.bytes) }}，更早的输出超出本窗口，请到磁盘查看。
    </v-alert>

    <v-alert
      v-if="liveMeta && liveMeta.dropped_bytes > 0"
      type="warning"
      variant="tonal"
      density="compact"
      class="mb-2"
    >
      环形缓冲已丢弃 {{ formatBytes(liveMeta.dropped_bytes) }}（{{
        liveMeta.dropped_chunks
      }}
      块），这部分历史已不可回看。
    </v-alert>

    <v-alert v-if="diskMeta?.truncated" type="info" variant="tonal" density="compact" class="mb-2">
      磁盘文件较大，只读取了最后 {{ diskWindowLabel }}；更早的内容在文件前部或轮转备份里。
    </v-alert>

    <v-alert
      v-if="notice"
      type="warning"
      variant="tonal"
      density="compact"
      closable
      class="mb-2"
      @click:close="clearNotice"
    >
      {{ notice }}
    </v-alert>

    <v-alert v-if="renderLimited" type="info" variant="tonal" density="compact" class="mb-2">
      仅显示最近 {{ RENDER_LIMIT }} 行，更早内容见磁盘。
    </v-alert>

    <!-- 日志本体：深底终端配色，只用文本插值（绝不 v-html） -->
    <div ref="logEl" class="log-surface" @scroll.passive="onScroll">
      <div v-if="loading" class="log-empty">
        <v-progress-circular indeterminate size="32" width="3" />
        <div class="mt-3 text-body-2">正在连接实时日志…</div>
      </div>

      <div v-else-if="emptyText" class="log-empty">
        <v-icon size="36" color="grey">mdi-text-box-outline</v-icon>
        <div class="mt-2 text-body-2">{{ emptyText }}</div>
      </div>

      <template v-else>
        <template v-for="(item, index) in renderedLines" :key="index">
          <!-- 运行边界：磁盘日志跨运行追加，不画出来会以为两次运行的事件是连续的 -->
          <div v-if="item.runStart" class="log-run-start">
            本次运行开始 · pid {{ item.runStart.pid }} · {{ item.runStart.at }}
          </div>
          <div v-else class="log-line">
            <span
              v-for="(span, spanIndex) in item.line.spans"
              :key="spanIndex"
              :class="span.cls"
              :style="span.color ? { color: span.color } : undefined"
            >{{ span.text }}</span>
          </div>
        </template>
      </template>
    </div>

    <div class="log-footer text-caption text-disabled mt-1">
      {{ lines.length.toLocaleString('en-US') }} lines
      <template v-if="liveMeta"> · seq {{ seq }}</template>
      · {{ endpoint }}
    </div>
  </div>
</template>

<script lang="ts" setup>
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useModuleLogs } from '@/composables/useModuleLogs'
import { relativeTime } from '@/composables/useModuleState'
import type { LogModule } from '@/types/logs'
import type { AnsiLine } from '@/utils/ansi'

const props = defineProps<{
  module: LogModule
}>()

const { mode, lines, seq, meta, follow, notice, error, loading, connect, clearNotice } =
  useModuleLogs(props.module)

/** 浏览器里只保留这么多行 DOM；内存中的行数不设限（后端窗口本身就是有界的）。 */
const RENDER_LIMIT = 3000

type Level = 'all' | 'info' | 'warn' | 'error'
const level = ref<Level>('all')
// clearable 的 v-text-field 清空时会把 model 置为 null，这里显式接纳它
const search = ref<string | null>('')

const title = computed(() => (props.module === 'vedal' ? 'vedal（管理进程）' : props.module))
const endpoint = computed(() =>
  mode.value === 'disk'
    ? `/manage/logs/${props.module}`
    : `/manage/${props.module}/logs/events`,
)

const liveMeta = computed(() => (meta.value?.source === 'memory' ? meta.value : null))
const diskMeta = computed(() => (meta.value?.source === 'disk' ? meta.value : null))
const diskInfo = computed(() => meta.value?.disk ?? null)

const pid = computed(() => liveMeta.value?.pid ?? diskMeta.value?.pid ?? null)

const sourceLabel = computed(() => {
  if (mode.value === 'live') return 'live'
  if (mode.value === 'disk') return 'disk'
  return 'unreachable'
})
const sourceColor = computed(() => {
  if (mode.value === 'live') return 'success'
  if (mode.value === 'disk') return 'warning'
  return 'error'
})
const sourceIcon = computed(() => {
  if (mode.value === 'live') return 'mdi-access-point'
  if (mode.value === 'disk') return 'mdi-harddisk'
  return 'mdi-lan-disconnect'
})

const diskUsage = computed(() => {
  const disk = diskInfo.value
  if (!disk || disk.limit <= 0) return null
  return { used: disk.size, limit: disk.limit, ratio: disk.size / disk.limit }
})
/** 超过 80% 就变 warning：轮转一旦发生，最旧的历史就真的没了。 */
const diskColor = computed(() =>
  diskUsage.value && diskUsage.value.ratio > 0.8 ? 'warning' : undefined,
)

/** 磁盘窗口实际读了多少内容（用 UTF-8 字节数，别把字符数当字节数）。 */
const diskWindowBytes = computed(() =>
  diskMeta.value ? new Blob([diskMeta.value.content]).size : 0,
)
const diskWindowLabel = computed(() => formatBytes(diskWindowBytes.value))
const diskBanner = computed(() => {
  const window = diskWindowLabel.value
  return diskMeta.value?.running
    ? `实时日志连接失败（该进程可能未启用内存日志）。下面是磁盘上最后 ${window}，不是实时输出。`
    : `进程未运行，下面是磁盘上最后 ${window}，不是实时输出。`
})

/** 按行文本判级；纯前端行为，不改变后端任何东西。 */
function classifyLine(text: string): Exclude<Level, 'all'> {
  if (
    /traceback \(most recent call last\)/i.test(text) ||
    /\b(?:ERROR|CRITICAL|FATAL)\b/i.test(text)
  ) {
    return 'error'
  }
  if (/\b(?:WARNING|WARN)\b/i.test(text)) return 'warn'
  return 'info'
}

const filteredLines = computed<AnsiLine[]>(() => {
  const keyword = (search.value ?? '').trim().toLowerCase()
  return lines.value.filter((line) => {
    if (level.value !== 'all' && classifyLine(line.text) !== level.value) return false
    if (keyword && !line.text.toLowerCase().includes(keyword)) return false
    return true
  })
})

const visibleLines = computed<AnsiLine[]>(() => {
  const matched = filteredLines.value
  return matched.length > RENDER_LIMIT ? matched.slice(-RENDER_LIMIT) : matched
})
const renderLimited = computed(() => filteredLines.value.length > RENDER_LIMIT)

/**
 * log_kit 的运行分隔行（`[log_kit] run start module=… pid=… at=…`）。
 *
 * 只在 **disk 模式**下渲染成边界线：磁盘日志跨运行追加（绝不启动截断），
 * 不把边界画出来的话，用户会把上一个进程留下的记录当成本次运行的输出。
 * live 模式只有本次运行，这一行按普通日志显示即可。
 */
const RUN_START = /^\[log_kit\] run start module=\S+ pid=(\d+) at=(\S+)/
const renderedLines = computed(() =>
  visibleLines.value.map((line) => {
    const match = mode.value === 'disk' ? RUN_START.exec(line.text) : null
    return {
      line,
      runStart: match ? { pid: match[1], at: (match[2] ?? '').replace('T', ' ') } : null,
    }
  }),
)

const emptyText = computed(() => {
  if (loading.value) return ''
  if (mode.value === 'error' && !lines.value.length) return '日志不可用。'
  if (mode.value === 'disk') {
    if (!diskMeta.value?.disk.exists) return '这个模块没有运行，也没有在磁盘上留下日志。'
    if (!lines.value.length) return '磁盘日志文件是空的。'
  }
  if (mode.value === 'live' && !lines.value.length) {
    return liveMeta.value?.retained_bytes === 0
      ? '这个进程还没写过任何东西。'
      : '当前窗口内没有可显示的内容。'
  }
  if (!visibleLines.value.length) return '没有匹配的日志行。'
  return ''
})

function formatBytes(value: number): string {
  if (!Number.isFinite(value) || value <= 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB'] as const
  let scaled = value
  let index = 0
  while (scaled >= 1024 && index < units.length - 1) {
    scaled /= 1024
    index += 1
  }
  const digits = index === 0 ? 0 : scaled >= 100 ? 0 : 1
  return `${scaled.toFixed(digits)} ${units[index] ?? 'B'}`
}

// ---------- 自动滚底 ----------
const logEl = ref<HTMLElement | null>(null)
let autoScrolling = false
let lastScrollTop = 0

function onScroll(): void {
  const el = logEl.value
  if (!el) return
  const top = el.scrollTop
  // 只认"用户把视口往上移"：追加内容时浏览器可能做滚动锚定（top 变大），
  // 那不是用户意图，不能因此关掉 follow。
  const movedUp = top < lastScrollTop - 1
  lastScrollTop = top
  if (autoScrolling) return
  const distance = el.scrollHeight - top - el.clientHeight
  if (movedUp && distance > 24 && follow.value) follow.value = false
}

function scrollToBottom(): void {
  const el = logEl.value
  if (!el) return
  autoScrolling = true
  el.scrollTop = el.scrollHeight
  lastScrollTop = el.scrollTop
  window.requestAnimationFrame(() => {
    autoScrolling = false
  })
}

watch(
  [follow, visibleLines],
  async () => {
    if (!follow.value) return
    await nextTick()
    scrollToBottom()
  },
  { flush: 'post' },
)

onMounted(connect)
</script>

<style scoped>
.logs-page {
  display: flex;
  flex-direction: column;
  /* 满屏日志：扣掉 app bar 与 v-container 的上下留白 */
  height: calc(100dvh - 112px);
  min-height: 420px;
}

.log-search {
  max-width: 280px;
  min-width: 180px;
}

/* 日志区固定深底：ANSI 标准色是按深色终端设计的，跟随浅色主题会看不清 */
.log-surface {
  flex: 1 1 auto;
  min-height: 0;
  overflow: auto;
  padding: 10px 12px;
  border: 1px solid rgba(255, 255, 255, 0.14);
  border-radius: 6px;
  background: #1b1b1b;
  color: #d4d4d4;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, 'Liberation Mono', monospace;
  font-size: 12.5px;
  line-height: 1.45;
  tab-size: 4;
}

.log-line {
  min-height: 1.45em;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

/* 运行边界：绿线 + 一行小字，明确"下面是另一个进程的输出" */
.log-run-start {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 10px 0 6px;
  color: #6fcf8b;
  font-size: 11.5px;
  letter-spacing: 0.02em;
}

.log-run-start::before,
.log-run-start::after {
  content: '';
  flex: 1 1 auto;
  height: 1px;
  background: rgba(111, 207, 139, 0.4);
}

.ansi-bold {
  font-weight: 700;
}

.log-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 100%;
  color: #9e9e9e;
  text-align: center;
}

.log-footer {
  font-variant-numeric: tabular-nums;
}
</style>
