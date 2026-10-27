/**
 * client/main.ts
 *
 * 独立观看前端的启动入口：Vue + Vuetify（无 router / pinia），
 * 字体与 dashboard 同源（mdi 图标 + Inter）。
 */

import { createApp } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'

import '@mdi/font/css/materialdesignicons.css'
import 'vuetify/styles'
import '@fontsource/inter/latin-400.css'
import '@fontsource/inter/latin-500.css'
import '@fontsource/inter/latin-600.css'
import '@fontsource/inter/latin-700.css'
import './styles/twitch.css'

import App from './App.vue'

const vuetify = createVuetify({
  // client 没有 dashboard 的 unplugin-vue-components：必须显式注册组件，
  // 否则 <v-app>/<v-menu> 会退化成未知自定义元素（菜单内容原地展开 → 聊天栏错乱）。
  components,
  directives,
  theme: {
    // 页面自身用 tw-dark/tw-light 控制颜色；这里只保证 Vuetify 组件不闪白底。
    defaultTheme: 'dark',
  },
})

createApp(App).use(vuetify).mount('#app')
