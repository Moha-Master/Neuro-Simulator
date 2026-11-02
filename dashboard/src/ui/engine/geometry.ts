/** 几何工具：box（舞台比例）↔ rect（舞台局部 CSS 像素）的纯函数。
 *
 * 没有 DOM 依赖，编辑层（`ui/stream/edit.ts`）与将来的缩放逻辑共用同一套数学，
 * 避免"预览一套、落库另一套"的漂移。
 *
 * 坐标语义：
 * - `box` 是**舞台比例**（`x/y/w/h` 0..1，可负 = 沉出画面）；
 *   `x/y` 是**边缘锚点**：默认左上，`edge.x:"right"` / `edge.y:"bottom"` 时
 *   表示右/下边到舞台右/下的距离。`w/h` 省略 = 内容自身尺寸（auto）。
 * - `rect` 是**舞台局部 CSS 像素**：`x/y` 为左上角，`w/h` 为布局尺寸；
 *   不含 `scale/rotate`（那是视觉变换，命中测试另算 AABB）。
 * - 吸附阈值一律按**像素**比较（`SNAP_TOL_PX`）：舞台在 Studio 里被 letterbox
 *   缩小后，换算成比例会让手感变差，所以只有落库时才回到比例。
 */

import type { Box } from './schema'

/** 舞台局部矩形（CSS 像素，不含 transform）。 */
export interface Rect {
  x: number
  y: number
  w: number
  h: number
}

/** `w/h` 为 auto 时由调用方传入的实测尺寸（offsetWidth/offsetHeight）。 */
export interface MeasuredSize {
  w: number
  h: number
}

/** 吸附目标：舞台或其它可见元素的某条边/中线。 */
export interface SnapTarget {
  axis: 'x' | 'y'
  /** 舞台局部像素位置 */
  posPx: number
  /** 平手时元素优先于舞台（与 OBS 手感一致） */
  source: 'stage' | 'element'
}

/** 参考线：轴 + 舞台局部像素位置（overlay 据此画 1px 贯穿线）。 */
export interface SnapGuide {
  axis: 'x' | 'y'
  posPx: number
}

export interface SnapResult {
  /** 需要附加到拖拽位移上的吸附修正量（px） */
  dx: number
  dy: number
  guides: SnapGuide[]
}

export type ResizeHandle = 'nw' | 'n' | 'ne' | 'e' | 'se' | 's' | 'sw' | 'w'

/** 吸附阈值（CSS 像素）。拖动时按住 Alt 可临时关闭。 */
export const SNAP_TOL_PX = 8

const round4 = (v: number): number => Math.round(v * 1e4) / 1e4

/** box 比例 → 舞台局部 rect。
 *
 * - `edge.x === 'right'`：`x` 是右边缘到舞台右边的距离 → `left = W - x*W - w`；
 * - `edge.y === 'bottom'`：`y` 是下边缘到舞台底边的距离 → `top = H - y*H - h`；
 * - `w/h` 省略时用 `measured`（元素 offset 尺寸）；没有实测值按 0 处理。
 */
export function boxToRect (
  box: Box,
  stageW: number,
  stageH: number,
  measured?: MeasuredSize,
): Rect {
  const w = box.w === undefined ? (measured?.w ?? 0) : box.w * stageW
  const h = box.h === undefined ? (measured?.h ?? 0) : box.h * stageH
  const x = box.edge?.x === 'right'
    ? stageW - box.x * stageW - w
    : box.x * stageW
  const y = box.edge?.y === 'bottom'
    ? stageH - box.y * stageH - h
    : box.y * stageH
  return { x, y, w, h }
}

/** 拖拽（平移）：保留 edge，只改 x/y。
 *
 * 右锚 `x' = x - dx/W`、下锚 `y' = y - dy/H`；其余字段（w/h/scale/rotate/opacity）
 * 原样保留。结果按 1e-4 取整（落库精度，避免浮点尾巴）。
 */
export function applyDrag (
  box: Box,
  dxPx: number,
  dyPx: number,
  stageW: number,
  stageH: number,
): Box {
  const next: Box = { ...box }
  if (stageW <= 0 || stageH <= 0) {
    return next
  }
  const dx = dxPx / stageW
  const dy = dyPx / stageH
  next.x = round4(box.edge?.x === 'right' ? box.x - dx : box.x + dx)
  next.y = round4(box.edge?.y === 'bottom' ? box.y - dy : box.y + dy)
  return next
}

export interface ResizeOptions {
  /** 最小布局尺寸（CSS 像素），防止拖成 0 或负值。 */
  minPx?: number
  /** 像素空间等比锁：`aspect = 起始宽 / 起始高`（两者都是屏幕像素）。 */
  ratio?: boolean
}

/** 缩放：8 方向手柄，**拖手柄即把锚点规范化为左上**。
 *
 * - 起点用 `boxToRect(box, W, H, measured)`：`edge` 的右/下锚按实测 rect 换算成
 *   left/top；`w/h` 为 auto 时用调用方传入的 offset 尺寸**冻结**成比例值。
 * - 结果总是 `{x,y,w,h}`，不再带 `edge`（规范化后的语义就是左上锚）。
 * - 比例锁必须在**像素空间**换算：`aspect = 起始 w / 起始 h`（都是 px），
 *   不能用 `box.w / box.h`（宽按舞台宽、高按舞台高，单位不同）。
 */
