/** neuro 窗口（dashboard 构建里的独立页面 `/ui/neuro/`）：数据驱动的场景 + 字幕/音频 + 立绘动作 + 高亮消息浮层。
 *
 * 这一页就是现实中 neuro.exe 的"窗口"：固定 16:9 逻辑画布，只有场景数据里声明的
 * 那些层（背景 / 开场视频 / 立绘 / 字幕 / 高亮消息），不含 chat / 游戏 / 合成——
 * 那些是 stream 合成器的层。它可以单独打开（与直播无关），也可以被合成器用 iframe 捕获。
 *
 * 两条控制权，边界很清楚：
 * - **场景**是后端的权威状态：`scene_set` 事件 / snapshot 告诉你现在该演哪个场景，
 *   本页只如实渲染（含按 `elapsed` 断点续播）。本页没有"切场景"的方法。
 * - **显隐**由父页面按元素 id 下发：`postMessage({ns:"neuro-window",
 *   type:"visibility", hidden:["bg_room"]})`，用于媒体场景露出合成器背景。
 *
 * **独立文档**（不是 dashboard 的 Vue 路由）：背景必须透明（合成器隐藏 bg_room 时
 * 要透出下面的背景），而且 color-scheme 要恒为 dark——iframe 内外色系不一致时
 * Chromium 会强制给 iframe 一个不透明底色。
 *
 * 事件来源有两种模式：
 * - **远程模式**：被 stream 合成器 iframe 捕获时，先发 `ready` + `feed_ready`，
 *   父页面在 500ms 内回 `feed_ready` 即进入远程模式——事件由父页面用 `feed` 帧
 *   提供，本页**不建 EventSource**（整条画面只占合成器那 1 条 SSE）；
 * - **本地模式**：顶层打开（`/ui/neuro/`）或父页面没有确认（`/neuro-view` 预览）
 *   时，500ms 后回退到 `NeuroFeed` 自连两条流。
 */

import type { SceneManifest } from '../engine/schema'

import type { CaptionPlayer, SpeechSeg } from './speech'
import type { HighlightMessage, HighlightOverlay } from './highlight'
import { ACTIONS } from '../engine/action'
import { makeAssetUrl } from '../engine/base'
import { ElementEngine } from '../engine/elements'
import { loadFonts } from './assets'
import { COMPONENTS } from './components'
import {
  type ContentSnapshot,
  type FeedEvent,
  NeuroFeed,
  type SceneInfo,
  type SectionOf,
  type StateFrame,
  type StateSnapshot,
  type TurnSeg,
} from './feed'
import { RemoteFeed } from './remote-feed'
import { fetchScenes } from './scenes'
import './styles/neuro.css'

/** 事件源抽象：本地自连（NeuroFeed）与父页面供帧（RemoteFeed）共用 handler。 */
interface FeedSource {
  onContent (type: string, fn: (ev: FeedEvent) => void): void
  onState (type: string, fn: (ev: FeedEvent) => void): void
  close (): void
}

/** 嵌入父页面时等待它确认 `feed_ready` 的时长；超时回退到本地自连（不白屏）。 */
const FEED_READY_TIMEOUT_MS = 500

