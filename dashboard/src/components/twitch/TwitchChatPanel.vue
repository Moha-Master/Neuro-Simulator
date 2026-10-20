<template>
  <aside class="tw-chat" :class="{ 'tw-chat--collapsed': collapsed }">
    <header class="tw-chat__header">
      <button
        class="tw-icon-btn tw-chat__collapse"
        type="button"
        :title="collapsed ? '展开聊天' : '折叠聊天'"
        @click="collapsed = !collapsed"
      >
        <TwitchIcon name="collapse" :size="20" />
      </button>

      <div class="tw-chat__title">直播聊天</div>

      <v-menu v-model="viewersOpen" location="bottom end">
        <template #activator="{ props: menuProps }">
          <button
            v-bind="menuProps"
            class="tw-icon-btn tw-chat__community"
            type="button"
            title="社区"
          >
            <TwitchIcon name="community" :size="20" />
          </button>
        </template>
        <v-list density="compact" max-height="320" class="py-1">
          <v-list-subheader>聊天室成员（{{ chatters.length }}）</v-list-subheader>
          <v-list-item v-for="name in chatters" :key="name" density="compact">
            <v-list-item-title class="text-body-2">{{ name }}</v-list-item-title>
          </v-list-item>
          <v-list-item v-if="!chatters.length" density="compact">
            <v-list-item-title class="text-body-2 text-disabled">暂无成员</v-list-item-title>
          </v-list-item>
        </v-list>
      </v-menu>
    </header>

    <div class="tw-chat__list-wrap">
      <div ref="listEl" class="tw-chat__list" @scroll="onScroll">
        <div v-if="!active && !messages.length" class="tw-chat__empty">聊天已关闭</div>

        <template v-for="m in messages" :key="m.id">
          <div
            v-if="m.priority_type"
            class="tw-chat__highlight"
            :class="`tw-chat__highlight--${m.priority_type}`"
          >
            <div class="tw-chat__highlight-head">
              <TwitchIcon
                :name="m.priority_type === 'bits' ? 'bits' : 'star'"
                :size="14"
              />
              <span>高亮消息</span>
              <span v-if="m.value > 0" class="tw-chat__highlight-value">
                {{ m.value }} {{ m.priority_type === 'bits' ? 'Bits' : 'Points' }}
              </span>
            </div>
            <div class="tw-chat__line">
              <span v-if="showTimestamps" class="tw-chat__time">{{ formatTime(m.created_at) }}</span>
              <span class="tw-chat__user" :style="{ color: colorFor(m.username) }">
                {{ m.username }}
              </span><span class="tw-chat__colon">: </span><span class="tw-chat__text">{{ m.content }}</span>
            </div>
          </div>

          <div v-else class="tw-chat__line">
            <span v-if="showTimestamps" class="tw-chat__time">{{ formatTime(m.created_at) }}</span>
            <span class="tw-chat__user" :style="{ color: colorFor(m.username) }">
              {{ m.username }}
            </span><span class="tw-chat__colon">: </span><span class="tw-chat__text">{{ m.content }}</span>
          </div>
        </template>
      </div>

      <button v-if="!atBottom" class="tw-chat__more" type="button" @click="scrollToBottom">
        <TwitchIcon name="chevronDown" :size="16" />
        更多消息
      </button>
    </div>

    <div class="tw-chat__footer">
      <div v-if="active && !connected" class="tw-chat__banner">正在重新连接聊天…</div>
      <div v-else-if="!active" class="tw-chat__banner">聊天已关闭</div>

      <div v-if="priorityMode" class="tw-cheer">
        <TwitchIcon :name="priorityMode === 'bits' ? 'bits' : 'star'" :size="16" />
        <span class="tw-cheer__label">{{ priorityMode === 'bits' ? 'Bits' : 'Points' }}</span>
        <input
          v-model.number="priorityValue"
          class="tw-cheer__value"
          type="number"
          min="1"
          step="1"
          aria-label="金额"
        />
        <button class="tw-cheer__cancel" type="button" @click="priorityMode = null">取消</button>
      </div>

      <div class="tw-chat__box" :class="{ 'tw-chat__box--disabled': !active }">
        <button
          class="tw-icon-btn tw-chat__badge"
          type="button"
          title="聊天室身份"
          @click="settingsOpen = true"
        >
          <img :src="subBadgeUrl" alt="" />
        </button>
        <input
          v-model="text"
          class="tw-chat__input"
          type="text"
          maxlength="500"
          autocomplete="off"
          :placeholder="active ? '发送消息' : '聊天已关闭'"
          :disabled="!active"
          @keydown.enter.prevent="submit"
        />
        <button
          class="tw-icon-btn tw-chat__tool"
          type="button"
          title="欢呼（Bits）"
          :disabled="!active"
          @click="togglePriority('bits')"
        >
          <TwitchIcon name="cheer" :size="20" />
        </button>
        <button
          class="tw-icon-btn tw-chat__tool"
          type="button"
          title="表情符号选择器"
          :disabled="!active"
        >
          <TwitchIcon name="emote" :size="20" />
        </button>
      </div>

      <div class="tw-chat__toolbar">
        <div class="tw-chat__toolbar-left">
          <button
            class="tw-chat__points"
            :class="{ 'tw-chat__points--selected': priorityMode === 'bits' }"
            type="button"
            title="欢呼（Bits）"
            :disabled="!active"
            @click="togglePriority('bits')"
          >
            <TwitchIcon name="bits" :size="18" />
            <span class="tw-chat__points-value">0</span>
          </button>
          <button
            class="tw-chat__points"
            :class="{ 'tw-chat__points--selected': priorityMode === 'points' }"
            type="button"
            title="频道点数"
            :disabled="!active"
            @click="togglePriority('points')"
          >
            <img class="tw-chat__points-icon" :src="channelPointsUrl" alt="" />
            <span class="tw-chat__points-value">0</span>
          </button>
        </div>

        <div class="tw-chat__toolbar-right">
          <v-menu v-model="settingsOpen" :close-on-content-click="false" location="top end">
            <template #activator="{ props: menuProps }">
              <button
                v-bind="menuProps"
                class="tw-icon-btn"
                type="button"
                title="聊天设置"
              >
                <TwitchIcon name="settings" :size="20" />
              </button>
            </template>
            <v-list density="compact" class="py-1 tw-chat__settings-menu">
              <div class="tw-chat__identity">
                <v-text-field
                  v-model="identityDraft"
                  density="compact"
                  variant="outlined"
                  hide-details
                  maxlength="32"
                  label="聊天身份"
                  @keydown.enter.prevent="applyIdentity"
                  @blur="onIdentityBlur"
                >
                  <template #append-inner>
                    <v-icon
                      v-if="identityCustom"
                      size="16"
                      class="tw-chat__identity-reset"
                      title="恢复默认（vedal987）"
                      @click="resetIdentity"
                    >
                      mdi-close-circle
                    </v-icon>
                  </template>
                </v-text-field>
                <div class="tw-chat__identity-hint">
                  仅当前页面生效，刷新后恢复默认；回车应用
                </div>
              </div>
              <v-divider />
              <v-list-item @click="showTimestamps = !showTimestamps">
                <template #prepend>
                  <v-icon size="18">mdi-clock-outline</v-icon>
                </template>
                <v-list-item-title class="text-body-2">显示时间戳</v-list-item-title>
                <template #append>
                  <v-icon v-if="showTimestamps" size="18">mdi-check</v-icon>
                </template>
              </v-list-item>
            </v-list>
          </v-menu>

          <button
            class="tw-chat__send"
            type="button"
            :title="`以 ${chatUsername} 身份发送`"
            :disabled="!canSend || sending"
            @click="submit"
          >
            聊天
          </button>
        </div>
      </div>
    </div>
  </aside>
