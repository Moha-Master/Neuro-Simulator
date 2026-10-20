<template>
  <div class="tw-offline">
    <div ref="cardEl" class="tw-offline__card">
      <div class="tw-offline__status">
        <strong class="tw-offline__badge">离线</strong>
        <h2 class="tw-offline__title">当前未连接到直播服务，或直播未开始。</h2>
      </div>
      <div class="tw-offline__notify">
        <button
          type="button"
          class="tw-offline__notify-btn"
          @click="toggleNotifications"
        >
          <TwitchIcon name="offlineBell" :size="20" />
          <span>{{ notifications ? '停止通知' : '开启通知' }}</span>
        </button>
      </div>
    </div>

    <!-- 旧 client 此处是最新 B 站回放的 iframe；本地环境无回放源，
         保留同款 16:9 播放器位（黑底），版式与旧 client 一致 -->
    <div ref="playerEl" class="tw-offline__player" />
  </div>
</template>

<script lang="ts" setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useNotifications } from '@/composables/twitchNotifications'
import TwitchIcon from './TwitchIcon.vue'

const { notifications, toggleNotifications } = useNotifications()

const cardEl = ref<HTMLElement | null>(null)
const playerEl = ref<HTMLElement | null>(null)
let observer: ResizeObserver | null = null

/**
 * 旧 client `adjustOfflineLayout` 的等价实现：
 * 桌面端卡片是正方形、边长 = 回放播放器高度；窄屏（≤767）改堆叠、
 * 卡片宽度 = 播放器宽度。用 ResizeObserver 代替旧 client 的 resize 监听。
 */
function adjustLayout(): void {
  const card = cardEl.value
  const player = playerEl.value
  if (!card || !player) return

  if (window.innerWidth <= 767) {
    card.style.flexWrap = 'wrap'
    card.style.width = `${player.offsetWidth}px`
    card.style.height = 'auto'
    card.style.flex = '0 0 auto'
  } else {
    const height = player.offsetHeight
    if (height > 0) {
      card.style.height = `${height}px`
      card.style.width = `${height}px`
    }
    card.style.flex = '0 1 auto'
  }
}

onMounted(() => {
  adjustLayout()
  if (playerEl.value) {
    observer = new ResizeObserver(() => adjustLayout())
    observer.observe(playerEl.value)
  }
})

onBeforeUnmount(() => {
  observer?.disconnect()
  observer = null
})
</script>

<style scoped>
/* 以下数值全部来自 client/src/styles/offline.css */
.tw-offline {
  display: flex;
  flex-direction: row;
  align-items: center;
  justify-content: center;
  flex-wrap: wrap;
  flex: 1 1 0;
  min-height: 500px; /* 旧 client offline.css 原值 */
  width: 100%;
  padding: 2rem;
  gap: 2rem;
  box-sizing: border-box;
  background-image: url('/banner.jpeg');
  background-size: cover;
  background-position: center;
  background-repeat: no-repeat;
}

.tw-offline__card {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  padding: 1.5rem;
  border-radius: 0;
  background: var(--tw-bg-base);
  color: var(--tw-text);
  box-shadow: 0 4px 10px rgba(0, 0, 0, 0.1);
  box-sizing: border-box;
  flex: 0 1 270px;
}

.tw-offline__status {
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: flex-start;
  flex-grow: 1;
  gap: 0.75rem;
}

/* 复刻 .live-indicator-rect 的样式（旧 client 的“离线”徽标即黑底白字） */
.tw-offline__badge {
  padding: 2px 6px;
  border-radius: 4px;
  background: #000;
  color: #fff;
  font-size: 0.9em;
  font-weight: 600;
  line-height: 1.2;
  text-transform: uppercase;
  white-space: nowrap;
}

.tw-offline__title {
  margin: 0;
  font-size: 1.6rem;
  font-weight: 600;
  line-height: 1.25;
}

.tw-offline__notify {
  width: 100%;
  /* 旧 client 用 -10px 抵消按钮左内边距，让图标与卡片内容左边对齐 */
  margin-left: -10px;
}

.tw-offline__notify-btn {
  display: inline-flex;
  align-items: center;
  justify-content: flex-start;
  gap: 6px;
  height: 32px;
  padding: 0 10px;
  border: 0;
  border-radius: 100px;
  background: transparent;
  color: var(--tw-purple-hover);
  font-family: inherit;
  font-size: 0.9em;
  font-weight: 600;
  line-height: 1;
  cursor: pointer;
  outline: none;
  transition: background-color 0.2s, color 0.2s;
}

.tw-offline__notify-btn :deep(svg) {
  width: 1.2em;
  height: 1.2em;
  fill: currentColor;
  vertical-align: middle;
}

.tw-offline__notify-btn span {
  padding-top: 2px;
}

.tw-offline__notify-btn:hover {
  background: var(--tw-bg-hover);
  color: var(--tw-text);
}

.tw-offline__player {
  aspect-ratio: 16 / 9;
  flex: 0 1 576px;
  min-width: 300px;
  max-width: 576px;
  background: #000;
}
</style>
