/** 元素引擎：把"场景 = 元素清单"（scenes/schema.ts）落成真实的 DOM 与生命周期。
 *
 * ⚠️ 这份文件是 `stream/ui/src/core/elements.ts` 的**近乎逐字拷贝**（两个前端是各自
 * 独立的 Vite 工程，没有共享包）。差异只有三处，都带注释：
 *   1. `at_end` / 场景时长的解析（neuro 的时长由后端探测视频轨道给出）；
 *   2. `media.play` 不写 duration 时按"播到场景结束"处理（换素材不必改数据）；
 *   3. `setHidden(ids)`：父页面（stream 合成器 / dashboard 预览）按元素 id 强制
 *      隐藏某一层，与场景数据取交集——这样新加一层就能直接被外部寻址。
 * 修 stream 那份引擎的 bug 时请对照同步。
 *
 * - 几何全部以舞台比例声明，引擎写成 inline style（left/top/bottom/width/height/z-index/transform）。
 * - **同 id 跨场景复用同一个 DOM 节点与同一个组件实例**：切场景只做 diff
 *   （离场 → 跑 exit 后隐藏、不销毁；进场 → 挂载并跑 enter；两边都有且几何变了 → 跑 move）。
 *   不销毁是有意为之：字幕队列、视频进度、组件内部状态都要跨场景活着。
 * - 默认无过渡（不写脚本 = 直接出现 / 直接落位）；要淡入或平滑移动就在场景数据里写脚本。
 * - 场景级 `timeline`（前端脚本）由引擎按秒执行；切场景会取消旧 timer/动画并暂停视频，
 *   snapshot 的 `elapsed` 可从断点恢复。后端脚本 `state_script` 不在这里执行。
 * - `update(id, payload)` 是通用元素状态通道（缓存最新 payload，元素首次挂载自动回放）。
 */

import { asset } from "./assets";
import { runAction } from "./action";
import { COMPONENTS, type ComponentInstance, type ElementView } from "./components";
import type {
  Box,
  Content,
  ElementDef,
  SceneDef,
  Script,
  SceneTimeline,
  TimelineStep,
} from "../scenes/schema";

interface Mounted {
  def: ElementDef;
  el: HTMLElement;
  view: ComponentInstance | null;
  /** 当前静止几何（补间的起点） */
  box: Box;
  visible: boolean;
}

const pct = (v: number): string => `${(v * 100).toFixed(4)}%`;

function geometryOf(box: Box): Partial<CSSStyleDeclaration> {
  const s: Partial<CSSStyleDeclaration> = {};
  if (box.edge?.x === "right") {
    s.right = pct(box.x);
    s.left = "auto";
  } else {
    s.left = pct(box.x);
    s.right = "auto";
  }
  if (box.edge?.y === "bottom") {
    s.bottom = pct(box.y);
    s.top = "auto";
  } else {
    s.top = pct(box.y);
    s.bottom = "auto";
  }
  s.width = box.w === undefined ? "auto" : pct(box.w);
  s.height = box.h === undefined ? "auto" : pct(box.h);
  const tf: string[] = [];
  if (box.scale !== undefined) tf.push(`scale(${box.scale})`);
  if (box.rotate !== undefined) tf.push(`rotate(${box.rotate}deg)`);
  s.transform = tf.join(" ");
  s.opacity = String(box.opacity ?? 1);
  return s;
}

function sameLayout(a: ElementDef, b: ElementDef): boolean {
  return (
    JSON.stringify(a.box) === JSON.stringify(b.box) &&
    a.z === b.z &&
    JSON.stringify(a.css ?? null) === JSON.stringify(b.css ?? null)
  );
}

function sameContent(a: Content, b: Content): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

export class ElementEngine {
  private stage: HTMLElement;
  private defs = new Map<string, SceneDef>();
  private mounted = new Map<string, Mounted>();
  private states = new Map<string, unknown>();
  /** 当前场景脚本的取消标记：每次切场景递增，旧 timer / 动作据此失效。 */
  private timelineToken = 0;
  private timelineTimers: number[] = [];
  /** 场景脚本触碰过的元素（切场景时取消其动画并暂停视频）。 */
  private timelineEls = new Set<HTMLElement>();
  /** 每个元素上一次由场景数据写入的 css 键（换场景时清掉不再声明的覆盖项）。 */
  private appliedCss = new WeakMap<HTMLElement, string[]>();
  /** 外部（父页面）强制隐藏的层 id；与场景声明的显隐取交集。 */
  private overrideHidden = new Set<string>();
  current: SceneDef | null = null;

