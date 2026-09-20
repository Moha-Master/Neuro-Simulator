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
          <div v-for="m in st.messages.value" :key="m.id" class="queue-item">
            <span class="queue-item__user">{{ m.username }}</span>
            <span class="queue-item__time">{{ m.created_at.slice(11, 16) }}</span>
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
              :disabled="!st.queueActive.value || !content.trim()"
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
import { nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
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
  if (await st.postMessage(username.value.trim() || 'viewer', text)) content.value = ''
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
