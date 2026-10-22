/** 动作组注册表：把元素位移/特效规范化为可命名、可复用、可参数化的动作。
 *
 * 约定：动作作用于元素（多数是立绘），返回 Promise 在动作完成时 resolve。
 * 位置类动画结束后把终值提交为 inline style，避免 fill:"forwards" 与后续
 * 场景切换的样式互踩。
 *
 * 这张表由宿主注入引擎（`createEngine({actions})`）：合成器与 neuro 窗口共用，
 * 某个页面用不到的动作留着也无害（没人点名就不会被调用）。
 */

export type ActionParams = Record<string, unknown>;
export type ActionFn = (el: HTMLElement, params?: ActionParams) => Promise<void>;

const AVATAR_RISE_FROM = "-207%"; // 旧 client 标定：只露头顶（起始位）
const AVATAR_RISE_MS = 3000; // 与 neuro 场景数据里 avatar.rise_in 的时长一致（合成器自身当前没有用到立绘动作）

function commit(el: HTMLElement, prop: string, value: string): void {
  el.style.setProperty(prop, value);
}

function animate(
  el: HTMLElement,
  keyframes: Keyframe[],
  options: KeyframeAnimationOptions,
): Promise<Animation> {
  const anim = el.animate(keyframes, { fill: "forwards", ...options });
  return anim.finished.then(() => anim);
}

export const ACTIONS: Record<string, ActionFn> = {
  /** 直播开始入场序列：从画面外（露头顶）→ 停留 2s → 升至元素在场景里声明的落位。
   *  落点读引擎已写入的 inline bottom，所以立绘停在哪完全由场景数据决定（params.from 可改起点）。 */
  "avatar.rise_in"(el, params) {
    const target = el.style.bottom || AVATAR_RISE_FROM;
    const from = String(params?.from ?? AVATAR_RISE_FROM);
    el.style.bottom = from;
    return animate(
      el,
      [
        { bottom: from, offset: 0, easing: "linear" },
        { bottom: from, offset: 2 / 3, easing: "cubic-bezier(0.4, 0, 1, 1)" },
        { bottom: target, offset: 1 },
      ],
      { duration: Number(params?.duration ?? AVATAR_RISE_MS) },
    ).then((anim) => {
      commit(el, "bottom", target);
      anim.cancel(); // 只取消自己的动画，避免误伤并发的 spin/zoom
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
    ).then((anim) => anim.cancel());
  },

  /** 放大驻留（scale 1.15，复刻旧 zoom-in） */
  "avatar.zoom_in"(el) {
    return animate(el, [{ transform: "scale(1)" }, { transform: "scale(1.15)" }], {
      duration: 400,
      easing: "ease-in-out",
    }).then((anim) => {
      commit(el, "transform", "scale(1.15)");
      anim.cancel();
    });
  },

  /** 取消放大 */
  "avatar.zoom_out"(el) {
    return animate(el, [{ transform: "scale(1.15)" }, { transform: "scale(1)" }], {
      duration: 400,
      easing: "ease-in-out",
    }).then((anim) => {
      commit(el, "transform", "");
      anim.cancel();
    });
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
    ).then((anim) => {
      el.classList.add("hidden");
      el.textContent = "";
      anim.cancel();
    });
  },
};
