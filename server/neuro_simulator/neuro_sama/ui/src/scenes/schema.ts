/** neuro 窗口的场景声明格式（`public/neuro_scenes.json`，与服务端 scenes.py 同构）。
 *
 * 模型：**场景 = 元素清单 + 可选固定时长脚本**，与 stream 合成器的场景同一套语法
 * （元素引擎是两棵树各自的拷贝，见 `core/elements.ts` 顶部说明）。
 *
 * **一份数据、两个执行者**：
 * - `timeline`（前端脚本）：由本文件的元素引擎按秒执行，只动 DOM；
 * - `state_script`（后端脚本）：由 neuro 服务端执行，只能 `scene.set` 改权威状态。
 *   服务端加载时校验这条边界，所以这里不可能写出"前端偷偷改状态"的场景。
 *
 * 关键约定：
 * - **同 id 跨场景 = 同一个 DOM 节点与同一个组件实例**：字幕队列、视频进度因此
 *   不会因为切场景而丢失（intro → normal 只是换一批层的显隐）。
 * - 默认**无过渡**：不写脚本就是直接出现 / 直接落位。
 * - 元素内部排版用自身宽度作 container query 单位（引擎给每个元素加
 *   `container-type: inline-size`），所以场景改元素的大小，元素里的字号会跟着缩。
 *   字幕的字号写在**元素自身**上，因此解析到祖先容器（舞台），与旧版一致。
 * - `at_end: N` = 场景有效时长 - N（时长可由视频轨道探测，换素材自动跟随，
 *   不必在数据里写死绝对时刻）。
 */

/** 相对舞台的几何：x/y 是边缘锚点（默认左上），数值为舞台宽/高的比例（0..1，可超出=沉出画面）。 */
export interface Box {
  x: number;
  y: number;
  /** 宽/高占舞台的比例；h 省略时由内容自身比例决定（图片、字幕常用） */
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

/** 前端脚本的一步：`at` 或 `at_end`（相对场景有效时长）对 `element` 执行动作。
 *
 * `duration` 是动作占用的秒数（视频播放、补间等），也是中途连接时判断
 * "该步已播到哪"的依据。瞬时动作可省略（0）。
 * 内置动作：`element.show` / `element.hide` / `media.play` / `media.pause`；
 * 其余按名字查 `core/action.ts` 的 ACTIONS。**这里不能写 scene.set**。
 */
export interface TimelineStep {
  at?: number;
  at_end?: number;
  element: string;
  action: string;
  params?: Record<string, unknown>;
  duration?: number;
}

/** 前端脚本：从进入场景开始计时的 DOM 时间线。 */
export interface SceneTimeline {
  /** 数据声明的时长（回退值）；后端下发的探测时长优先 */
  duration?: number;
  steps: TimelineStep[];
}

/** 后端脚本的一步：只允许状态动作（服务端校验），由 neuro 后端按自己的时钟执行。 */
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
  /** 默认 true；false = 挂载但隐藏（如常态场景里的开场视频，由脚本 show 出来） */
  visible?: boolean;
  /** 少量样式覆盖（filter / background / will-change 等），键为 CSS 属性名 */
  css?: Record<string, string>;
  script?: { enter?: Script; exit?: Script; move?: Script };
}

export interface SceneDef {
  id: string;
  label: string;
  /** dashboard 按钮图标（mdi 名） */
  icon?: string;
  /** 默认 false；true = 只在内部使用，不列进 dashboard 手动切换清单 */
  hidden?: boolean;
  /** 场景有效时长：秒数，或"按某个 video 元素的轨道时长 + 之后还剩的秒数" */
  duration?: number | { media: string; after: number };
  /** 前端脚本（本引擎执行） */
  timeline?: SceneTimeline;
  /** 后端脚本（服务端执行，只改状态） */
  state_script?: StateScript;
  elements: ElementDef[];
}

/** neuro_scenes.json 的顶层结构 */
export interface SceneManifest {
  version: number;
  /** 进程启动/画面首帧落到的场景 */
  default_scene: string;
  /** 代码需要认出的元素（工具动作、字幕实例都按这里的 id 取） */
  refs?: { avatar?: string; caption?: string };
  scenes: SceneDef[];
}
