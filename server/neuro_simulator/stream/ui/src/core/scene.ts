/** 场景引擎：背景 + 立绘位姿 + 图层可见性的数据驱动定义，切换时自动 crossfade。
 *
 * 场景是纯数据（scenes.ts 注册）；切场景 = 背景淡入淡出 + avatar 落位
 * （或播放入场动作）+ starting_soon 等专属元素开关。控制输入走 CommandIngest
 * 抽象（当前=浮动面板手动；未来=直播管理器推送），场景引擎本身不感知来源。
 */

import { runAction } from "./action";
import { asset } from "./assets";

export interface SceneDef {
  id: string;
  label: string;
  background: { image: string; dim?: boolean };
  /** 立绘位姿；null 表示该场景无立绘（保持现状不动） */
  avatar: { pose: "hidden" | "step1" | "step2" } | null;
  /** 入场动作（优先于 avatar.pose，如开播后 rise_in 自带落位） */
  enterAction?: string;
  elements?: { startingSoon?: boolean };
}

export class SceneManager {
  private defs = new Map<string, SceneDef>();
  private front: HTMLElement;
  private back: HTMLElement;
  private avatar: HTMLElement;
  private startingSoon: HTMLElement;
  current: SceneDef | null = null;

  constructor() {
    this.front = document.getElementById("bg-a")!;
    this.back = document.getElementById("bg-b")!;
    this.avatar = document.getElementById("neuro-avatar")!;
    this.startingSoon = document.getElementById("starting-soon")!;
    const img = this.avatar.querySelector("img")!;
    img.src = asset("neurosama.png");
  }

  register(def: SceneDef): void {
    this.defs.set(def.id, def);
  }

  list(): SceneDef[] {
    return [...this.defs.values()];
  }

  /** 应用场景。opts.enterAction 覆盖场景定义的入场动作（如开播序列专用 rise_in）。 */
  async set(id: string, opts?: { enterAction?: string }): Promise<void> {
    const def = this.defs.get(id);
    if (!def) throw new Error(`未知场景: ${id}`);
    this.current = def;

    // 背景：back 缓冲装载 → 交换 visible 类 → 完成淡入
    if (def.background.image) {
      this.back.style.backgroundImage = `url(${asset(def.background.image)})`;
      this.back.style.backgroundColor = "";
    } else {
      this.back.style.backgroundImage = "none";
      this.back.style.backgroundColor = "#000";
    }
    this.back.classList.toggle("dim", !!def.background.dim);
    const prev = this.front;
    this.front = this.back;
    this.back = prev;
    this.front.classList.add("visible");
    this.back.classList.remove("visible");

    // 立绘（入场动作 await：开播序列依赖"立绘就位"的完成时机）
    const enter = opts?.enterAction ?? def.enterAction;
    if (enter) {
      await runAction(enter, this.avatar);
    } else if (def.avatar) {
      void runAction("avatar.place", this.avatar, { pose: def.avatar.pose });
    }

    // 专属元素
    this.startingSoon.classList.toggle("hidden", !def.elements?.startingSoon);
  }
}
