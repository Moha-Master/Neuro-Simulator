/** 元素组件注册表（neuro 窗口）：场景数据里 `content.kind === "component"` 的 name 查这张表。
 *
 * 约定与 `src/ui/stream/components.ts` 一致：
 * - 组件只负责**自己那一层**的内部排版与动画；位置/大小/层级由场景数据决定（引擎写 inline style）。
 * - 元素根节点带 `container-type: inline-size`，组件**内部**尺寸用 `cqi`；但写在元素自身
 *   那一层的属性会解析到祖先容器（舞台）——字幕要的正是这个（见 neuro.css）。
 * - `update(payload)` 是通用元素状态通道；实时管线类组件（字幕、高亮消息）由
 *   main.ts 直接拿实例调方法。
 */

import type { Box } from '../engine/schema'
import type { ComponentFactory } from '../engine/types'
import { HighlightOverlay } from './highlight'
import { CaptionPlayer } from './speech'

export const COMPONENTS: Record<string, ComponentFactory> = {
  caption: (el, _props, stage) => new CaptionPlayer(el, stage),
  highlight: el => new HighlightOverlay(el),
}
