/** stream 模块 /ui/events 画面事件通道（SSE，只读）。
 *
 * 协议（stream.loop.publish 的镜像）：
 * snapshot{state,scene,session} / state / session_begin / session_end /
 * intro / scene_set{id} / speech{index,text,audio,mime,duration} /
 * turn_begin / turn_end{reason}
 */

export interface FeedEvent {
  type: string;
  [key: string]: unknown;
}

export interface TurnSeg {
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
  intro_elapsed?: number; // starting 中：开场序列已过秒数（视频续播）
  turn?: { active: boolean; speech: TurnSeg[] }; // 当前/最近一轮的字幕时间线
}

const EVENT_NAMES = [
  "snapshot",
  "state",
  "session_begin",
  "session_end",
  "intro",
  "scene_set",
  "speech",
  "turn_begin",
  "turn_end",
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

/** UI → stream 上行回报（fire-and-forget，失败静默）。 */
export async function report(event: string): Promise<void> {
  try {
    await fetch(resolveEndpoint("report"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ event }),
    });
  } catch (e) {
    console.warn("report 失败", e);
  }
}