</template>

<script lang="ts" setup>
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useTheme } from 'vuetify'
import type { QueueMessage } from '@/types/stream'
import { DEFAULT_CHAT_USERNAME, useChatIdentity } from '@/composables/chatIdentity'
import { colorForUsername } from '@/utils/twitchColors'
import TwitchIcon from './TwitchIcon.vue'

const props = defineProps<{
  messages: QueueMessage[]
  /** 有进行中的场次（决定输入是否可用） */
  active: boolean
  connected: boolean
  send: (
    content: string,
    priority?: { type: 'bits' | 'points'; value: number },
  ) => Promise<boolean>
}>()

const collapsed = defineModel<boolean>('collapsed', { default: false })

const theme = useTheme()
const isDark = computed(() => theme.global.current.value.dark)

const subBadgeUrl = '/twitch/sub_badge.svg'
const channelPointsUrl = '/twitch/channel_points.png'

const listEl = ref<HTMLElement | null>(null)
const atBottom = ref(true)
const viewersOpen = ref(false)
const settingsOpen = ref(false)

/** 聊天身份编辑：仅当前页面会话生效（刷新丢失），回车应用、× 恢复默认 */
const { username: chatUsername, setUsername } = useChatIdentity()
const identityDraft = ref(chatUsername.value)
const identityCustom = computed(() => chatUsername.value !== DEFAULT_CHAT_USERNAME)

