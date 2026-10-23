<template>
  <div class="neuro-view">
    <div v-if="!status.checked.value" class="neuro-view__fill">
      <v-progress-circular indeterminate size="48" width="4" />
    </div>

    <ModuleOffline
      v-else-if="!status.reachable.value"
      module-name="neuro_sama"
      :on-started="status.check"
    />

    <template v-else>
      <div class="neuro-view__toolbar">
        <div class="d-flex align-center ga-2">
          <v-icon color="primary">mdi-television-play</v-icon>
          <span class="text-subtitle-1 font-weight-medium">Neuro View</span>
          <v-chip v-if="currentSceneId" size="small" color="success" variant="tonal">
            {{ currentSceneLabel }}
          </v-chip>
        </div>

        <v-spacer />

        <v-btn
          variant="text"
          size="small"
          prepend-icon="mdi-open-in-new"
          @click="openStandalone"
        >
          新窗口
        </v-btn>
      </div>

      <div class="neuro-view__body">
        <div class="neuro-view__stage">
          <iframe
            ref="frame"
            class="neuro-view__frame"
            src="/ui/neuro/"
            title="Neuro window"
            allow="autoplay; fullscreen"
            @load="sendVisibility"
          />
        </div>

        <!-- 控制台侧栏：场景切换（后端的权威状态）+ 层显隐（本页的临时合成意图） -->
        <div class="neuro-view__side">
          <v-card variant="outlined" density="compact">
            <v-card-title class="text-body-2 font-weight-medium">场景</v-card-title>
            <v-card-text class="pt-0">
              <div v-if="!scenes.length" class="text-caption text-grey-darken-1">
                没读到场景清单：确认 neuro 模块在运行（场景数据由 neuro 提供）。
              </div>
              <v-btn
                v-for="sc in scenes"
                :key="sc.id"
                class="neuro-view__scene-btn"
                block
                size="small"
                variant="flat"
                :color="sc.id === currentSceneId ? 'primary' : undefined"
                :prepend-icon="sc.icon || 'mdi-image-frame'"
                :loading="switching === sc.id"
                :title="sceneHint(sc)"
                @click="switchScene(sc)"
              >
                {{ sc.label }}
              </v-btn>
              <div class="text-caption text-grey-darken-1 mt-2">
                场景是 neuro 后端的权威状态：切一次广播一次，OBS / 合成器 / 本页一起收敛。
                「开场」放完会按场景数据自动回常态。
              </div>
              <v-btn
                class="mt-2"
                size="x-small"
                variant="text"
                prepend-icon="mdi-code-json"
                @click="sceneEditor = true"
              >
                编辑场景数据
              </v-btn>
            </v-card-text>
          </v-card>

          <v-card variant="outlined" density="compact">
            <v-card-title class="d-flex align-center text-body-2 font-weight-medium">
              层显隐
              <v-spacer />
              <v-btn
                size="x-small"
                variant="text"
                :disabled="!hiddenLayers.length"
                @click="restoreLayers"
              >
                全部恢复
              </v-btn>
            </v-card-title>
            <v-card-text class="pt-0">
              <div v-if="!layerRows.length" class="text-caption text-grey-darken-1">
                当前场景没有可读的层。
              </div>
              <div v-for="row in layerRows" :key="row.id" class="neuro-view__layer">
                <v-switch
                  v-if="row.declared"
                  :model-value="visible[row.id] !== false"
                  color="primary"
                  density="compact"
                  hide-details
                  :label="row.name"
                  @update:model-value="toggleLayer(row.id, $event as boolean)"
                />
                <div v-else class="text-caption text-grey-darken-1 neuro-view__layer--off">
                  {{ row.name }}
                  <v-chip size="x-small" variant="tonal">本场景不显示</v-chip>
                </div>
              </div>
              <div class="text-caption text-grey-darken-1 mt-1">
                这里只影响本页预览；合成器按 stream 场景数据里的
                <code>neuro.hide</code> 各自控制，互不干扰。
              </div>
            </v-card-text>
          </v-card>

          <v-card v-if="current" variant="outlined" density="compact">
            <v-card-title class="text-body-2 font-weight-medium">当前状态</v-card-title>
            <v-card-text class="text-caption">
              <div>场景：{{ current.id }}（{{ fmt(current.duration) }}s）</div>
              <div v-if="current.duration">
                已进入：{{ fmt(current.elapsed) }}s / {{ fmt(current.duration) }}s
              </div>
              <div v-else>常驻（该场景没有脚本时长）</div>
              <div v-if="hiddenLayers.length">已隐藏：{{ hiddenLayers.join(', ') }}</div>
            </v-card-text>
          </v-card>
        </div>
      </div>

      <div class="neuro-view__hint text-caption text-grey-darken-1">
        这一页是 neuro.exe 的窗口预览（背景 / 开场 / 立绘 / 字幕）。层的几何与场景都来自
        neuro 模块的场景数据；合成后的直播画面在 Stream → View。
      </div>

      <SceneManifestEditor
        v-model="sceneEditor"
        base="/manage/neuro_sama"
        title="Neuro 窗口的场景数据"
      />
    </template>

    <v-snackbar v-model="snackbar" :timeout="3500" location="bottom">
      {{ snackbarText }}
    </v-snackbar>
  </div>
