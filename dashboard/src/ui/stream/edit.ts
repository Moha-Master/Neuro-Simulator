/** 画布编辑层（Studio 编辑模式专用，OBS 直挂永不创建）。
 *
 * 设计要点（见 `/tmp/opencode/studio-edit-design.md` 第 2 期）：
 * - 覆盖层是 `#stage` 的绝对子级（inset:0），坐标天然是舞台局部像素；
 * - 只有 `window.parent !== window` 且父页面显式发 `edit {active:true}` 才会激活；
 * - 命中/吸附全部基于 `geometry.ts` 的纯函数，不读 `getBoundingClientRect`
 *   （后者含 scale/rotate，会把数据算歪；这里只对选中框做 AABB）；
 * - 拖动只改覆盖层里选中框的样式，**不碰元素本体 DOM**（避免和运行中的脚本互踩）；
 *   松手才发 `commit`，Studio 写 manifest → 后端广播 → 各画布热替换落位。
 */

import type { Box } from '../engine/schema'
import type { ElementEngine } from '../engine/elements'
import type { Rect, SnapGuide, SnapTarget } from '../engine/geometry'
import { applyDrag, boxToRect, computeSnap, snapTargets } from '../engine/geometry'
import type { NeuroLayer } from './neuro'

const DRAG_THRESHOLD_PX = 2
const PREVIEW_INTERVAL_MS = 50
const PRIMARY = '#3b82f6'

interface Candidate {
  kind: 'element' | 'neuro-window'
  id: string
  z: number
  /** 视觉 AABB（含 scale/rotate；平移与 transform 互不冲突） */
  rect: Rect
  /** 数据里的 box（拖拽结果据此回写；neuro 窗口由场景 neuro 块构造） */
  box: Box
}

interface DragState {
  candidate: Candidate
  sceneId: string
  startX: number
  startY: number
  lastX: number
  lastY: number
  moved: boolean
  /** pointerdown 时算一次的目标集（不随拖动更新） */
  targets: SnapTarget[]
  frame: number | null
  lastPreview: number
  dx: number
  dy: number
}

export interface EditLayerOptions {
  stage: HTMLElement
  engine: ElementEngine
  neuro: () => NeuroLayer | null
  /** 发往父页面（main.ts 负责同源 targetOrigin 与方向） */
  post: (payload: Record<string, unknown>) => void
}

export class EditLayer {
  private stage: HTMLElement
  private engine: ElementEngine
  private neuro: () => NeuroLayer | null
  private post: (payload: Record<string, unknown>) => void

  private root: HTMLElement
  private boxEl: HTMLElement
  private labelEl: HTMLElement
  private guideX: HTMLElement
  private guideY: HTMLElement

  private selected: { kind: Candidate['kind'], id: string } | null = null
  private drag: DragState | null = null
  private altKey = false
  private destroyed = false
  /** 拖动期间收到的热替换刷新：不能中途 cancel 拖动（会吞掉本次 commit），
   *  等松手后再由下一次 manifest_changed 收敛。 */
  private pendingRefresh = false
  private observer: ResizeObserver | null = null

  constructor (opts: EditLayerOptions) {
    this.stage = opts.stage
    this.engine = opts.engine
    this.neuro = opts.neuro
    this.post = opts.post

    this.root = document.createElement('div')
    this.root.className = 'ns-edit-overlay'

    this.boxEl = document.createElement('div')
    this.boxEl.className = 'ns-edit-overlay__box'
    this.boxEl.style.display = 'none'

    this.labelEl = document.createElement('div')
    this.labelEl.className = 'ns-edit-overlay__label'
    this.boxEl.append(this.labelEl)

    this.guideX = document.createElement('div')
    this.guideX.className = 'ns-edit-overlay__guide ns-edit-overlay__guide--x'
    this.guideX.style.display = 'none'
    this.guideY = document.createElement('div')
    this.guideY.className = 'ns-edit-overlay__guide ns-edit-overlay__guide--y'
    this.guideY.style.display = 'none'

    this.root.append(this.guideX, this.guideY, this.boxEl)
    this.stage.append(this.root)

    this.root.addEventListener('pointerdown', this.onPointerDown)
    this.root.addEventListener('pointermove', this.onPointerMove)
    this.root.addEventListener('pointerup', this.onPointerUp)
    this.root.addEventListener('pointercancel', this.onPointerCancel)
    window.addEventListener('keydown', this.onKeyDown)
    window.addEventListener('keyup', this.onKeyUp)
    if (typeof ResizeObserver !== 'undefined') {
      this.observer = new ResizeObserver(() => this.refresh())
      this.observer.observe(this.stage)
    }
  }

