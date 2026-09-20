/** /ui 画面入口：纯渲染面。事件全部来自 stream 的 /ui/events 通道
 *  （字幕/音频 = 转发的 speech；场景 = scene_set/intro；无本地控制面板——
 *  手动场景/循环操作属于 dashboard 与 stream API）。 */

import "./styles/stream.css";

import { loadFonts } from "./core/assets";
import { report, StreamFeed, type FeedEvent, type Snapshot } from "./core/feed";
import { SceneManager } from "./core/scene";
import { CaptionPlayer } from "./core/speech";
import { abortIntro, playIntro, registerScenes, SCENE_ROOM, SCENE_STARTING_SOON } from "./scenes";

async function boot(): Promise<void> {
  await loadFonts();

  const mgr = new SceneManager();
  registerScenes(mgr);
  const player = new CaptionPlayer();
  await mgr.set(SCENE_STARTING_SOON); // 默认待机，snapshot/state 事件会校正

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
  });
  feed.on("state", (ev: FeedEvent) => {
    const s = String(ev.state ?? "");
    if (s === "live" || s === "paused") {
      abortIntro();
      void setScene(SCENE_ROOM);
    } else if (s === "offline") {
      abortIntro();
      player.clear();
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
    void setScene(SCENE_STARTING_SOON);
  });

  // 调试/E2E 句柄：控制台或自动化可直接驱动渲染管线（不影响运行逻辑）
  (window as unknown as Record<string, unknown>).__ns = { mgr, player, feed, runIntro, setScene };
}

void boot();
