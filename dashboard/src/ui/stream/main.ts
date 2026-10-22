/** 直播合成器（dashboard 构建里的独立页面 `/ui/stream/`）：实际背景 + game/media + neuro 窗口 iframe + chat/highlight。
 *
 * 现实对应关系：
 * - stream 只提供"其它背景 / 靠后的内容 / 靠前的前景"；
 * - neuro 窗口（背景/开场/立绘/字幕）是独立进程的整窗捕获，这里用 iframe 承载，
 *   只做整体 translate+scale，并通过 postMessage 控制内部层显隐；
 * - 字幕/音频/工具动作不再经过 stream 的 SSE——neuro iframe 自己订阅 neuro 的
 *   /ui/events。
 *
 * 事件（stream /ui/events）只负责：场景、session/state、游戏元素状态、聊天、
 * 优先消息、settings（neuro channel）。
 *
 * 这个页面是**独立文档**（不是 dashboard 的 Vue 路由）：OBS 浏览器源要的是一个
 * 干净的画布（不受 Vuetify 主题/reset 影响），neuro 窗口的 iframe 也要透明背景。
 * 它与 dashboard 同一个构建、同一个 origin（由 vedal 静态托管）。
 */

import "./styles/stream.css";

import { ACTIONS } from "../engine/action";
import { ElementEngine } from "../engine/elements";
import { foreignModuleBase, makeAssetUrl } from "../engine/base";
import type { SceneDef } from "../engine/schema";
import { loadFonts } from "./assets";
import { ChatOverlay, HighlightOverlay } from "./chat";
import { NeuroLayer } from "./neuro";
import { COMPONENTS } from "./components";
import {
  StreamFeed,
  type ChatMsg,
  type FeedEvent,
  type NeuroSettings,
  type Snapshot,
} from "./feed";
import { SCENE_ROOM, SCENE_ROOM_INTRO, SCENE_STARTING_SOON, fetchScenes } from "./scenes";

/** neuro 窗口页是**同一个构建里的兄弟入口**（`/ui/stream/` → `/ui/neuro/`），
 *  不再由后端下发（后端那边的 `/ui/` 页面已经不存在了）。 */
function neuroPageUrl(): string {
  return new URL("../neuro/", window.location.href).href;
}

/** neuro 模块的健康检查地址（经 vedal 代理的同源路径）。 */
function neuroHealthUrl(): string {
  return `${foreignModuleBase("data-neuro-module-base", "/manage/neuro_sama/")}health`;
}

function resolveNeuroSettings(settings?: NeuroSettings): {
  uiUrl: string;
  healthUrl: string;
  channel: string;
} {
  return {
    uiUrl: neuroPageUrl(),
    healthUrl: neuroHealthUrl(),
    channel: settings?.neuro_channel || "stream",
  };
}