export function applyResize (
  box: Box,
  handle: ResizeHandle,
  dxPx: number,
  dyPx: number,
  stageW: number,
  stageH: number,
  measured?: MeasuredSize,
  opts: ResizeOptions = {},
): Box {
  const start = boxToRect(box, stageW, stageH, measured)
  const minPx = Math.max(1, opts.minPx ?? 8)
  if (stageW <= 0 || stageH <= 0) {
    return { ...box }
  }

  const west = handle.includes('w')
  const east = handle.includes('e')
  const north = handle.includes('n')
  const south = handle.includes('s')

  // 起始边（像素）
  let left = start.x
  let top = start.y
  let right = start.x + start.w
  let bottom = start.y + start.h

  if (west) {
    left = start.x + dxPx
  }
  if (east) {
    right = start.x + start.w + dxPx
  }
  if (north) {
    top = start.y + dyPx
  }
  if (south) {
    bottom = start.y + start.h + dyPx
  }

  // 先按最小尺寸夹住被拖的边，避免负宽高
  if (right - left < minPx) {
    if (west) {
      left = right - minPx
    } else {
      right = left + minPx
    }
  }
  if (bottom - top < minPx) {
    if (north) {
      top = bottom - minPx
    } else {
      bottom = top + minPx
    }
  }

  let w = right - left
  let h = bottom - top

  if (opts.ratio && start.w > 0 && start.h > 0) {
    const aspect = start.w / start.h
    const corner = (west || east) && (north || south)
    if (corner) {
      // 角手柄：取"变化更明显"的那个轴作为驱动，避免斜拖时抖动
      const widthDriven = Math.abs(dxPx) >= Math.abs(dyPx) * aspect
      if (widthDriven) {
        w = Math.max(minPx, w)
        h = w / aspect
      } else {
        h = Math.max(minPx, h)
        w = h * aspect
      }
    } else if (west || east) {
      // 只拖左右边：高度跟随宽度（锚在起始上边）
      w = Math.max(minPx, w)
      h = w / aspect
    } else {
      // 只拖上下边：宽度跟随高度（锚在起始左边）
      h = Math.max(minPx, h)
      w = h * aspect
    }
    // 根据被拖的方向重新贴边
    if (west) {
      left = right - w
    } else {
      right = left + w
    }
    if (north) {
      top = bottom - h
    } else {
      bottom = top + h
    }
  }

  const next: Box = {
    x: round4(left / stageW),
    y: round4(top / stageH),
    w: round4(w / stageW),
    h: round4(h / stageH),
  }
  // 缩放后锚点已是左上：保留视觉变换，但删除 edge
  if (box.scale !== undefined) {
    next.scale = box.scale
  }
  if (box.rotate !== undefined) {
    next.rotate = box.rotate
  }
  if (box.opacity !== undefined) {
    next.opacity = box.opacity
  }
  return next
}

/** 吸附目标集：舞台的左/中/右、上/中/下 + 其它可见元素的边/中线。
 *
 * 调用方负责排除被拖元素自身与隐藏元素（隐藏元素不参与目标集）。
 */
export function snapTargets (
  stageW: number,
  stageH: number,
  others: Rect[] = [],
): SnapTarget[] {
  const targets: SnapTarget[] = [
    { axis: 'x', posPx: 0, source: 'stage' },
    { axis: 'x', posPx: stageW / 2, source: 'stage' },
    { axis: 'x', posPx: stageW, source: 'stage' },
    { axis: 'y', posPx: 0, source: 'stage' },
    { axis: 'y', posPx: stageH / 2, source: 'stage' },
    { axis: 'y', posPx: stageH, source: 'stage' },
  ]
  for (const rect of others) {
    targets.push(
      { axis: 'x', posPx: rect.x, source: 'element' },
      { axis: 'x', posPx: rect.x + rect.w / 2, source: 'element' },
      { axis: 'x', posPx: rect.x + rect.w, source: 'element' },
      { axis: 'y', posPx: rect.y, source: 'element' },
      { axis: 'y', posPx: rect.y + rect.h / 2, source: 'element' },
      { axis: 'y', posPx: rect.y + rect.h, source: 'element' },
    )
  }
  return targets
}

/** 计算吸附：把 `rect` 的左/中/右与上/中/下分别对准最近的 target。
 *
 * - 多命中取像素距离最小；平手优先元素对齐，再舞台；
 * - 两个轴独立计算，每个轴最多一条参考线；
 * - 未命中返回 dx/dy = 0、guides 为空。
 */
export function computeSnap (
  rect: Rect,
  targets: SnapTarget[],
  tolPx: number = SNAP_TOL_PX,
): SnapResult {
  const xPositions = [rect.x, rect.x + rect.w / 2, rect.x + rect.w]
  const yPositions = [rect.y, rect.y + rect.h / 2, rect.y + rect.h]
  let bestX: { delta: number, pos: number, dist: number, element: boolean } | null = null
  let bestY: { delta: number, pos: number, dist: number, element: boolean } | null = null

  for (const target of targets) {
    const positions = target.axis === 'x' ? xPositions : yPositions
    for (const pos of positions) {
      const delta = target.posPx - pos
      const dist = Math.abs(delta)
      if (dist > tolPx) {
        continue
      }
      const element = target.source === 'element'
      const best = target.axis === 'x' ? bestX : bestY
      const better
        = !best
        || dist < best.dist - 1e-6
        || (Math.abs(dist - best.dist) <= 1e-6 && element && !best.element)
      if (better) {
        const next = { delta, pos: target.posPx, dist, element }
        if (target.axis === 'x') {
          bestX = next
        } else {
          bestY = next
        }
      }
    }
  }

  const guides: SnapGuide[] = []
  if (bestX) {
    guides.push({ axis: 'x', posPx: bestX.pos })
  }
  if (bestY) {
    guides.push({ axis: 'y', posPx: bestY.pos })
  }
  return {
    dx: bestX?.delta ?? 0,
    dy: bestY?.delta ?? 0,
    guides,
  }
}
