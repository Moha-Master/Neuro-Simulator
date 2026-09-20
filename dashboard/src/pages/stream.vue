<template>
  <div class="stream-page">
    <div v-if="!status.checked.value" class="stream-loading">
      <v-progress-circular indeterminate size="48" width="4" />
    </div>

    <ModuleOffline
      v-else-if="!status.reachable.value"
      module-name="stream"
      :on-started="status.check"
    />

    <template v-else>
      <v-alert
        v-if="st.error.value"
        type="error"
        variant="tonal"
        density="compact"
        closable
        class="mb-4"
        @click:close="st.error.value = ''"
      >
        {{ st.error.value }}
      </v-alert>

      <div class="d-flex align-center mb-4">
        <div class="text-h5">Stream Control</div>
        <v-spacer />
        <v-chip :color="stateColor" variant="tonal" class="ml-2">
          <v-icon start size="small" :icon="stateIcon" />
          {{ stateLabel }}
        </v-chip>
      </div>

      <!-- 当前场次 -->
      <v-card class="mb-6">
        <v-card-title class="text-subtitle-1">当前场次</v-card-title>
        <v-card-text>
          <template v-if="st.status.value && st.status.value.state !== 'offline'">
            <v-row dense>
              <v-col cols="6" md="3">
                <div class="text-caption text-disabled">Session</div>
                <div class="text-body-2">{{ short(sid) }}</div>
              </v-col>
              <v-col cols="6" md="3">
                <div class="text-caption text-disabled">对话</div>
                <div class="text-body-2">{{ short(conv) }}</div>
              </v-col>
              <v-col cols="6" md="3">
                <div class="text-caption text-disabled">轮次 / 待消费消息</div>
                <div class="text-body-2">{{ rounds }} / {{ st.status.value.queue_depth_unconsumed ?? '—' }}</div>
              </v-col>
              <v-col cols="6" md="3">
                <div class="text-caption text-disabled">队列</div>
                <div class="text-body-2">
                  <router-link to="/stream-view" class="text-primary">查看 →</router-link>
                </div>
              </v-col>
            </v-row>
          </template>
          <div v-else class="text-body-2 text-disabled">
            未在直播。点击下方「开始直播」新建场次（会话 + 消息队列 + 画面入场）。
          </div>
        </v-card-text>
        <v-divider />
        <v-card-actions>
          <v-btn
            color="success"
            prepend-icon="mdi-broadcast"
            :disabled="currentState !== 'offline'"
            :loading="st.busy.value"
            @click="doAction('start')"
          >
            开始直播
          </v-btn>
          <v-btn
            color="warning"
            variant="tonal"
            prepend-icon="mdi-pause"
            :disabled="currentState !== 'live'"
            @click="doAction('pause')"
          >
            暂停
          </v-btn>
          <v-btn
            color="info"
            variant="tonal"
            prepend-icon="mdi-play"
            :disabled="currentState !== 'paused'"
            @click="doAction('resume')"
          >
            继续
          </v-btn>
          <v-btn
            color="error"
            variant="tonal"
            prepend-icon="mdi-stop"
            :disabled="currentState === 'offline'"
            @click="doAction('stop')"
          >
            停止
          </v-btn>
        </v-card-actions>
      </v-card>

      <!-- 场景切换 -->
      <v-card class="mb-6">
        <v-card-title class="text-subtitle-1">场景（直播画面）</v-card-title>
        <v-card-actions class="flex-wrap gap-2 pa-4">
          <v-btn
            v-for="sc in SCENES"
            :key="sc.id"
            size="small"
            variant="outlined"
            :prepend-icon="sc.icon"
            @click="st.setScene(sc.id)"
          >
            {{ sc.label }}
          </v-btn>
          <div class="text-caption text-disabled" style="width: 100%">
            手动广播 scene_set 到 /ui 画面；直播循环的 intro 由「开始直播」自动触发。
          </div>
        </v-card-actions>
      </v-card>

      <!-- 场次史 -->
      <v-card>
        <v-card-title class="text-subtitle-1">最近场次</v-card-title>
        <v-table v-if="st.sessions.value.length" density="comfortable">
          <thead>
            <tr>
              <th class="text-left">Session</th>
              <th class="text-left">状态</th>
              <th class="text-left">开始</th>
              <th class="text-left">结束</th>
              <th class="text-left">轮次</th>
              <th class="text-right">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="s in st.sessions.value" :key="s.session_id">
              <td class="text-body-2">{{ short(s.session_id) }}</td>
              <td>
                <v-chip size="x-small" :color="s.state === 'offline' ? 'grey' : 'success'" variant="tonal">
                  {{ s.state }}
                </v-chip>
              </td>
              <td class="text-body-2">{{ s.started_at }}</td>
              <td class="text-body-2">{{ s.ended_at ?? '—' }}</td>
              <td class="text-body-2">{{ s.rounds }}</td>
              <td class="text-right">
                <v-btn
                  icon="mdi-delete-outline"
                  size="x-small"
                  color="error"
                  variant="text"
                  title="删除场次及消息队列"
                  @click="handleDelete(s.session_id)"
                />
              </td>
            </tr>
          </tbody>
        </v-table>
        <v-card-text v-else class="text-disabled">暂无场次记录。</v-card-text>
      </v-card>
    </template>
  </div>