watch(settingsOpen, (open) => {
  if (open) identityDraft.value = chatUsername.value
})

function applyIdentity(): void {
  setUsername(identityDraft.value)
  identityDraft.value = chatUsername.value
}

function resetIdentity(): void {
  setUsername(null)
  identityDraft.value = chatUsername.value
}

function onIdentityBlur(): void {
  // 空输入没有意义：草稿回显为当前生效身份（不应用）
  if (!identityDraft.value.trim()) identityDraft.value = chatUsername.value
}

const TIMESTAMP_KEY = 'ns.twitch.showTimestamps'
const showTimestamps = ref(localStorage.getItem(TIMESTAMP_KEY) === '1')
watch(showTimestamps, (value) => localStorage.setItem(TIMESTAMP_KEY, value ? '1' : '0'))

const text = ref('')
const sending = ref(false)
const priorityMode = ref<'bits' | 'points' | null>(null)
const priorityValue = ref(100)

const canSend = computed(() => props.active && text.value.trim().length > 0)

/** 社区按钮的成员列表：当前队列里去重后的用户名（保持首次出现顺序）。 */
const chatters = computed(() => {
  const seen = new Set<string>()
  const names: string[] = []
  for (const m of props.messages) {
    const name = m.username.trim()
    if (!name || seen.has(name)) continue
    seen.add(name)
    names.push(name)
  }
  return names.sort((a, b) => a.localeCompare(b))
})

function colorFor(name: string): string {
  return colorForUsername(name, isDark.value)
}

function formatTime(value: string): string {
  const d = new Date(value.includes('T') ? value : value.replace(' ', 'T'))
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
}

function onScroll(): void {
  const el = listEl.value
  if (!el) return
  atBottom.value = el.scrollHeight - el.scrollTop - el.clientHeight < 24
}

function scrollToBottom(): void {
  const el = listEl.value
  if (!el) return
  el.scrollTop = el.scrollHeight
  atBottom.value = true
}

/** 用最后一条消息的 id 触发滚动（不能用 length：列表封顶 500 后长度不再变化）。 */
const lastMessageId = computed(() =>
  props.messages.length ? props.messages[props.messages.length - 1]!.id : 0,
)

watch(lastMessageId, async (id) => {
  if (id === 0) {
    atBottom.value = true
    return
  }
  if (!atBottom.value) return
  await nextTick()
  scrollToBottom()
})

onMounted(async () => {
  await nextTick()
  scrollToBottom()
})

// 展开聊天栏时回到最新消息（折叠期间列表高度为 0，atBottom 不可信）
watch(collapsed, async (value) => {
  if (value) return
  await nextTick()
  scrollToBottom()
})

function togglePriority(mode: 'bits' | 'points'): void {
  priorityMode.value = priorityMode.value === mode ? null : mode
}

async function submit(): Promise<void> {
  const content = text.value.trim()
  if (!content || !props.active || sending.value) return
  sending.value = true
  const priority = priorityMode.value
    ? {
        type: priorityMode.value,
        value: Math.max(1, Math.floor(Number(priorityValue.value) || 1)),
      }
    : undefined
  const ok = await props.send(content, priority)
  if (ok) {
    text.value = ''
    // Twitch 的 Cheer / 频道点数弹窗发送后即关闭
    if (priority) priorityMode.value = null
  }
  sending.value = false
}
</script>

