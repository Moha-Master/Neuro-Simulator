<template>
  <section class="tw-info">
    <div class="tw-info__left">
      <a
        class="tw-info__avatar-link"
        href="https://twitch.tv/vedal987"
        target="_blank"
        rel="noopener"
      >
        <div class="tw-info__avatar-wrap">
          <img class="tw-info__avatar" :src="avatarUrl" alt="vedal987" />
          <div v-if="live" class="tw-info__live-wrap">
            <div class="tw-info__live">LIVE</div>
          </div>
        </div>
      </a>
    </div>

    <div class="tw-info__right">
      <div class="tw-info__top">
        <div class="tw-info__identity">
          <h1 class="tw-info__name">vedal987</h1>
          <TwitchIcon name="verified" :size="16" class="tw-info__verified" />
        </div>

        <div class="tw-info__actions">
          <button
            class="tw-info__btn tw-info__btn--icon"
            :class="{ 'tw-info__btn--active': followed }"
            type="button"
            :title="followed ? '取消关注' : '关注'"
            @click="toggleFollow"
          >
            <TwitchIcon name="heart" :size="18" />
          </button>
          <button
            class="tw-info__btn tw-info__btn--icon"
            :class="{ 'tw-info__btn--active': notifications }"
            type="button"
            :title="notifications ? '关闭通知' : '通知'"
            @click="toggleNotifications"
          >
            <TwitchIcon name="bell" :size="18" />
          </button>
          <button class="tw-info__btn" type="button" title="购买呼币">
            <TwitchIcon name="bits" :size="18" />
            <span>购买呼币</span>
          </button>
          <button class="tw-info__btn" type="button" title="赠送一次订阅">
            <TwitchIcon name="gift" :size="18" />
            <span>赠送一次订阅</span>
            <TwitchIcon name="chevronDown" :size="18" />
          </button>
          <button class="tw-info__btn tw-info__btn--primary" type="button" title="订阅">
            <TwitchIcon name="star" :size="18" />
            <span>订阅</span>
            <TwitchIcon name="chevronDown" :size="18" />
          </button>
        </div>
      </div>

      <div v-if="!isOffline" class="tw-info__details">
        <div class="tw-info__details-left">
          <p class="tw-info__title">{{ title }}</p>
          <div class="tw-info__cat-tags">
            <a class="tw-info__category" href="#" @click.prevent>{{ category }}</a>
            <div v-if="tags.length" class="tw-info__tags">
              <a
                v-for="tag in tags"
                :key="tag"
                class="tw-info__tag"
                href="#"
                @click.prevent
              >
                {{ tag }}
              </a>
            </div>
          </div>
        </div>

        <div class="tw-info__details-right">
          <div class="tw-info__stats">
            <div class="tw-info__viewers" :title="viewerTitle">
              <TwitchIcon name="viewers" :size="18" />
              <strong>{{ viewerText }}</strong>
            </div>
            <div class="tw-info__duration">{{ duration }}</div>
          </div>
          <div class="tw-info__secondary">
            <button class="tw-info__btn tw-info__btn--icon" type="button" title="分享">
              <TwitchIcon name="share" :size="18" />
            </button>
            <button class="tw-info__btn tw-info__btn--icon" type="button" title="更多选项">
              <TwitchIcon name="more" :size="18" />
            </button>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>

<script lang="ts" setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import TwitchIcon from './TwitchIcon.vue'
import { useNotifications } from '@/composables/twitchNotifications'
import type { AudienceStatus } from '@/types/status'

const props = withDefaults(
  defineProps<{
    title: string
    category: string
    tags?: string[]
    /** offline / starting / live / paused */
    state: string
    /** 本场开始时间（DB 的 YYYY-MM-DD HH:MM:SS） */
    startedAt: string | null
    /** 频道观众数（stream 后端的 audience 分区；null = 暂不可用） */
    audience?: AudienceStatus | null
  }>(),
  { tags: () => [], audience: null },
)

const avatarUrl = '/avatar.webp'

const live = computed(() => props.state === 'live' || props.state === 'paused')
/** 旧 client 离线时隐藏整行详情（标题/分区/标签/观众数/时长/分享） */
const isOffline = computed(() => props.state === 'offline')

const FOLLOW_KEY = 'ns.twitch.followed'
const followed = ref(false)
const { notifications, toggleNotifications } = useNotifications()

onMounted(() => {
  followed.value = localStorage.getItem(FOLLOW_KEY) === '1'
})

function toggleFollow(): void {
  followed.value = !followed.value
  localStorage.setItem(FOLLOW_KEY, followed.value ? '1' : '0')
}

/** 观众数由 stream 后端缓存（TwitchTracker 平均观众）；前端不再直连外部接口。 */
const viewerText = computed(() =>
  props.audience?.viewers == null
    ? '—'
    : Number(props.audience.viewers).toLocaleString('en-US'),
)
const viewerTitle = computed(() =>
  props.audience?.viewers == null ? '观众数暂不可用' : 'TwitchTracker 平均观众（非实时）',
)

const now = ref(Date.now())
let timer: number | null = null

onMounted(() => {
  timer = window.setInterval(() => (now.value = Date.now()), 1000)
})
onUnmounted(() => {
  if (timer !== null) window.clearInterval(timer)
})