  destroy (): void {
    if (this.destroyed) {
      return
    }
    this.destroyed = true
    this.cancelDrag()
    this.clear()
    this.root.remove()
    window.removeEventListener('keydown', this.onKeyDown)
    window.removeEventListener('keyup', this.onKeyUp)
    this.observer?.disconnect()
    this.observer = null
  }

  /** 父页面列表选中 → 画布画选中框（双向同步的另一个方向）。 */
  select (kind: Candidate['kind'], id: string): void {
    if (this.destroyed || !id) {
      this.clear()
      return
    }
    const hit = this.candidates().find(c => c.kind === kind && c.id === id)
    if (!hit) {
      this.clear()
      return
    }
    this.selected = { kind: hit.kind, id: hit.id }
    this.showBox(hit.rect)
  }

  /** 清单热替换 / stage 尺寸变化后刷新选中框；元素被删则清空。 */
  refresh (): void {
    if (this.destroyed) {
      return
    }
    if (this.drag) {
      // 自己上一次 commit 的广播 / 别的编辑者保存都可能在此刻到达；
      // 不能 cancel 当前拖动，否则 pointerup 时 drag 已为空、提交丢失。
      this.pendingRefresh = true
      return
    }
    this.pendingRefresh = false
    if (!this.selected) {
      return
    }
    const hit = this.candidates().find(
      c => c.kind === this.selected?.kind && c.id === this.selected?.id,
    )
    if (!hit) {
      this.clear()
      return
    }
    this.showBox(hit.rect)
  }

  /** 切场景：拖动预览与选中都作废（Studio 也会先发 edit false）。 */
  sceneChanged (): void {
    this.cancelDrag()
    this.pendingRefresh = false
    this.clear()
  }

  clear (): void {
    this.selected = null
    this.boxEl.style.display = 'none'
    this.showGuides([])
  }

  // ---------- 命中 ----------

  private candidates (): Candidate[] {
    const scene = this.engine.current
    if (!scene) {
      return []
    }
    const W = this.stage.clientWidth
    const H = this.stage.clientHeight
    if (W <= 0 || H <= 0) {
      return []
    }
    const out: Candidate[] = []
    for (const def of scene.elements) {
      const el = this.engine.element(def.id)
      // 隐藏（场景 visible:false / 父页面 visibility / 脚本 hide）不参与命中与吸附
      if (!el || !this.engine.visible(def.id)) {
        continue
      }
      const layout = boxToRect(def.box, W, H, {
        w: el.offsetWidth,
        h: el.offsetHeight,
      })
      out.push({
        kind: 'element',
        id: def.id,
        z: def.z ?? 0,
        rect: visualRect(layout, def.box),
        box: def.box,
      })
    }
    const layer = this.neuro()
    const neuroRect = layer?.getRect()
    if (layer && neuroRect && scene.neuro) {
      const cfg = scene.neuro
      out.push({
        kind: 'neuro-window',
        id: 'neuro.window',
        z: layer.getZ(),
        rect: neuroRect,
        // 窗口是 16:9，h = w（与 neuro.ts 的 applyBox 一致）
        box: {
          x: cfg.x,
          y: cfg.y,
          w: cfg.w,
          h: cfg.w,
          ...(cfg.edge ? { edge: { ...cfg.edge } } : {}),
        },
      })
    }
    // 顶部 = 最上层 → z 降序；同 z 保持场景声明顺序
    return out.sort((a, b) => b.z - a.z)
  }

