/** 直播合成器（dashboard 构建里的独立页面 `/ui/stream/`）：实际背景 + game/media + neuro 窗口 iframe + chat。
 *
 * 现实对应关系：
 * - stream 只提供"其它背景 / 靠后的内容 / 靠前的前景"；
 * - neuro 窗口（背景/开场/立绘/字幕/高亮消息浮层）是独立进程的整窗捕获，这里用
 *   iframe 承载，只做整体 translate+scale，并通过 postMessage 控制内部层显隐；
 * - 字幕/音频/工具动作不再经过 stream 的 SSE——neuro iframe 自己订阅 neuro 的
 *   /ui/events。
 *
 * 事件（stream /ui/events）只负责：场景、session/state、游戏元素状态、聊天、
 * 高亮消息入列通知、settings（neuro channel）。**高亮消息浮层归 neuro 窗口**：
 * 入列时这里只追加一条普通聊天行，等消息真正注入后才由 neuro 弹层。
 *
 * 字幕/音频/立绘动作来自后端转发的 `neuro {plane, event, data}` 帧：本页把它
 * 通过 postMessage 喂给 neuro iframe（iframe 因此不建自己的 SSE）。同时本页
 * 作为子页面被 `/stream-view` 嵌入时，把收到的每一条事件与最近一次 snapshot
 * 用 `{ns:'stream-feed', ...}` 转发给父页面。
 *
 * 这个页面是**独立文档**（不是 dashboard 的 Vue 路由）：OBS 浏览器源要的是一个
 * 干净的画布（不受 Vuetify 主题/reset 影响），neuro 窗口的 iframe 也要透明背景。
 * 它与 dashboard 同一个构建、同一个 origin（由 vedal 静态托管）。
 */

import type { SceneDef } from '../engine/schema'

import type { ChatOverlay } from './chat'
import { ACTIONS } from '../engine/action'
import { foreignModuleBase, makeAssetUrl } from '../engine/base'
import { ElementEngine } from '../engine/elements'
import { loadFonts } from './assets'
import { COMPONENTS } from './components'
import {
  type ChatMsg,
  type FeedEvent,
  type NeuroMirrorEvent,
  type NeuroSettings,
  type Snapshot,
  StreamFeed,
} from './feed'
import { NeuroLayer } from './neuro'
import { fetchScenes, SCENE_ROOM, SCENE_ROOM_INTRO, SCENE_STARTING_SOON } from './scenes'
import './styles/stream.css'

/** neuro 窗口页是**同一个构建里的兄弟入口**（`/ui/stream/` → `/ui/neuro/`），
 *  不再由后端下发（后端那边的 `/ui/` 页面已经不存在了）。 */
function neuroPageUrl (): string {
  return new URL('../neuro/', window.location.href).href
}

/** neuro 模块的健康检查地址（经 vedal 代理的同源路径）。 */
function neuroHealthUrl (): string {
  return `${foreignModuleBase('data-neuro-module-base', '/manage/neuro_sama/')}health`
}

function resolveNeuroSettings (settings?: NeuroSettings): {
  uiUrl: string
  healthUrl: string
  channel: string
} {
  return {
    uiUrl: neuroPageUrl(),
    healthUrl: neuroHealthUrl(),
    channel: settings?.neuro_channel || 'stream',
  }
}

