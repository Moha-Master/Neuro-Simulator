/** 场景数据来源。
 *
 * 场景本身是数据（`public/scenes.json` → 构建到 dist 根，stream 与画面读同一份）。
 * 开播 intro 也是其中一个普通场景，带固定时长与时间线；由 `ElementEngine` 执行，
 * 后端只按同一份时长推进并在结束时广播 `scene_set room`。
 */

import { asset } from "./core/assets";
import type { SceneDef, SceneManifest } from "./scenes/schema";

export const SCENE_STARTING_SOON = "starting_soon";
export const SCENE_ROOM = "room";
export const SCENE_INTRO = "intro";
export const AVATAR_ELEMENT = "avatar";

/** 拉取场景清单（相对当前页面解析，直挂 / 代理 / dev 三种前缀都成立）。 */
export async function fetchScenes(): Promise<SceneDef[]> {
  const resp = await fetch(asset("scenes.json"), { cache: "no-store" });
  if (!resp.ok) throw new Error(`scenes.json 读取失败: HTTP ${resp.status}`);
  const data = (await resp.json()) as SceneManifest;
  return data.scenes ?? [];
}
