/** 元素组件注册表：场景数据里 `content.kind === "component"` 的 name 查这张表。
 *
 * 约定：
 * - 组件只负责**自己那一层**的内部排版与动画；位置/大小/层级由场景数据决定（引擎写 inline style）。
 * - 元素根节点带 `container-type: inline-size`，所以组件**内部**尺寸用 `cqi`（= 自身宽度百分比）
 *   表达，场景把元素改大改小，里面的字号会跟着缩放。注意元素自身那一层的属性用不到自己的
 *   容器单位（会解析到祖先容器 = 舞台），要随元素宽度缩放的字号必须写在子节点上
 *   （见 stream.css 的 `.chat-lines`）。
 * - `update(payload)` 是通用元素状态通道（幂等全量快照）；实时管线类组件（chat/caption/highlight）
 *   由 main.ts 直接拿实例调方法，不必走 update。
 */

import { ChatOverlay, HighlightOverlay } from "./chat";
import { ChessBoardView } from "./chess";
import { CaptionPlayer } from "./speech";
import { WordleGameView } from "./wordle";
import type { Box } from "../scenes/schema";

export interface ElementView {
  /** 全量状态快照（element_state 事件 / snapshot.elements） */
  update?(payload: unknown): void;
  /** 场景改了本元素的几何 */
  resize?(box: Box): void;
  dispose?(): void;
}

/** 组件实例：实现 ElementView 的走通用状态通道；实时管线类（chat/caption/highlight）
 *  只暴露自有方法，由 main.ts 用 `engine.view<T>(id)` 断言后直接调。 */
export type ComponentInstance = object;

export type ComponentFactory = (el: HTMLElement, props: Record<string, unknown>, stage: HTMLElement) => ComponentInstance;

export const COMPONENTS: Record<string, ComponentFactory> = {
  "chat.overlay": (el) => new ChatOverlay(el),
  "caption": (el, _props, stage) => new CaptionPlayer(el, stage),
  "highlight": (el) => new HighlightOverlay(el),
  "wordle.game": (el) => new WordleGameView(el),
  "chess.board": (el) => new ChessBoardView(el),
};