export async function bootstrapStream(stage: HTMLElement): Promise<void> {
  await loadFonts();

  let scenes: SceneDef[];
  try {
    scenes = await fetchScenes();
  } catch (e) {
    console.error("场景清单加载失败，画面无法渲染", e);
    return;
  }
  const sceneMap = new Map(scenes.map((s) => [s.id, s]));
  const engine = new ElementEngine(stage, scenes, {
    components: COMPONENTS,
    actions: ACTIONS,
    assetUrl: makeAssetUrl(),
  });
  await engine.setScene(SCENE_STARTING_SOON, { instant: true });

  // 实时管线组件从元素引擎取（starting_soon 也声明了 chat/highlight，保证实例先建好）
  const chat = engine.view<ChatOverlay>("chat");
  const highlight = engine.view<HighlightOverlay>("highlight");
  for (const [id, view] of [["chat", chat], ["highlight", highlight]] as const) {
    if (!view) console.warn(`场景缺少 ${id} 元素，相关事件将被忽略`);
  }

  const noSource = document.getElementById("no-source");
  let neuro: NeuroLayer | null = null;
  let streamState = "offline";
  let neuroHealthy = true;
  let healthUrl = "";
  let healthTimer: number | null = null;

  /** 当前场景的 neuro 配置（场景切换 / iframe 晚于场景创建时都要对齐）。 */
  function applySceneNeuro(): void {
    const current = engine.current;
    neuro?.setScene(current ? sceneMap.get(current.id)?.neuro : undefined);
  }

  function ensureNeuro(settings?: NeuroSettings): void {
    const next = resolveNeuroSettings(settings);
    if (!neuro) {
      neuro = new NeuroLayer(stage!, { uiUrl: next.uiUrl, channel: next.channel });
      applySceneNeuro();
    } else {
      neuro.setSettings({ uiUrl: next.uiUrl, channel: next.channel });
    }
    startHealth(next.healthUrl);
  }

  async function setScene(
    id: string,
    opts: { instant?: boolean; elapsed?: number; duration?: number } = {},
  ): Promise<void> {
    try {
      await engine.setScene(id, opts);
      applySceneNeuro();
    } catch (e) {
      console.warn(`场景切换失败: ${id}`, e);
    }
  }

  /** 元素状态（游戏棋盘等）：先写进引擎缓存，元素挂载时会自动回放。 */
  function applyElements(map?: Record<string, unknown>): void {
    if (!map) return;
    for (const [id, payload] of Object.entries(map)) engine.update(id, payload);
  }

  // ---------- neuro 健康轮询：中途掉线 → no_source ----------

  function updateSource(): void {
    const down = streamState !== "offline" && !neuroHealthy;
    noSource?.classList.toggle("hidden", !down);
    neuro?.setAvailable(!down);
  }

  async function checkHealth(): Promise<void> {
    if (!healthUrl) return;
    const ctrl = new AbortController();
    const timer = window.setTimeout(() => ctrl.abort(), 4000);
    let ok = false;
    try {
      const r = await fetch(healthUrl, { cache: "no-store", signal: ctrl.signal });
      ok = r.ok;
    } catch {
      ok = false;
    } finally {
      window.clearTimeout(timer);
    }
    if (ok !== neuroHealthy) {
      neuroHealthy = ok;
      updateSource();
    }
  }

  function startHealth(url: string): void {
    if (url === healthUrl && healthTimer !== null) return;
    healthUrl = url;
    if (healthTimer !== null) {
      window.clearInterval(healthTimer);
      healthTimer = null;
    }
    if (!url) {
      neuroHealthy = true;
      updateSource();
      return;
    }
    void checkHealth();
    healthTimer = window.setInterval(() => void checkHealth(), 5000);
  }

  const feed = new StreamFeed();

  feed.on("snapshot", (ev: FeedEvent) => {
    const snap = ev as unknown as Snapshot;
    streamState = snap.state;
    ensureNeuro(snap.settings);
    updateSource();

    // 聊天层：重连时用 snapshot 的最近记录整体恢复；无场次则清空
    if (snap.chat?.length) chat?.replace(snap.chat);
    else chat?.clear();
    applyElements(snap.elements);

    if (snap.state === "offline") {
      highlight?.clear();
      void setScene(SCENE_STARTING_SOON, { instant: true });
      return;
    }
    if (snap.state === "starting" || snap.state === "live" || snap.state === "paused") {
      // 落到 snapshot 声明的实际场景（含循环期间手动切换过的场景），未知则 room。
      // 重连用 instant：不重复播淡入，画面不会因为 SSE 断开重连而闪一下。
      // elapsed/duration 供场景前端脚本（timeline）断点恢复。
      void setScene(snap.scene?.id || SCENE_ROOM, {
        instant: true,
        elapsed: Number(snap.scene?.elapsed ?? 0),
        duration: Number(snap.scene?.duration ?? 0),
      });
    } else {
      void setScene(SCENE_STARTING_SOON, { instant: true });
    }
  });

  // 新场次开始：清掉上一场残留聊天/优先弹窗（场景由 start 广播的 scene_set 驱动）
  feed.on("session_begin", () => {
    chat?.clear();
    highlight?.clear();
  });

  feed.on("state", (ev: FeedEvent) => {
    const s = String(ev.state ?? "");
    streamState = s;
    updateSource();
    if (s === "starting" || s === "live" || s === "paused") {
      // 后端是唯一时钟：若还停在待机，就落到**该阶段**的默认画面——
      // starting 用 room_intro（chat 不该盖在开场动画上），live/paused 用 room。
      // 已经在某个直播场景里（含游戏）就别把它拽回去——暂停/继续不该换画面。
      // 正常路径由 scene_set 驱动，这里只兜"迟到的渲染器"。
      if (!engine.current || engine.current.id === SCENE_STARTING_SOON) {
        void setScene(s === "starting" ? SCENE_ROOM_INTRO : SCENE_ROOM);
      }
    } else if (s === "offline") {
      chat?.clear();
      highlight?.clear();
      void setScene(SCENE_STARTING_SOON);
    }
  });

  feed.on("scene_set", (ev: FeedEvent) => {
    void setScene(String(ev.id ?? ""), {
      elapsed: Number(ev.elapsed ?? 0),
      duration: Number(ev.duration ?? 0),
    });
  });

  feed.on("element_state", (ev: FeedEvent) => {
    engine.update(String(ev.id ?? ""), ev.payload);
  });

  feed.on("settings", (ev: FeedEvent) => {
    // 配置热重载：neuro UI 地址/channel 可能变了；健康地址一起更新。
    ensureNeuro(ev as unknown as NeuroSettings);
  });

  feed.on("session_end", () => {
    chat?.clear();
    highlight?.clear();
    void setScene(SCENE_STARTING_SOON);
  });

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
    engine,
    get neuro() {
      return neuro;
    },
    feed,
    chat,
    highlight,
    noSource,
    setScene,
    ensureNeuro,
    isNeuroHealthy: () => neuroHealthy,
  };
}
