/** 场景声明格式（纯数据，可序列化为 JSON / 未来直接进 DB 由 dashboard 编辑）。
 *
 * 模型：**场景 = 元素清单 + 可选固定时长脚本 + neuro 窗口层**。每个元素有稳定
 * `id`、内容（图片/视频/纯色/文本/组件）、相对舞台的比例化几何、层级、以及
 * 生命周期脚本（出现/消失/跨场景移动）；场景可声明 `timeline`，由引擎按秒执行。
 * neuro 窗口（背景/立绘/字幕）是单独的一层（`neuro`），由整窗 iframe 承载，
 * 合成器只对它做 translate+scale。
 *
 * 关键约定：
 * - **同 id 跨场景 = 同一个 DOM 节点与同一个组件实例**，所以"聊天层从旧位置平滑移动
 *   到新位置"只是给它写 `script.move`；实例不销毁也保住了聊天历史。
 * - 默认**无过渡**：不写脚本就是直接出现 / 直接落位（游戏背景这类"突显叠加"要的正是这个）。
 * - 元素内部排版用自身宽度作 container query 单位（引擎给每个元素加
 *   `container-type: inline-size`），所以场景改元素的大小，元素里的字号会跟着缩。
 * - `scale` / `rotate` 会写进 `transform`，与内部自己动 transform 的动作互斥；
 *   需要放大用 `w`，别用 scale。
 */

/** 相对舞台的几何：x/y 是边缘锚点（默认左上），数值为舞台宽/高的比例（0..1，可超出=沉出画面）。 */
export interface Box {
  x: number;
  y: number;
  /** 宽/高占舞台的比例；h 省略时由内容自身比例决定（图片、字幕、弹窗常用） */
  w?: number;
  h?: number;
  /** y 的锚边：top（默认）= 舞台顶到元素顶；bottom = 舞台底到元素底（可为负，立绘沉在画面外） */
  edge?: { x?: "left" | "right"; y?: "top" | "bottom" };
  scale?: number;
  rotate?: number;
  opacity?: number;
}

export type Content =
  | { kind: "color"; color: string }
  | { kind: "image"; src: string; fit?: "cover" | "contain" | "fill" }
  | { kind: "video"; src: string; muted?: boolean; loop?: boolean; autoplay?: boolean }
  | { kind: "text"; text: string; class?: string }
  /** name 查 COMPONENTS 注册表；props 透传给组件工厂 */
  | { kind: "component"; name: string; props?: Record<string, unknown> };

/** 脚本：命名动作（查 ACTIONS）或几何/透明度补间。缺省 = 瞬时。 */
export type Script =
  | { action: string; params?: Record<string, unknown> }
  | {
      tween: Partial<Pick<Box, "x" | "y" | "w" | "h" | "scale" | "rotate" | "opacity">>;
      duration: number;
      easing?: string;
    };

/** 场景时间线的一步：进入场景后第 `at` 秒对 `element` 执行动作。
 *
 * `duration` 是动作占用的秒数（视频播放、补间等）；它既是场景总时长的一部分，
 * 也是中途连接时判断“该步已播到哪”的依据。瞬时动作可省略（0）。
 * 内置动作：`element.show` / `element.hide` / `media.play` / `media.pause`；
 * 其余按名字查 `core/action.ts` 的 ACTIONS。
 */
export interface TimelineStep {
  at: number;
  element: string;
  action: string;
  params?: Record<string, unknown>;
  duration?: number;
}

/** 场景脚本：固定时长 + 从进入场景开始计时的动作时间线。
 *
 * 当前没有内置场景使用（开播 intro 已移到 neuro 窗口）；保留给未来的固定时长
 * 场景。前端只按这份数据渲染；某个画面卡住不会反过来影响后端。
 */
export interface SceneTimeline {
  duration: number;
  steps: TimelineStep[];
}

export interface ElementDef {
  id: string;
  name?: string;
  content: Content;
  box: Box;
  z: number;
  /** 默认 true；false = 挂载但隐藏（如开场视频平时不显示，由场景时间线 show()） */
  visible?: boolean;
  /** 少量样式覆盖（filter / mix-blend-mode 等），键为 CSS 属性名 */
  css?: Record<string, string>;
  script?: { enter?: Script; exit?: Script; move?: Script };
}

/** neuro 窗口层（iframe）的场景配置：整窗只有 translate+scale，不进入内部。
 *
 * - x/y/w 是相对舞台的比例；w 同时决定整窗缩放（16:9 画布，height 自然为 w）。
 * - hide 是让 neuro 窗口隐藏的层 id（目前主要是 bg_room；媒体场景要露出合成器背景）。
 * - hidden = 本场景完全不显示 neuro（offline/no-source）。
 */
export interface NeuroLayerConfig {
  x: number;
  y: number;
  w: number;
  z?: number;
  hide?: string[];
  hidden?: boolean;
  edge?: { x?: "left" | "right"; y?: "top" | "bottom" };
}

export interface SceneDef {
  id: string;
  label: string;
  /** dashboard 按钮图标（mdi 名） */
  icon?: string;
  /** 注入 LLM 的本场景说明；空 = 回落到 config 的 stream.scene_context */
  prompt_context?: string;
  /** 默认 false；true = 只能程序化调用（on_scene），不出现在 dashboard /
   *  GET /stream/scenes，且 POST /scene 拒绝。starting_soon 这类内部场景设 true。 */
  hidden?: boolean;
  /** 固定时长的场景脚本；当前没有内置场景使用（intro 已移到 neuro 窗口） */
  timeline?: SceneTimeline;
  /** neuro 窗口层；缺省 = 本场景不显示 neuro */
  neuro?: NeuroLayerConfig;
  elements: ElementDef[];
}

/** scenes.json 的顶层结构 */
export interface SceneManifest {
  version: number;
  scenes: SceneDef[];
}
