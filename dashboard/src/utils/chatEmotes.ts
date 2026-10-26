/** 聊天表情 token 化（框架无关，供合成器与 dashboard 侧栏共用）。
 *
 * 规则与 Twitch 一致：按空白切词、**大小写敏感的全词匹配**（`LUL.` 带标点不命中）。
 * 图片 URL 由调用方按自己的模块基址拼好塞进 index；本文件不碰 window/location。
 */

export type EmoteToken =
  | { type: 'text', text: string }
  | { type: 'emote', name: string, url: string }

/** 相对图片路径（调用方在前面拼模块基址，如 `/manage/stream/`）。 */
export const EMOTE_BASE_FALLBACK = 'emotes/images/'

/** 表情名 → 同源相对路径（名字可能含 `:`，编码后放进路径段）。 */
export function emoteImagePath (name: string): string {
  return `${EMOTE_BASE_FALLBACK}${encodeURIComponent(name)}`
}

export function tokenize (
  text: string,
  index: ReadonlyMap<string, string>,
): EmoteToken[] {
  // 没有表情索引时整体当文本，保留原始空白（不 split，避免空串/空格被改写）
  if (index.size === 0) {
    return [{ type: 'text', text }]
  }

  // 保留空白段：直接 split(/\s+/) 会把词粘在一起，渲染出来没有间隔
  const parts = text.split(/(\s+)/)
  const tokens: EmoteToken[] = []
  for (const part of parts) {
    if (!part) {
      continue
    }
    const isSpace = /^\s+$/.test(part)
    const url = isSpace ? undefined : index.get(part)
    if (url !== undefined) {
      tokens.push({ type: 'emote', name: part, url })
      continue
    }
    const previous = tokens[tokens.length - 1]
    if (previous?.type === 'text') {
      previous.text += part
    } else {
      tokens.push({ type: 'text', text: part })
    }
  }
  return tokens
}
