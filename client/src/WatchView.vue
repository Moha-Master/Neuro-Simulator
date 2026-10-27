<template>
  <div class="twitch-shell" :class="isDark ? 'tw-dark' : 'tw-light'">
    <div v-if="!status.checked.value" class="twitch-loading">
      <v-progress-circular indeterminate size="48" width="4" />
    </div>

    <ModuleOffline
      v-else-if="!status.reachable.value"
      module-name="stream"
      :on-started="status.check"
    />

    <div v-else class="twitch-watch">
      <!-- 左列：播放器自适应剩余高度（画面在 iframe 内自行 letterbox），
           频道信息恒显其下，整页无滚动条（Twitch 首屏模型） -->
      <div class="twitch-watch__main">
        <TwitchOfflinePanel v-if="!isLive" />
        <div v-else class="twitch-player">
          <iframe
            :src="uiUrl"
            class="twitch-player__iframe"
            allow="autoplay"
            title="Neuro-Sama Stream"
          />
        </div>

        <TwitchChannelInfo
          :title="title"
          :category="category"
          :tags="tags"
          :state="channelState"
          :started-at="startedAt"
          :audience="audience"
        />
      </div>

      <!-- 右列：Twitch 聊天栏。
           判据与画面区一致 = metadata.live：正常开播 starting 起观众就能看开场 +
           聊天；record（模拟直播）metadata.live 恒 false，整个观看页完全离线。 -->
      <TwitchChatPanel
        v-if="isLive"
        v-model:collapsed="chatCollapsed"
        :messages="chatMessages"
        :active="chatActive"
        :connected="chatConnected"
        :send="sendMessage"
      />

      <!-- 折叠后的展开按钮（浮在播放器右上角） -->
      <button
        v-if="chatCollapsed && isLive"
        class="twitch-expand-chat"
        type="button"
        title="展开聊天"
        @click="chatCollapsed = false"
      >
        <TwitchIcon name="showChat" :size="20" />
      </button>
    </div>

    <v-snackbar v-model="snackbar" :timeout="4000" color="error" location="bottom">
      {{ snackbarText }}
    </v-snackbar>
  </div>
</template>

<script lang="ts" setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import ModuleOffline from '@/components/ModuleOffline.vue'
import TwitchChannelInfo from '@/components/twitch/TwitchChannelInfo.vue'
import TwitchChatPanel from '@/components/twitch/TwitchChatPanel.vue'
import TwitchIcon from '@/components/twitch/TwitchIcon.vue'
import TwitchOfflinePanel from '@/components/twitch/TwitchOfflinePanel.vue'
import { useChatIdentity } from '@/composables/chatIdentity'
import { useModuleStatus } from '@/composables/useModuleStatus'
import { useStream } from '@/composables/useStream'
import { useStreamChat } from '@/composables/useStreamChat'
import { isDark } from '@/theme'

const status = useModuleStatus('stream')

const { status: streamStatus, error: streamError, refreshStatus, postMessage } = useStream()
const {
  messages: chatMessages,
  state: chatState,
  metadata: chatMetadata,
  connected: chatConnected,
  active: chatSessionActive,
} = useStreamChat()
const { username: chatUsername } = useChatIdentity()

// 合成器是 dashboard 构建里的独立页面（与 neuro 窗口同一个构建），
// 经 vedal origin 访问时 `/ui/stream/` 才是对的；直连 :8200/view 时由页面降级兜住。
const uiUrl = '/ui/stream/'

const state = computed(
  // 轮询拿到的是**进程级**生命周期，比 iframe 转发的 chatState 更可靠：
  // 下播会卸载播放器 iframe，而重新开播的 session_begin 又只能由 iframe 转发，
  // 若这里让 chatState 优先就会永久停在 offline（iframe 永远不再挂载）。
  () => streamStatus.value?.sections.loop.current?.state || chatState.value || 'offline',
)

/** 对外是否在播：唯一判据 = `metadata.live`（有场次且非 record）。
 *  正常开播 starting 起就为 true——观众要能看开场；record 全程 false，
 *  观看页因此完全离线（离线面板、无聊天栏）。 */
const isLive = computed(() => Boolean(streamStatus.value?.sections.metadata.current?.live))
/** 频道信息条：非 live（record/待机）不显示 LIVE 徽标与直播详情。 */
const channelState = computed(() => (isLive.value ? state.value : 'offline'))

