/**
 * 观看页主题（默认 dark）。
 *
 * client 没有 vue-router / pinia，也不需要 Vuetify 的 theme 实例：一个模块级
 * ref 就够了。`tw-dark` / `tw-light` class 仍是样式开关；顶栏的 theme-toggle
 * 是当前唯一的临时控件。
 */

import { ref, watch } from 'vue'

const THEME_KEY = 'ns.client.theme'

function readStoredDark(): boolean {
  try {
    return localStorage.getItem(THEME_KEY) !== 'light'
  } catch {
    return true
  }
}

export const isDark = ref(readStoredDark())

watch(
  isDark,
  (dark) => {
    try {
      localStorage.setItem(THEME_KEY, dark ? 'dark' : 'light')
    } catch {
      // 隐私模式等场景写不了 localStorage：主题仍在本页生效
    }
  },
  { immediate: true },
)

export function toggleTheme(): void {
  isDark.value = !isDark.value
}
