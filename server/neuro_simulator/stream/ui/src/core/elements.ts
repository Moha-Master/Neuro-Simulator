/** 元素引擎：把"场景 = 元素清单"（scenes/schema.ts）落成真实的 DOM 与生命周期。
 *
 * - 几何全部以舞台比例声明，引擎写成 inline style（left/top/bottom/width/height/z-index/transform）。
 * - **同 id 跨场景复用同一个 DOM 节点与同一个组件实例**：切场景只做 diff
 *   （离场 → 跑 exit 后隐藏、不销毁；进场 → 挂载并跑 enter；两边都有且几何变了 → 跑 move）。
 *   不销毁是有意为之：聊天历史、字幕队列、游戏动画状态都要跨场景活着。
 * - 默认无过渡（不写脚本 = 直接出现 / 直接落位）；要淡入或平滑移动就在场景数据里写脚本。
 * - `update(id, payload)` 是通用元素状态通道（stream 的 element_state 事件 + snapshot.elements），
 *   引擎缓存每层最新 payload，组件首次挂载时立刻回放，中途连接也能渲染出当前状态。
 */

import { asset } from "./assets";
import { runAction } from "./action";
import { COMPONENTS, type ComponentInstance, type ElementView } from "./components";
import type { Box, Content, ElementDef, SceneDef, Script } from "../scenes/schema";

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

  /** 元素根节点（intro 序列要拿里面的 <video>） */
  element(id: string): HTMLElement | null {
    return this.mounted.get(id)?.el ?? null;
  }

  /** 组件实例（main.ts 用它驱动 chat / caption / highlight 的实时管线） */
  view<T>(id: string): T | null {
    return (this.mounted.get(id)?.view ?? null) as T | null;
  }

  visible(id: string): boolean {
    return this.mounted.get(id)?.visible ?? false;
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

  /** 手动显隐（开播序列的开场视频用） */
  show(id: string): void {
    const m = this.mounted.get(id);
    if (!m) return;
    m.visible = true;
    m.el.classList.remove("ns-el--hidden");
  }

  hide(id: string): void {
    const m = this.mounted.get(id);
    if (!m) return;
    m.visible = false;
    m.el.classList.add("ns-el--hidden");
  }

  /** 对某元素跑命名动作（开播序列的 avatar.rise_in 用） */
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
   * - `enter`：本次切换里指定元素改用的入场动作（元素 id → ACTIONS 名），
   *   开播序列就靠它把 `avatar.rise_in` 挂到 room 场景的立绘上。
   * - `awaitEnter`：需要等待完成的入场动作（其余过渡并发跑完即可，不阻塞状态机）。
   * - `keepVisible`：本次切换**不要**自动隐藏的 `visible: false` 元素（开播序列要让末帧
   *   一直挂到立绘到位的那一瞬间，由序列自己收尾）。
   * - `instant`：跳过所有过渡（snapshot 校正状态时用，避免每次重连都淡入一遍）。
   */
  async setScene(
    id: string,
    opts?: {
      enter?: Record<string, string>;
      awaitEnter?: string[];
      keepVisible?: string[];
      instant?: boolean;
    },
  ): Promise<void> {
    const def = this.defs.get(id);
    if (!def) throw new Error(`未知场景: ${id}`);
    this.current = def;
    const want = new Map(def.elements.map((e) => [e.id, e]));
    const pending: Promise<void>[] = [];
    const schedule = (eid: string, p: Promise<void>): void => {
      if (opts?.awaitEnter?.includes(eid)) void pending.push(p);
      else p.catch((e) => console.warn(`元素过渡失败: ${eid}`, e));
    };

    // 1) 离场：跑 exit 脚本后隐藏（不销毁，实例与内部状态留着）
    for (const [eid, m] of [...this.mounted]) {
      if (want.has(eid) || !m.visible) continue;
      if (opts?.instant) this.hide(eid);
      else schedule(eid, this.runExit(m));
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
      // 场景数据里的 visible 是权威：平时藏着的元素（开场视频、待机场景的立绘）
      // 不能因为"两边都列出了同一 id"就被复活。
      if (e.visible === false) {
        if (m.visible && !opts?.keepVisible?.includes(e.id)) schedule(e.id, this.runExit(m));
        continue;
      }
      const forced = opts?.enter?.[e.id];
      if (forced) {
        schedule(e.id, this.runActionScript(m, { action: forced }));
      } else if (!m.visible) {
        schedule(e.id, this.runScript(m, e.script?.enter, "enter"));
      } else if (moved) {
        schedule(e.id, this.runScript(m, e.script?.move, "move"));
      }
    }
    await Promise.all(pending);
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

  /** 组件实例里实现了通用状态通道的部分（chat/caption 等没有 update，返回后不调用即可） */
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
    for (const [k, v] of Object.entries(m.def.css ?? {})) m.el.style.setProperty(k, v);
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
