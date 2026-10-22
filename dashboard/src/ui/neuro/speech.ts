/** 字幕播放器：speech 事件队列 → 音频播放 + 逐词显示（与发声时长同步，
 *  即"伪流式"效果的时间基准来自 TTS 而不是 LLM 吐字速度）。
 *
 * 逐词推进：token 按字符长度比例分摊 duration（CJK 逐字、拉丁逐词）；
 * 有音频时以音频 ended 为句尾锚点（防止音字漂移），无音频（虚拟 TTS）纯计时。
 * 行为对齐旧 neuroCaption：文本跨句累积、播完后按 neuro 配置的 caption_hide_ms
 * 淡出清空、超高自动缩字号；跨轮先清空，避免新旧字幕拼接。
 * 同一轮的工具链 step 之间插一个 caption_step_gap_ms 的停顿（旧实现由 stream
 * 延迟转发 speech 事件完成，现在画面端自己掌握节奏）。
 */

export interface SpeechSeg {
  index: number;
  text: string;
  audio?: string;
  mime?: string;
  duration: number;
  turn_id?: number; // 所属轮次（服务端 turn_id；跨轮字幕不拼在一起）
  step?: number; // 所属 LLM step（同一轮内的工具链步骤）
  consumed?: number; // 仅快照续播用：已发声秒数
  skip_s?: number; // 续播时音频起播位置（= consumed，秒）
}

import type { TurnSeg } from "./feed";

export type { TurnSeg };

