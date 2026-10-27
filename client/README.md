# client（观看前端）

stream 模块自托管的 Twitch 风格观看页，**独立 Vite + Vue 3 + Vuetify 工程**
（无 vue-router、无 pinia）。构建产物由 `stream` 挂载在 `/view`。

## 访问形态

- 推荐：`http://<vedal>:8100/manage/stream/view/`
  （vedal 通用代理把 `/manage/stream/*` 透传给 stream，HTML/JS/CSS 与同源
  `/manage/...` API 都天然可用）
- 直连：`http://<stream>:8200/view/` 可以渲染页面外框，但页面里的
  `/manage/...` API（状态 / 聊天 / 场景）与 iframe 合成器 `/ui/stream/`
  都不可达——`useModuleStatus` 与 `describeError` 会显示模块离线 / 网络失败的
  降级提示，属预期行为。
- OBS 浏览器源也建议用 vedal 入口（`/manage/stream/view/`），合成器
  iframe（`/ui/stream/`）由 vedal 的 dashboard dist 托管。

## 结构

```
src/
  main.ts              Vue + Vuetify（mdi / Inter 字体）
  App.vue              48px 临时空白顶栏（预留模块状态/设置入口）+ 主题开关
  WatchView.vue        旧 dashboard stream-view 的逐字平移（播放器 + 频道信息 + 聊天）
  theme.ts             模块级主题 ref（localStorage: ns.client.theme）
  api.ts               /manage fetch 封装 + describeError（原 stores/connection）
  components/          ModuleOffline + twitch/*（聊天 / 频道信息 / 离线面板 / 图标）
  composables/         useStream / useStreamChat / useModuleStatus / 聊天身份与通知
  utils/ types/        表情 token、用户名配色、stream/status/emotes 类型契约
```

## 协议（不要改字段名）

- **client ↔ 合成器**：`{ns:'stream-feed', type:'snapshot'|'event'|'hello'|'ready'}`
  —— 合成器是画面流唯一的 SSE 订阅者，client 只接收转发。
- **合成器 ↔ neuro 窗口**：`{ns:'neuro-window', type:'visibility'|'volume'|'ready'|'feed'}`
  —— `visibility` 只隐藏；`volume {value, muted}` 控制 neuro 的 speech 播放。
- **OBS 页 ↔ 合成器**：`{ns:'stream-canvas', type:'ready'|'visibility'|'volume'}`
  —— 合成器挂载后发 `ready`，父页面补发显隐与音量。

## 构建

```bash
npm install
npm run build        # type-check + build-only
npm run build-only   # 只产出 dist/（hatch_build 用这个脚本名）
npm run dev          # 5176，/manage 与 /health 代理到 127.0.0.1:8100
```
