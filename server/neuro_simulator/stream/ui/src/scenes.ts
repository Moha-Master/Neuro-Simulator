/** 场景数据来源与开播序列。
 *
 * 场景本身是数据（`public/scenes.json` → 构建到 dist 根，stream 与画面读同一份），
 * 这里只负责：取清单、以及把"开场视频 → 立绘升起"这条时间线跑在元素引擎上。
 */

import { asset } from "./core/assets";
import type { ElementEngine } from "./core/elements";
import type { SceneDef, SceneManifest } from "./scenes/schema";

export const SCENE_STARTING_SOON = "starting_soon";
export const SCENE_ROOM = "room";
export const SCENE_WORDLE = "play_wordle";
export const INTRO_VIDEO_ELEMENT = "intro_video";
export const AVATAR_ELEMENT = "avatar";

/** 拉取场景清单（相对当前页面解析，直挂 / 代理 / dev 三种前缀都成立）。 */
export async function fetchScenes(): Promise<SceneDef[]> {
  const resp = await fetch(asset("scenes.json"), { cache: "no-store" });
  if (!resp.ok) throw new Error(`scenes.json 读取失败: HTTP ${resp.status}`);
  const data = (await resp.json()) as SceneManifest;
  return data.scenes ?? [];
}

let introRunning = false;
let currentVideoDone: (() => void) | null = null;

/** 视频总时长（秒）缓存，用于续播时判断是否已越过片尾。 */
let videoDurationS = 0;

/** 强制中断开播序列（服务端已进入 live 时的状态同步兜底）。 */
export function abortIntro(): void {
  if (currentVideoDone) {
    currentVideoDone();
    currentVideoDone = null;
  }
}

/**
 * 开播序列（严格时序）：
 *  1. 全屏播放 neuro_start.mp4（续播时从 elapsed 秒处接着放）
 *  2. 播完停在末帧不消失（层级黑屏<背景<视频<立绘）
 *  3. 切 room 场景 + 立绘 rise_in 升起（立绘天然浮在静止末帧之上）
 *  4. 立绘到位的一瞬间视频消失
 * elapsed>0 用于中途连接的续播；elapsed>=时长则跳过视频只做立绘升起。
 */
export async function playIntro(engine: ElementEngine, elapsed = 0): Promise<void> {
  if (introRunning) return;
  introRunning = true;
  const host = engine.element(INTRO_VIDEO_ELEMENT);
  const video = host?.querySelector("video") ?? null;
  try {
    const dur = await probeVideoDuration();
    const skipVideo = !video || (dur > 0 && elapsed >= dur - 0.1);
    if (!skipVideo && video) {
      engine.show(INTRO_VIDEO_ELEMENT);
      await playVideo(video, engine.mediaSource(INTRO_VIDEO_ELEMENT) ?? video.src, elapsed);
      // 视频停在末帧（不 pause：ended 后元素自然留在最后一帧）
    }
    // 期间画面可能被显式切走（手动 /scene，或闸门里就开了游戏）：那种情况下那次切换
    // 已经把立绘落好位，这里只收掉视频，不再强行把人拽回 room。
    const moved = engine.current && engine.current.id !== SCENE_ROOM && engine.current.id !== SCENE_STARTING_SOON
    if (moved) {
      engine.hide(INTRO_VIDEO_ELEMENT);
    } else {
      // 视频保持末帧直到立绘到位（keepVisible 让它不在切场景时被顺手收掉）
      await engine.setScene(SCENE_ROOM, {
        enter: { [AVATAR_ELEMENT]: "avatar.rise_in" },
        awaitEnter: [AVATAR_ELEMENT],
        keepVisible: [INTRO_VIDEO_ELEMENT],
      });
      // 立绘到位的一瞬间：视频消失
      engine.hide(INTRO_VIDEO_ELEMENT);
    }
    if (video) {
      video.pause();
      video.removeAttribute("src");
      video.load();
    }
  } finally {
    introRunning = false;
  }
}

/** 播放开场视频（可从中途秒数续播），返回"播放到末帧"的 Promise。 */
async function playVideo(video: HTMLVideoElement, src: string, elapsed: number): Promise<void> {
  if (!video.currentSrc) video.src = src;
  await new Promise<void>((resolve) => {
    let settled = false;
    const done = () => {
      if (settled) return;
      settled = true;
      currentVideoDone = null;
      video.removeEventListener("ended", done);
      video.removeEventListener("error", done);
      resolve(); // 不 pause：ended 后元素停在末帧
    };
    currentVideoDone = done;
    video.addEventListener("ended", done);
    video.addEventListener("error", done);
    if (elapsed > 0.1) {
      const seek = () => {
        try {
          video.currentTime = Math.min(elapsed, (video.duration || 0) - 0.05 || elapsed);
        } catch {
          /* 元数据未就绪：忽略，play 后尽力而为 */
        }
        video.removeEventListener("loadedmetadata", seek);
      };
      video.addEventListener("loadedmetadata", seek);
    }
    video.play().catch(() => {
      video.muted = true;
      video.play().catch(done);
    });
  });
}

/** 读取视频时长（带一次缓存）；失败返回 0。 */
async function probeVideoDuration(): Promise<number> {
  if (videoDurationS > 0) return videoDurationS;
  return new Promise<number>((resolve) => {
    const probe = document.createElement("video");
    probe.preload = "metadata";
    probe.src = asset("neuro_start.mp4");
    const finish = (v: number) => {
      probe.onloadedmetadata = null;
      probe.onerror = null;
      if (v > 0) videoDurationS = v;
      resolve(v);
    };
    probe.onloadedmetadata = () => finish(probe.duration || 0);
    probe.onerror = () => finish(0);
    setTimeout(() => finish(videoDurationS || 0), 3000);
  });
}
