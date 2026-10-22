/** 元素组件注册表（neuro 窗口）：场景数据里 `content.kind === "component"` 的 name 查这张表。
 *
 * 约定与 `stream/ui/src/core/components.ts` 一致：
 * - 组件只负责**自己那一层**的内部排版与动画；位置/大小/层级由场景数据决定（引擎写 inline style）。
 * - 元素根节点带 `container-type: inline-size`，组件**内部**尺寸用 `cqi`；但写在元素自身
 *   那一层的属性会解析到祖先容器（舞台）——字幕要的正是这个（见 neuro.css）。
 * - `update(payload)` 是通用元素状态通道；实时管线类组件（字幕）由 main.ts 直接拿实例调方法。
 */

import { CaptionPlayer } from "./speech";
import type { Box } from "../scenes/schema";

export interface ElementView {
  /** 全量状态快照 */
  update?(payload: unknown): void;
  /** 场景改了本元素的几何 */
  resize?(box: Box): void;
  dispose?(): void;
}

/** 组件实例：实现 ElementView 的走通用状态通道；实时管线类（字幕）只暴露自有方法。 */
export type ComponentInstance = object;

export type ComponentFactory = (
  el: HTMLElement,
  props: Record<string, unknown>,
  stage: HTMLElement,
) => ComponentInstance;

export const COMPONENTS: Record<string, ComponentFactory> = {
  caption: (el, _props, stage) => new CaptionPlayer(el, stage),
};