</template>

<script lang="ts" setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import ModuleOffline from '@/components/ModuleOffline.vue'
import SceneManifestEditor from '@/components/scene/SceneManifestEditor.vue'
import { useModuleStatus } from '@/composables/useModuleStatus'
import { api, describeError } from '@/stores/connection'

interface SceneElement {
  id: string
  name?: string | null
  z?: number | null
  visible?: boolean
  content?: string | null
  component?: string | null
}

interface NeuroScene {
  id: string
  label: string
  icon?: string | null
  hidden?: boolean
  duration: number
  auto_returns?: boolean
  elements: SceneElement[]
}

interface SceneState {
  id?: string
  duration?: number
  elapsed?: number
}

interface ScenesResponse {
  scenes: NeuroScene[]
  default: string
  refs?: Record<string, string>
  current: SceneState
}

const status = useModuleStatus('neuro_sama')
const frame = ref<HTMLIFrameElement | null>(null)

const data = ref<ScenesResponse | null>(null)
const visible = ref<Record<string, boolean>>({})
const switching = ref('')
const snackbar = ref(false)
const snackbarText = ref('')
/** 场景数据编辑器（存的是 neuro 模块自己 DB 里的那份） */
const sceneEditor = ref(false)

const scenes = computed(() => (data.value?.scenes ?? []).filter((s) => !s.hidden))
const current = computed<SceneState | null>(() => data.value?.current ?? null)
const currentSceneId = computed(() => current.value?.id ?? '')
const currentScene = computed<NeuroScene | null>(
  () => (data.value?.scenes ?? []).find((s) => s.id === currentSceneId.value) ?? null,
)
const currentSceneLabel = computed(() => currentScene.value?.label ?? currentSceneId.value)

const layerRows = computed(() =>
  (currentScene.value?.elements ?? []).map((e) => ({
    id: e.id,
    name: e.name || e.id,
    // 场景数据里 visible:false 的层由脚本（如开场时间线）负责显示，
    // 外部只能"再隐藏"，不能强制显示——所以这类行不给开关。
    declared: e.visible !== false,
  })),
)

const hiddenLayers = computed(() =>
  Object.entries(visible.value)
    .filter(([, on]) => !on)
    .map(([id]) => id),
)

function fmt(v?: number): string {
  return v === undefined ? '-' : v.toFixed(v >= 10 ? 1 : 2)
}

function sceneHint(sc: NeuroScene): string {
  const parts = [
    `时长 ${fmt(sc.duration)}s`,
    sc.auto_returns ? '放完自动回常态' : '不会自动离开',
    `元素：${sc.elements.map((e) => e.id).join(', ')}`,
  ]
  return parts.join(' · ')
}

