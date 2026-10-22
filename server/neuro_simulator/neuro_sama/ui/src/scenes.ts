/** neuro 窗口场景数据来源。
 *
 * 场景是数据（`public/neuro_scenes.json` → 构建到 dist 根）。当前两个场景：
 * - `normal`：常态（房间背景 + 立绘 + 字幕），启动即落在这里；
 * - `intro`：开场（视频 + 立绘 rise_in），放完由后端脚本自动回 `normal`。
 *
 * 服务端读同一份文件做校验与后端脚本调度（`neuro_sama/scenes.py`）——
 * **加一个 neuro 场景只改这一处**。
 */

import { asset } from "./core/assets";
import type { SceneManifest } from "./scenes/schema";

export async function fetchScenes(): Promise<SceneManifest> {
  const resp = await fetch(asset("neuro_scenes.json"), { cache: "no-store" });
  if (!resp.ok) throw new Error(`neuro_scenes.json 读取失败: HTTP ${resp.status}`);
  const data = (await resp.json()) as SceneManifest;
  if (!Array.isArray(data.scenes) || !data.default_scene) {
    throw new Error("neuro_scenes.json 结构不合法（缺 scenes / default_scene）");
  }
  return data;
}
