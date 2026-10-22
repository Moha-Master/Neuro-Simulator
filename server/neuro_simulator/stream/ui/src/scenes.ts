/** 场景数据来源。
 *
 * 场景本身是数据（`public/scenes.json` → 构建到 dist 根，stream 与画面读同一份）。
 * 开播 intro 已移到 neuro 窗口（`POST /ui/intro`），stream 只负责合成背景/游戏/
 * neuro iframe/chat；`starting_soon` 是内部 no-source 场景，不出现在 dashboard。
 */

import { asset } from "./core/assets";
import type { SceneDef, SceneManifest } from "./scenes/schema";

export const SCENE_STARTING_SOON = "starting_soon";
export const SCENE_ROOM = "room";

/** 拉取场景清单（相对当前页面解析，直挂 / 代理 / dev 三种前缀都成立）。 */
export async function fetchScenes(): Promise<SceneDef[]> {
  const resp = await fetch(asset("scenes.json"), { cache: "no-store" });
  if (!resp.ok) throw new Error(`scenes.json 读取失败: HTTP ${resp.status}`);
  const data = (await resp.json()) as SceneManifest;
  return data.scenes ?? [];
}
