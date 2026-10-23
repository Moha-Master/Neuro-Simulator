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

      <!-- 上一次"异常终止开播"的原因：服务端在下次成功开播时清空，所以它只描述
           刚才那一次为什么没开成（例如 neuro 卡在开场场景、轮询不到状态）。 -->
      <v-alert
        v-if="lastError"
        type="warning"
        variant="tonal"
        density="compact"
        class="mb-4"
      >
        {{ lastError.message }}
        <div class="text-caption mt-1 text-medium-emphasis">
          neuro 场景 {{ lastError.detail?.neuro_scene ?? '—' }} · 轮询
          {{ lastError.detail?.polls ?? 0 }} 次
          <template v-if="lastError.detail?.status_error">
            · 状态读取失败：{{ lastError.detail.status_error }}
          </template>
        </div>
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
          <template v-if="currentState !== 'offline'">
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
                <div class="text-caption text-disabled">轮次 / 待消费（普通 · 优先）</div>
                <div class="text-body-2">
                  {{ rounds }} / {{ st.status.value?.sections.chat.current?.normal_pending ?? '—' }} ·
                  {{ st.status.value?.sections.chat.current?.priority_pending ?? '—' }}
                </div>
              </v-col>
              <v-col cols="6" md="3">
                <div class="text-caption text-disabled">队列</div>
                <div class="text-body-2">
                  <router-link to="/stream-view" class="text-primary">查看 →</router-link>
                </div>
              </v-col>
            </v-row>
            <!-- 开场阶段：循环在等 neuro 离开她自己的开场场景（判据是她的权威状态，
                 不是本地倒计时）。这里把"在等什么、等了多久、还剩多久"摊开，
                 免得卡在开播中时只能看到一个不动的状态 chip。 -->
            <div v-if="introGate" class="text-caption text-medium-emphasis mt-2">
              <v-icon size="x-small" icon="mdi-movie-open" class="mr-1" />
              等待开场结束 · neuro 场景 {{ introGate.neuro_scene ?? '—' }} · 已等
              {{ introGate.waited_s }}s / 上限 {{ introGate.timeout_s }}s
              <template v-if="introGate.status_error">
                · 状态读取失败：{{ introGate.status_error }}
              </template>
            </div>
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
            @click="startStream"
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

      <!-- 本场信息：标题 / 分区（开播时作为初值，直播中可改并实时广播） -->
      <v-card class="mb-6">
        <v-card-title class="text-subtitle-1 d-flex align-center">
          本场信息
          <v-spacer />
          <v-chip size="x-small" :color="live ? 'success' : 'grey'" variant="tonal">
            {{ live ? '直播中 · 实时生效' : '未开播 · 作为下一场初值' }}
          </v-chip>
        </v-card-title>
        <v-card-text>
          <v-row dense align="center">
            <v-col cols="12" md="7">
              <v-text-field
                v-model="metaTitle"
                label="标题（title）"
                variant="outlined"
                density="compact"
                hide-details
                placeholder="AI VTuber chatting with chat!"
                counter="120"
              />
            </v-col>
            <v-col cols="12" md="5">
              <v-combobox
                v-model="metaCategory"
                :items="CATEGORIES"
                label="分区（category）"
                variant="outlined"
                density="compact"
                hide-details
                clearable
              />
            </v-col>
          </v-row>
          <v-row dense class="mt-1">
            <v-col cols="12">
              <div class="text-caption text-medium-emphasis mb-1">标签（tags）</div>
              <TagInput v-model="metaTags" />
            </v-col>
          </v-row>
          <div v-pre class="text-caption text-disabled mt-2">
            标题 / 分区会作为 <code>{{title}}</code> / <code>{{category}}</code> /
            <code>{{stream_info}}</code> 注入 Neuro 与 Chatbot 的消息模板；
            标题、分区、标签都会实时同步到 dashboard 观看页。
          </div>
        </v-card-text>
        <v-card-actions>
          <v-btn
            color="primary"
            variant="tonal"
            prepend-icon="mdi-content-save-outline"
            :disabled="!live || !metaDirty"
            :loading="st.busy.value"
            @click="saveMetadata"
          >
            应用到场次
          </v-btn>
          <v-btn variant="text" :disabled="!metaDirty" @click="syncMetadata">放弃更改</v-btn>
        </v-card-actions>
      </v-card>

      <!-- 场景：只读显示。切换由直播循环自动驱动（开播 → room_intro、开场放完 → room、
           开局 → 游戏场景、下播 → no-source 待机）；手动切会让画面与循环/游戏状态
           互相打架，所以 dashboard 不再提供按钮（API 仍然可用）。
           room_intro 是 hidden 场景（不在 GET /stream/scenes 清单里），名字与图标
           由服务端 status.sections.scene.current 给出。 -->
      <v-card class="mb-6">
        <v-card-title class="text-subtitle-1">场景（直播画面）</v-card-title>
        <v-card-text>
          <div class="d-flex align-center flex-wrap ga-2">
            <v-chip size="small" color="primary" variant="tonal" :prepend-icon="currentSceneIcon">
              {{ currentSceneLabel }}
            </v-chip>
            <v-chip
              v-if="st.status.value?.sections.games.current?.active"
              size="small"
              variant="tonal"
              prepend-icon="mdi-gamepad-variant"
            >
              游戏：{{ st.status.value.sections.games.current?.active }}
            </v-chip>
            <v-spacer />
            <v-btn
              size="x-small"
              variant="text"
              prepend-icon="mdi-code-json"
              @click="sceneEditor = true"
            >
              编辑场景数据
            </v-btn>
          </div>
          <div class="text-caption text-disabled mt-3">
            场景由直播循环自动切换：开播进 room（开场由 neuro 窗口播放），
            开局切到对应游戏场景，下播回到 no-source 待机。神经窗口自身的场景
            （常态 / 开场）在 <b>Neuro Sama → View</b> 的侧栏切换。
          </div>
        </v-card-text>
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
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useModuleStatus } from '@/composables/useModuleStatus'
import { useStream } from '@/composables/useStream'
import ModuleOffline from '@/components/ModuleOffline.vue'
import SceneManifestEditor from '@/components/scene/SceneManifestEditor.vue'
import TagInput from '@/components/tags/TagInput.vue'

