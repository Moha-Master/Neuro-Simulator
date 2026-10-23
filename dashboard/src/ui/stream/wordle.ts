/** Wordle 元素组件：把服务端推来的全量状态画成棋盘 + 屏幕键盘。
 *
 * 状态权威在 stream（`stream/games/wordle.py`），这里只是渲染器：
 * - 首次拿到状态（含中途连接时 snapshot 回放）= 瞬时画好，不补动画；
 * - 之后每次与上一份状态做 diff 推导动画：
 *   打字母 → 逐字弹入；提交 → 该行按 Neurdle 时序翻牌（240ms 错开、600ms 翻面），
 *   翻完再给键盘上色；删除 → 直接消失；结束 → 顶部横幅揭晓答案。
 * - `flash` 是一次性提示（seq 变了才算新的），用于"这不是个词"这类 toast。
 *
 * 尺寸全部用 cqi（= 本元素宽度百分比，引擎给元素加了 container-type），
 * 所以场景里改 game_wordle 的 box，整块棋盘会等比缩放。
 */

import type { ElementView } from '../engine/types'

interface Flash {
  kind: string
  text: string
  seq: number
}

interface WordleState {
  game: string
  status: 'idle' | 'playing' | 'won' | 'lost'
  word_len: number
  max_rows: number
  neuro_word?: boolean
  rows: string[][]
  statuses: (string[] | null)[]
  current_row: number
  guesses_used: number
  keyboard: Record<string, string>
  answer: string | null
  flash: Flash | null
}

const FLIP_MS = 600 // Neurdle 同款翻牌时长
const STAGGER_MS = 240 // 逐字错开
const POP_MS = 140 // 打字母的弹入
const TOAST_MS = 1600

const KBD_ROWS = ['QWERTYUIOP', 'ASDFGHJKL', 'ZXCVBNM']

export class WordleGameView implements ElementView {
  private board: HTMLElement
  private kbd: HTMLElement
  private toast: HTMLElement
  private banner: HTMLElement
  private keys = new Map<string, HTMLElement>()
  private tiles: HTMLElement[][] = []
  private prev: WordleState | null = null
  private rows = 0
  private cols = 0
  private lastFlash = 0
  private toastTimer: number | null = null

  constructor (el: HTMLElement) {
    el.classList.add('wl')
    this.board = document.createElement('div')
    this.board.className = 'wl-board'
    this.kbd = document.createElement('div')
    this.kbd.className = 'wl-kbd'
    this.toast = document.createElement('div')
    this.toast.className = 'wl-toast'
    this.banner = document.createElement('div')
    this.banner.className = 'wl-banner'
    this.buildKeyboard()
    el.append(this.board, this.kbd, this.toast, this.banner)
  }

  update (payload: unknown): void {
    const s = payload as WordleState | null
    if (!s || s.game !== 'wordle' || s.status === 'idle') {
      this.prev = null
      this.board.replaceChildren()
      this.tiles = []
      this.banner.classList.remove('show')
      this.hideToast()
      return
    }
    const reinit
      = this.prev === null || s.word_len !== this.cols || s.max_rows !== this.rows || s.rows.length < s.max_rows
    if (reinit) {
      this.buildBoard(s.max_rows, s.word_len)
    }

    const prev = reinit ? null : this.prev!
    const fresh: number[] = [] // 本次刚提交、需要翻牌的行
    if (prev) {
      for (let r = 0; r < s.max_rows; r++) {
        if (s.statuses[r] && !prev.statuses[r]) {
          fresh.push(r)
        }
      }
    }
    this.renderLetters(s, new Set(fresh))

    if (prev && fresh.length === 0) {
      this.popNewLetters(prev, s)
    }
    for (const r of fresh) {
      this.flipRow(s, r)
    }
    if (fresh.length === 0) {
      this.paintKeyboard(s.keyboard)
    }

    if (!prev || s.status !== prev.status) {
      this.paintBanner(s)
    }
    if (s.flash && s.flash.seq !== this.lastFlash) {
      this.showToast(s.flash)
    } else if (!s.flash) {
      this.hideToast()
    }
    this.prev = s
  }

  // ---------- 渲染 ----------

  /** 把所有字母与已定型的颜色写进 DOM（幂等全量渲染）；fresh 里的行留给翻牌动画上色。 */
  private renderLetters (s: WordleState, fresh: Set<number>): void {
    for (let r = 0; r < this.rows; r++) {
      const letters = s.rows[r] ?? []
      const statuses = fresh.has(r) ? null : s.statuses[r]
      for (let c = 0; c < this.cols; c++) {
        const tile = this.tiles[r]![c]!
        const inner = tile.querySelector('.wl-inner') as HTMLElement
        const back = tile.querySelector('.wl-back') as HTMLElement
        const ch = letters[c] ?? ''
        this.setTileText(tile, ch)
        back.className = 'wl-face wl-back'
        const st = statuses?.[c]
        if (st) {
          back.classList.add(`wl-${st}`)
          inner.classList.add('wl-flip')
        } else {
          inner.classList.remove('wl-flip')
        }
      }
    }
  }

