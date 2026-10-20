/** 场景声明格式（纯数据，可序列化为 JSON / 未来直接进 DB 由 dashboard 编辑）。
 *
 * 模型：**场景 = 元素清单**。每个元素有稳定 `id`、内容（图片/视频/纯色/文本/组件）、
 * 相对舞台的比例化几何、层级、以及生命周期脚本（出现/消失/跨场景移动）。
 *
 * 关键约定：
 * - **同 id 跨场景 = 同一个 DOM 节点与同一个组件实例**，所以"立绘从旧位置平滑移动到
 *   新位置"只是给它写 `script.move`；实例不销毁也保住了聊天历史与字幕队列。
 * - 默认**无过渡**：不写脚本就是直接出现 / 直接落位（游戏背景这类"突显叠加"要的正是这个）。
 * - 元素内部排版用自身宽度作 container query 单位（引擎给每个元素加
 *   `container-type: inline-size`），所以场景改元素的大小，元素里的字号会跟着缩。
 * - `scale` / `rotate` 会写进 `transform`，与内部自己动 transform 的命名动作
 *   （avatar.spin / zoom_in）互斥；需要放大用 `w`，别用 scale。
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

export interface ElementDef {
  id: string;
  name?: string;
  content: Content;
  box: Box;
  z: number;
  /** 默认 true；false = 挂载但隐藏（如开场视频平时不显示，intro 序列里 show()） */
  visible?: boolean;
  /** 少量样式覆盖（filter / mix-blend-mode 等），键为 CSS 属性名 */
  css?: Record<string, string>;
  script?: { enter?: Script; exit?: Script; move?: Script };
}

export interface SceneDef {
  id: string;
  label: string;
  /** dashboard 按钮图标（mdi 名） */
  icon?: string;
  /** 注入 LLM 的本场景说明；空 = 回落到 config 的 stream.scene_context */
  prompt_context?: string;
  elements: ElementDef[];
}

/** scenes.json 的顶层结构 */
export interface SceneManifest {
  version: number;
  scenes: SceneDef[];
}
