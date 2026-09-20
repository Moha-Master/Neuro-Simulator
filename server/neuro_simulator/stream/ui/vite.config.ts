import { defineConfig } from "vite";

// base 用相对路径：产物资源引用（./assets/...）跟随页面所在路径解析，
// 使同一份 dist 既能被 stream 直挂（/ui/），也能被 vedal 代理（/manage/stream/ui/）。
// API（events/report）在 feed.ts 里基于 location.href 推导，天然跟随挂载前缀。
export default defineConfig({
  base: "./",
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
  server: {
    port: 5175,
    // dev 模式页面挂在 /（vite 对相对 base 的 dev 行为）：把裸路径代理到模块服务
    proxy: {
      "/events": "http://127.0.0.1:8200/ui/events",
      "/report": "http://127.0.0.1:8200/ui/report",
    },
  },
});
