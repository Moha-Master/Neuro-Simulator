/** neuro 窗口入口：数据驱动的场景 + 字幕/音频 + 立绘动作。
 *
 * 这一页就是现实中 neuro.exe 的"窗口"：固定 16:9 逻辑画布，只有场景数据里声明的
 * 那些层（背景 / 开场视频 / 立绘 / 字幕），不含 chat / 游戏 / 合成——那些是 stream
 * 合成器的层。它可以单独打开（与直播无关），也可以被 stream/ui 用 iframe 捕获。
 *
 * 两条控制权，边界很清楚：
 * - **场景**是后端的权威状态：`scene_set` 事件 / snapshot 告诉你现在该演哪个场景，
 *   本页只如实渲染（含按 `elapsed` 断点续播）。本页没有"切场景"的方法。
 * - **显隐**由父页面按元素 id 下发：`postMessage({ns:"neuro-window",
 *   type:"visibility", hidden:["bg_room"]})`，用于媒体场景露出合成器背景。
 */

import "./styles/neuro.css";

import { loadFonts } from "./core/assets";
import { ElementEngine } from "./core/elements";
import { CaptionPlayer, type SpeechSeg } from "./core/speech";
import {
  NeuroFeed,
  type FeedEvent,
  type SceneInfo,
  type Snapshot,
  type TurnSeg,
} from "./core/feed";
import { fetchScenes } from "./scenes";
import type { SceneManifest } from "./scenes/schema";

async function boot(): Promise<void> {
  await loadFonts();

  const stage = document.getElementById("stage");
  if (!stage) return;

  let manifest: SceneManifest;
  try {
    manifest = await fetchScenes();
  } catch (e) {
    console.error("场景清单（neuro_scenes.json）加载失败，窗口无法渲染", e);
    return;
  }

  const refs = manifest.refs ?? {};
  const avatarId = refs.avatar ?? "avatar";
  const captionId = refs.caption ?? "caption";

  const engine = new ElementEngine(stage, manifest.scenes);
  // 首帧先按数据落到 default_scene；SSE 的 snapshot 一到就用后端的权威状态纠正。
  try {
    await engine.setScene(manifest.default_scene, { instant: true });
  } catch (e) {
    console.error(`default_scene 无法渲染: ${manifest.default_scene}`, e);
    return;
  }

  // ---------- 字幕实例（跨场景复用同一个元素与实例） ----------

  let player: CaptionPlayer | null = null;
  let captionSettings: Snapshot["settings"] | null = null;

  function applyCaptionSettings(): void {
    const caption = captionSettings?.caption;
    if (!caption || !player) return;
    if (caption.hide_ms !== undefined) player.setHideDelay(Number(caption.hide_ms));
    if (caption.step_gap_ms !== undefined) player.setStepGap(Number(caption.step_gap_ms));
  }

  /** 元素内容变了会换实例（改数据/换组件），这里重新认一次并补设置 */
  function ensurePlayer(): CaptionPlayer | null {
    const view = engine.view<CaptionPlayer>(captionId);
    if (view && view !== player) {
      player = view;
      player.onIdle = () => resetZoom();
      applyCaptionSettings();
    }
    return player;
  }
  ensurePlayer();

  // ---------- 立绘动作（tool_start 驱动；zoom 在语音播完后复位） ----------

  let zoomed = false;

  function resetZoom(instant = false): void {
    if (!zoomed) return;
    zoomed = false;
    const el = engine.element(avatarId);
    if (!el) return;
    if (instant) {
      el.style.transform = "";
    } else {
      void engine.run(avatarId, "avatar.zoom_out");
    }
  }

  // ---------- 场景（只读渲染后端的权威状态） ----------

  function applyScene(info: SceneInfo | undefined): void {
    if (!info || !info.id) return;
    if (!engine.has(info.id)) {
      console.warn(`后端要求渲染未知场景: ${info.id}`);
      return;
    }
    void engine
      .setScene(info.id, { elapsed: Number(info.elapsed ?? 0), duration: Number(info.duration ?? 0) })
      .then(ensurePlayer)
      .catch((e) => console.warn(`场景切换失败: ${info.id}`, e));
  }

  // ---------- 父页面（合成器 / dashboard 预览）控制层的显隐 ----------

  window.addEventListener("message", (event: MessageEvent) => {
    const data = event.data as { ns?: string; type?: string; hidden?: string[] } | undefined;
    if (!data || data.ns !== "neuro-window") return;
    if (data.type === "visibility") {
      engine.setHidden(Array.isArray(data.hidden) ? data.hidden : []);
    }
  });
  if (window.parent && window.parent !== window) {
    window.parent.postMessage({ ns: "neuro-window", type: "ready" }, "*");
  }

  // ---------- 事件通道 ----------

  const feed = new NeuroFeed();

  feed.on("snapshot", (ev: FeedEvent) => {
    const snap = ev as unknown as Snapshot;
    captionSettings = snap.settings ?? null;
    applyCaptionSettings();
    applyScene(snap.scene);
    const p = ensurePlayer();
    if (!p) return;
    if (snap.turn?.speech?.length) {
      p.resumeFromSnapshot(snap.turn.speech as TurnSeg[], snap.turn.id);
      if (!snap.turn.active) p.endOfTurn(snap.turn.id);
    } else {
      p.clear();
    }
  });

  feed.on("scene_set", (ev: FeedEvent) => {
    applyScene({
      id: String(ev.id ?? ""),
      duration: Number(ev.duration ?? 0),
      elapsed: Number(ev.elapsed ?? 0),
    });
  });

  feed.on("start", (ev: FeedEvent) => {
    const turnId = ev.turn_id === undefined ? undefined : Number(ev.turn_id);
    ensurePlayer()?.beginTurn(turnId);
  });

  feed.on("speech", (ev: FeedEvent) => {
    const seg: SpeechSeg = {
      index: Number(ev.index ?? 0),
      text: String(ev.text ?? ""),
      audio: String(ev.audio ?? ""),
      mime: String(ev.mime ?? ""),
      duration: Number(ev.duration ?? 0),
      step: Number(ev.step ?? 0),
    };
    if (ev.turn_id !== undefined) seg.turn_id = Number(ev.turn_id);
    ensurePlayer()?.enqueue(seg);
  });

  for (const type of ["done", "cancelled", "error"] as const) {
    feed.on(type, (ev: FeedEvent) => {
      const turnId = ev.turn_id === undefined ? undefined : Number(ev.turn_id);
      ensurePlayer()?.endOfTurn(turnId);
    });
  }

  feed.on("tool_start", (ev: FeedEvent) => {
    const name = String(ev.name ?? "");
    if (name === "model_spin") {
      void engine.run(avatarId, "avatar.spin");
    } else if (name === "model_zoom") {
      zoomed = true;
      void engine.run(avatarId, "avatar.zoom_in");
    } else if (name.startsWith("model_")) {
      console.warn(`未处理的 neuro 画面工具: ${name}`);
    }
  });

  feed.on("settings", (ev: FeedEvent) => {
    captionSettings = (ev as unknown as Snapshot["settings"]) ?? null;
    applyCaptionSettings();
  });

  // 控制台/父页面调试入口（与 stream/ui 的 window.__ns 对齐）
  (window as unknown as Record<string, unknown>).__ns = {
    stage,
    engine,
    feed,
    manifest,
    get player() {
      return player;
    },
    setHidden: (ids: string[]) => engine.setHidden(ids),
    resetZoom,
  };
}

void boot();
