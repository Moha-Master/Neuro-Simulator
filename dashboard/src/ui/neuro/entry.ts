/** neuro 窗口页入口（dashboard 构建里的独立 HTML 入口 `/ui/neuro/`）。
 *
 * 独立文档：背景必须透明（合成器隐藏 bg_room 时要透出下面的背景），
 * color-scheme 恒为 dark（iframe 内外色系不一致时 Chromium 会强制给 iframe
 * 一个不透明底色，媒体场景就会露白）。
 */
import { bootstrapNeuro } from "./main";

const stage = document.getElementById("stage");
if (stage) {
  void bootstrapNeuro(stage);
} else {
  console.error("找不到 #stage 容器，neuro 窗口无法启动");
}