  constructor(stage: HTMLElement, scenes: SceneDef[]) {
    this.stage = stage;
    for (const s of scenes) this.defs.set(s.id, s);
  }

  list(): SceneDef[] {
    return [...this.defs.values()];
  }

  has(id: string): boolean {
    return this.defs.has(id);
  }

  /** 元素根节点（场景时间线的 media.* 动作要拿里面的 <video>） */
  element(id: string): HTMLElement | null {
    return this.mounted.get(id)?.el ?? null;
  }

  /** 组件实例（main.ts 用它驱动字幕的实时管线） */
  view<T>(id: string): T | null {
    return (this.mounted.get(id)?.view ?? null) as T | null;
  }

  visible(id: string): boolean {
    const m = this.mounted.get(id);
    return !!m && m.visible && !this.overrideHidden.has(id);
  }

  /** 通用元素状态：缓存 + 路由给组件（幂等全量快照，中途连接靠 snapshot 回放） */
  update(id: string, payload: unknown): void {
    this.states.set(id, payload);
    this.asView(this.mounted.get(id))?.update?.(payload);
  }

  state(id: string): unknown {
    return this.states.get(id);
  }

  /** 元素声明的媒体地址（图片/视频，已按挂载前缀解析）；其它内容返回 null */
  mediaSource(id: string): string | null {
    const content = this.mounted.get(id)?.def.content;
    if (content && (content.kind === "image" || content.kind === "video")) return asset(content.src);
    return null;
  }

  /** 外部按元素 id 强制隐藏/恢复（`hidden` 里没有的 id 一律交回场景数据决定）。
   *  这是父页面唯一的显隐入口：新加一层不需要改两边代码。 */
  setHidden(ids: string[]): void {
    this.overrideHidden = new Set(ids);
    for (const [id, m] of this.mounted) this.applyHiddenFlag(id, m);
  }

  /** 内部显隐 + 外部覆盖一起决定最终是否显示 */
  private applyHiddenFlag(id: string, m: Mounted): void {
    const hidden = !m.visible || this.overrideHidden.has(id);
    m.el.classList.toggle("ns-el--hidden", hidden);
  }

  /** 手动显隐（场景时间线用） */
  show(id: string): void {
    const m = this.mounted.get(id);
    if (!m) return;
    m.visible = true;
    this.applyHiddenFlag(id, m);
  }

  hide(id: string): void {
    const m = this.mounted.get(id);
    if (!m) return;
    m.visible = false;
    this.applyHiddenFlag(id, m);
  }

  /** 对某元素跑命名动作（tool_start 的 avatar.spin / zoom_in 用） */
  async run(id: string, action: string, params?: Record<string, unknown>): Promise<void> {
    const m = this.mounted.get(id);
    if (!m) {
      console.warn(`元素未挂载，跳过动作 ${action}: ${id}`);
      return;
    }
    await runAction(action, m.el, params);
  }

