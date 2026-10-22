/** 直播画面聊天层 + 优先消息弹窗。
 *
 * - ChatOverlay：左上角 Twitch 风格弹幕（jChat/ChatIS 风格：Segoe UI、
 *   800 字重、白字黑描边、Twitch 官方用户名色板、自动滚底）。
 * - HighlightOverlay：优先消息（bits / points）额外弹一个 sc_purple / sc_pink
 *   背景的高亮框，时序同旧 client：滑入 1s → 停留 8s → 滑出 1s。
 *   多条排队逐条展示，避免快速到达时互相覆盖。
 *
 * 优先消息同时会出现在聊天层（与旧 client 行为一致：普通行 + 高亮弹窗）。
 */

import { asset } from "./assets";
import type { ChatMsg } from "./feed";

/** Twitch 官方默认用户名色板（jChat/ChatIS 直接使用 IRC color tag；
 *  这里没有真实 color tag，用用户名哈希稳定映射，刷新/重连不换色）。 */
const USERNAME_COLORS = [
  "#FF0000",
  "#0000FF",
  "#00FF00",
  "#B22222",
  "#FF7F50",
  "#9ACD32",
  "#FF4500",
  "#2E8B57",
  "#DAA520",
  "#D2691E",
  "#5F9EA0",
  "#1E90FF",
  "#FF69B4",
  "#8A2BE2",
  "#00FF7F",
];

const MAX_LINES = 80;
const MAX_SEEN = 1000;
const SC_HOLD_MS = 8000;
const SC_FADE_MS = 1000;

/** 稳定映射用户名 → Twitch 色板（小写后做 FNV-1a，Twitch 用户名不区分大小写）。 */
function colorFor(username: string): string {
  const key = username.toLowerCase();
  let hash = 0x811c9dc5;
  for (let i = 0; i < key.length; i++) {
    hash ^= key.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193);
  }
  // 取模必然落在数组内；`??` 只是为了满足 noUncheckedIndexedAccess
  return USERNAME_COLORS[(hash >>> 0) % USERNAME_COLORS.length] ?? "#FFFFFF";
}

export class ChatOverlay {
  private el: HTMLElement;
  /** 内层承载字号：元素自身设 container-type，字号写在子节点上才能按本层宽度缩放 */
  private lines: HTMLElement;
  /** 已渲染的消息 id（snapshot 与实时事件可能重叠，按 id 去重）。 */
  private seen = new Set<number>();

  constructor(el: HTMLElement) {
    this.el = el;
    this.lines = document.createElement("div");
    this.lines.className = "chat-lines";
    el.append(this.lines);
  }

  clear(): void {
    this.lines.replaceChildren();
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

    const userInfo = document.createElement("span");
    userInfo.className = "chat-line__user-info";

    const user = document.createElement("span");
    user.className = "chat-line__username";
    user.style.color = colorFor(msg.username || "viewer");
    user.textContent = msg.username || "viewer";

    const colon = document.createElement("span");
    colon.className = "chat-line__colon";
    colon.textContent = ":";

    const text = document.createElement("span");
    text.className = "text-fragment";
    text.textContent = msg.content;

    userInfo.append(user, colon);
    line.append(userInfo, text);
    this.lines.append(line);

    // 只保留最近若干行，长时间直播不会无限堆 DOM
    while (this.lines.childElementCount > MAX_LINES) {
      this.lines.firstElementChild?.remove();
    }
    this.el.scrollTop = this.el.scrollHeight;
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
