/** 动作组注册表：把元素位移/特效规范化为可命名、可复用、可参数化的动作。
 *
 * 约定：动作作用于元素（多数是 #neuro-avatar），返回 Promise 在动作完成时 resolve。
 * 位置类动画结束后把终值提交为 inline style，避免 fill:"forwards" 与后续
 * 场景切换的样式互踩。
 */

export type ActionParams = Record<string, unknown>;
export type ActionFn = (el: HTMLElement, params?: ActionParams) => Promise<void>;

const AVATAR_STEP1_BOTTOM = "-207%"; // 旧 client 标定：露出头顶
const AVATAR_STEP2_BOTTOM = "-125%"; // 旧 client 标定：落位待机
const AVATAR_HIDDEN_BOTTOM = "-207%";

function commit(el: HTMLElement, prop: string, value: string): void {
  el.style.setProperty(prop, value);
}

function animate(el: HTMLElement, keyframes: Keyframe[], options: KeyframeAnimationOptions): Promise<void> {
  return el
    .animate(keyframes, { fill: "forwards", ...options })
    .finished.then(() => undefined); // 丢弃 Animation 返回值，统一 Promise<void>
}

export const ACTIONS: Record<string, ActionFn> = {
  /** 立绘即时落位：pose = hidden | step1 | step2 */
  "avatar.place"(el, params) {
    const pose = String(params?.pose ?? "step2");
    el.style.transition = "none";
    el.style.visibility = pose === "hidden" ? "hidden" : "visible";
    el.style.bottom = pose === "step2" ? AVATAR_STEP2_BOTTOM : AVATAR_HIDDEN_BOTTOM;
    return Promise.resolve();
  },

  /** 直播开始出场序列：屏幕外(露头顶) → 停留 2s → 升至待机位（复刻旧 STEP1→STEP2） */
  "avatar.rise_in"(el) {
    el.style.visibility = "visible";
    return animate(
      el,
      [
        { bottom: AVATAR_STEP1_BOTTOM, offset: 0, easing: "linear" },
        { bottom: AVATAR_STEP1_BOTTOM, offset: 2 / 3, easing: "cubic-bezier(0.4, 0, 1, 1)" },
        { bottom: AVATAR_STEP2_BOTTOM, offset: 1 },
      ],
      { duration: 3000 },
    ).then(() => {
      commit(el, "bottom", AVATAR_STEP2_BOTTOM);
      el.getAnimations().forEach((a) => a.cancel());
    });
  },

  /** 逆时针旋转一圈（复刻旧 spin-counter-clockwise 0.75s） */
  "avatar.spin"(el) {
    return animate(
      el,
      [
        { transform: "rotate(0deg)" },
        { transform: "rotate(-360deg)" },
      ],
      { duration: 750, easing: "linear" },
    ).then(() => el.getAnimations().forEach((a) => a.cancel()));
  },

  /** 放大驻留（scale 1.15，复刻旧 zoom-in） */
  "avatar.zoom_in"(el) {
    return animate(el, [{ transform: "scale(1)" }, { transform: "scale(1.15)" }], {
      duration: 400,
      easing: "ease-in-out",
    }).then(() => commit(el, "transform", "scale(1.15)"));
  },

  /** 取消放大 */
  "avatar.zoom_out"(el) {
    return animate(el, [{ transform: "scale(1.15)" }, { transform: "scale(1)" }], {
      duration: 400,
      easing: "ease-in-out",
    }).then(() => commit(el, "transform", ""));
  },

  /** highlight 消息：出现 → 停留 → 消失 */
  "highlight.show"(el, params) {
    const text = String(params?.text ?? "");
    const holdMs = Number(params?.holdMs ?? 4000);
    el.textContent = text;
    el.classList.remove("hidden");
    const inMs = 300;
    const outMs = 500;
    const total = inMs + holdMs + outMs;
    return animate(
      el,
      [
        { opacity: 0, transform: "translate(-50%, 20px) scale(0.85)", offset: 0, easing: "ease-out" },
        { opacity: 1, transform: "translate(-50%, 0) scale(1)", offset: inMs / total, easing: "linear" },
        { opacity: 1, transform: "translate(-50%, 0) scale(1)", offset: (inMs + holdMs) / total, easing: "ease-in" },
        { opacity: 0, transform: "translate(-50%, -14px) scale(0.95)", offset: 1 },
      ],
      { duration: total },
    ).then(() => {
      el.classList.add("hidden");
      el.textContent = "";
      el.getAnimations().forEach((a) => a.cancel());
    });
  },
};

export async function runAction(name: string, target: HTMLElement | null, params?: ActionParams): Promise<void> {
  const fn = ACTIONS[name];
  if (!fn) {
    console.warn(`未知动作: ${name}`);
    return;
  }
  if (!target) {
    console.warn(`动作 ${name} 缺少目标元素`);
    return;
  }
  await fn(target, params);
}
