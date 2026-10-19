<template>
  <div class="stream-view-page">
    <div v-if="!status.checked.value" class="stream-loading">
      <v-progress-circular indeterminate size="48" width="4" />
    </div>

    <ModuleOffline
      v-else-if="!status.reachable.value"
      module-name="stream"
      :on-started="status.check"
    />

    <div v-else class="stream-view-layout">
      <!-- 主体：嵌入直播画面（经 vedal 同源代理 /manage/stream/ui/ → stream /ui/） -->
      <div class="stream-view-stage">
        <iframe
          :src="uiUrl"
          class="stream-view-frame"
          allow="autoplay"
          title="Neuro-Sama Stream"
        />
      </div>

      <!-- 右侧栏：观众消息队列 + 发送 -->
      <div class="stream-view-side">
        <div class="d-flex align-center pa-3 pb-1">
          <div class="text-subtitle-2">Viewer Queue</div>
          <v-spacer />
          <v-chip size="x-small" :color="st.queueActive.value ? 'success' : 'grey'" variant="tonal">
            {{ st.queueActive.value ? '进行中' : '无场次' }}
          </v-chip>
        </div>
        <div v-if="st.error.value" class="px-3">
          <v-alert type="error" variant="tonal" density="compact" closable @click:close="st.error.value = ''">
            {{ st.error.value }}
          </v-alert>
        </div>

        <div ref="listEl" class="queue-list">
          <div v-if="!st.messages.value.length" class="queue-empty">
            {{ st.queueActive.value ? '队列为空——发送消息开始与 Neuro 对话' : '开始直播后队列可用' }}
          </div>
          <div
            v-for="m in st.messages.value"
            :key="m.id"
            class="queue-item"
            :class="{ 'queue-item--priority': m.priority_type }"
          >
            <div class="d-flex align-center">
              <span class="queue-item__user">{{ m.username }}</span>
              <v-chip
                v-if="m.priority_type"
                size="x-small"
                :color="m.priority_type === 'bits' ? 'deep-purple' : 'pink'"
                variant="flat"
                class="ml-2"
              >
                {{ m.value }} {{ m.priority_type }}
              </v-chip>
              <v-spacer />
              <span class="queue-item__time">{{ m.created_at.slice(11, 16) }}</span>
            </div>
            <div class="queue-item__content">{{ m.content }}</div>
          </div>
        </div>

        <v-divider />
        <div class="pa-3">
          <v-text-field
            v-model="username"
            label="username"
            variant="outlined"
            density="compact"
            hide-details
            class="mb-2"
            :disabled="!st.queueActive.value"
          />
          <v-switch
            v-model="priority"
            label="优先消息"
            density="compact"
            hide-details
            color="deep-purple"
            :disabled="!st.queueActive.value"
            class="mb-2"
          />
          <!-- 类型切换：Bits/Points 按钮组（配色与直播画面弹窗一致），测试时一键换型 -->
          <div v-if="priority" class="d-flex align-center gap-2 mb-2">
            <v-btn-toggle
              v-model="priorityType"
              density="compact"
              divided
              :color="priorityType === 'bits' ? 'deep-purple' : 'pink'"
              :disabled="!st.queueActive.value"
            >
              <v-btn value="bits" size="small">Bits</v-btn>
              <v-btn value="points" size="small">Points</v-btn>
            </v-btn-toggle>
            <v-text-field
              v-model.number="priorityValue"
              type="number"
              min="1"
              label="value"
              density="compact"
              variant="outlined"
              hide-details
              style="max-width: 110px"
            />
          </div>
          <div class="d-flex gap-2">
            <v-text-field
              v-model="content"
              placeholder="发送观众消息…"
              variant="outlined"
              density="compact"
              hide-details
              :disabled="!st.queueActive.value"
              @keydown.enter.prevent="send"
            />
            <v-btn
              color="primary"
              icon="mdi-send"
              :disabled="!canSend"
              :loading="sending"
              @click="send"
            />
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script lang="ts" setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useModuleStatus } from '@/composables/useModuleStatus'
import { useStream } from '@/composables/useStream'
import ModuleOffline from '@/components/ModuleOffline.vue'

const status = useModuleStatus('stream')
const st = useStream()

const uiUrl = '/manage/stream/ui/'
const username = ref('viewer')
const content = ref('')
const sending = ref(false)
const listEl = ref<HTMLElement | null>(null)

const priority = ref(false)
const priorityType = ref<'bits' | 'points'>('bits')
const priorityValue = ref(100)

const canSend = computed(() => {
  if (!st.queueActive.value || !content.value.trim()) return false
  if (priority.value && Number(priorityValue.value) < 1) return false
  return true
})

let queueTimer: number | null = null

async function pollQueue() {
  // 无进行中场次时不请求队列（避免注定 409 的请求刷屏控制台）
  if (st.status.value?.state === 'offline') {
    st.resetQueue()
    return
  }
  await st.pollQueue()
  await nextTick()
  const el = listEl.value
  if (el) el.scrollTop = el.scrollHeight
}

function startTimers() {
  if (queueTimer !== null) return
  void (async () => {
    await st.refreshStatus()
    await pollQueue()
  })()
  queueTimer = window.setInterval(async () => {
    await st.refreshStatus()
    await pollQueue()
  }, 2000)
}

function stopTimers() {
  if (queueTimer !== null) {
    window.clearInterval(queueTimer)
    queueTimer = null
  }
}

async function send() {
  const text = content.value.trim()
  if (!text) return
  sending.value = true
  const pri = priority.value
    ? { type: priorityType.value, value: Math.max(1, Math.floor(Number(priorityValue.value) || 1)) }
    : undefined
  if (await st.postMessage(username.value.trim() || 'viewer', text, pri)) content.value = ''
  sending.value = false
}

watch(
  () => status.reachable.value,
  (r) => (r ? startTimers() : stopTimers()),
)

onMounted(() => {
  if (status.reachable.value) startTimers()
})
onUnmounted(stopTimers)
</script>

<style scoped>
.stream-view-page {
  height: calc(100vh - 112px);
  min-height: 480px;
}
.stream-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 50vh;
}
.stream-view-layout {
  display: flex;
  gap: 12px;
  height: 100%;
}
.stream-view-stage {
  flex: 1 1 auto;
  min-width: 0;
  background: #000;
  border-radius: 8px;
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: center;
}
.stream-view-frame {
  width: 100%;
  height: 100%;
  border: 0;
  background: #000;
}
.stream-view-placeholder {
  color: #888;
  font-size: 14px;
}
.stream-view-side {
  flex: 0 0 320px;
  display: flex;
  flex-direction: column;
  background: rgb(var(--v-theme-surface));
  border-radius: 8px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  overflow: hidden;
}
.queue-list {
  flex: 1 1 auto;
  overflow-y: auto;
  padding: 8px 12px;
}
.queue-empty {
  color: #888;
  font-size: 12px;
  text-align: center;
  margin-top: 24px;
}
.queue-item {
  margin-bottom: 8px;
  font-size: 13px;
  padding: 4px 6px;
  border-radius: 4px;
}
.queue-item--priority {
  background: rgba(145, 71, 255, 0.08);
  border-left: 2px solid #b388ff;
}
.queue-item__user {
  color: #b388ff;
  font-weight: 600;
}
.queue-item__time {
  color: #777;
  font-size: 11px;
  margin-left: 6px;
}
.queue-item__content {
  word-break: break-word;
}
</style>