/** 每个渲染器独立控制显隐：与 stream 合成器同一套 postMessage 协议
 *  （只发要隐藏的层 id，新加一层不必改这里的代码）。 */
function sendVisibility(): void {
  frame.value?.contentWindow?.postMessage(
    { ns: 'neuro-window', type: 'visibility', hidden: hiddenLayers.value },
    '*',
  )
}

function toggleLayer(id: string, on: boolean): void {
  visible.value = { ...visible.value, [id]: on }
  sendVisibility()
}

function restoreLayers(): void {
  visible.value = {}
  sendVisibility()
}

async function loadScenes(): Promise<void> {
  try {
    data.value = await api<ScenesResponse>('/manage/neuro_sama/ui/scenes')
  } catch (e) {
    data.value = null
    snackbarText.value = `读取场景失败：${describeError(e)}`
    snackbar.value = true
  }
}

async function switchScene(sc: NeuroScene): Promise<void> {
  switching.value = sc.id
  try {
    const resp = await api<{ status: string; scene: SceneState }>('/manage/neuro_sama/ui/scene', {
      method: 'POST',
      body: JSON.stringify({ id: sc.id }),
    })
    // 先乐观落地，避免与 3s 轮询的在途请求抢先后导致按钮状态闪回
    if (data.value && resp?.scene?.id) data.value = { ...data.value, current: resp.scene }
    await loadScenes()
  } catch (e) {
    snackbarText.value = `切换场景失败：${describeError(e)}`
    snackbar.value = true
  } finally {
    switching.value = ''
  }
}

/** iframe 的 boot 是异步的（字体加载等），load 事件可能早于它的 message 监听；
 *  收到窗口的 ready 后再补发一次可见性。 */
function onIframeMessage(event: MessageEvent): void {
  if (event.source !== frame.value?.contentWindow) return
  const d = event.data as { ns?: string; type?: string } | undefined
  if (d?.ns === 'neuro-window' && d.type === 'ready') sendVisibility()
}

function openStandalone(): void {
  window.open('/ui/neuro/', '_blank', 'noopener')
}

// 场景被后端换掉（开播 intro / 自动回常态）时，层清单跟着变
watch(currentSceneId, () => sendVisibility())

let timer: number | null = null
onMounted(() => {
  window.addEventListener('message', onIframeMessage)
  void loadScenes()
  timer = window.setInterval(() => void loadScenes(), 3000)
})
onUnmounted(() => {
  window.removeEventListener('message', onIframeMessage)
  if (timer !== null) window.clearInterval(timer)
})
</script>

<style scoped>
.neuro-view {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 16px 20px 20px;
  min-height: 0;
  flex: 1;
}

.neuro-view__fill {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
}

.neuro-view__toolbar {
  display: flex;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
}

.neuro-view__body {
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  align-items: flex-start;
  gap: 16px;
}

.neuro-view__stage {
  flex: 1 1 auto;
  min-width: 0;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #111;
  border-radius: 10px;
  overflow: hidden;
}

/* 16:9 逻辑画布：宽度取容器与高度约束中较小者，避免溢出 */
.neuro-view__frame {
  width: min(100%, calc((100vh - 220px) * 16 / 9));
  aspect-ratio: 16 / 9;
  border: 0;
  display: block;
  background: #000;
  color-scheme: dark;
}

.neuro-view__side {
  flex: 0 0 300px;
  width: 300px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  max-height: 100%;
  overflow-y: auto;
}

.neuro-view__scene-btn {
  justify-content: flex-start;
  margin-bottom: 6px;
  text-transform: none;
  letter-spacing: 0;
}

.neuro-view__layer :deep(.v-switch) {
  margin-top: 0;
}

.neuro-view__layer--off {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 4px 0;
}

.neuro-view__hint {
  flex: 0 0 auto;
}

.neuro-view__hint code,
.neuro-view__side code {
  font-size: 0.85em;
}
</style>
