import { relativeToPage } from "./base";

/** 资产寻址：相对当前页面解析（见 base.ts），使 public/ 资产在直挂/代理/dev 三态下都正确。 */
export function asset(path: string): string {
  return relativeToPage(path);
}

/** 画面用字体（经 JS 加载以兼容非根 base）。
 *  - First Coffee：字幕 / 待机标题
 *  - Noto Sans SC：聊天覆盖层的中文回退（拉丁优先用系统 Segoe UI）
 *  - Causten：优先消息弹窗（旧 client 规格）
 */
const FONTS: Array<[family: string, path: string]> = [
  ["First Coffee", "fonts/first-coffee.woff2"],
  ["Causten", "fonts/causten.woff2"],
  ["Noto Sans SC", "fonts/noto-sans-sc.woff2"],
];

export async function loadFonts(): Promise<void> {
  await Promise.allSettled(
    FONTS.map(async ([family, path]) => {
      try {
        const face = new FontFace(family, `url(${asset(path)})`);
        await face.load();
        document.fonts.add(face);
      } catch (e) {
        console.warn(`${family} 字体加载失败，回退系统字体`, e);
      }
    }),
  );
}
