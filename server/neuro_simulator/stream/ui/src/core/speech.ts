/** 字幕播放器：speech 事件队列 → 音频播放 + 逐词显示（与发声时长同步，
 *  即"伪流式"效果的时间基准来自 TTS 而不是 LLM 吐字速度）。
 *
 * 逐词推进：token 按字符长度比例分摊 duration（CJK 逐字、拉丁逐词）；
 * 有音频时以音频 ended 为句尾锚点（防止音字漂移），无音频（虚拟 TTS）纯计时。
 * 行为对齐旧 neuroCaption：文本跨句累积、静默 3s 后淡出清空、超高自动缩字号。
 */

export interface SpeechSeg {
  index: number;
  text: string;
  audio?: string;
  mime?: string;
  duration: number;
  consumed?: number; // 仅快照续播用：已发声秒数
  skip_s?: number; // 续播时音频起播位置（= consumed，秒）
}

import type { TurnSeg } from "./feed";

export type { TurnSeg };

const CAPTION_HIDE_MS = 3000;
const MIN_TOKEN_MS = 50;
const HEIGHT_THRESHOLD = 0.4;
const MIN_FONT_PX = 16;
const FONT_STEP_PX = 2;

const CJK_RE = /[\u2E80-\u9FFF\uF900-\uFAFF\uFF00-\uFFEF\u3040-\u30FF\uAC00-\uD7AF]/;

function isCJK(s: string): boolean {
  return CJK_RE.test(s);
}

/** CJK 逐字、其余按空白分词（混合文本各自成段）。 */
export function tokenize(text: string): string[] {
  const out: string[] = [];
  for (const run of text.split(/\s+/).filter(Boolean)) {
    let buf = "";
    let bufCjk = false;
    for (const ch of run) {
      const cjk = isCJK(ch);
      if (cjk) {
        if (buf) {
          out.push(buf);
          buf = "";
        }
        out.push(ch);
        bufCjk = true;
      } else {
        if (buf && bufCjk) {
          out.push(buf);
          buf = "";
        }
        buf += ch;
        bufCjk = false;
      }
    }
    if (buf) out.push(buf);
  }
  return out;
}

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

export class CaptionPlayer {
  private caption: HTMLElement;
  private stage: HTMLElement;
  private audioEl = new Audio();
  private queue: SpeechSeg[] = [];
  private busy = false;
  private gen = 0; // 换代计数器：clear()/换绑定时打断在途播放
  private hideTimer: number | null = null;
  private lastIndex = -1;
  muted = false;
  audioBlocked = false;
  private audioOk = false; // 当前句音频是否已成功起播（waitAudio 依据）

  constructor() {
    this.caption = document.getElementById("neuro-caption")!;
    this.stage = document.getElementById("stage")!;
  }

  /** 一轮新生成开始：取消隐藏计时；空闲状态下清空旧文本。 */
  beginTurn(): void {
    this.cancelHide();
    if (!this.busy && this.queue.length === 0) {
      this.clearText();
      this.lastIndex = -1;
    }
  }

  /** 一轮结束（done/cancelled/stream_aborted）：队列播完后进入静默计时。 */
  endOfTurn(): void {
    if (!this.busy && this.queue.length === 0) this.scheduleHide();
  }

  enqueue(seg: SpeechSeg): void {
    if (seg.index <= this.lastIndex) return; // 重连快照后去重
    this.cancelHide();
    this.queue.push(seg);
    if (!this.busy) void this.drain();
  }

  /** 快照断点续播：已说完的句子静态显示、正在说的显示已蹦 token 后继续、
   *  未开始的整句入队（顺序衔接实时到达的后续 speech 事件）。 */
  resumeFromSnapshot(segs: TurnSeg[]): void {
    this.cancelHide();
    let tailTaken = false;
    for (const s of segs) {
      if (s.index <= this.lastIndex) continue;
      const dur = Math.max(0.3, s.duration);
      if (s.consumed >= dur - 0.05) {
        this.appendText(s.text + " ");
        this.lastIndex = s.index;
      } else if (s.consumed > 0.05 && !tailTaken) {
        tailTaken = true;
        const tokens = tokenize(s.text);
        const totalLen = Math.max(1, s.text.replace(/\s+/g, "").length);
        const msTotal = dur * 1000;
        let accMs = 0;
        const rest: string[] = [];
        let prevCjk: boolean | null = this.caption.textContent
          ? isCJK(this.caption.textContent.slice(-1))
          : null;
        for (const tok of tokens) {
          accMs += Math.max(MIN_TOKEN_MS, (tok.length / totalLen) * msTotal);
          if (accMs <= s.consumed * 1000) {
            const cjk = isCJK(tok);
            this.appendText((prevCjk === false && !cjk ? " " : "") + tok);
            prevCjk = cjk;
          } else {
            rest.push(tok);
          }
        }
        this.lastIndex = s.index;
        const restDur = Math.max(0.2, dur - s.consumed);
        const restText = rest.join(" ");
        if (restText) {
          // 余部以略高的 index 入队：有音频则 seek 到 consumed 续播，无则纯计时轴
          this.enqueue({
            index: s.index + 0.001,
            text: restText,
            audio: s.audio,
            mime: s.mime,
            duration: restDur,
            skip_s: s.consumed,
          });
        }
      } else {
        this.enqueue({ index: s.index, text: s.text, duration: dur });
      }
    }
    if (this.caption.textContent) this.show();
  }

