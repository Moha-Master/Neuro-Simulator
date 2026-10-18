/** /ui 画面入口：纯渲染面。事件全部来自 stream 的 /ui/events 通道
 *  （字幕/音频 = 转发的 speech；场景 = scene_set/intro；无本地控制面板——
 *  手动场景/循环操作属于 dashboard 与 stream API）。 */

import "./styles/stream.css";

import { loadFonts } from "./core/assets";
import { runAction } from "./core/action";
import { ChatOverlay, HighlightOverlay } from "./core/chat";
import { report, StreamFeed, type ChatMsg, type FeedEvent, type Snapshot } from "./core/feed";
import { SceneManager } from "./core/scene";
import { CaptionPlayer } from "./core/speech";
import { abortIntro, playIntro, registerScenes, SCENE_ROOM, SCENE_STARTING_SOON } from "./scenes";

async function boot(): Promise<void> {
  await loadFonts();

  const mgr = new SceneManager();
  registerScenes(mgr);
  const player = new CaptionPlayer();
  const chat = new ChatOverlay();
  const highlight = new HighlightOverlay();
  const avatarEl = document.getElementById("neuro-avatar")!;
  let avatarZoomed = false;
  await mgr.set(SCENE_STARTING_SOON); // 默认待机，snapshot/state 事件会校正

  /** 结束本轮语音后复位 zoom（旧 client：zoom 持续到整段回答播完）。 */
  function resetAvatarZoom(immediate = false): void {
    if (!avatarZoomed) return;
    avatarZoomed = false;
    if (immediate) {
      avatarEl.getAnimations().forEach((a) => a.cancel());
      avatarEl.style.transform = "";
    } else {
      void runAction("avatar.zoom_out", avatarEl);
    }
  }
  player.onIdle = () => resetAvatarZoom();

  /** neuro 的画面工具（model_spin / model_zoom）→ 前端动作。
   *  其他工具（memory_add / MCP 等）与直播画面无关，静默忽略。 */
  function onToolCall(ev: FeedEvent): void {
    const name = String(ev.name ?? "");
    if (name === "model_spin") {
      void runAction("avatar.spin", avatarEl);
    } else if (name === "model_zoom") {
      // 旧 client 的 class 是幂等的：已经放大时不重复播放 zoom_in
      if (!avatarZoomed) {
        avatarZoomed = true;
        void runAction("avatar.zoom_in", avatarEl);
      }
    } else if (name.startsWith("model_")) {
      console.warn(`未处理的画面工具: ${name}`);
    }
  }

  let introPlaying = false;
  async function runIntro(elapsed = 0): Promise<void> {
    if (introPlaying) return;
    introPlaying = true;
    try {
      await playIntro(mgr, elapsed); // 视频（可续播） → 立绘 rise_in 入场
      await report("intro_done"); // 通知 stream：可以开始注入消息了
    } finally {
      introPlaying = false;
    }
  }

  async function setScene(id: string): Promise<void> {
    try {
      await mgr.set(id);
    } catch (e) {
      console.warn(`场景切换失败: ${id}`, e);
    }
  }

  const feed = new StreamFeed();

  feed.on("snapshot", (ev: FeedEvent) => {
    const snap = ev as unknown as Snapshot;
    // 聊天层：重连时用 snapshot 的最近记录整体恢复；无场次则清空
    if (snap.chat?.length) chat.replace(snap.chat);
    else chat.clear();
    if (snap.state === "offline") highlight.clear();
    if (snap.scene === "intro" && snap.state === "starting") {
      // 中途连上：按已过时长续播开场序列（视频 seek 到断点；已过片尾则只升起立绘）
      void runIntro(Number(snap.intro_elapsed ?? 0));
      return;
    }
    if (snap.state === "starting" || snap.state === "live" || snap.state === "paused") {
      abortIntro();
      // 落到 snapshot 声明的实际场景（含循环期间手动切换过的场景），未知则 room
      void setScene(snap.scene && snap.scene !== "intro" ? snap.scene : SCENE_ROOM);
      if (snap.turn?.speech?.length) {
        player.resumeFromSnapshot(snap.turn.speech);
        if (!snap.turn.active) player.endOfTurn(); // 轮间连接：恢复完即进入淡出计时
      }
    } else {
      abortIntro();
      void setScene(SCENE_STARTING_SOON); // offline：黑屏等待
    }
  });

  feed.on("intro", () => void runIntro());
  // 新场次开始：清掉上一场残留字幕（intro 由独立 intro 事件驱动）
  feed.on("session_begin", () => {
    abortIntro();
    player.clear();
    chat.clear();
    highlight.clear();
    resetAvatarZoom(true);
  });
  feed.on("state", (ev: FeedEvent) => {
    const s = String(ev.state ?? "");
    if (s === "live" || s === "paused") {
      abortIntro();
      void setScene(SCENE_ROOM);
    } else if (s === "offline") {
      abortIntro();
      player.clear();
      chat.clear();
      highlight.clear();
      resetAvatarZoom(true);
      void setScene(SCENE_STARTING_SOON);
    }
  });
  feed.on("scene_set", (ev: FeedEvent) => {
    abortIntro();
    void setScene(String(ev.id ?? ""));
  });
  feed.on("speech", (ev: FeedEvent) => {
    abortIntro();
    player.enqueue({
      index: Number(ev.index ?? 0),
      text: String(ev.text ?? ""),
      audio: String(ev.audio ?? ""),
      mime: String(ev.mime ?? ""),
      duration: Number(ev.duration ?? 0),
    });
  });
  feed.on("turn_begin", () => {
    abortIntro();
    player.beginTurn();
  });
  feed.on("turn_end", () => player.endOfTurn());
  feed.on("session_end", () => {
    abortIntro();
    player.clear();
    chat.clear();
    highlight.clear();
    resetAvatarZoom(true);
    void setScene(SCENE_STARTING_SOON);
  });
  feed.on("tool_start", onToolCall); // 工具语义在前端：目前只处理 model_* 画面动作
  feed.on("chat_message", (ev: FeedEvent) => {
    chat.append(ev as unknown as ChatMsg);
  });
  feed.on("priority_message", (ev: FeedEvent) => {
    const msg = ev as unknown as ChatMsg;
    chat.append(msg); // 与旧 client 一致：优先消息也占一条普通聊天行
    highlight.show(msg);
  });

  // 调试/E2E 句柄：控制台或自动化可直接驱动渲染管线（不影响运行逻辑）
  (window as unknown as Record<string, unknown>).__ns = {
    mgr, player, feed, chat, highlight, runIntro, setScene, onToolCall, resetAvatarZoom,
  };
}

void boot();
