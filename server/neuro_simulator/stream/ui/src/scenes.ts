/** 内置场景注册 + 开播序列（作为可复用时间线，任意入口可触发）。 */

import { asset } from "./core/assets";
import type { SceneManager } from "./core/scene";

export const SCENE_STARTING_SOON = "starting_soon";
export const SCENE_ROOM = "room";

export function registerScenes(mgr: SceneManager): void {
  mgr.register({
    id: SCENE_STARTING_SOON,
    label: "Starting Soon",
    background: { image: "" }, // 纯黑（SceneManager 处理空串）
    avatar: { pose: "hidden" },
    elements: { startingSoon: false }, // 不再显示 text
  });
  mgr.register({
    id: SCENE_ROOM,
    label: "房间（默认）",
    background: { image: "background.webp" },
    avatar: { pose: "step2" },
  });
  mgr.register({
    id: "community_art",
    label: "Community Art",
    background: { image: "background.webp", dim: true },
    avatar: { pose: "step2" },
  });
  mgr.register({
    id: "gameplay",
    label: "Game Play",
    background: { image: "background.webp", dim: true },
    avatar: { pose: "step2" },
  });
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
 *  3. 其下切 room 场景 + 立绘 rise_in 升起（立绘天然浮在静止末帧之上）
 *  4. 立绘到位的一瞬间视频消失
 *  elapsed>0 用于中途连接的续播；elapsed>=时长则跳过视频只做立绘升起。
 */
export async function playIntro(mgr: SceneManager, elapsed = 0): Promise<void> {
  if (introRunning) return;
  introRunning = true;
  const mediaLayer = document.getElementById("media-layer")!;
  const video = document.getElementById("startup-video") as HTMLVideoElement;
  try {
    const dur = await probeVideoDuration();
    const skipVideo = dur > 0 && elapsed >= dur - 0.1;
    if (!skipVideo) {
      mediaLayer.classList.remove("hidden");
      video.src = asset("neuro_start.mp4");
      await new Promise<void>((resolve) => {
        let resolved = false;
        const done = () => {
          if (resolved) return;
          resolved = true;
          currentVideoDone = null;
          video.removeEventListener("ended", done);
          video.removeEventListener("error", done);
          resolve(); // 不 pause：ended 后元素停在末帧
        };
        currentVideoDone = done;
        video.addEventListener("ended", done);
        video.addEventListener("error", done);
        if (elapsed > 0.1) {
          // 续播：元数据就绪后跳到已过时长再起播
          const seek = () => {
            try {
              video.currentTime = Math.min(elapsed, (video.duration || dur) - 0.05);
            } catch {
              /* seeking before ready：忽略，play 后尽力而为 */
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
    // 视频停在末帧（或已跳过），在其下升起立绘
    await mgr.set(SCENE_ROOM, { enterAction: "avatar.rise_in" });
    // 立绘到位的一瞬间：视频消失
    mediaLayer.classList.add("hidden");
    video.pause();
    video.removeAttribute("src");
    video.load();
  } finally {
    introRunning = false;
  }
}

/** 读取视频时长（带一次缓存）；失败返回 0。 */
async function probeVideoDuration(): Promise<number> {
  if (videoDurationS > 0) return videoDurationS;
  const src = asset("neuro_start.mp4");
  return new Promise<number>((resolve) => {
    const probe = document.createElement("video");
    probe.preload = "metadata";
    probe.src = src;
    const finish = (v: number) => {
      probe.onloadedmetadata = null;
      probe.onerror = null;
      videoDurationS = v;
      resolve(v);
    };
    probe.onloadedmetadata = () => finish(probe.duration || 0);
    probe.onerror = () => finish(0);
    setTimeout(() => finish(videoDurationS || 0), 3000);
  });
}
