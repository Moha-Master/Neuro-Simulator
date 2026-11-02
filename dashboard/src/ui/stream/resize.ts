/** 元素缩放约束（stream 合成器）。
 *
 * 约束是**行为**而不是几何，所以放在元素级字段 `resize`，不塞进 `box`：
 * - `free`：8 个手柄都可拖；
 * - `ratio`：只给四个角，等比缩放（像素空间比例锁）；
 * - `width`：只给左右边手柄（高度由内容决定）；
 * - `none`：不给手柄，只允许移动。
 *
 * 组件默认值归代码（注册表旁），元素可用 `resize` 覆盖；内建内容类型默认 free。
 * 修改这里要同步 `scene_kit.check_elements` 的枚举校验与 Studio 下拉。
 */

import type { Content, ElementDef } from '../engine/schema'

export type ResizeConstraint = 'free' | 'ratio' | 'width' | 'none'

export const RESIZE_CONSTRAINTS: ResizeConstraint[] = ['free', 'ratio', 'width', 'none']

/** 组件默认约束（组件名 → 约束）。 */
export const COMPONENT_RESIZE: Record<string, ResizeConstraint> = {
  'chat.overlay': 'free',
  'wordle.game': 'ratio',
  'chess.board': 'ratio',
}

/** 内建内容类型默认约束。iframe 需要显式宽高，但页面没有固定比例，默认 free。 */
export function contentResize (content: Content | undefined): ResizeConstraint {
  if (!content) {
    return 'free'
  }
  if (content.kind === 'component') {
    return COMPONENT_RESIZE[content.name] ?? 'free'
  }
  return 'free'
}

/** 元素实际生效的约束：元素覆盖优先，其次组件/内容默认。 */
export function resolveResize (def: ElementDef): ResizeConstraint {
  return def.resize ?? contentResize(def.content)
}
