// Plugins
import AutoImport from 'unplugin-auto-import/vite'
import Components from 'unplugin-vue-components/vite'
import Fonts from 'unplugin-fonts/vite'
import Layouts from 'vite-plugin-vue-layouts-next'
import Vue from '@vitejs/plugin-vue'
import VueRouter from 'unplugin-vue-router/vite'
import { VueRouterAutoImports } from 'unplugin-vue-router'
import Vuetify, { transformAssetUrls } from 'vite-plugin-vuetify'

// Utilities
import { defineConfig, type Plugin } from 'vite'
import { fileURLToPath, URL } from 'node:url'

/**
 * 画面页（/ui/stream/、/ui/neuro/）不需要 dashboard 的 Roboto 预加载。
 *
 * unplugin-fonts 会给**每个** html 入口都注入 200+ 条 `rel=preload`（Roboto 的
 * 各语言子集共 1.7MB）——OBS 浏览器源会白下载一堆自己根本不用的字体，还刷一屏
 * "preloaded but not used" 警告。主页保留预加载（Vuetify 用 Roboto），画面页摘掉。
 */
function stripFontPreloads(): Plugin {
  return {
    name: 'ns-strip-font-preloads',
    enforce: 'post',
    transformIndexHtml: {
      order: 'post',
      handler(html, ctx) {
        if (!ctx.path || !ctx.path.includes('/ui/')) return html
        return html.replace(/<link[^>]+rel="preload"[^>]*as="font"[^>]*>/g, '')
      },
    },
  }
}

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [
    VueRouter({
      dts: 'src/typed-router.d.ts',
    }),
    Layouts(),
    AutoImport({
      imports: [
        'vue',
        VueRouterAutoImports,
        {
          pinia: ['defineStore', 'storeToRefs'],
        },
      ],
      dts: 'src/auto-imports.d.ts',
      eslintrc: {
        enabled: true,
      },
      vueTemplate: true,
    }),
    Components({
      dts: 'src/components.d.ts',
    }),
    Vue({
      template: { transformAssetUrls },
    }),
    // https://github.com/vuetifyjs/vuetify-loader/tree/master/packages/vite-plugin#readme
    Vuetify({
      autoImport: true,
      styles: {
        configFile: 'src/styles/settings.scss',
      },
    }),
    Fonts({
      fontsource: {
        families: [
          {
            name: 'Roboto',
            weights: [100, 300, 400, 500, 700, 900],
            styles: ['normal', 'italic'],
          },
        ],
      },
    }),
    stripFontPreloads(),
  ],
  optimizeDeps: {
    exclude: [
      'vuetify',
      'vue-router',
      'unplugin-vue-router/runtime',
      'unplugin-vue-router/data-loaders',
      'unplugin-vue-router/data-loaders/basic',
    ],
  },
  define: { 'process.env': {} },
  build: {
    rollupOptions: {
      // 三个入口 = 三个独立文档（同一构建、同一 origin）：
      //   index.html        dashboard 主应用（Vue SPA）
      //   ui/stream/        直播合成器（OBS 浏览器源指向它）
      //   ui/neuro/         neuro 窗口（被合成器 iframe 捕获）
      // 画面页是**独立文档**而不是 Vue 路由：OBS 要干净画布，neuro 窗口要透明
      // 背景 + 恒定 dark 色系——都容不下 dashboard 的 chrome 与 Vuetify reset。
      input: {
        main: fileURLToPath(new URL('index.html', import.meta.url)),
        stream: fileURLToPath(new URL('ui/stream/index.html', import.meta.url)),
        neuro: fileURLToPath(new URL('ui/neuro/index.html', import.meta.url)),
      },
    },
  },
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('src', import.meta.url)),
    },
    extensions: [
      '.js',
      '.json',
      '.jsx',
      '.mjs',
      '.ts',
      '.tsx',
      '.vue',
    ],
  },
  server: {
    port: 3000,
    // dev 时把后端请求转给 vedal（dashboard 只与 vedal 通信；画面页要连各模块）
    proxy: {
      '/manage': 'http://127.0.0.1:8100',
      '/health': 'http://127.0.0.1:8100',
    },
  },
})