const status = useModuleStatus('stream')
const st = useStream()

/** 场景数据编辑器（存的是 stream 模块自己 DB 里的那份） */
const sceneEditor = ref(false)

/** 常用 Twitch 分区（可自由输入其它值） */
const CATEGORIES = [
  'Just Chatting',
  'IRL',
  'Music',
  'Art',
  'Minecraft',
  'osu!',
  'Chess',
  'Talking',
]

let timer: number | null = null

const currentState = computed(
  () => st.status.value?.sections.loop.current?.state ?? 'offline',
)
const sid = computed(() => st.status.value?.sections.loop.current?.session_id ?? '')
const conv = computed(() => st.status.value?.sections.loop.current?.conversation_id ?? '')
const rounds = computed(() => st.status.value?.sections.loop.current?.round ?? 0)
const live = computed(() => Boolean(st.status.value?.sections.metadata.current?.live))
const currentScene = computed(() => st.status.value?.sections.scene.current?.id ?? '')
const currentSceneInfo = computed(
  () => st.scenes.value.find((s) => s.id === currentScene.value) ?? null,
)
const currentSceneLabel = computed(() => {
  if (currentSceneInfo.value) return currentSceneInfo.value.label
  // hidden 场景（开场的 room_intro）不在手动清单里 → 用服务端给的 label，别显示裸 id
  const fromServer = st.status.value?.sections.scene.current?.label
  if (fromServer) return fromServer
  return currentScene.value ? currentScene.value : '待机（no source）'
})
const currentSceneIcon = computed(
  () =>
    currentSceneInfo.value?.icon ??
    st.status.value?.sections.scene.current?.icon ??
    'mdi-television-pause',
)
// 开场闸门与"上次为什么没开成"（服务端在下次成功开播时清空 last_error）
const introGate = computed(() => st.status.value?.sections.loop.current?.intro ?? null)
const lastError = computed(() => st.status.value?.sections.loop.current?.last_error ?? null)

// 本场信息编辑区：以服务端值为基线；用户有未保存改动时不被轮询覆盖，
// 但场次切换（开播/下播）会重新对齐（新场次的标题来自上一场或 config 默认值）。
const metaTitle = ref('')
const metaCategory = ref('')
const metaTags = ref<string[]>([])
const metaBase = ref({ title: '', category: '', tags: [] as string[], live: false })

function sameTags(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((tag, i) => tag === b[i])
}

const metaDirty = computed(
  () =>
    metaTitle.value !== metaBase.value.title ||
    metaCategory.value !== metaBase.value.category ||
    !sameTags(metaTags.value, metaBase.value.tags),
)

watch(
  () => st.status.value?.sections.metadata.current,
  (m) => {
    if (!m) return
    if (!metaDirty.value || m.live !== metaBase.value.live) {
      metaBase.value = {
        title: m.title,
        category: m.category,
        tags: [...(m.tags ?? [])],
        live: m.live,
      }
      metaTitle.value = m.title
      metaCategory.value = m.category
      metaTags.value = [...(m.tags ?? [])]
    }
  },
  { immediate: true, deep: true },
)

function syncMetadata() {
  metaTitle.value = metaBase.value.title
  metaCategory.value = metaBase.value.category
  metaTags.value = [...metaBase.value.tags]
}

async function saveMetadata() {
  if (await st.setMetadata(metaTitle.value, metaCategory.value, metaTags.value)) {
    metaBase.value = {
      title: metaTitle.value,
      category: metaCategory.value,
      tags: [...metaTags.value],
      live: true,
    }
  }
}

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

/** 开播：把编辑区里的标题/分区/标签作为本场信息带上（留空则用 config 默认值） */
async function startStream() {
  await st.action('start', {
    title: metaTitle.value,
    category: metaCategory.value,
    tags: metaTags.value,
  })
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
  void st.refreshScenes()
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