  /** 换绑定/强制清空：立即停播。 */
  clear(): void {
    this.gen++;
    this.queue = [];
    this.busy = false;
    this.cancelHide();
    this.audioEl.pause();
    this.audioEl.removeAttribute("src");
    this.audioEl.load();
    this.clearText();
    this.lastIndex = -1;
  }

  private async drain(): Promise<void> {
    this.busy = true;
    const gen = this.gen;
    while (this.queue.length) {
      const seg = this.queue.shift()!;
      await this.playSeg(seg, gen);
      if (gen !== this.gen) return; // 期间被 clear
    }
    this.busy = false;
    if (this.hideTimer === null) this.scheduleHide();
  }

  private async playSeg(seg: SpeechSeg, gen: number): Promise<void> {
    this.lastIndex = seg.index;
    const tokens = tokenize(seg.text);
    const totalLen = Math.max(1, seg.text.replace(/\s+/g, "").length);
    const msTotal = Math.max(0.3, seg.duration) * 1000;

    const audioStarted = seg.audio ? this.startAudio(seg) : false;
    this.show();

    let prevCjk: boolean | null = null;
    if (this.caption.textContent) prevCjk = isCJK(this.caption.textContent.slice(-1));
    for (const tok of tokens) {
      const tokLen = tok.length;
      const wait = Math.max(MIN_TOKEN_MS, (tokLen / totalLen) * msTotal);
      await sleep(wait);
      if (gen !== this.gen) return;
      const cjk = isCJK(tok);
      this.appendText((prevCjk === false && !cjk ? " " : "") + tok);
      prevCjk = cjk;
      this.adjustFontSize();
    }

    if (audioStarted) await this.waitAudio(msTotal);
    if (gen !== this.gen) return;
    this.appendText(" ");
  }

  private startAudio(seg: SpeechSeg): boolean {
    if (!seg.audio || !seg.mime) return false;
    this.audioEl.pause();
    this.audioEl.src = `data:${seg.mime};base64,${seg.audio}`;
    this.audioEl.muted = this.muted;
    this.audioOk = true;
    // 断点续播：数据 URI 元数据就绪后 seek 到已发声处
    const seek = seg.skip_s ?? 0;
    if (seek > 0.05) {
      const doSeek = () => {
        try {
          this.audioEl.currentTime = Math.min(seek, (this.audioEl.duration || seek) - 0.02);
        } catch {
          /* 未就绪：忽略，按整句播 */
        }
        this.audioEl.removeEventListener("loadedmetadata", doSeek);
      };
      this.audioEl.addEventListener("loadedmetadata", doSeek);
    }
    const p = this.audioEl.play();
    if (p) {
      p.catch(() => {
        // 自动播放策略拦截：标记后字幕走纯计时轴，面板提示用户点按解禁
        this.audioOk = false;
        this.audioBlocked = true;
      });
    }
    return true;
  }

  private async waitAudio(expectedMs: number): Promise<void> {
    const el = this.audioEl;
    if (!this.audioOk || el.ended) return; // 音频缺失或被拦截：不等
    const cap = expectedMs + 5000; // 兜底上限，防音频异常卡死队列
    await Promise.race([
      new Promise<void>((r) => {
        el.addEventListener("ended", () => r(), { once: true });
        el.addEventListener("error", () => r(), { once: true });
      }),
      sleep(cap),
    ]);
  }

  // ---------- 文本与样式 ----------

  private show(): void {
    this.caption.classList.add("show");
  }

  private appendText(s: string): void {
    this.caption.textContent += s;
  }

  private clearText(): void {
    this.caption.classList.remove("show");
    this.caption.textContent = "";
    this.caption.style.fontSize = "";
  }

  private scheduleHide(): void {
    this.cancelHide();
    this.hideTimer = window.setTimeout(() => {
      this.hideTimer = null;
      this.clearText();
    }, CAPTION_HIDE_MS);
  }

  private cancelHide(): void {
    if (this.hideTimer !== null) {
      window.clearTimeout(this.hideTimer);
      this.hideTimer = null;
    }
  }

  /** 超高自动缩字号（旧逻辑：>40% 舞台高时以 2px 步进缩到 16px 下限）。 */
  private adjustFontSize(): void {
    const max = this.stage.clientHeight * HEIGHT_THRESHOLD;
    if (!this.caption.textContent) return;
    this.caption.style.fontSize = "";
    if (this.caption.offsetHeight <= max) return;
    let fs = parseFloat(window.getComputedStyle(this.caption).fontSize);
    while (fs > MIN_FONT_PX && this.caption.offsetHeight > max) {
      fs = Math.max(MIN_FONT_PX, fs - FONT_STEP_PX);
      this.caption.style.fontSize = `${fs}px`;
    }
  }
}
