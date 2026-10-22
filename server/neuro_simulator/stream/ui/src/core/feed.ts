/** stream 模块 /ui/events 合成器事件通道（SSE，只读）。
 *
 * 合成器只关心"场景/背景/游戏/聊天"这些 stream 自己的层：
 *   snapshot{state,scene,session,settings,chat?,metadata?,elements?}
 *   state / session_begin / session_end / scene_set{id} / settings{neuro_*} /
 *   element_state{id,payload} / stream_metadata{title,category} /
 *   chat_message / priority_message
 *
 * 字幕/音频/立绘动作不再经过 stream：neuro 窗口（合成器里的 iframe）直接订阅
 * neuro 的 /ui/events。工具事件同理，不在这里镜像。
 */

import { relativeToPage } from "./base";

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

export interface NeuroSettings {
  neuro_ui_url?: string;
  neuro_health_url?: string;
  neuro_channel?: string;
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
  settings?: NeuroSettings; // neuro 窗口地址 / 健康检查地址 / channel
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
  "chat_message",
  "priority_message",
];

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
