/** 直播画面聊天层 + 优先消息弹窗。
 *
 * - ChatOverlay：左上角 Twitch 风格弹幕（规格移植自旧 client 的
 *   #twitch-chat-overlay：Comic Sans、随机用户名色、白字黑描边、自动滚底）。
 * - HighlightOverlay：优先消息（bits / points）额外弹一个 sc_purple / sc_pink
 *   背景的高亮框，时序同旧 client：滑入 1s → 停留 8s → 滑出 1s。
 *   多条排队逐条展示，避免快速到达时互相覆盖。
 *
 * 优先消息同时会出现在聊天层（与旧 client 行为一致：普通行 + 高亮弹窗）。
 */

import { asset } from "./assets";
import type { ChatMsg } from "./feed";

/** 旧 client 的用户名色板（饱和色 + 黑描边保证可读性）。 */
const USERNAME_COLORS = [
  "#FF0000",
  "#00FF00",
  "#0000FF",
  "#00FFFF",
  "#FF00FF",
  "#FF4500",
  "#ADFF2F",
  "#1E90FF",
  "#FFD700",
  "#8A2BE2",
  "#00CED1",
  "#FF69B4",
  "#DA70D6",
  "#BA55D3",
  "#87CEEB",
  "#32CD32",
  "#CD853F",
];

const MAX_LINES = 80;
const MAX_SEEN = 1000;
const SC_HOLD_MS = 8000;
const SC_FADE_MS = 1000;

export class ChatOverlay {
  private el: HTMLElement;
  private colors = new Map<string, string>();
  /** 已渲染的消息 id（snapshot 与实时事件可能重叠，按 id 去重）。 */
  private seen = new Set<number>();

  constructor(el: HTMLElement | null = document.getElementById("twitch-chat-overlay")) {
    if (!el) throw new Error("缺少 #twitch-chat-overlay");
    this.el = el;
    this.el.classList.remove("hidden");
  }

  clear(): void {
    this.el.replaceChildren();
    this.seen.clear();
  }

  /** snapshot 恢复：用最近 N 条整体替换（重连/刷新后聊天层不空）。 */
  replace(messages: ChatMsg[]): void {
    this.clear();
    for (const m of messages) this.append(m);
  }

  append(msg: ChatMsg): void {
    if (this.seen.has(msg.id)) return;
    this.seen.add(msg.id);
    if (this.seen.size > MAX_SEEN) this.seen.clear();

    const line = document.createElement("div");
    line.className = "chat-line__message";

    const user = document.createElement("span");
    user.className = "chat-line__username";
    user.style.color = this.colorFor(msg.username || "viewer");
    user.textContent = msg.username || "viewer";

    const text = document.createElement("span");
    text.className = "text-fragment";
    text.textContent = `: ${msg.content}`;

    line.append(user, text);
    this.el.append(line);

    // 只保留最近若干行，长时间直播不会无限堆 DOM
    while (this.el.childElementCount > MAX_LINES) {
      this.el.firstElementChild?.remove();
    }
    this.el.scrollTop = this.el.scrollHeight;
  }

  private colorFor(username: string): string {
    let color = this.colors.get(username);
    if (!color) {
      color = USERNAME_COLORS[Math.floor(Math.random() * USERNAME_COLORS.length)];
      this.colors.set(username, color);
    }
    return color;
  }
}

export class HighlightOverlay {
  private el: HTMLElement;
  private queue: ChatMsg[] = [];
  private showing = false;
  private hideTimer: number | null = null;
  private removalTimer: number | null = null;

  constructor(el: HTMLElement | null = document.getElementById("highlight-message-overlay")) {
    if (!el) throw new Error("缺少 #highlight-message-overlay");
    this.el = el;
    this.el.classList.add("hidden");
  }

  show(msg: ChatMsg): void {
    const type = msg.priority_type;
    if (type !== "bits" && type !== "points") return;
    this.queue.push(msg);
    this.next();
  }

  clear(): void {
    this.queue = [];
    this.clearTimers();
    this.showing = false;
    this.el.classList.remove("is-visible");
    this.el.classList.add("hidden");
    this.el.replaceChildren();
  }

  private next(): void {
    if (this.showing) return;
    const msg = this.queue.shift();
    if (!msg) return;
    this.showing = true;

    const bits = msg.priority_type === "bits";
    const value = Number(msg.value ?? 0);
    const label = `HIGHLIGHTED MESSAGE${
      value > 0 ? ` (${value} ${bits ? "BITS" : "POINTS"})` : ""
    }`;

    this.el.innerHTML = `
      <img src="${asset(bits ? "sc_purple.png" : "sc_pink.png")}" class="sc-background-image" alt="">
      <div class="sc-content">
        <div class="sc-user"></div>
        <div class="sc-label">${label}</div>
        <div class="sc-message"></div>
      </div>`;

    // 用户输入一律走 textContent（不拼进 innerHTML）
    (this.el.querySelector(".sc-user") as HTMLElement).textContent = msg.username || "viewer";
    const messageEl = this.el.querySelector(".sc-message") as HTMLElement;
    messageEl.textContent = msg.content;
    messageEl.style.color = bits
      ? "var(--sc-purple-bg-color)"
      : "var(--sc-pink-bg-color)";

    // 先移除 hidden 让初态（屏幕外）生效，强制 reflow 后再加 is-visible 触发 transition
    this.el.classList.remove("hidden");
    void this.el.offsetHeight;
    this.el.classList.add("is-visible");

    this.hideTimer = window.setTimeout(() => {
      this.el.classList.remove("is-visible"); // 滑出
      this.removalTimer = window.setTimeout(() => {
        this.el.classList.add("hidden");
        this.el.replaceChildren();
        this.showing = false;
        this.next();
      }, SC_FADE_MS);
    }, SC_HOLD_MS + SC_FADE_MS);
  }

  private clearTimers(): void {
    if (this.hideTimer !== null) {
      window.clearTimeout(this.hideTimer);
      this.hideTimer = null;
    }
    if (this.removalTimer !== null) {
      window.clearTimeout(this.removalTimer);
      this.removalTimer = null;
    }
  }
}
