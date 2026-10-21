/** stream 模块 /ui/events 画面事件通道（SSE，只读）。
 *
 * protocol（stream.loop.publish 的镜像）：
 * snapshot{state,scene,session,settings,intro_elapsed?,turn?,chat?,metadata?,elements?} /
 * state / session_begin{title,category} / session_end / scene_set{id} /
 * settings{speech_cooldown_ms} /
 * element_state{id,payload}（游戏等元素层的全量状态）/
 * stream_metadata{title,category} / speech{turn_id,step,index,text,audio,mime,duration} /
 * turn_begin{turn_id} / turn_end{turn_id,reason} / chat_message / priority_message /
 * tool_start / tool_result（neuro 工具事件原样镜像，画面端按 name 自行消费）
 */

export interface FeedEvent {
  type: string;
  [key: string]: unknown;
}

export interface ChatMsg {
  id: number;
  username: string;
  content: string;
  created_at?: string;
  priority_type?: string | null;
  value?: number;
}

export interface TurnSeg {
  step?: number; // 所属 LLM step（工具链中跨 step 清字幕）
  index: number;
  text: string;
  duration: number;
  consumed: number; // 服务端已发声秒数（断点续播基准）
  audio?: string; // 仅"正在发声"的句子携带（seek 到 consumed 续播）
  mime?: string;
}

export interface Snapshot {
  state: string; // offline | starting | live | paused
  scene: string | null;
  session: {
    session_id: string;
    queue_id: string;
    channel: string;
    conversation_id: string;
  } | null;
  intro_elapsed?: number; // starting 中：intro 场景时间线已过秒数（视频 seek/动作顺延）
  turn?: { id: number; active: boolean; speech: TurnSeg[] }; // 当前/最近一轮的字幕时间线
  settings?: { speech_cooldown_ms?: number }; // 画面端运行参数（字幕停留/轮间冷却）
  chat?: ChatMsg[]; // 最近聊天记录（重连时恢复聊天层）
  metadata?: { title?: string; category?: string }; // 本场标题/分区（dashboard 观看页消费；画面本身不使用）
  elements?: Record<string, unknown>; // 各元素层的全量状态（key = 元素 id，如 game_wordle）
}

const EVENT_NAMES = [
  "snapshot",
  "state",
  "session_begin",
  "session_end",
  "stream_metadata",
  "settings",
  "scene_set",
  "element_state",
  "speech",
  "turn_begin",
  "turn_end",
  "chat_message",
  "priority_message",
  "tool_start",
  "tool_result",
];

import { relativeToPage } from "./base";

/** 相对当前页面解析 API 端点（见 base.ts）。 */
function resolveEndpoint(name: string): string {
  return relativeToPage(name);
}

type Handler = (ev: FeedEvent) => void;

export class StreamFeed {
  private es: EventSource;
  private handlers = new Map<string, Set<Handler>>();
  onStatus?: (connected: boolean) => void;

  constructor(url?: string) {
    this.es = new EventSource(url ?? resolveEndpoint("events"));
    this.es.onopen = () => this.onStatus?.(true);
    this.es.onerror = () => this.onStatus?.(false); // EventSource 原生自动重连
    for (const name of EVENT_NAMES) {
      this.es.addEventListener(name, (e: Event) => {
        try {
          const data = JSON.parse((e as MessageEvent).data) as FeedEvent;
          this.dispatch(name, data);
        } catch (err) {
          console.warn("events 帧解析失败", err);
        }
      });
    }
  }

  on(type: string, fn: Handler): () => void {
    if (!this.handlers.has(type)) this.handlers.set(type, new Set());
    this.handlers.get(type)!.add(fn);
    return () => this.handlers.get(type)?.delete(fn);
  }

  private dispatch(type: string, ev: FeedEvent): void {
    ev.type = ev.type || type;
    for (const fn of this.handlers.get(type) ?? []) {
      try {
        fn(ev);
      } catch (e) {
        console.warn(`事件 ${type} 处理异常`, e);
      }
    }
  }

  close(): void {
    this.es.close();
  }
}
