import { rootAsset } from "../engine/base";

/** 资产寻址：相对当前页面解析（见 base.ts），使 public/ 资产在直挂/代理/dev 三态下都正确。 */
export function asset(path: string): string {
  return rootAsset(path);
}

/** 画面用字体（经 JS 加载以兼容非根 base）。
 *  - Noto Sans SC：聊天覆盖层的中文回退（拉丁优先用系统 Segoe UI）
 *  - Causten：优先消息弹窗（旧 client 规格）
 *  - Inter：游戏元素（Wordle 棋盘/键盘，Neurdle 同款字面）
 *
 * 字幕的 First Coffee 已随字幕层移到 neuro 窗口，stream 合成器不再需要。
 */
const FONTS: Array<[family: string, path: string, descriptor?: FontFaceDescriptors]> = [
  ["Causten", "fonts/causten.woff2"],
  ["Noto Sans SC", "fonts/noto-sans-sc.woff2"],
  ["Inter", "fonts/inter-400.woff2", { weight: "400" }],
  ["Inter", "fonts/inter-700.woff2", { weight: "700" }],
];

export async function loadFonts(): Promise<void> {
  await Promise.allSettled(
    FONTS.map(async ([family, path, descriptor]) => {
      try {
        const face = new FontFace(family, `url(${asset(path)})`, descriptor);
        await face.load();
        document.fonts.add(face);
      } catch (e) {
        console.warn(`${family} 字体加载失败，回退系统字体`, e);
      }
    }),
  );
}