  // ---------- 指针 ----------

  private onPointerDown = (ev: PointerEvent): void => {
    if (ev.button !== 0 || this.destroyed) {
      return
    }
    const point = this.localPoint(ev)
    const hit = this.candidates().find(c => contains(c.rect, point))
    if (!hit) {
      this.clear()
      this.postSelect(null, null)
      return
    }

    this.selected = { kind: hit.kind, id: hit.id }
    this.showBox(hit.rect)
    this.postSelect(hit.kind, hit.id)

    const W = this.stage.clientWidth
    const H = this.stage.clientHeight
    const others = this.candidates()
      .filter(c => !(c.kind === hit.kind && c.id === hit.id))
      .map(c => c.rect)
    this.drag = {
      candidate: hit,
      sceneId: this.engine.current?.id ?? '',
      startX: point.x,
      startY: point.y,
      lastX: point.x,
      lastY: point.y,
      moved: false,
      targets: snapTargets(W, H, others),
      frame: null,
      lastPreview: 0,
      dx: 0,
      dy: 0,
    }
    try {
      this.root.setPointerCapture(ev.pointerId)
    } catch {
      /* 指针捕获失败时仍可用 root 上的 move/up 事件 */
    }
    ev.preventDefault()
  }

  private onPointerMove = (ev: PointerEvent): void => {
    const drag = this.drag
    if (!drag) {
      return
    }
    const point = this.localPoint(ev)
    drag.lastX = point.x
    drag.lastY = point.y
    if (
      Math.abs(point.x - drag.startX) > DRAG_THRESHOLD_PX
      || Math.abs(point.y - drag.startY) > DRAG_THRESHOLD_PX
    ) {
      drag.moved = true
    }
    this.schedule(drag)
  }

  private onPointerUp = (ev: PointerEvent): void => {
    const drag = this.drag
    if (!drag) {
      return
    }
    this.drag = null
    this.pendingRefresh = false
    if (drag.frame !== null) {
      cancelAnimationFrame(drag.frame)
      drag.frame = null
    }
    try {
      this.root.releasePointerCapture(ev.pointerId)
    } catch {
      /* 已释放 */
    }
    if (!drag.moved) {
      // 单击（无位移）：只选中，不落库
      this.showBox(drag.candidate.rect)
      this.showGuides([])
      return
    }
    this.renderDrag(drag)
    const box = this.committedBox(drag)
    this.post({
      ns: 'stream-canvas',
      type: 'commit',
      sceneId: drag.sceneId,
      kind: drag.candidate.kind,
      id: drag.candidate.id,
      box,
    })
  }

  private onPointerCancel = (): void => {
    this.cancelDrag()
    this.clear()
  }

  private onKeyDown = (ev: KeyboardEvent): void => {
    if (ev.key === 'Alt') {
      this.altKey = true
      if (this.drag) {
        this.schedule(this.drag)
      }
      return
    }
    if (ev.key === 'Escape') {
      this.cancelDrag()
      if (this.selected) {
        this.clear()
        this.postSelect(null, null)
      }
    }
  }

  private onKeyUp = (ev: KeyboardEvent): void => {
    if (ev.key === 'Alt') {
      this.altKey = false
      if (this.drag) {
        this.schedule(this.drag)
      }
    }
  }

  private cancelDrag (): void {
    const drag = this.drag
    this.drag = null
    if (drag?.frame != null) {
      cancelAnimationFrame(drag.frame)
    }
    this.showGuides([])
  }

  // ---------- 拖动渲染 ----------

  private schedule (drag: DragState): void {
    if (drag.frame !== null) {
      return
    }
    drag.frame = requestAnimationFrame(() => {
      drag.frame = null
      if (this.drag === drag) {
        this.renderDrag(drag)
      }
    })
  }

