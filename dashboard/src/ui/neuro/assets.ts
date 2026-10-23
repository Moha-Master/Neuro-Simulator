import { rootAsset } from '../engine/base'

/** 资产寻址：相对当前页面解析（见 base.ts），使 public/ 资产在直挂/代理/dev 三态下都正确。 */
export function asset (path: string): string {
  return rootAsset(path)
}

/** Neuro 窗口用字体（经 JS 加载以兼容非根 base）。
 *  - First Coffee：字幕
 *  - Noto Sans SC：中日韩字形回退（拉丁优先 First Coffee 自带字形）
 */
const FONTS: Array<[family: string, path: string, descriptor?: FontFaceDescriptors]> = [
  ['First Coffee', 'fonts/first-coffee.woff2'],
  ['Noto Sans SC', 'fonts/noto-sans-sc.woff2'],
]

export async function loadFonts (): Promise<void> {
  await Promise.allSettled(
    FONTS.map(async ([family, path, descriptor]) => {
      try {
        const face = new FontFace(family, `url(${asset(path)})`, descriptor)
        await face.load()
        document.fonts.add(face)
      } catch (error) {
        console.warn(`${family} 字体加载失败，回退系统字体`, error)
      }
    }),
  )
}
