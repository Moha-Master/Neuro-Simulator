/** /ui 画面入口：纯渲染面。
 *
 * 舞台里没有任何写死的层：场景与元素来自 scenes.json（见 scenes/schema.ts），
 * 由 ElementEngine 按当前场景挂载/隐藏。事件全部来自 stream 的 /ui/events 通道
 * （字幕/音频 = 转发的 speech；场景 = scene_set；游戏等元素状态 = element_state；
 *  无本地控制面板——手动场景/循环操作属于 dashboard 与 stream API）。
 */

import "./styles/stream.css";

import { loadFonts } from "./core/assets";
import { runAction } from "./core/action";
import { ChatOverlay, HighlightOverlay } from "./core/chat";
import { ElementEngine } from "./core/elements";
import { StreamFeed, type ChatMsg, type FeedEvent, type Snapshot } from "./core/feed";
import { CaptionPlayer } from "./core/speech";
import {
  AVATAR_ELEMENT,
  SCENE_INTRO,
  SCENE_ROOM,
  SCENE_STARTING_SOON,
  fetchScenes,
} from "./scenes";

async function boot(): Promise<void> {
  await loadFonts();

  const stage = document.getElementById("stage");
  if (!stage) return;
  let engine: ElementEngine;
  try {
    engine = new ElementEngine(stage, await fetchScenes());
  } catch (e) {
    console.error("场景清单（scenes.json）加载失败，画面无法渲染", e);
    return;
  }
  await engine.setScene(SCENE_STARTING_SOON, { instant: true });

  // 实时管线组件从元素引擎取（元素 id 见 scenes.json）
  const player = engine.view<CaptionPlayer>("caption");
  const chat = engine.view<ChatOverlay>("chat");
  const highlight = engine.view<HighlightOverlay>("highlight");
  for (const [id, view] of [["caption", player], ["chat", chat], ["highlight", highlight]] as const) {
    if (!view) console.warn(`当前场景缺少 ${id} 元素，相关事件将被忽略`);
  }

  let avatarZoomed = false;

  /** 结束本轮语音后复位 zoom（旧 client：zoom 持续到整段回答播完）。 */
  function resetAvatarZoom(immediate = false): void {
    if (!avatarZoomed) return;
    avatarZoomed = false;
    const el = engine.element(AVATAR_ELEMENT);
    if (!el) return;
    if (immediate) {
      el.getAnimations().forEach((a) => a.cancel());
      el.style.transform = "";
    } else {
      void runAction("avatar.zoom_out", el);
    }
  }
  if (player) player.onIdle = () => resetAvatarZoom();

  /** neuro 的画面工具（model_spin / model_zoom）→ 立绘动作。
   *  其他工具（memory_add / 游戏 / MCP 等）与画面动作无关，静默忽略。 */
  function onToolCall(ev: FeedEvent): void {
    const name = String(ev.name ?? "");
    const el = engine.element(AVATAR_ELEMENT);
    if (!el) return;
    if (name === "model_spin") {
      void runAction("avatar.spin", el);
    } else if (name === "model_zoom") {
      // 旧 client 的 class 是幂等的：已经放大时不重复播放 zoom_in
      if (!avatarZoomed) {
        avatarZoomed = true;
        void runAction("avatar.zoom_in", el);
      }
    } else if (name.startsWith("model_")) {
      console.warn(`未处理的画面工具: ${name}`);
    }
  }

  async function setScene(
    id: string,
    opts: { instant?: boolean; elapsed?: number } = {},
  ): Promise<void> {
    try {
      await engine.setScene(id, opts);
    } catch (e) {
      console.warn(`场景切换失败: ${id}`, e);
    }
  }

  /** 元素状态（游戏棋盘等）：先写进引擎缓存，元素挂载时会自动回放。 */
  function applyElements(map?: Record<string, unknown>): void {
    if (!map) return;
    for (const [id, payload] of Object.entries(map)) engine.update(id, payload);
  }

  const feed = new StreamFeed();

  feed.on("snapshot", (ev: FeedEvent) => {
    const snap = ev as unknown as Snapshot;
    // 画面端运行参数（字幕停留/轮间冷却；配置热重载时另有 settings 事件）
    player?.setHideDelay(Number(snap.settings?.speech_cooldown_ms ?? 1000));
    // 聊天层：重连时用 snapshot 的最近记录整体恢复；无场次则清空
    if (snap.chat?.length) chat?.replace(snap.chat);
    else chat?.clear();
    applyElements(snap.elements);
    if (snap.state === "offline") {
      highlight?.clear();
      void setScene(SCENE_STARTING_SOON, { instant: true }); // 黑屏等待
      return;
    }
    if (snap.state === "starting" && snap.scene === SCENE_INTRO) {
      // 中途连上：按场景时间线已过秒数续播（视频 seek、未开始的立绘动作顺延）
      void setScene(SCENE_INTRO, {
        instant: true,
        elapsed: Number(snap.intro_elapsed ?? 0),
      });
      return;
    }
    if (snap.state === "starting" || snap.state === "live" || snap.state === "paused") {
      // 落到 snapshot 声明的实际场景（含循环期间手动切换过的场景），未知则 room。
      // 重连用 instant：不重复播淡入，画面不会因为 SSE 断开重连而闪一下。
      void setScene(snap.scene && snap.scene !== SCENE_INTRO ? snap.scene : SCENE_ROOM, {
        instant: true,
      });
      if (snap.turn?.speech?.length && player) {
        player.resumeFromSnapshot(snap.turn.speech, Number(snap.turn.id ?? 0));
        if (!snap.turn.active) player.endOfTurn(Number(snap.turn.id ?? 0)); // 轮间连接：恢复完即进入淡出计时
      }
    } else {
      void setScene(SCENE_STARTING_SOON, { instant: true });
    }
  });
  // 新场次开始：清掉上一场残留字幕/聊天（场景本身由 start 广播的 scene_set 驱动）
  feed.on("session_begin", () => {
    player?.clear();
    chat?.clear();
    highlight?.clear();
    resetAvatarZoom(true);
  });
  feed.on("state", (ev: FeedEvent) => {
    const s = String(ev.state ?? "");
    if (s === "live" || s === "paused") {
      // 后端是唯一时钟：进入 live 时若还停在待机/开场，直接落 room。
      // 已经在某个直播场景里（含游戏）就别把它拽回 room——暂停/继续不该换画面。
      // 即使本画面的开场视频卡住，也会被这一帧纠正；后端从不等待它。
      if (
        !engine.current ||
        engine.current.id === SCENE_STARTING_SOON ||
        engine.current.id === SCENE_INTRO
      ) {
        void setScene(SCENE_ROOM);
      }
    } else if (s === "offline") {
      player?.clear();
      chat?.clear();
      highlight?.clear();
      resetAvatarZoom(true);
      void setScene(SCENE_STARTING_SOON);
    }
  });
  feed.on("scene_set", (ev: FeedEvent) => {
    // 场景脚本的取消由 ElementEngine.setScene 负责：旧 intro 的 timer/动画不会
    // 在 room 上继续跑，因此这里不需要额外的 abort 协议。
    void setScene(String(ev.id ?? ""));
  });
  feed.on("element_state", (ev: FeedEvent) => {
    engine.update(String(ev.id ?? ""), ev.payload);
  });
  feed.on("settings", (ev: FeedEvent) => {
    player?.setHideDelay(Number(ev.speech_cooldown_ms ?? 1000));
  });
  feed.on("speech", (ev: FeedEvent) => {
    player?.enqueue({
      index: Number(ev.index ?? 0),
      text: String(ev.text ?? ""),
      audio: String(ev.audio ?? ""),
      mime: String(ev.mime ?? ""),
      duration: Number(ev.duration ?? 0),
      turn_id: Number(ev.turn_id ?? 0),
    });
  });
  feed.on("turn_begin", (ev: FeedEvent) => {
    player?.beginTurn(Number(ev.turn_id ?? 0));
  });
  feed.on("turn_end", (ev: FeedEvent) => player?.endOfTurn(Number(ev.turn_id ?? 0)));
  feed.on("session_end", () => {
    player?.clear();
    chat?.clear();
    highlight?.clear();
    resetAvatarZoom(true);
    void setScene(SCENE_STARTING_SOON);
  });
  feed.on("tool_start", onToolCall); // 工具语义在前端：目前只处理 model_* 画面动作
  feed.on("chat_message", (ev: FeedEvent) => {
    chat?.append(ev as unknown as ChatMsg);
  });
  feed.on("priority_message", (ev: FeedEvent) => {
    const msg = ev as unknown as ChatMsg;
    chat?.append(msg); // 与旧 client 一致：优先消息也占一条普通聊天行
    highlight?.show(msg);
  });

  // 调试/E2E 句柄：控制台或自动化可直接驱动渲染管线（不影响运行逻辑）
  (window as unknown as Record<string, unknown>).__ns = {
    engine, player, feed, chat, highlight, setScene, onToolCall, resetAvatarZoom,
  };
}

void boot();
