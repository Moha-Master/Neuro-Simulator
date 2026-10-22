/** 场景声明格式（纯数据，可序列化为 JSON / 未来直接进 DB 由 dashboard 编辑）。
 *
 * 模型：**场景 = 元素清单 + 前端脚本 + 可选后端脚本 + neuro 窗口层**。每个元素有
 * 稳定 `id`、内容（图片/视频/纯色/文本/组件）、相对舞台的比例化几何、层级、以及
 * 生命周期脚本（出现/消失/跨场景移动）。
 *
 * **一份数据、两个执行者**（服务端 `stream/scenes.py` 加载时校验这条边界）：
 * - `timeline`（前端脚本）：由各渲染器的元素引擎按秒执行，只动 DOM；
 * - `state_script`（后端脚本）：由服务端 `scene_kit.SceneRunner` 执行，只改权威
 *   状态（`scene.set`），改完广播 `scene_set`，所有渲染器按 `elapsed` 收敛。
 *
 * 关键约定：
 * - **同 id 跨场景 = 同一个 DOM 节点与同一个组件实例**，所以"聊天层从旧位置平滑移动
 *   到新位置"只是给它写 `script.move`；实例不销毁也保住了聊天历史。
 * - 默认**无过渡**：不写脚本就是直接出现 / 直接落位（游戏背景这类"突显叠加"要的正是这个）。
 * - 元素内部排版用自身宽度作 container query 单位（引擎给每个元素加
 *   `container-type: inline-size`），所以场景改元素的大小，元素里的字号会跟着缩。
 * - `scale` / `rotate` 会写进 `transform`，与内部自己动 transform 的动作互斥；
 *   需要放大用 `w`，别用 scale。
 * - 元素引擎（`core/elements.ts`）与 neuro 窗口那份**逐字相同**，有测试防漂移。
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

/** 前端脚本的一步：`at`（或 `at_end`）对 `element` 执行动作。
 *
 * `duration` 是动作占用的秒数（视频播放、补间等），也是中途连接时判断
 * "该步已播到哪"的依据；`media.play` 省略它 = 播到场景结束。瞬时动作可省略（0）。
 * 内置动作：`element.show` / `element.hide` / `media.play` / `media.pause`；
 * 其余按名字查 `core/action.ts` 的 ACTIONS。**这里不能写 scene.set**。
 */
export interface TimelineStep {
  at?: number;
  /** 相对场景有效时长的提前量：at = duration - at_end */
  at_end?: number;
  element: string;
  action: string;
  params?: Record<string, unknown>;
  duration?: number;
}

/** 前端脚本：从进入场景开始计时的 DOM 时间线。 */
export interface SceneTimeline {
  /** 数据声明的时长（回退值）；宿主可以用探测值覆盖（`setScene(id,{duration})`） */
  duration?: number;
  steps: TimelineStep[];
}

/** 后端脚本的一步：只允许状态动作（服务端校验），由后端按自己的时钟执行。 */
export interface StateStep {
  at?: number;
  at_end?: number;
  action: "scene.set";
  scene: string;
  duration?: number;
}

export interface StateScript {
  steps: StateStep[];
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
 * - hide 是让 neuro 窗口隐藏的**层 id** 列表（neuro 加新层不必改合成器代码）。
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
  /** 场景有效时长：秒数，或"按某个 video 元素的轨道时长 + 之后还剩的秒数" */
  duration?: number | { media: string; after: number };
  /** 前端脚本（元素引擎执行）；当前没有内置场景使用 */
  timeline?: SceneTimeline;
  /** 后端脚本（服务端执行，只改状态）；当前没有内置场景使用 */
  state_script?: StateScript;
  /** neuro 窗口层；缺省 = 本场景不显示 neuro */
  neuro?: NeuroLayerConfig;
  elements: ElementDef[];
}

/** 场景清单的顶层结构（stream 与 neuro 窗口共用同一套语法）。
 *
 * - `version`：数据格式版本；
 * - `default_scene` / `refs`：neuro 窗口用（首帧落哪个场景、代码要认出哪些元素 id）；
 *   stream 没有 default_scene（进入哪个场景由直播循环决定）。
 */
export interface SceneManifest {
  version: number;
  /** 进程启动/画面首帧落到的场景（neuro 窗口用） */
  default_scene?: string;
  /** 代码需要认出的元素（工具动作、字幕实例都按这里的 id 取） */
  refs?: { avatar?: string; caption?: string };
  scenes: SceneDef[];
}
