/** 场景数据来源。
 *
 * 场景本身是数据（`public/scenes.json` → 构建到 dist 根，stream 与画面读同一份）。
 * 开播的开场动画归 neuro 窗口自己放（`POST /ui/intro`），但合成器也有一个配套的
 * 内部场景 `room_intro`：开场期间只有 neuro 全屏，**不叠 chat/highlight**
 * （真实开场没有弹幕盖在动画上）。她离开开场场景后循环才切到 `room`。
 * `starting_soon` / `room_intro` 都是 hidden 内部场景，不出现在 dashboard 清单。
 */

import { asset } from "./core/assets";
import type { SceneDef, SceneManifest } from "./scenes/schema";

export const SCENE_STARTING_SOON = "starting_soon";
/** 开场期间的合成画面（hidden；neuro 全屏，chat/highlight 不参与）。 */
export const SCENE_ROOM_INTRO = "room_intro";
export const SCENE_ROOM = "room";

/** 拉取场景清单（相对当前页面解析，直挂 / 代理 / dev 三种前缀都成立）。 */
export async function fetchScenes(): Promise<SceneDef[]> {
  const resp = await fetch(asset("scenes.json"), { cache: "no-store" });
  if (!resp.ok) throw new Error(`scenes.json 读取失败: HTTP ${resp.status}`);
  const data = (await resp.json()) as SceneManifest;
  return data.scenes ?? [];
}
