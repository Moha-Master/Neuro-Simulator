/** 合成器页入口（dashboard 构建里的独立 HTML 入口 `/ui/stream/`）。
 *
 * OBS 浏览器源直接指向这个页面：它是**独立文档**，不带 dashboard 的 chrome、
 * 不受 Vuetify 主题与 reset 影响。dashboard 自己的页面（Stream → View）用
 * iframe 引用同一个地址。
 */
import { bootstrapStream } from './main'

const stage = document.querySelector<HTMLElement>('#stage')
if (stage) {
  void bootstrapStream(stage)
} else {
  console.error('找不到 #stage 容器，合成器无法启动')
}
