import vuetify from 'eslint-config-vuetify'

// eslint-config-vuetify 的默认导出是 Promise（ESLint 支持异步配置），
// 所以这里整体包一层：先一条全局 ignores，再拼上 vuetify 的配置。
export default (async () => [
  {
    // 画面渲染器（合成器 / neuro 窗口）不是 Vue 应用代码：它是搬进来的独立
    // 前端（vanilla TS），有自己的代码风格，也从来没经过 dashboard 的 eslint
    // （之前住在各自的 Vite 工程里）。这里排除，免得它的风格差异刷屏。
    ignores: ['src/ui/**'],
  },
  ...(await vuetify()),
])()
