/**
 * 状态机投影的类型契约（与后端字段逐字对齐）。
 *
 * 这些类型是**契约守卫**：dashboard 的所有状态读取都走具名类型，`vue-tsc` 会在
 * 后端改字段名/改形状时直接报错，而不是运行时读到 undefined 才发现。
 *
 * 形状来源：
 * - `GET /status`（neuro_sama）
 * - `GET /stream/status`（stream）
 * - `GET /manage/state`（vedal 聚合，`status` 原样透传上面两者的 envelope）
 */

// ---------- graph 通用形状 ----------

export interface GraphState {
  id: string
  label: string
  desc?: string
}

export interface GraphTransition {
  from: string
  to: string
  by?: string
}

export interface StateGraph {
  states: GraphState[]
  transitions: GraphTransition[]
}

export interface Section<T = Record<string, unknown>> {
  current: T | null
  graph?: StateGraph
  /** provider 抛异常时该分区降级为 {current:null,error}，其余分区照常 */
  error?: string
}

export interface StatusEnvelope<S> {
  module: string
  rev: number
  ts: string
  sections: S
}

// ---------- stream 分区 ----------

/** 开场闸门（仅 state=starting 时有值）。 */
export interface IntroGate {
  waiting_for: string
  neuro_scene: string | null
  polls: number
  timeout_s: number
  waited_s: number
  remaining_s: number
  status_error?: string | null
  /** 实际放行路径（E-4：订阅优先、轮询兜底） */
  ready_via?: 'sse' | 'poll' | 'none' | null
  /** 订阅路径收到的状态帧数（排查"订阅活着但不推"） */
  updates?: number
}

/** 异常终止开播的原因（下次成功开播时服务端清空）。 */
export interface StreamLastError {
  code: string
  message: string
  at: string
  detail?: {
    waiting_for?: string
    neuro_scene?: string | null
    polls?: number
    status_error?: string | null
  }
}

export interface PersistedSession {
  session_id: string
  queue_id?: string
  channel: string
  conversation_id: string
  state: string
  started_at: string
  ended_at: string | null
  cursor_msg?: number
  rounds: number
  tags?: string[]
}

export interface LoopStatus {
  state: string
  session_id: string | null
  queue_id: string | null
  channel: string | null
  conversation_id: string | null
  round: number
  cursor: number | null
  highlight_cursor: number | null
  /** true = 高亮消息队列暂停（不消费高亮轨、游标不推进） */
  highlight_paused: boolean
  /** 最后写入开关的来源：scene:stream / scene:neuro / manual */
  highlight_source: string | null
  intro: IntroGate | null
  last_error: StreamLastError | null
  started_at: string | null
  persisted: PersistedSession | null
}

export interface StreamSceneStatus {
  id: string
  label: string
  icon: string | null
  /** 面向机器/LLM 的英文状态自述 */
  desc: string
  /** 该场景是否允许高亮消息进入队列消费 */
  highlight_messages: boolean
  duration: number | null
  elapsed: number | null
}

export interface StreamMetadata {
  title: string
  category: string
  tags: string[]
  /** false = 无场次，此时值是 config 的默认值（作为下一场初值） */
  live: boolean
}

export interface AudienceStatus {
  /** TwitchTracker 平均观众；拿不到时为 null（不编造数字） */
  viewers: number | null
  viewers_source: string
  updated_at: string | null
  error: string | null
}

export interface ChatStatus {
  normal_pending: number | null
  highlight_pending: number | null
  last_message_id: number | null
}

export interface ChatbotRuntime {
  enabled: boolean
  model: string | null
  model_ref: string | null
  interval_s: number
  count: number
  max_concurrent: number
  interval_jitter: number
  in_flight: number
  /** tool call 之外/失败的工具调用被静默丢弃的累计次数 */
  discarded: number
  /** 最近一次丢弃（reason + preview），成功批次不清空 */
  last_discard: { at: string; reason: string; preview: string } | null
  /** 消息模板最近一次实况渲染失败（成功即清除） */
  template_error: TemplateErrorStatus | null
}

export interface TemplateErrorStatus {
  name: string
  at: string
  message: string
}