<style scoped>
.tw-chat {
  display: flex;
  flex-direction: column;
  width: var(--tw-sidebar-width);
  height: 100%;
  min-height: 0;
  background: var(--tw-bg-base);
  border-left: 1px solid var(--tw-border);
  color: var(--tw-text);
  overflow: hidden;
  transition: width 0.3s ease, border-color 0.3s ease;
}

.tw-chat--collapsed {
  width: 0;
  border-left-width: 0;
}

/* ---- header：40px，1px 底边线，标题绝对居中 ---- */
.tw-chat__header {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex: 0 0 auto;
  height: var(--tw-chat-header-height);
  padding: 0 8px;
  border-bottom: 1px solid var(--tw-border);
}

.tw-chat__title {
  position: absolute;
  left: 50%;
  transform: translateX(-50%);
  font-size: 14px;
  font-weight: 600;
  white-space: nowrap;
  pointer-events: none;
}

.tw-chat__collapse,
.tw-chat__community {
  z-index: 1;
}

/* ---- 消息列表：独立滚动 ---- */
.tw-chat__list-wrap {
  position: relative;
  flex: 1 1 auto;
  min-height: 0;
}

.tw-chat__list {
  position: absolute;
  inset: 0;
  overflow-y: auto;
  overflow-anchor: none;
  padding-bottom: 10px;
  font-size: var(--tw-chat-font-size);
  line-height: var(--tw-chat-line-height);
  scrollbar-width: thin;
  scrollbar-color: var(--tw-scrollbar) transparent;
}

.tw-chat__list::-webkit-scrollbar {
  width: 8px;
}

.tw-chat__list::-webkit-scrollbar-track {
  background: transparent;
}

.tw-chat__list::-webkit-scrollbar-thumb {
  background: var(--tw-scrollbar);
  border-radius: 4px;
}

.tw-chat__empty {
  padding: 24px 16px;
  color: var(--tw-text-hint);
  text-align: center;
}

/* Twitch 消息行：4px 16px，无圆角、无 hover 底色 */
.tw-chat__line {
  padding: 4px 16px;
  word-break: break-word;
  overflow-wrap: anywhere;
}

.tw-chat__time {
  margin-right: 4px;
  color: var(--tw-text-hint);
  font-size: 12px;
}

.tw-chat__user {
  font-weight: 700;
}

.tw-chat__colon {
  font-weight: 400;
}

.tw-chat__text {
  font-weight: 400;
}

/* ---- 优先消息 = Twitch Highlighted Message ---- */
.tw-chat__highlight {
  margin: 4px 8px;
  padding: 5px 8px;
  border-radius: var(--tw-radius);
}

.tw-chat__highlight--bits {
  background: var(--tw-highlight-bits-bg);
  color: var(--tw-highlight-bits-text);
}

.tw-chat__highlight--points {
  background: var(--tw-highlight-points-bg);
  color: var(--tw-highlight-points-text);
}

.tw-chat__highlight-head {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-bottom: 2px;
  font-size: 12px;
  font-weight: 700;
  line-height: 16px;
  text-transform: uppercase;
  opacity: 0.9;
}

.tw-chat__highlight-value {
  margin-left: auto;
  font-weight: 600;
  text-transform: none;
}

.tw-chat__highlight .tw-chat__line {
  padding: 0;
}

.tw-chat__highlight .tw-chat__colon,
.tw-chat__highlight .tw-chat__text {
  color: currentColor;
}

/* ---- 回到底部 ---- */
.tw-chat__more {
  position: absolute;
  bottom: 8px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 1;
  display: flex;
  align-items: center;
  gap: 2px;
  height: 28px;
  padding: 0 10px;
  border: 0;
  border-radius: 9000px;
  background: var(--tw-purple);
  color: #fff;
  font-family: inherit;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.35);
}

/* ---- 输入区 ---- */
.tw-chat__footer {
  flex: 0 0 auto;
  padding: 0 8px 8px;
}