  private renderDrag (drag: DragState): void {
    const W = this.stage.clientWidth
    const H = this.stage.clientHeight
    const rawDx = drag.lastX - drag.startX
    const rawDy = drag.lastY - drag.startY
    const moving: Rect = {
      ...drag.candidate.rect,
      x: drag.candidate.rect.x + rawDx,
      y: drag.candidate.rect.y + rawDy,
    }
    let dx = rawDx
    let dy = rawDy
    let guides: SnapGuide[] = []
    if (!this.altKey) {
      const snap = computeSnap(moving, drag.targets)
      dx += snap.dx
      dy += snap.dy
      guides = snap.guides
    }
    drag.dx = dx
    drag.dy = dy
    this.showBox({ ...drag.candidate.rect, x: moving.x + (dx - rawDx), y: moving.y + (dy - rawDy) })
    this.showGuides(guides)
    this.preview(drag, W, H)
  }

  private preview (drag: DragState, W: number, H: number): void {
    const now = performance.now()
    if (now - drag.lastPreview < PREVIEW_INTERVAL_MS) {
      return
    }
    drag.lastPreview = now
    this.post({
      ns: 'stream-canvas',
      type: 'preview',
      sceneId: drag.sceneId,
      kind: drag.candidate.kind,
      id: drag.candidate.id,
      box: applyDrag(drag.candidate.box, drag.dx, drag.dy, W, H),
    })
  }

  private committedBox (drag: DragState): Box {
    return applyDrag(
      drag.candidate.box,
      drag.dx,
      drag.dy,
      this.stage.clientWidth,
      this.stage.clientHeight,
    )
  }

  // ---------- 覆盖层绘制 ----------

  private showBox (rect: Rect): void {
    this.boxEl.style.display = 'block'
    this.boxEl.style.left = `${rect.x}px`
    this.boxEl.style.top = `${rect.y}px`
    this.boxEl.style.width = `${Math.max(0, rect.w)}px`
    this.boxEl.style.height = `${Math.max(0, rect.h)}px`
    this.labelEl.textContent = `${Math.round(rect.w)} × ${Math.round(rect.h)}`
    this.labelEl.style.color = '#fff'
    this.boxEl.style.borderColor = PRIMARY
    this.labelEl.style.background = PRIMARY
  }

  private showGuides (guides: SnapGuide[]): void {
    const x = guides.find(g => g.axis === 'x')
    const y = guides.find(g => g.axis === 'y')
    if (x) {
      this.guideX.style.display = 'block'
      this.guideX.style.left = `${x.posPx}px`
    } else {
      this.guideX.style.display = 'none'
    }
    if (y) {
      this.guideY.style.display = 'block'
      this.guideY.style.top = `${y.posPx}px`
    } else {
      this.guideY.style.display = 'none'
    }
  }

  private postSelect (kind: Candidate['kind'] | null, id: string | null): void {
    this.post({ ns: 'stream-canvas', type: 'select', kind, id })
  }

  private localPoint (ev: PointerEvent): { x: number, y: number } {
    const rect = this.root.getBoundingClientRect()
    return { x: ev.clientX - rect.left, y: ev.clientY - rect.top }
  }
}

function contains (rect: Rect, point: { x: number, y: number }): boolean {
  return (
    point.x >= rect.x
    && point.x <= rect.x + rect.w
    && point.y >= rect.y
    && point.y <= rect.y + rect.h
  )
}

/** scale/rotate 后的轴对齐包围盒（围绕元素中心；transform-origin 默认 50% 50%）。 */
function visualRect (rect: Rect, box: Box): Rect {
  const scale = box.scale ?? 1
  const rotate = box.rotate ?? 0
  if (scale === 1 && rotate === 0) {
    return rect
  }
  const rad = (rotate * Math.PI) / 180
  const cos = Math.abs(Math.cos(rad))
  const sin = Math.abs(Math.sin(rad))
  const w = Math.abs(scale) * (rect.w * cos + rect.h * sin)
  const h = Math.abs(scale) * (rect.w * sin + rect.h * cos)
  const cx = rect.x + rect.w / 2
  const cy = rect.y + rect.h / 2
  return { x: cx - w / 2, y: cy - h / 2, w, h }
}