  /** 切场景：diff 元素清单。
   *
   * - `instant`：跳过所有过渡（snapshot 校正状态时用，避免每次重连都淡入一遍）。
   * - `elapsed`：场景脚本已过的秒数（snapshot 中途连接续播）；不会补播已过动作，
   *   正在进行的动作按剩余时长收尾，未开始的按剩余延迟排程。
   * - `duration`：场景有效时长，由后端给（可能来自视频轨道探测）；`at_end` 靠它换算。
   * 元素进场/离场过渡与时间线都由数据驱动；权威状态（换场景）在 neuro 后端。
   */
  async setScene(
    id: string,
    opts?: {
      instant?: boolean;
      elapsed?: number;
      duration?: number;
    },
  ): Promise<void> {
    const def = this.defs.get(id);
    if (!def) throw new Error(`未知场景: ${id}`);
    // 先取消上一个场景的时间线，再改 current；旧 timer / 动画不能影响新场景。
    const token = this.resetTimeline();
    this.current = def;
    const want = new Map(def.elements.map((e) => [e.id, e]));
    const transition = (eid: string, p: Promise<void>): void => {
      p.catch((e) => console.warn(`元素过渡失败: ${eid}`, e));
    };

    // 1) 离场：跑 exit 脚本后隐藏（不销毁，实例与内部状态留着）
    for (const [eid, m] of [...this.mounted]) {
      if (want.has(eid) || !m.visible) continue;
      if (opts?.instant) this.hide(eid);
      else transition(eid, this.runExit(m));
    }

    // 2) 进场 / 留在场景内
    for (const e of want.values()) {
      let m = this.mounted.get(e.id);
      if (!m) {
        this.mount(e);
        m = this.mounted.get(e.id)!;
      } else if (!sameContent(m.def.content, e.content)) {
        // 内容变了（改数据/换组件）→ 重建内部，实例随之换新
        this.asView(m)?.dispose?.();
        m.el.replaceChildren();
        m.view = this.buildContent(m.el, e.content);
        this.replay(m);
      }
      const moved = !sameLayout(m.def, e);
      m.def = e;
      m.el.style.zIndex = String(e.z);
      this.applyCss(m);

      if (opts?.instant) {
        m.box = e.box;
        this.applyGeometry(m);
        if (e.visible === false) this.hide(e.id);
        else this.show(e.id);
        continue;
      }
      // 场景数据里的 visible 是权威：平时藏着的元素（开场视频、intro 期间的立绘）
      // 不能因为"两边都列出了同一 id"就被复活。
      if (e.visible === false) {
        if (m.visible) transition(e.id, this.runExit(m));
        continue;
      }
      if (!m.visible) {
        transition(e.id, this.runScript(m, e.script?.enter, "enter"));
      } else if (moved) {
        transition(e.id, this.runScript(m, e.script?.move, "move"));
      }
    }
    // 只有当前场景还能启动脚本；同步段之后 token 不会变（过渡都是异步的）。
    if (token !== this.timelineToken) return;
    if (def.timeline) {
      this.startTimeline(
        def.timeline,
        Math.max(0, Number(opts?.elapsed ?? 0)),
        this.sceneDuration(def, opts?.duration),
        token,
      );
    }
  }

  /** 场景有效时长：后端下发优先，其次数据里写死的秒数；
   *  `{"media": ...}` 这类需要探测的时长只能由后端给（它是时权的唯一来源）。 */
  private sceneDuration(def: SceneDef, override?: number): number {
    if (override !== undefined && override > 0) return override;
    return typeof def.duration === "number" && def.duration > 0 ? def.duration : 0;
  }

  /** `at_end: N` → `时长 - N`（与服务端 scenes.resolve_steps 同一规则）。 */
  private resolveAt(step: TimelineStep, duration: number): number {
    if (step.at_end !== undefined) return Math.max(0, duration - Number(step.at_end));
    return Math.max(0, Number(step.at) || 0);
  }

  // ---------- 场景脚本（前端侧） ----------

  /** 取消当前场景脚本：清 timer、停视频、取消它启动的动画。 */
  private resetTimeline(): number {
    this.timelineToken += 1;
    for (const t of this.timelineTimers) window.clearTimeout(t);
    this.timelineTimers = [];
    for (const el of this.timelineEls) {
      // 时间线动作被中途取消时，先把元素的旧场景落位提交回 inline style，
      // 否则 rise_in 会停在半空；下一个场景若几何相同就不会再 applyGeometry。
      const m = el.dataset.el ? this.mounted.get(el.dataset.el) : undefined;
      el.getAnimations().forEach((a) => a.cancel());
      if (m) {
        m.box = m.def.box;
        this.applyGeometry(m);
      }
      const videos =
        el instanceof HTMLVideoElement ? [el] : Array.from(el.querySelectorAll("video"));
      for (const v of videos) v.pause();
    }
    this.timelineEls.clear();
    return this.timelineToken;
  }

  /** 从 elapsed 秒处开始/恢复场景时间线。 */
  private startTimeline(timeline: SceneTimeline, elapsed: number, duration: number, token: number): void {
    for (const step of timeline.steps ?? []) {
      const at = this.resolveAt(step, duration);
      const stepDuration = Math.max(0, Number(step.duration) || 0);
      // media.play 不写 duration = "一直播到场景结束"（换素材不用回去改数据）
      const openEnded = step.action === "media.play" && stepDuration === 0;
      const end = openEnded
        ? Math.max(at + 0.001, duration > at ? duration : Number.POSITIVE_INFINITY)
        : at + stepDuration;
      if (end <= elapsed) {
        this.applyTimelineStep(step, "final", 0, token);
      } else if (at <= elapsed) {
        this.applyTimelineStep(step, "partial", elapsed - at, token);
      } else {
        const timer = window.setTimeout(() => {
          if (token !== this.timelineToken) return;
          this.applyTimelineStep(step, "start", 0, token);
        }, (at - elapsed) * 1000);
        this.timelineTimers.push(timer);
      }
    }
  }

