import { ref, watch } from 'vue'

/**
 * Twitch 通知开关：频道信息区的铃铛与离线页的“开启通知”按钮共享同一状态，
 * localStorage 持久化（模块级单例 ref，两个组件天然同步）。
 */
const NOTIFY_KEY = 'ns.twitch.notifications'

const notifications = ref(localStorage.getItem(NOTIFY_KEY) === '1')

watch(notifications, (value) => localStorage.setItem(NOTIFY_KEY, value ? '1' : '0'))

export function useNotifications() {
  return {
    notifications,
    toggleNotifications: () => (notifications.value = !notifications.value),
  }
}