</template>

<script lang="ts" setup>
import { computed, onMounted, onUnmounted, watch } from 'vue'
import { useModuleStatus } from '@/composables/useModuleStatus'
import { useStream } from '@/composables/useStream'
import ModuleOffline from '@/components/ModuleOffline.vue'

const status = useModuleStatus('stream')
const st = useStream()

const SCENES = [
  { id: 'room', label: 'Room', icon: 'mdi-sofa' },
  { id: 'starting_soon', label: 'Starting Soon', icon: 'mdi-clock-outline' },
  { id: 'community_art', label: 'Community Art', icon: 'mdi-palette' },
  { id: 'gameplay', label: 'Game Play', icon: 'mdi-gamepad-variant' },
]

let timer: number | null = null

const currentState = computed(() => st.status.value?.state ?? 'offline')
const sid = computed(() => st.status.value?.session?.session_id ?? '')
const conv = computed(() => st.status.value?.session?.conversation_id ?? '')
const rounds = computed(() => st.status.value?.persisted?.rounds ?? 0)

const stateLabel = computed(
  () => ({ offline: '离线', starting: '开播中', live: '直播中', paused: '已暂停' } as Record<string, string>)[
    currentState.value
  ] ?? currentState.value,
)
const stateColor = computed(
  () => ({ offline: 'grey', starting: 'info', live: 'success', paused: 'warning' } as Record<string, string>)[
    currentState.value
  ] ?? 'grey',
)
const stateIcon = computed(() => (currentState.value === 'live' ? 'mdi-record' : 'mdi-circle'))

function short(id: string): string {
  return id ? id.slice(0, 8) : '—'
}

async function doAction(kind: 'start' | 'pause' | 'resume' | 'stop') {
  await st.action(kind)
}

async function handleDelete(sessionId: string) {
  if (!confirm(`确定要删除场次 ${short(sessionId)} 及其对应的消息队列和 Neuro 对话吗？`)) return
  await st.deleteSession(sessionId)
}

function startPolling() {
  if (timer !== null) return
  void st.refreshStatus()
  void st.refreshSessions()
  timer = window.setInterval(() => {
    void st.refreshStatus()
    void st.refreshSessions()
  }, 3000)
}

function stopPolling() {
  if (timer !== null) {
    window.clearInterval(timer)
    timer = null
  }
}

watch(
  () => status.reachable.value,
  (r) => (r ? startPolling() : stopPolling()),
)

onMounted(() => {
  if (status.reachable.value) startPolling()
})
onUnmounted(stopPolling)
</script>

<style scoped>
.stream-page {
  max-width: 960px;
  margin: 0 auto;
  padding: 8px 0 32px;
}
.stream-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 50vh;
}
</style>
