/** 元素组件注册表：场景数据里 `content.kind === "component"` 的 name 查这张表。
 *
 * 约定：
 * - 组件只负责**自己那一层**的内部排版与动画；位置/大小/层级由场景数据决定（引擎写 inline style）。
 * - 元素根节点带 `container-type: inline-size`，所以组件**内部**尺寸用 `cqi`（= 自身宽度百分比）
 *   表达，场景把元素改大改小，里面的字号会跟着缩放。注意元素自身那一层的属性用不到自己的
 *   容器单位（会解析到祖先容器 = 舞台），要随元素宽度缩放的字号必须写在子节点上
 *   （见 stream.css 的 `.chat-lines`）。
 * - `update(payload)` 是通用元素状态通道（幂等全量快照）；实时管线类组件（chat）
 *   由 main.ts 直接拿实例调方法，不必走 update。
 */

import type { Box } from '../engine/schema'
import type { ComponentFactory } from '../engine/types'
import { ChatOverlay } from './chat'
import { ChessBoardView } from './chess'
import { WordleGameView } from './wordle'

export const COMPONENTS: Record<string, ComponentFactory> = {
  'chat.overlay': el => new ChatOverlay(el),
  'wordle.game': el => new WordleGameView(el),
  'chess.board': el => new ChessBoardView(el),
}
