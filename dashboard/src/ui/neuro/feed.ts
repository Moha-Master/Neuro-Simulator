/** neuro 窗口事件通道（SSE，只读）。
 *
 * 与 stream 的 `/ui/events` 分开：neuro 只关心自己作为"一个窗口"需要的内容——
 * 场景、立绘动作、字幕/音频。chat / 游戏 / 合成属于 stream。
 *
 * 事件：
 *   snapshot{scene?, turn?, settings?}
 *   scene_set{id, duration, elapsed}        ← 权威场景状态（后端脚本或手动切换）
 *   start{turn_id, channel, conversation_id, model}
 *   speech{turn_id, step, index, text, audio, mime, duration}
 *   done / cancelled / error{turn_id, ...}
 *   tool_start{turn_id, name, arguments}
 *   settings{caption{hide_ms, step_gap_ms}}
 *
 * `?channel=stream` 时服务端只推该 channel 的 turn/speech/tool；独立打开
 * （不带 channel）则推全部。scene_set/settings 是全局事件，始终下发。
 */

import { apiUrl } from "../engine/base";

export interface FeedEvent {
  type: string;
  [key: string]: unknown;
}

export interface TurnSeg {
  step?: number;
  index: number;
  text: string;
  duration: number;
  consumed: number;
  audio?: string;
  mime?: string;
}

export interface SceneInfo {
  id: string;
  /** 场景有效时长（后端可能按视频轨道探测）；`at_end` 靠它换算 */
  duration: number;
  /** 已进入场景的秒数（中途连接从这里续播） */
  elapsed: number;
}

export interface Snapshot {
  scene?: SceneInfo;
  turn?: { id: number; active: boolean; speech: TurnSeg[] };
  settings?: { caption?: { hide_ms?: number; step_gap_ms?: number } };
}

const EVENT_NAMES = [
  "snapshot",
  "scene_set",
  "start",
  "speech",
  "done",
  "cancelled",
  "error",
  "tool_start",
  "settings",
];

type Handler = (ev: FeedEvent) => void;

export class NeuroFeed {
  private es: EventSource;
  private handlers = new Map<string, Set<Handler>>();
  onStatus?: (connected: boolean) => void;

  constructor(url?: string) {
    this.es = new EventSource(url ?? this.endpoint());
    this.es.onopen = () => this.onStatus?.(true);
    this.es.onerror = () => this.onStatus?.(false); // EventSource 原生自动重连
    for (const name of EVENT_NAMES) {
      this.es.addEventListener(name, (e: Event) => {
        try {
          const data = JSON.parse((e as MessageEvent).data) as FeedEvent;
          this.dispatch(name, data);
        } catch (err) {
          console.warn("neuro events 帧解析失败", err);
        }
      });
    }
  }

  /** 页面 URL 可带 ?channel=stream，原样传给事件端点做服务端过滤。 */
  private endpoint(): string {
    const channel = new URLSearchParams(window.location.search).get("channel");
    // 事件流是**模块 API**：走模块基址（/manage/neuro_sama/ 或直挂时的 /）
    return apiUrl(
      channel ? `ui/events?channel=${encodeURIComponent(channel)}` : "ui/events",
    );
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
        console.warn(`neuro 事件 ${type} 处理异常`, e);
      }
    }
  }

  close(): void {
    this.es.close();
  }
}