  private applyTimelineStep(
    step: TimelineStep,
    mode: "start" | "partial" | "final",
    offset: number,
    token: number,
  ): void {
    const m = this.mounted.get(step.element);
    if (!m) {
      console.warn(`场景脚本目标未挂载: ${step.element}`);
      return;
    }
    const el = m.el;
    this.timelineEls.add(el);

    if (step.action === "element.show") {
      m.box = m.def.box;
      this.applyGeometry(m);
      this.show(step.element);
      return;
    }
    if (step.action === "element.hide") {
      this.hide(step.element);
      return;
    }
    if (step.action === "media.play" || step.action === "media.pause") {
      m.box = m.def.box;
      this.applyGeometry(m);
      this.show(step.element);
      const video = el.querySelector("video");
      if (!video) {
        console.warn(`场景脚本目标不是视频: ${step.element}`);
        return;
      }
      this.timelineEls.add(video);
      if (step.action === "media.pause" || mode === "final") {
        this.settleVideo(video, Number(step.duration) || 0);
      } else {
        this.playVideo(video, mode === "partial" ? offset : 0, token);
      }
      return;
    }

    // 命名动作：先把元素放到场景声明的落位并显示（rise_in 会从落位反推起点）。
    m.box = m.def.box;
    this.applyGeometry(m);
    this.show(step.element);

    if (mode === "final") {
      el.getAnimations().forEach((a) => a.cancel());
      return;
    }
    const params: Record<string, unknown> = { ...(step.params ?? {}) };
    if (mode === "partial") {
      const remaining = Math.max(0, (Number(step.duration) || 0) - offset);
      if (remaining <= 0.05) {
        el.getAnimations().forEach((a) => a.cancel());
        return;
      }
      params.duration = remaining * 1000;
    }
    void runAction(step.action, el, params).catch((e) => {
      // 切场景时主动 cancel 动画会以 AbortError 拒绝，不是故障。
      if ((e as { name?: string } | null)?.name === "AbortError") return;
      console.warn(`场景动作失败: ${step.action}`, e);
    });
  }

  /** 视频从 offset 秒开始播放（元数据未就绪时等 loadedmetadata）。 */
  private playVideo(video: HTMLVideoElement, offset: number, token: number): void {
    const start = (): void => {
      if (token !== this.timelineToken) return;
      try {
        video.currentTime = Math.min(Math.max(0, offset), video.duration || offset);
      } catch {
        /* 元数据异常：保持默认起点 */
      }
      video.play().catch(() => {
        video.muted = true;
        video.play().catch(() => {
          /* 浏览器拒绝播放：画面停在当前帧，后端不受影响 */
        });
      });
    };
    if (video.readyState >= 1 /* HAVE_METADATA */) start();
    else video.addEventListener("loadedmetadata", start, { once: true });
  }

  /** 把视频停在末帧（已过播放段的中途连接 / 动作已完成）。
   *  `end <= 0` 表示"数据没写死时长"，用视频自身的时长。 */
  private settleVideo(video: HTMLVideoElement, end: number): void {
    video.pause();
    const seek = (): void => {
      try {
        const duration = video.duration || end;
        const target = end > 0 ? Math.min(end, duration) : duration;
        video.currentTime = Math.max(0, target - 0.05);
      } catch {
        /* 忽略 seek 失败 */
      }
    };
    if (video.readyState >= 1 /* HAVE_METADATA */) seek();
    else video.addEventListener("loadedmetadata", seek, { once: true });
  }

  // ---------- 内部 ----------

  private mount(def: ElementDef): void {
    const el = document.createElement("div");
    el.className = "ns-el ns-el--hidden";
    el.id = `el-${def.id}`;
    el.dataset.el = def.id;
    const m: Mounted = { def, el, view: null, box: def.box, visible: false };
    this.mounted.set(def.id, m);
    this.stage.append(el);
    m.box = this.startBox(def);
    this.applyGeometry(m);
    this.applyCss(m);
    m.view = this.buildContent(el, def.content);
    this.replay(m);
    if (def.visible !== false) this.show(def.id);
  }

  /** 挂载初值：enter 是"淡入到 1"的补间时，起点先压成透明 */
  private startBox(def: ElementDef): Box {
    const s = def.script?.enter;
    if (s && "tween" in s && "opacity" in s.tween) {
      return { ...def.box, opacity: 0 };
    }
    return def.box;
  }