const title = computed(
  () => chatMetadata.value.title || streamStatus.value?.sections.metadata.current?.title || '',
)
const category = computed(
  () =>
    chatMetadata.value.category ||
    streamStatus.value?.sections.metadata.current?.category ||
    'Just Chatting',
)
const tags = computed(() =>
  chatMetadata.value.tags.length
    ? chatMetadata.value.tags
    : (streamStatus.value?.sections.metadata.current?.tags ?? []),
)

const audience = computed(() => streamStatus.value?.sections.audience.current ?? null)

const startedAt = computed(() => {
  const persisted = streamStatus.value?.sections.loop.current?.persisted
  if (!persisted || persisted.state === 'offline') return null
  return persisted.started_at
})

const chatActive = computed(
  () =>
    chatSessionActive.value ||
    (Boolean(streamStatus.value?.sections.loop.current?.session_id) && isLive.value),
)

// 折叠状态与 Twitch 一样持久化
const COLLAPSE_KEY = 'ns.twitch.chatCollapsed'
const chatCollapsed = ref(localStorage.getItem(COLLAPSE_KEY) === '1')
watch(chatCollapsed, (value) => localStorage.setItem(COLLAPSE_KEY, value ? '1' : '0'))

const snackbar = ref(false)
const snackbarText = ref('')

async function sendMessage(
  content: string,
  highlight?: { type: 'bits' | 'points'; value: number },
): Promise<boolean> {
  const ok = await postMessage(chatUsername.value, content, highlight)
  if (!ok) {
    snackbarText.value = `发送失败：${streamError.value}`
    snackbar.value = true
  }
  return ok
}

let statusTimer: number | null = null

onMounted(() => {
  void refreshStatus()
  statusTimer = window.setInterval(() => void refreshStatus(), 5000)
})

onUnmounted(() => {
  if (statusTimer !== null) window.clearInterval(statusTimer)
})
</script>

<style scoped>
.twitch-shell {
  display: flex;
  flex-direction: column;
  flex: 1 1 0;
  min-height: 0;
  overflow: hidden;
  background: var(--tw-bg-body);
  color: var(--tw-text);
}

.twitch-loading {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
}

/* Twitch 观看页：左列（播放器 + 信息）+ 右列聊天；两列都不产生整页滚动 */
.twitch-watch {
  position: relative;
  flex: 1 1 auto;
  min-height: 0;
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  grid-template-rows: minmax(0, 1fr);
}

.twitch-watch__main {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  /* 正常窗口内容恰好占满不滚动；极小窗口（离线面板保留旧 client 的
     500px 下限）时才出现滚动兜底 */
  overflow-y: auto;
  overflow-x: hidden;
  scrollbar-width: thin;
  scrollbar-color: var(--tw-scrollbar) transparent;
}

.twitch-watch__main::-webkit-scrollbar {
  width: 8px;
}

.twitch-watch__main::-webkit-scrollbar-thumb {
  background: var(--tw-scrollbar);
  border-radius: 4px;
}

/* 播放器占满左列剩余高度；16:9 画面由 stream/ui 的 #stage 在 iframe
   内部 letterbox（黑边），与 Twitch 播放器容器行为一致 */
.twitch-player {
  flex: 1 1 0;
  min-height: 0;
  position: relative;
  background: #000;
}

.twitch-player__iframe {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  border: 0;
  background: #000;
}

.twitch-expand-chat {
  position: absolute;
  top: 16px;
  right: 16px;
  z-index: 5;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border: 0;
  border-radius: 50%;
  background: #333;
  color: #fff;
  cursor: pointer;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.4);
}

.twitch-expand-chat:hover {
  background: #444;
}

/* 窄屏：聊天栏改为下方 42vh，播放器占满宽度 */
@media (max-width: 1024px) {
  .twitch-shell {
    --tw-sidebar-width: 100%;
  }

  .twitch-watch {
    display: flex;
    flex-direction: column;
  }

  .twitch-watch__main {
    flex: 1 1 0;
    min-height: 0;
  }

  .twitch-watch > .tw-chat {
    flex: 0 0 42vh;
    height: 42vh;
    border-left: 0;
    border-top: 1px solid var(--tw-border);
  }
}
</style>
