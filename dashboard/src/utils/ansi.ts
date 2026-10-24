/**
 * ANSI SGR → 可安全渲染的行内片段。
 *
 * 为什么不用 `v-html`：日志里可能混有外部文本（聊天内容、模型输出、文件名），
 * 拼进 HTML 就是 XSS 面。这里只产出 `{ text, cls?, color? }`，由 Vue 的文本插值
 * 转义；颜色走内联 style（256 色无法预先枚举成 class）。
 *
 * 未知/残缺的转义序列一律丢弃——宁可少一段颜色，也不能让 `\x1b` 或控制字符
 * 漏到页面上变成乱码。解析器同时维护跨行的样式状态：终端里颜色一旦设置就会
 * 延续到后续行，逐行独立解析会得到错误的着色。
 */

export interface AnsiSpan {
  text: string
  /** 仅用于"没有颜色只有字重"的样式；颜色统一走 `color` */
  cls?: 'ansi-bold'
  /** CSS color，如 `#cd3131` / `rgb(1,2,3)` */
  color?: string
}

export interface AnsiStyle {
  bold: boolean
  fg: string | null
}

export interface AnsiLine {
  spans: AnsiSpan[]
  /** 去掉转义后的纯文本，供级别判定与关键字搜索使用 */
  text: string
}

/** xterm 前 16 色的固定取值（不跟随页面主题：日志区始终是深底终端配色）。 */
const ANSI_16 = [
  '#000000',
  '#cd3131',
  '#0dbc79',
  '#e5e510',
  '#2472c8',
  '#bc3fbc',
  '#11a8cd',
  '#e5e5e5',
  '#666666',
  '#f14c4c',
  '#23d18b',
  '#f5f543',
  '#3b8eea',
  '#d670d6',
  '#29b8db',
  '#ffffff',
] as const

/** xterm 256 色的 6×6×6 色立方与灰阶。 */
const CUBE_STEPS = [0, 95, 135, 175, 215, 255] as const

export function createAnsiStyle(): AnsiStyle {
  return { bold: false, fg: null }
}

function xtermColor(index: number): string | null {
  if (!Number.isInteger(index) || index < 0 || index > 255) return null
  if (index < 16) return ANSI_16[index] ?? null
  if (index < 232) {
    const n = index - 16
    const r = CUBE_STEPS[Math.floor(n / 36)] ?? 0
    const g = CUBE_STEPS[Math.floor(n / 6) % 6] ?? 0
    const b = CUBE_STEPS[n % 6] ?? 0
    return `rgb(${r},${g},${b})`
  }
  const level = 8 + (index - 232) * 10
  return `rgb(${level},${level},${level})`
}

function isByte(value: number | undefined): value is number {
  return value !== undefined && Number.isInteger(value) && value >= 0 && value <= 255
}

/** 应用一段 SGR 参数（`\x1b[` 与 `m` 之间的部分）。不认识的码直接忽略。 */
function applySgr(body: string, style: AnsiStyle): void {
  const codes =
    body === ''
      ? [0]
      : body.split(';').map((part) => {
          const value = Number(part === '' ? 0 : part)
          return Number.isFinite(value) ? value : -1
        })

  for (let i = 0; i < codes.length; i++) {
    const code = codes[i] ?? -1
    if (code === 0) {
      style.bold = false
      style.fg = null
    } else if (code === 1) {
      style.bold = true
    } else if (code === 22) {
      style.bold = false
    } else if (code === 39) {
      style.fg = null
    } else if (code >= 30 && code <= 37) {
      style.fg = ANSI_16[code - 30] ?? null
    } else if (code >= 90 && code <= 97) {
      style.fg = ANSI_16[code - 90 + 8] ?? null
    } else if (code === 38) {
      const mode = codes[i + 1]
      if (mode === 5) {
        const color = xtermColor(codes[i + 2] ?? -1)
        if (color !== null) {
          style.fg = color
          i += 2
        } else {
          i += 1
        }
      } else if (mode === 2) {
        // 真彩色不在后端契约里，但实际输出常见；参数合法时顺手支持，
        // 否则整组跳过，避免把 R/G/B 当成独立的 SGR 码误判。
        const r = codes[i + 2]
        const g = codes[i + 3]
        const b = codes[i + 4]
        if (isByte(r) && isByte(g) && isByte(b)) {
          style.fg = `rgb(${r},${g},${b})`
          i += 4
        } else {
          i += 1
        }
      }
    }
  }
}

/** 跳过 OSC / DCS 等"字符串型"转义：直到 BEL 或 ST（`ESC \`）。 */
function skipStringSequence(raw: string, start: number): number {
  let i = start + 2
  while (i < raw.length) {
    const code = raw.charCodeAt(i)
    if (code === 0x07) return i + 1
    if (code === 0x1b && raw.charCodeAt(i + 1) === 0x5c) return i + 2
    i++
  }
  return raw.length
}

/**
 * 解析一行（不含换行符）。返回值里的 `style` 是行末样式，调用方应把它传给
 * 下一行，才能保持跨行颜色。
 */
export function parseAnsiLine(
  raw: string,
  initial?: AnsiStyle,
): { line: AnsiLine; style: AnsiStyle } {
  const style: AnsiStyle = initial ? { ...initial } : createAnsiStyle()
  const spans: AnsiSpan[] = []
  let buffer = ''

  const flush = () => {
    if (!buffer) return
    const span: AnsiSpan = { text: buffer }
    if (style.bold) span.cls = 'ansi-bold'
    if (style.fg) span.color = style.fg
    spans.push(span)
    buffer = ''
  }

  let i = 0
  while (i < raw.length) {
    const code = raw.charCodeAt(i)

    if (code === 0x1b) {
      flush()
      const next = raw.charCodeAt(i + 1)
      if (Number.isNaN(next)) {
        // 行尾孤立 ESC：丢弃
        i += 1
      } else if (next === 0x5b) {
        // CSI：参数 + 中间字节 + 终止字节（0x40–0x7e）
        let j = i + 2
        while (j < raw.length) {
          const c = raw.charCodeAt(j)
          if (c >= 0x40 && c <= 0x7e) break
          j++
        }
        if (j >= raw.length) {
          // 没有终止字节：把剩余部分当作残缺转义，整段丢弃
          i = raw.length
        } else {
          if (raw.charCodeAt(j) === 0x6d) applySgr(raw.slice(i + 2, j), style)
          i = j + 1
        }
      } else if (next === 0x5d || next === 0x50 || next === 0x58 || next === 0x5e || next === 0x5f) {
        // OSC / DCS / SOS / PM / APC
        i = skipStringSequence(raw, i)
      } else if (next === 0x28 || next === 0x29 || next === 0x23) {
        // 字符集选择等三字节序列：ESC ( B / ESC ) 0 / ESC # 8
        i += 3
      } else {
        i += 2
      }
      continue
    }

    if (code === 0x0d) {
      // 进度条/覆盖写只保留最终可见文本；裸 CR 不显示。
      i += 1
      continue
    }
    if (code < 0x20 && code !== 0x09) {
      // 除 TAB 外的 C0 控制字符（BEL、NUL……）不落到页面上
      i += 1
      continue
    }

    buffer += raw.charAt(i)
    i += 1
  }

  flush()

  return {
    line: {
      spans,
      text: spans.map((span) => span.text).join(''),
    },
    style,
  }
}