export interface GameEntry {
  /** 由 element 快照派生的状态 id（如 wordle.typing） */
  phase: string | null
  /** 游戏自己给的一句叙事（英文，不含棋盘细节） */
  summary: string
  /** 面向观众的大众化解说（chatbot 用；同样不含 SAN/UCI） */
  viewer: string
  /** 原始 element_state 快照（与画面广播同一份对象） */
  element: Record<string, unknown>
}

export interface GamesStatus {
  active: string | null
  /** 活跃游戏的条目（key = 游戏 id） */
  [gameId: string]: GameEntry | string | null
}

export interface NeuroLinkStatus {
  reachable: boolean
  observed_at: string | null
  url: string
  error: string | null
}

export interface StreamSections {
  loop: Section<LoopStatus>
  scene: Section<StreamSceneStatus>
  metadata: Section<StreamMetadata>
  audience: Section<AudienceStatus>
  chat: Section<ChatStatus>
  chatbot: Section<ChatbotRuntime>
  games: Section<GamesStatus>
  neuro_link: Section<NeuroLinkStatus>
}

export type StreamStatus = StatusEnvelope<StreamSections>

// ---------- neuro_sama 分区 ----------

export interface NeuroSceneStatus {
  id: string
  label: string
  /** 面向机器/LLM 的英文状态自述（原样展示，不翻译） */
  desc: string
  icon: string | null
  duration: number | null
  elapsed: number | null
  auto_returns: boolean
  /** 该场景是否显示高亮消息浮层（intro=false） */
  highlight_messages: boolean
}

export interface TurnStatus {
  turn_id: string | null
  channel: string | null
  generating: boolean
  speaking: boolean
  /** 当前不在发声（无阈值派生事实；阈值属于模板策略） */
  idle: boolean
  /** 距最后一次语音结束的秒数；本轮没有 speech / 从未说话时为 null */
  silent_s: number | null
  remaining_s: number
  /** 本轮语音预测播完的 unix 秒；本轮没有 speech 时为 null */
  ends_at: number | null
  segments: number
  segments_done: number
  in_flight: number
}

export interface LayerStatus {
  id: string
  name: string
  visible: boolean
}

export interface ModelStatus {
  service_ok: boolean
}

export interface TtsStatus {
  effective_provider: string | null
  degraded: boolean
}

/** 字幕参数（`UiState.settings()` 的 caption 部分；配置的唯一读取点）。 */
export interface CaptionStatus {
  hide_ms: number
  step_gap_ms: number
}

/** 场景清单元信息（复用 `SceneStore.source()`，与 `/ui/manifest/source` 同一函数）。 */
export interface ManifestStatus {
  is_default: boolean
  updated_at: string
  chars: number
}

export interface NeuroSections {
  scene: Section<NeuroSceneStatus>
  turn: Section<TurnStatus>
  layers: Section<LayerStatus[]>
  model: Section<ModelStatus>
  tts: Section<TtsStatus>
  caption: Section<CaptionStatus>
  manifest: Section<ManifestStatus>
}

export type NeuroStatus = StatusEnvelope<NeuroSections>

// ---------- vedal 聚合 ----------

/** 聚合器不认识的模块分区（未来模块）：只在 JSON 回落里出现 */
export type UnknownSections = Record<string, Section<unknown>>

export interface ModuleState<S = UnknownSections> {
  url: string | null
  reachable: boolean
  observed_at: string
  elapsed_ms: number
  /** 模块的 `/status` envelope 原样透传；不可达时为 null */
  status: StatusEnvelope<S> | null
  error: string | null
}

export interface VedalState {
  vedal: { module: string; ts: string }
  modules: Record<string, ModuleState>
}

/**
 * 聚合结果里取某个模块的具名分区。
 *
 * 后端 `modules` 是异质 map（key = 模块名），TS 无法从配置静态推出每个 value 的
 * 分区类型；这里把边界收在两个函数里，页面内部仍然全部走具名类型。
 */
export function streamModule(root: VedalState | null): ModuleState<StreamSections> | null {
  if (!root) return null
  const entry = root.modules.stream
  return entry ? (entry as unknown as ModuleState<StreamSections>) : null
}

export function neuroModule(root: VedalState | null): ModuleState<NeuroSections> | null {
  if (!root) return null
  const entry = root.modules.neuro_sama
  return entry ? (entry as unknown as ModuleState<NeuroSections>) : null
}