  /** 组件实例里实现了通用状态通道的部分（字幕这类没有 update，返回后不调用即可） */
  private asView(m: Mounted | undefined): ElementView | null {
    return (m?.view ?? null) as ElementView | null;
  }

  private replay(m: Mounted): void {
    const cached = this.states.get(m.def.id);
    if (cached !== undefined) this.asView(m)?.update?.(cached);
  }

  private applyGeometry(m: Mounted): void {
    Object.assign(m.el.style, geometryOf(m.box));
  }

  private applyCss(m: Mounted): void {
    const next = m.def.css ?? {};
    // 清掉换场景后不再声明的覆盖项，否则 inline style 会一直留着上一个场景的值。
    for (const key of this.appliedCss.get(m.el) ?? []) {
      if (!(key in next)) m.el.style.removeProperty(key);
    }
    for (const [k, v] of Object.entries(next)) m.el.style.setProperty(k, v);
    this.appliedCss.set(m.el, Object.keys(next));
  }

  private buildContent(el: HTMLElement, content: Content): ComponentInstance | null {
    delete el.dataset.component;
    switch (content.kind) {
      case "color":
        el.style.background = content.color;
        return null;
      case "image": {
        const img = document.createElement("img");
        img.alt = "";
        img.src = asset(content.src);
        if (el.style.height !== "auto" && el.style.height !== "") {
          img.style.width = "100%";
          img.style.height = "100%";
          img.style.objectFit = content.fit ?? "cover";
        } else {
          img.style.width = "100%";
        }
        el.append(img);
        return null;
      }
      case "video": {
        const v = document.createElement("video");
        v.playsInline = true;
        v.preload = "auto";
        v.muted = content.muted !== false;
        v.loop = !!content.loop;
        if (content.autoplay) v.autoplay = true;
        v.src = asset(content.src);
        v.style.width = "100%";
        v.style.height = "100%";
        v.style.objectFit = "contain";
        el.append(v);
        return null;
      }
      case "text": {
        const t = document.createElement("div");
        t.className = `ns-text${content.class ? ` ${content.class}` : ""}`;
        t.textContent = content.text;
        el.append(t);
        return null;
      }
      case "component": {
        const factory = COMPONENTS[content.name];
        if (!factory) {
          console.warn(`未注册的元素组件: ${content.name}`);
          return null;
        }
        // 组件名是代码拥有的稳定标识：样式按 data-component 挂，不按元素 id
        // （元素 id 属于场景数据，将来可在 dashboard 改名）
        el.dataset.component = content.name;
        return factory(el, content.props ?? {}, this.stage);
      }
    }
  }

  private async runExit(m: Mounted): Promise<void> {
    await this.runScript(m, m.def.script?.exit, "exit");
  }

  private async runScript(
    m: Mounted,
    script: Script | undefined,
    phase: "enter" | "exit" | "move",
  ): Promise<void> {
    if (!script) {
      m.box = m.def.box;
      this.applyGeometry(m);
      if (phase === "exit") this.hide(m.def.id);
      else this.show(m.def.id);
      return;
    }
    if ("action" in script) return this.runActionScript(m, script);
    const target: Box = { ...m.def.box, ...script.tween };
    if (phase !== "exit") this.show(m.def.id);
    await this.animateTo(m, target, script.duration, script.easing ?? "ease");
    if (phase === "exit") this.hide(m.def.id);
  }

  /** 命名动作自己负责起点与落点（如 avatar.rise_in 读元素当前 bottom 作为终点） */
  private async runActionScript(
    m: Mounted,
    script: { action: string; params?: Record<string, unknown> },
  ): Promise<void> {
    m.box = m.def.box;
    this.applyGeometry(m);
    this.show(m.def.id);
    await runAction(script.action, m.el, script.params);
  }

  private animateTo(m: Mounted, target: Box, duration: number, easing: string): Promise<void> {
    const from = geometryOf(m.box);
    const to = geometryOf(target);
    const pick = (s: Partial<CSSStyleDeclaration>) => ({
      left: s.left,
      top: s.top,
      right: s.right,
      bottom: s.bottom,
      width: s.width,
      height: s.height,
      opacity: s.opacity,
      transform: s.transform,
    });
    m.box = target;
    if (duration <= 0) {
      this.applyGeometry(m);
      return Promise.resolve();
    }
    const anim = m.el.animate([pick(from), pick(to)], { duration, easing, fill: "forwards" });
    return anim.finished.then(() => {
      this.applyGeometry(m);
      anim.cancel();
    });
  }
}
