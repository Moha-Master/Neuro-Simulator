import { fileURLToPath, URL } from 'node:url'

import Vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

/**
 * 观看前端（client）是 stream 模块自托管的独立工程。
 *
 * - `base: './'`：经 vedal 挂在 `/manage/stream/view/` 这种深层路径下，
 *   资源必须相对加载（否则会去 vedal 根目录找 assets）；
 * - 页面与 iframe 合成器（`/ui/stream/`）同源于 vedal，postMessage 协议不变；
 * - dev server 5176，`/manage` 与 `/health` 代理到 vedal（8100）。
 */
export default defineConfig({
  base: './',
  plugins: [Vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('src', import.meta.url)),
    },
    extensions: ['.js', '.json', '.jsx', '.mjs', '.ts', '.tsx', '.vue'],
  },
  server: {
    port: 5176,
    proxy: {
      '/manage': 'http://127.0.0.1:8100',
      '/health': 'http://127.0.0.1:8100',
    },
  },
})