export async function bootstrapStream (stage: HTMLElement): Promise<void> {
  await loadFonts()

  let scenes: SceneDef[]
  try {
    scenes = await fetchScenes()
  } catch (error) {
    console.error('场景清单加载失败，画面无法渲染', error)
    return
  }
  const sceneMap = new Map(scenes.map(s => [s.id, s]))
  const engine = new ElementEngine(stage, scenes, {
    components: COMPONENTS,
    actions: ACTIONS,
    assetUrl: makeAssetUrl(),
  })
  await engine.setScene(SCENE_STARTING_SOON, { instant: true })

  // 实时管线组件从元素引擎取（starting_soon 也声明了 chat，保证实例先建好）
  const chat = engine.view<ChatOverlay>('chat')
  if (!chat) {
    console.warn('场景缺少 chat 元素，聊天事件将被忽略')
  }

  const noSource = document.querySelector('#no-source')
  let neuro: NeuroLayer | null = null
  let streamState = 'offline'
  let neuroHealthy = true
  let healthUrl = ''
  let healthTimer: number | null = null

  // ---------- 父页面转发（/stream-view 嵌入时）----------
  // 本页在 OBS 直载时是顶层文档；被 Vue 观看页嵌入时是 iframe，父页面替它
  // 展示聊天栏/标题，但不再自己连 SSE。
  const parent = window.parent !== window ? window.parent : null
  let lastSnapshot: Snapshot | null = null
  let neuroNeedsSnapshot = true

  /** neuro 的实时基线。`lastSnapshot` 只在 `/ui/events` 建连时更新，
   *  iframe 重载必须按**现在**恢复，所以 `neuro` 增量帧也要合并进来。 */
  const neuroBaseline: {
    turn: Record<string, unknown> | null
    state: Record<string, unknown>
  } = { turn: null, state: {} }

  function resetNeuroBaseline (snap: Snapshot | null): void {
    neuroBaseline.turn = (snap?.neuro?.turn as Record<string, unknown> | null) ?? null
    neuroBaseline.state = { ...(snap?.neuro?.state ?? {}) }
  }

  /** 与 `NeuroMirror` 的缓存规则一致：状态按分区合并，turn 跟随 start/speech/终态。 */
  function trackNeuro (e: NeuroMirrorEvent): void {
    const data = (e.data ?? {}) as Record<string, unknown>
    if (e.plane === 'state') {
      const sections = data.sections
      if (!sections || typeof sections !== 'object') {
        return
      }
      for (const [name, section] of Object.entries(sections as Record<string, unknown>)) {
        const current
          = section && typeof section === 'object' && 'current' in section
            ? (section as Record<string, unknown>).current
            : section
        neuroBaseline.state[name] = current
      }
      return
    }
    if (e.event === 'snapshot') {
      neuroBaseline.turn = (data.turn as Record<string, unknown> | null) ?? null
      return
    }
    if (e.event === 'start') {
      neuroBaseline.turn = { id: data.turn_id, active: true, speech: [] }
      return
    }
    if (e.event === 'speech') {
      const turn = neuroBaseline.turn ?? { id: data.turn_id, active: true, speech: [] }
      const speech = Array.isArray(turn.speech)
        ? turn.speech as Array<Record<string, unknown>>
        : []
      const seg: Record<string, unknown> = {}
      for (const key of ['step', 'index', 'text', 'duration', 'audio', 'mime']) {
        if (key in data) {
          seg[key] = data[key]
        }
      }
      // mirror 不知道前端播放进度；重放时从 0 开始（与后端缓存一致）
      seg.consumed = 0
      speech.push(seg)
      neuroBaseline.turn = { ...turn, speech }
      return
    }
    if (e.event === 'done' || e.event === 'cancelled' || e.event === 'error') {
      if (neuroBaseline.turn) {
        neuroBaseline.turn = { ...neuroBaseline.turn, active: false }
      }
    }
  }

  function toParent (type: string, data: unknown): void {
    parent?.postMessage({ ns: 'stream-feed', type, data }, '*')
  }

  window.addEventListener('message', (event: MessageEvent) => {
    const data = event.data as { ns?: string, type?: string } | undefined
    if (data?.ns !== 'stream-feed' || data.type !== 'ready') {
      return
    }
    if (lastSnapshot) {
      toParent('snapshot', lastSnapshot)
    }
  })

  /** 当前场景的 neuro 配置（场景切换 / iframe 晚于场景创建时都要对齐）。 */
  function applySceneNeuro (): void {
    const current = engine.current
    neuro?.setScene(current ? sceneMap.get(current.id)?.neuro : undefined)
  }

  /** 把**当前**的 neuro 基线补发给刚进入远程模式的 iframe。
   *  形状与 neuro 直连时的首帧一致：内容 `{turn}`、状态 `{sections}`。 */
  function pushNeuroSnapshot (): void {
    if (!neuroNeedsSnapshot || !neuro) {
      return
    }
    neuroNeedsSnapshot = false
    neuro.forward({
      plane: 'content',
      event: 'snapshot',
      data: { turn: neuroBaseline.turn },
    })
    neuro.forward({
      plane: 'state',
      event: 'snapshot',
      data: { sections: neuroBaseline.state },
    })
  }

  function ensureNeuro (settings?: NeuroSettings): void {
    const next = resolveNeuroSettings(settings)
    if (neuro) {
      neuro.setSettings({ uiUrl: next.uiUrl, channel: next.channel })
    } else {
      neuro = new NeuroLayer(stage!, { uiUrl: next.uiUrl, channel: next.channel })
      applySceneNeuro()
    }
    neuro.onFeedReady = () => {
      neuroNeedsSnapshot = true
      pushNeuroSnapshot()
    }
    startHealth(next.healthUrl)
  }

  async function setScene (
    id: string,
    opts: { instant?: boolean, elapsed?: number, duration?: number } = {},
  ): Promise<void> {
    try {
      await engine.setScene(id, opts)
      applySceneNeuro()
    } catch (error) {
      console.warn(`场景切换失败: ${id}`, error)
    }
  }

  /** 元素状态（游戏棋盘等）：先写进引擎缓存，元素挂载时会自动回放。 */
  function applyElements (map?: Record<string, unknown>): void {
    if (!map) {
      return
    }
    for (const [id, payload] of Object.entries(map)) {
      engine.update(id, payload)
    }
  }

  // ---------- neuro 健康轮询：中途掉线 → no_source ----------

  function updateSource (): void {
    const down = streamState !== 'offline' && !neuroHealthy
    noSource?.classList.toggle('hidden', !down)
    neuro?.setAvailable(!down)
  }

  async function checkHealth (): Promise<void> {
    if (!healthUrl) {
      return
    }
    const ctrl = new AbortController()
    const timer = window.setTimeout(() => ctrl.abort(), 4000)
    let ok = false
    try {
      const r = await fetch(healthUrl, { cache: 'no-store', signal: ctrl.signal })
      ok = r.ok
    } catch {
      ok = false
    } finally {
      window.clearTimeout(timer)
    }
    if (ok !== neuroHealthy) {
      neuroHealthy = ok
      updateSource()
    }
  }

  function startHealth (url: string): void {
    if (url === healthUrl && healthTimer !== null) {
      return
    }
    healthUrl = url
    if (healthTimer !== null) {
      window.clearInterval(healthTimer)
      healthTimer = null
    }
    if (!url) {
      neuroHealthy = true
      updateSource()
      return
    }
    void checkHealth()
    healthTimer = window.setInterval(() => void checkHealth(), 5000)
  }

  const feed = new StreamFeed()
  // 所有事件同时旁路给父页面（含 snapshot；父页面自己按 type 分发）
  feed.onAny = (type, ev) => toParent('event', { event: type, data: ev })

  feed.on('snapshot', (ev: FeedEvent) => {
    const snap = ev as unknown as Snapshot
    lastSnapshot = snap
    resetNeuroBaseline(snap)
    streamState = snap.state
    ensureNeuro(snap.settings)
    updateSource()

    // 聊天层：重连时用 snapshot 的最近记录整体恢复；无场次则清空
    if (snap.chat?.length) {
      chat?.replace(snap.chat)
    } else {
      chat?.clear()
    }
    applyElements(snap.elements)

    if (snap.state === 'offline') {
      void setScene(SCENE_STARTING_SOON, { instant: true })
      return
    }
    if (snap.state === 'starting' || snap.state === 'live' || snap.state === 'paused') {
      // 落到 snapshot 声明的实际场景（含循环期间手动切换过的场景），未知则 room。
      // 重连用 instant：不重复播淡入，画面不会因为 SSE 断开重连而闪一下。
      // elapsed/duration 供场景前端脚本（timeline）断点恢复。
      void setScene(snap.scene?.id || SCENE_ROOM, {
        instant: true,
        elapsed: Number(snap.scene?.elapsed ?? 0),
        duration: Number(snap.scene?.duration ?? 0),
      })
    } else {
      void setScene(SCENE_STARTING_SOON, { instant: true })
    }
    // iframe 可能在这个 snapshot 之前就完成握手（例如热更新）；补一次基线
    neuroNeedsSnapshot = true
    pushNeuroSnapshot()
    // 父页面（/stream-view）可能在 iframe 就绪前就注册了监听，早发的 ready 会丢；
    // 这里反向通知一次"基线已就绪"，让它按需请求（同一份 snapshot 只回一次）
    toParent('hello', null)
  })

  // 新场次开始：清掉上一场残留聊天（场景由 start 广播的 scene_set 驱动）
  feed.on('session_begin', () => {
    chat?.clear()
  })

  feed.on('state', (ev: FeedEvent) => {
    const s = String(ev.state ?? '')
    streamState = s
    updateSource()
    if (s === 'starting' || s === 'live' || s === 'paused') {
      // 后端是唯一时钟：若还停在待机，就落到**该阶段**的默认画面——
      // starting 用 room_intro（chat 不该盖在开场动画上），live/paused 用 room。
      // 已经在某个直播场景里（含游戏）就别把它拽回去——暂停/继续不该换画面。
      // 正常路径由 scene_set 驱动，这里只兜"迟到的渲染器"。
      if (!engine.current || engine.current.id === SCENE_STARTING_SOON) {
        void setScene(s === 'starting' ? SCENE_ROOM_INTRO : SCENE_ROOM)
      }
    } else if (s === 'offline') {
      chat?.clear()
      void setScene(SCENE_STARTING_SOON)
    }
  })

  feed.on('scene_set', (ev: FeedEvent) => {
    void setScene(String(ev.id ?? ''), {
      elapsed: Number(ev.elapsed ?? 0),
      duration: Number(ev.duration ?? 0),
    })
  })

  feed.on('element_state', (ev: FeedEvent) => {
    engine.update(String(ev.id ?? ''), ev.payload)
  })

  feed.on('manifest_changed', () => {
    // 场景清单被改了（dashboard 里编辑过）：本页只在启动时拉一次清单，
    // 重载页面最省心——也最不容易出现"数据换了、画面没换"的错位。
    console.info('场景清单已更新，重新加载画面')
    window.location.reload()
  })

  feed.on('settings', (ev: FeedEvent) => {
    // 配置热重载：neuro UI 地址/channel 可能变了；健康地址一起更新。
    ensureNeuro(ev as unknown as NeuroSettings)
  })

  feed.on('session_end', () => {
    chat?.clear()
    void setScene(SCENE_STARTING_SOON)
  })

  feed.on('chat_message', (ev: FeedEvent) => {
    chat?.append(ev as unknown as ChatMsg)
  })

  // 高亮消息入列：聊天层照样占一条普通行；浮层由 neuro 窗口在真正注入后弹。
  feed.on('highlight_message', (ev: FeedEvent) => {
    chat?.append(ev as unknown as ChatMsg)
  })

  // neuro 的两条总线由 stream 后端聚合后原样转发：iframe 只吃这一条通道
  feed.on('neuro', (ev: FeedEvent) => {
    const e = ev as unknown as NeuroMirrorEvent
    // 先合并进基线，再转发：iframe 重载时拿到的必须是"现在"的状态
    trackNeuro(e)
    neuro?.forward({ plane: e.plane, event: e.event, data: e.data })
  });

  // 调试/E2E 句柄：控制台或自动化可直接驱动渲染管线（不影响运行逻辑）
  (window as unknown as Record<string, unknown>).__ns = {
    engine,
    get neuro () {
      return neuro
    },
    feed,
    chat,
    noSource,
    setScene,
    ensureNeuro,
    isNeuroHealthy: () => neuroHealthy,
  }
}