export async function bootstrapNeuro (stage: HTMLElement): Promise<void> {
  await loadFonts()

  let manifest: SceneManifest
  try {
    manifest = await fetchScenes()
  } catch (error) {
    console.error('场景清单加载失败，窗口无法渲染', error)
    return
  }

  const refs = manifest.refs ?? {}
  const avatarId = refs.avatar ?? 'avatar'
  const captionId = refs.caption ?? 'caption'
  const highlightId = refs.highlight ?? 'highlight'

  const engine = new ElementEngine(stage, manifest.scenes, {
    components: COMPONENTS,
    actions: ACTIONS,
    assetUrl: makeAssetUrl(),
  })
  // 首帧先按数据落到 default_scene；SSE 的 snapshot 一到就用后端的权威状态纠正。
  const defaultScene = manifest.default_scene ?? manifest.scenes[0]?.id
  if (!defaultScene) {
    console.error('场景清单里没有 default_scene，窗口无法渲染')
    return
  }
  try {
    await engine.setScene(defaultScene, { instant: true })
  } catch (error) {
    console.error(`default_scene 无法渲染: ${defaultScene}`, error)
    return
  }

  // ---------- 字幕实例（跨场景复用同一个元素与实例） ----------

  let player: CaptionPlayer | null = null
  let captionSettings: SectionOf<'caption'> | null = null

  function applyCaptionSettings (): void {
    if (!captionSettings || !player) {
      return
    }
    if (captionSettings.hide_ms !== undefined) {
      player.setHideDelay(Number(captionSettings.hide_ms))
    }
    if (captionSettings.step_gap_ms !== undefined) {
      player.setStepGap(Number(captionSettings.step_gap_ms))
    }
  }

  /** 元素内容变了会换实例（改数据/换组件），这里重新认一次并补设置 */
  function ensurePlayer (): CaptionPlayer | null {
    const view = engine.view<CaptionPlayer>(captionId)
    if (view && view !== player) {
      player = view
      player.onIdle = () => resetZoom()
      applyCaptionSettings()
    }
    return player
  }
  ensurePlayer()

  // ---------- 高亮消息浮层（事件驱动；场景开关决定是否显示） ----------

  let highlight: HighlightOverlay | null = null

  function ensureHighlight (): HighlightOverlay | null {
    const view = engine.view<HighlightOverlay>(highlightId)
    if (view && view !== highlight) {
      highlight = view
    }
    return highlight
  }
  ensureHighlight()

  // ---------- 立绘动作（tool_start 驱动；zoom 在语音播完后复位） ----------

  let zoomed = false

  function resetZoom (instant = false): void {
    if (!zoomed) {
      return
    }
    zoomed = false
    const el = engine.element(avatarId)
    if (!el) {
      return
    }
    if (instant) {
      el.style.transform = ''
    } else {
      void engine.run(avatarId, 'avatar.zoom_out')
    }
  }

  // ---------- 场景（只读渲染后端的权威状态） ----------

  function applyScene (info: SceneInfo | undefined): void {
    if (!info || !info.id) {
      return
    }
    if (!engine.has(info.id)) {
      console.warn(`后端要求渲染未知场景: ${info.id}`)
      return
    }
    void engine
      .setScene(info.id, { elapsed: Number(info.elapsed ?? 0), duration: Number(info.duration ?? 0) })
      .then(ensurePlayer)
      .then(ensureHighlight)
      .catch(error => console.warn(`场景切换失败: ${info.id}`, error))
  }

  function applySceneSection (scene: SectionOf<'scene'> | null | undefined): void {
    if (!scene) {
      return
    }
    applyScene({
      id: String(scene.id ?? ''),
      duration: Number(scene.duration ?? 0),
      elapsed: Number(scene.elapsed ?? 0),
    })
    // 场景声明不显示高亮浮层（intro）时清空队列并忽略后续 highlight 事件
    ensureHighlight()?.setEnabled(scene.highlight_messages !== false)
  }

  // ---------- 层显隐基线（状态平面的 layers；父页面仍可按 id 追加隐藏） ----------

  let parentHidden: string[] = []
  let layerHidden: string[] = []

  function applyHidden (): void {
    // 父页面只能隐藏、不能强制显示：两边的隐藏集合取并集
    engine.setHidden([...new Set([...parentHidden, ...layerHidden])])
  }

  function applyLayers (layers: SectionOf<'layers'> | null | undefined): void {
    layerHidden = (layers ?? []).filter(layer => !layer.visible).map(layer => layer.id)
    applyHidden()
  }

  // ---------- 清单变更（比较 updated_at，幂等比较是防 reload 循环的唯一保险） ----------

  let manifestUpdatedAt: string | null = null
  let manifestBaselineSet = false
  let reloading = false

  function applyManifest (manifest: SectionOf<'manifest'> | null | undefined): void {
    if (!manifest || reloading) {
      return
    }
    const stamp = String(manifest.updated_at ?? '')
    if (!manifestBaselineSet) {
      // boot 基线：本页启动后看到的第一份清单就是"我现在渲染的那份"
      manifestUpdatedAt = stamp
      manifestBaselineSet = true
      return
    }
    if (stamp === manifestUpdatedAt) {
      return
    }
    // 只有"和 boot 时不同"才 reload；reload 后第一份快照又会把基线写成新值，
    // 所以同一个清单不会触发第二次 reload（没有这道比较就会无限刷新）。
    reloading = true
    console.info('场景清单已更新，重新加载窗口', manifestUpdatedAt, '→', stamp)
    window.location.reload()
  }

  // ---------- 父页面（合成器 / dashboard 预览）控制层的显隐 ----------

  window.addEventListener('message', (event: MessageEvent) => {
    const data = event.data as { ns?: string, type?: string, hidden?: string[] } | undefined
    if (!data || data.ns !== 'neuro-window') {
      return
    }
    if (data.type === 'visibility') {
      parentHidden = Array.isArray(data.hidden) ? data.hidden : []
      applyHidden()
    }
  })
  // ---------- 事件通道：内容（字幕/音频）+ 状态（场景/设置/层/清单） ----------

  /** 顶层 = 本地；被嵌入时先问父页面能不能供帧，500ms 没有回应就自连。 */
  function connectFeed (): Promise<FeedSource> {
    if (window.parent === window) {
      return Promise.resolve(new NeuroFeed())
    }
    return new Promise(resolve => {
      let settled = false
      let timer = 0
      const finish = (source: FeedSource): void => {
        if (settled) {
          return
        }
        settled = true
        window.removeEventListener('message', handshake)
        window.clearTimeout(timer)
        resolve(source)
      }
      const handshake = (event: MessageEvent): void => {
        const data = event.data as { ns?: string, type?: string } | undefined
        if (data?.ns !== 'neuro-window' || data.type !== 'feed_ready') {
          return
        }
        finish(new RemoteFeed())
      }
      window.addEventListener('message', handshake)
      // ready 供父页面做显隐；feed_ready 表示愿意接收远程画面帧
      window.parent.postMessage({ ns: 'neuro-window', type: 'ready' }, '*')
      window.parent.postMessage({ ns: 'neuro-window', type: 'feed_ready' }, '*')
      timer = window.setTimeout(() => finish(new NeuroFeed()), FEED_READY_TIMEOUT_MS)
    })
  }

  const feed: FeedSource = await connectFeed()

  // 内容连接
  feed.onContent('snapshot', (ev: FeedEvent) => {
    const snap = ev as unknown as ContentSnapshot
    const p = ensurePlayer()
    if (!p) {
      return
    }
    if (snap.turn?.speech?.length) {
      p.resumeFromSnapshot(snap.turn.speech as TurnSeg[], snap.turn.id)
      if (!snap.turn.active) {
        p.endOfTurn(snap.turn.id)
      }
    } else {
      p.clear()
    }
  })

  feed.onContent('start', (ev: FeedEvent) => {
    const turnId = ev.turn_id === undefined ? undefined : Number(ev.turn_id)
    ensurePlayer()?.beginTurn(turnId)
  })

  // 高亮消息：stream 在注入时随 POST /chat 发来，neuro 在 start 之后发内容事件。
  feed.onContent('highlight', (ev: FeedEvent) => {
    const messages = Array.isArray(ev.messages) ? ev.messages : []
    for (const msg of messages) {
      ensureHighlight()?.show(msg as HighlightMessage)
    }
  })

  feed.onContent('speech', (ev: FeedEvent) => {
    const seg: SpeechSeg = {
      index: Number(ev.index ?? 0),
      text: String(ev.text ?? ''),
      audio: String(ev.audio ?? ''),
      mime: String(ev.mime ?? ''),
      duration: Number(ev.duration ?? 0),
      step: Number(ev.step ?? 0),
    }
    if (ev.turn_id !== undefined) {
      seg.turn_id = Number(ev.turn_id)
    }
    ensurePlayer()?.enqueue(seg)
  })

  for (const type of ['done', 'cancelled', 'error'] as const) {
    feed.onContent(type, (ev: FeedEvent) => {
      const turnId = ev.turn_id === undefined ? undefined : Number(ev.turn_id)
      ensurePlayer()?.endOfTurn(turnId)
    })
  }

  feed.onContent('tool_start', (ev: FeedEvent) => {
    const name = String(ev.name ?? '')
    if (name === 'model_spin') {
      void engine.run(avatarId, 'avatar.spin')
    } else if (name === 'model_zoom') {
      zoomed = true
      void engine.run(avatarId, 'avatar.zoom_in')
    } else if (name.startsWith('model_')) {
      console.warn(`未处理的 neuro 画面工具: ${name}`)
    }
  })

  // 状态连接：首帧是完整信封，之后每帧只带变化的分区；按分区名合并即可
  function applyState (frame: StateFrame | StateSnapshot): void {
    const sections = frame.sections ?? {}
    if (sections.caption) {
      captionSettings = sections.caption.current
      applyCaptionSettings()
    }
    if (sections.scene) {
      applySceneSection(sections.scene.current)
    }
    if (sections.layers) {
      applyLayers(sections.layers.current)
    }
    if (sections.manifest) {
      applyManifest(sections.manifest.current)
    }
  }

  feed.onState('snapshot', (ev: FeedEvent) => applyState(ev as unknown as StateSnapshot))
  feed.onState('state', (ev: FeedEvent) => applyState(ev as unknown as StateFrame))

  // 控制台/父页面调试入口（与 stream/ui 的 window.__ns 对齐）
  ;(window as unknown as Record<string, unknown>).__ns = {
    stage,
    engine,
    feed,
    manifest,
    get player () {
      return player
    },
    get highlight () {
      return highlight
    },
    setHidden: (ids: string[]) => engine.setHidden(ids),
    resetZoom,
  }
}