.tw-chat__banner {
  margin: 0 0 6px;
  padding: 4px 8px;
  border-radius: var(--tw-radius);
  background: var(--tw-bg-alt);
  color: var(--tw-text-hint);
  font-size: 12px;
  line-height: 18px;
}

.tw-chat__box {
  display: flex;
  align-items: center;
  height: 41px;
  border-radius: var(--tw-radius);
  background: var(--tw-bg-input);
  box-shadow: 0 0 0 1px var(--tw-border-input) inset;
  transition: box-shadow 0.12s ease;
}

.tw-chat__box:focus-within {
  box-shadow: 0 0 0 1px var(--tw-border-focus) inset;
}

.tw-chat__box--disabled {
  opacity: 0.6;
}

.tw-chat__badge {
  flex: 0 0 auto;
  width: 28px;
  height: 28px;
}

.tw-chat__badge img {
  display: block;
  width: 20px;
  height: 20px;
  object-fit: contain;
}

.tw-chat__input {
  flex: 1 1 auto;
  width: 100%;
  height: 100%;
  padding: 0 4px;
  border: 0;
  outline: 0;
  background: transparent;
  color: var(--tw-text);
  font-family: inherit;
  font-size: 14px;
  line-height: 21px;
}

.tw-chat__input::placeholder {
  color: var(--tw-text-hint);
}

.tw-chat__tool {
  color: var(--tw-text-hint);
}

.tw-chat__toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 8px;
}

.tw-chat__toolbar-left,
.tw-chat__toolbar-right {
  display: flex;
  align-items: center;
  gap: 4px;
}

.tw-chat__points {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 32px;
  padding: 0 4px;
  border: 0;
  border-radius: var(--tw-radius);
  background: transparent;
  color: var(--tw-text-hint);
  font-family: inherit;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
}

.tw-chat__points:hover:not(:disabled) {
  background: var(--tw-bg-hover);
}

.tw-chat__points:disabled {
  opacity: 0.5;
  cursor: default;
}

.tw-chat__points--selected {
  color: var(--tw-purple);
  background: rgba(145, 71, 255, 0.15);
  box-shadow: 0 0 0 1px var(--tw-purple) inset;
}

.tw-chat__points-icon {
  display: block;
  width: 18px;
  height: 18px;
  object-fit: contain;
}

.tw-chat__points-value {
  line-height: 1;
}

.tw-chat__send {
  flex: 0 0 auto;
  height: 32px;
  min-width: 56px;
  padding: 0 12px;
  border: 0;
  border-radius: 9000px;
  background: var(--tw-purple);
  color: #fff;
  font-family: inherit;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  transition: background-color 0.12s ease;
}

.tw-chat__send:hover:not(:disabled) {
  background: var(--tw-purple-hover);
}

.tw-chat__send:disabled {
  opacity: 0.5;
  cursor: default;
}

/* ---- 聊天设置菜单（身份编辑 + 时间戳） ---- */
.tw-chat__settings-menu {
  min-width: 260px;
}

.tw-chat__identity {
  padding: 10px 12px 8px;
}

.tw-chat__identity-hint {
  margin-top: 4px;
  color: var(--tw-text-hint);
  font-size: 11px;
  line-height: 1.4;
}

.tw-chat__identity-reset {
  cursor: pointer;
  opacity: 0.7;
}

.tw-chat__identity-reset:hover {
  opacity: 1;
}

/* ---- 优先消息输入（Bits / Points 金额） ---- */
.tw-cheer {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 6px;
  padding: 4px 8px;
  border-radius: var(--tw-radius);
  background: var(--tw-bg-alt);
  font-size: 12px;
}

.tw-cheer__label {
  font-weight: 600;
}

.tw-cheer__value {
  width: 72px;
  height: 24px;
  padding: 0 6px;
  border: 0;
  border-radius: var(--tw-radius);
  background: var(--tw-bg-input);
  color: var(--tw-text);
  font-family: inherit;
  font-size: 12px;
  outline: 0;
  box-shadow: 0 0 0 1px var(--tw-border-input) inset;
}

.tw-cheer__cancel {
  margin-left: auto;
  border: 0;
  background: transparent;
  color: var(--tw-link);
  font-family: inherit;
  font-size: 12px;
  cursor: pointer;
}
</style>
