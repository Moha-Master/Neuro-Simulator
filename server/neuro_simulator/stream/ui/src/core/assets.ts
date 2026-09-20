import { relativeToPage } from "./base";

/** 资产寻址：相对当前页面解析（见 base.ts），使 public/ 资产在直挂/代理/dev 三态下都正确。 */
export function asset(path: string): string {
  return relativeToPage(path);
}

/** First Coffee 字体（经 JS 加载以兼容非根 base）。 */
export async function loadFonts(): Promise<void> {
  try {
    const face = new FontFace("First Coffee", `url(${asset("fonts/first-coffee.woff2")})`);
    await face.load();
    document.fonts.add(face);
  } catch (e) {
    console.warn("First Coffee 字体加载失败，字幕回退系统字体", e);
  }
}
