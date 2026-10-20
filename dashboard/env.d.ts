/// <reference types="vite/client" />
/// <reference types="unplugin-vue-router/client" />
/// <reference types="vite-plugin-vue-layouts-next/client" />

import 'vue-router'

declare module 'vue-router' {
  interface RouteMeta {
    /** 内容区全宽（不套 v-container），用于 Twitch 观看页等沉浸式页面。 */
    fullBleed?: boolean
  }
}