  /** 刚提交的一行：逐字错开翻面（Neurdle 时序），翻完给键盘上色 */
  private flipRow (s: WordleState, r: number): void {
    const statuses = s.statuses[r]
    if (!statuses) {
      return
    }
    for (let c = 0; c < this.cols; c++) {
      const tile = this.tiles[r]![c]!
      const inner = tile.querySelector('.wl-inner') as HTMLElement
      const back = tile.querySelector('.wl-back') as HTMLElement
      const st = statuses[c]
      if (!st) {
        continue
      }
      window.setTimeout(() => {
        back.classList.add(`wl-${st}`)
        inner.classList.add('wl-flip')
      }, c * STAGGER_MS)
    }
    window.setTimeout(() => this.paintKeyboard(s.keyboard), (this.cols - 1) * STAGGER_MS + FLIP_MS)
  }

  /** 当前行新蹦出来的字母：逐个弹入 */
  private popNewLetters (prev: WordleState, s: WordleState): void {
    if (s.current_row !== prev.current_row) {
      return
    }
    const was = (prev.rows[prev.current_row] ?? []).length
    const now = s.rows[s.current_row] ?? []
    for (let c = was; c < now.length; c++) {
      const tile = this.tiles[s.current_row]![c]
      if (!tile) {
        return
      }
      tile.classList.add('wl-tile--pop')
      window.setTimeout(() => tile.classList.remove('wl-tile--pop'), POP_MS)
    }
  }

  // ---------- 构建 ----------

  private buildBoard (rows: number, cols: number): void {
    this.rows = rows
    this.cols = cols
    this.tiles = []
    this.board.replaceChildren()
    this.board.style.gridTemplateColumns = `repeat(${cols}, 1fr)`
    for (let r = 0; r < rows; r++) {
      const line: HTMLElement[] = []
      for (let c = 0; c < cols; c++) {
        const tile = document.createElement('div')
        tile.className = 'wl-tile'
        const inner = document.createElement('div')
        inner.className = 'wl-inner'
        const front = document.createElement('div')
        front.className = 'wl-face wl-front'
        const back = document.createElement('div')
        back.className = 'wl-face wl-back'
        inner.append(front, back)
        tile.append(inner)
        this.board.append(tile)
        line.push(tile)
      }
      this.tiles.push(line)
    }
  }

  private buildKeyboard (): void {
    this.kbd.replaceChildren()
    this.keys.clear()
    const addRow = (letters: string[], special?: string[]) => {
      const row = document.createElement('div')
      row.className = 'wl-kbd__row'
      if (special?.[0]) {
        row.append(this.makeKey(special[0], 'ENTER'))
      }
      for (const ch of letters) {
        row.append(this.makeKey(ch, ch))
      }
      if (special?.[1]) {
        row.append(this.makeKey(special[1], 'BACKSPACE'))
      }
      this.kbd.append(row)
    }
    addRow(KBD_ROWS[0]!.split(''))
    addRow(KBD_ROWS[1]!.split(''))
    addRow(KBD_ROWS[2]!.split(''), ['ENTER', '⌫'])
  }

  private makeKey (label: string, id: string): HTMLElement {
    const k = document.createElement('div')
    k.className = 'wl-key' + (id === 'ENTER' || id === 'BACKSPACE' ? ' wl-key--wide' : '')
    k.textContent = label
    k.dataset.key = id
    if (id.length === 1) {
      this.keys.set(id, k)
    }
    return k
  }

  private setTileText (tile: HTMLElement, ch: string): void {
    (tile.querySelector('.wl-front') as HTMLElement).textContent = ch;
    (tile.querySelector('.wl-back') as HTMLElement).textContent = ch
    tile.classList.toggle('wl-tile--filled', !!ch)
  }

  private paintKeyboard (kb: Record<string, string>): void {
    for (const [ch, el] of this.keys) {
      const st = kb[ch]
      el.className = 'wl-key' + (st ? ` wl-${st}` : '')
      if (st) {
        el.dataset.key = ch
      }
    }
  }

  private paintBanner (s: WordleState): void {
    if (s.status === 'won') {
      this.banner.textContent = `NEURO GOT IT · ${s.answer}`
      this.banner.className = 'wl-banner wl-banner--win show'
    } else if (s.status === 'lost') {
      this.banner.textContent = `THE WORD WAS ${s.answer}`
      this.banner.className = 'wl-banner wl-banner--lose show'
    } else {
      this.banner.className = 'wl-banner'
      this.banner.textContent = ''
    }
  }

  private showToast (f: Flash): void {
    this.lastFlash = f.seq
    // 胜负由顶部横幅常驻表达，不再叠一条 toast（会盖住棋盘中间的行）
    if (f.kind === 'win' || f.kind === 'lose') {
      this.hideToast()
      return
    }
    this.toast.textContent = f.text
    this.toast.className = `wl-toast wl-toast--${f.kind} show`
    if (this.toastTimer !== null) {
      window.clearTimeout(this.toastTimer)
    }
    this.toastTimer = window.setTimeout(() => this.hideToast(), TOAST_MS)
  }

  private hideToast (): void {
    this.toast.className = 'wl-toast'
    this.toast.textContent = ''
    if (this.toastTimer !== null) {
      window.clearTimeout(this.toastTimer)
      this.toastTimer = null
    }
  }
}
