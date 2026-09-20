/** 相对当前页面解析端点/资产路径，使同一份产物适配三种挂载前缀：
 *  - stream 直挂（页面 /ui/）           → /ui/events、/ui/background.webp
 *  - vedal 代理（页面 /manage/stream/ui/）→ /manage/stream/ui/...（代理转发回 stream /ui/...）
 *  - vite dev（页面 /，base=./）         → /events（proxy 映射 8200）、/background.webp（public）
 *
 * 依赖页面 URL 以斜杠结尾（StaticFiles html=True、vedal 代理路径、vite dev 均保证）。
 * 传入的 path 需为不含前导斜杠的相对名（"events"、"background.webp"、"fonts/x.woff2"）。
 */
export function relativeToPage(path: string): string {
  const clean = path.replace(/^\.?\//, "");
  try {
    return new URL(clean, window.location.href).href;
  } catch {
    return `/${clean}`;
  }
}