function parseStart(value: string | null): number | null {
  if (!value) return null
  const t = Date.parse(value.includes('T') ? value : value.replace(' ', 'T'))
  return Number.isFinite(t) ? t : null
}

/** 旧 client / Twitch 的时长格式：HH:MM:SS。 */
const duration = computed(() => {
  const start = parseStart(props.startedAt)
  const total = live.value && start !== null ? Math.max(0, Math.floor((now.value - start) / 1000)) : 0
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(h)}:${pad(m)}:${pad(s)}`
})
</script>

<style scoped>
.tw-info {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 10px 20px;
  background: var(--tw-bg-body);
  border-bottom: 1px solid var(--tw-border);
  color: var(--tw-text);
}

/* ---- 左列：头像 + LIVE 角标 ---- */
.tw-info__left {
  flex-shrink: 0;
}

.tw-info__avatar-link {
  display: block;
}

.tw-info__avatar-wrap {
  position: relative;
}

.tw-info__avatar {
  display: block;
  width: 64px;
  height: 64px;
  border-radius: 50%;
  border: 2px solid transparent;
  object-fit: cover;
  transition: border-color 0.2s;
}

.tw-info__avatar-link:hover .tw-info__avatar {
  border-color: var(--tw-purple);
}

.tw-info__live-wrap {
  position: absolute;
  bottom: -2px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 2;
}

.tw-info__live {
  padding: 2px 6px;
  border: 2px solid var(--tw-bg-body);
  border-radius: 4px;
  background: var(--tw-live);
  color: #fff;
  font-size: 11px;
  font-weight: 600;
  line-height: 1.2;
  text-transform: uppercase;
  white-space: nowrap;
}

/* ---- 右列 ---- */
.tw-info__right {
  display: flex;
  flex-direction: column;
  flex-grow: 1;
  gap: 4px;
  min-width: 0;
}

.tw-info__top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.tw-info__identity {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.tw-info__name {
  margin: 0;
  overflow: hidden;
  color: var(--tw-text);
  font-size: 17px;
  font-weight: 500;
  line-height: 22px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tw-info__verified {
  color: var(--tw-purple);
}

.tw-info__actions {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  gap: 6px;
}

/* Twitch 频道页按钮：30px 高、全圆角 */
.tw-info__btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  height: 30px;
  padding: 0 10px;
  border: 0;
  border-radius: 9000px;
  background: var(--tw-bg-alt);
  color: var(--tw-text);
  font-family: inherit;
  font-size: 12px;
  font-weight: 600;
  line-height: 1;
  white-space: nowrap;
  cursor: pointer;
  transition: background-color 0.12s ease, color 0.12s ease;
}

.tw-info__btn:hover {
  background: var(--tw-bg-hover);
}

.tw-info__btn--icon {
  width: 30px;
  padding: 0;
}

.tw-info__btn--active {
  color: var(--tw-purple);
}

.tw-info__btn--primary {
  background: var(--tw-purple);
  color: #fff;
}

.tw-info__btn--primary:hover {
  background: var(--tw-purple-hover);
}

/* ---- 标题 / 分区 / 标签 ---- */
.tw-info__details {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.tw-info__details-left {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}

.tw-info__title {
  margin: 0;
  overflow: hidden;
  color: var(--tw-text);
  font-size: 13px;
  font-weight: 600;
  line-height: 18px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tw-info__cat-tags {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.tw-info__category {
  flex-shrink: 0;
  color: var(--tw-purple);
  font-size: 13px;
  font-weight: 500;
  text-decoration: none;
}

.tw-info__category:hover {
  text-decoration: underline;
}

.tw-info__tags {
  display: flex;
  align-items: center;
  gap: 6px;
  overflow-x: auto;
  scrollbar-width: none;
}

.tw-info__tags::-webkit-scrollbar {
  display: none;
}

.tw-info__tag {
  display: inline-flex;
  flex-shrink: 0;
  align-items: center;
  height: 20px;
  padding: 0 8px;
  border-radius: 9000px;
  background: var(--tw-bg-alt);
  color: var(--tw-text-alt);
  font-size: 11px;
  font-weight: 600;
  line-height: 20px;
  text-decoration: none;
  transition: background-color 0.2s;
}

.tw-info__tag:hover {
  background: var(--tw-bg-hover);
}

/* ---- 右侧统计 / 次级操作 ---- */
.tw-info__details-right {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  gap: 12px;
}

.tw-info__stats {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--tw-text-hint);
  font-size: 13px;
}

.tw-info__viewers {
  display: flex;
  align-items: center;
  gap: 4px;
  color: var(--tw-text-live);
}

.tw-info__viewers strong {
  font-weight: 600;
}

.tw-info__duration {
  color: var(--tw-text);
  font-weight: 500;
  font-variant-numeric: tabular-nums;
  min-width: 4.5em;
  text-align: right;
}

.tw-info__secondary {
  display: flex;
  gap: 6px;
}

.tw-info__secondary .tw-info__btn {
  background: transparent;
}

.tw-info__secondary .tw-info__btn:hover {
  background: var(--tw-bg-hover);
}

@media (max-width: 767px) {
  .tw-info {
    padding: 10px 16px;
  }

  .tw-info__avatar {
    width: 50px;
    height: 50px;
  }

  .tw-info__details-right,
  .tw-info__actions {
    display: none;
  }
}
</style>