const DEFAULT_HIDE_DELAY_MS = 1000;
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
  private turnId = 0; // 当前轮次（服务端 turn_id）
  private textTurnId = 0; // 当前字幕文本所属轮次：跨轮先清空，避免新旧拼接
  private textStep = 0; // 当前字幕文本所属 LLM step：同一轮工具链跨 step 清空
  /** speak 完成后字幕停留时长（neuro_sama.caption_hide_ms，snapshot/settings 下发）。 */
  hideDelayMs = DEFAULT_HIDE_DELAY_MS;
  /** 同一轮内工具链 step 之间的停顿（neuro_sama.caption_step_gap_ms）。 */
  stepGapMs = DEFAULT_HIDE_DELAY_MS;
  private turnEnded = false; // 已收到 turn_end（生成侧结束）
  private idleFired = false; // 本轮 idle 回调已触发
  muted = false;
  audioBlocked = false;
  private audioOk = false; // 当前句音频是否已成功起播（waitAudio 依据）
  /** 本轮全部语音播完（队列空且已收到 turn_end）时回调；供画面联动（zoom 复位）。 */
  onIdle?: () => void;

  constructor(caption: HTMLElement, stage: HTMLElement) {
    this.caption = caption;
    this.stage = stage;
  }

  /** 一轮新生成开始：保留上一轮字幕剩余的停留时间，等它自己的 hide timer
   *  结束（或新轮第一句真正开始）再清空，不能因为服务端先发了 turn_begin
   *  就把还在 cooldown 里的上一轮文本提前抹掉。 */
  beginTurn(turnId?: number): void {
    if (turnId !== undefined && turnId !== this.turnId) {
      this.turnId = turnId;
      this.lastIndex = -1;
    }
    this.turnEnded = false;
    this.idleFired = false;
    const hasText = this.caption.textContent.length > 0;
    if (!hasText) this.cancelHide();
    if (!this.busy && this.queue.length === 0) {
      if (hasText) {
        // 旧字幕还在淡出计时中：不要重排 timer（否则会延长停留），
        // 只有 timer 已丢失时才补一个完整 cooldown。
        if (this.hideTimer === null) this.scheduleHide();
      } else {
        this.clearText();
        this.textTurnId = this.turnId;
        this.textStep = 0;
        this.lastIndex = -1;
      }
    }
  }

  /** 一轮结束（done/cancelled/stream_aborted）：队列播完后进入静默计时。 */
  endOfTurn(turnId?: number): void {
    if (turnId !== undefined && turnId !== this.turnId) return; // 旧轮迟到的 turn_end
    this.turnEnded = true;
    if (!this.busy && this.queue.length === 0) {
      this.fireIdle();
      this.scheduleHide();
    }
  }

  enqueue(seg: SpeechSeg): void {
    const segTurn = seg.turn_id ?? this.turnId;
    if (segTurn < this.turnId) return; // 旧轮迟到事件
    if (segTurn > this.turnId) {
      // 没收到 turn_begin（重连/乱序兜底）：直接切到新轮，索引重新计
      this.turnId = segTurn;
      this.lastIndex = -1;
      this.turnEnded = false;
      this.idleFired = false;
    }
    if (seg.index <= this.lastIndex) return; // 同轮重连快照后去重
    this.cancelHide();
    this.queue.push(seg);
    if (!this.busy) void this.drain();
  }

  /** 快照断点续播：已说完的句子静态显示、正在说的显示已蹦 token 后继续、
   *  未开始的整句入队（顺序衔接实时到达的后续 speech 事件）。 */
  resumeFromSnapshot(segs: TurnSeg[], turnId = 0): void {
    if (turnId !== this.turnId) {
      this.clearText();
      this.turnId = turnId;
      this.textTurnId = turnId;
      this.textStep = 0;
      this.lastIndex = -1;
    }
    this.cancelHide();
    let tailTaken = false;
    for (const s of segs) {
      if (s.index <= this.lastIndex) continue;
      const step = s.step ?? 0;
      if (step !== this.textStep) {
        this.clearText();
        this.textStep = step;
        this.textTurnId = turnId;
      }
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
            step,
            index: s.index + 0.001,
            text: restText,
            audio: s.audio,
            mime: s.mime,
            duration: restDur,
            skip_s: s.consumed,
            turn_id: turnId,
          });
        }
      } else {
        this.enqueue({ step, index: s.index, text: s.text, duration: dur, turn_id: turnId });
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
    this.turnId = 0;
    this.textTurnId = 0;
    this.textStep = 0;
    this.turnEnded = false;
    this.idleFired = false;
  }

  /** 更新“播完后字幕停留”时长（配置热重载时可由 settings 事件调用）。 */
  setHideDelay(ms: number): void {
    if (!Number.isFinite(ms)) return;
    this.hideDelayMs = Math.max(0, ms);
  }

  /** 更新同一轮内工具链 step 之间的停顿。 */
  setStepGap(ms: number): void {
    if (!Number.isFinite(ms)) return;
    this.stepGapMs = Math.max(0, ms);
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
    if (this.turnEnded) this.fireIdle();
    if (this.hideTimer === null) this.scheduleHide();
  }

  /** 本轮语音播完只通知一次（turn_end 可能先于/晚于队列排空）。 */
  private fireIdle(): void {
    if (this.idleFired) return;
    this.idleFired = true;
    this.onIdle?.();
  }

  private async playSeg(seg: SpeechSeg, gen: number): Promise<void> {
    const segTurn = seg.turn_id ?? this.turnId;
    const segStep = seg.step ?? 0;
    const stepChanged = segTurn === this.textTurnId && segStep !== this.textStep;
    if (segTurn !== this.textTurnId || stepChanged) {
      // 跨轮/跨 step 边界：旧字幕可能还没淡出，新消息第一句开始前先清空，避免拼接
      this.clearText();
      this.textTurnId = segTurn;
      this.textStep = segStep;
    }
    if (stepChanged && this.stepGapMs > 0) {
      // 同一轮内上一步的语音播完后停一下，再开始下一步的字幕/音频
      await sleep(this.stepGapMs);
      if (gen !== this.gen) return;
    }
    // 旧轮残留队列可能还在播：不能让它覆盖新一轮的去重水位线
    if (segTurn === this.turnId) this.lastIndex = seg.index;
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
    }, this.hideDelayMs);
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
