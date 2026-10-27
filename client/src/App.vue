<template>
  <!-- client 没有 unplugin-vue-components：组件在 main.ts 显式注册。
       v-app 同时提供 Vuetify 的布局/主题上下文与 overlay 宿主——缺组件注册时
       <v-menu> 会退化成未知元素、菜单内容原地展开进聊天栏（侧栏布局错乱）。 -->
  <v-app class="client-app" :class="isDark ? 'tw-dark' : 'tw-light'">
    <div class="client-root">
      <!-- 暂空，未来放模块状态/设置入口；当前只保留主题开关。 -->
      <header class="client-topbar">
        <div class="client-topbar__spacer" />
        <v-btn
          class="theme-toggle"
          :icon="isDark ? 'mdi-weather-night' : 'mdi-white-balance-sunny'"
          variant="text"
          size="small"
          :title="isDark ? '切换到浅色主题' : '切换到深色主题'"
          @click="toggleTheme"
        />
      </header>

      <WatchView />
    </div>
  </v-app>
</template>

<script lang="ts" setup>
import WatchView from './WatchView.vue'
import { isDark, toggleTheme } from '@/theme'
</script>

<style>
html,
body,
#app {
  height: 100%;
  margin: 0;
}

body {
  overflow: hidden;
  background: #0e0e10;
  color: #efeff1;
  font-family: Inter, Roboto, sans-serif;
}

.client-app {
  height: 100dvh;
}

/* v-app 默认用 100vh + 自己的 wrap；这里把高度链接到 client-root，
   让聊天栏的 height:100% 有确定参照（与旧 dashboard 的 fullBleed 同理）。 */
.client-app > .v-application__wrap {
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.client-root {
  display: flex;
  flex-direction: column;
  flex: 1 1 0;
  min-height: 0;
}

/* 固定 48px 的临时顶栏：内容区自己不滚动，聊天/dock 内部滚 */
.client-app.tw-dark {
  --client-bg: #18181b;
  --client-border: #2f2f35;
  --client-text: #efeff1;
}

.client-app.tw-light {
  --client-bg: #f7f7f8;
  --client-border: #dedee3;
  --client-text: #0e0e10;
}

.client-topbar {
  display: flex;
  align-items: center;
  flex: 0 0 48px;
  height: 48px;
  padding: 0 12px;
  background: var(--client-bg, #18181b);
  border-bottom: 1px solid var(--client-border, #2f2f35);
  color: var(--client-text, #efeff1);
}

.client-topbar__spacer {
  flex: 1 1 auto;
}
</style>
