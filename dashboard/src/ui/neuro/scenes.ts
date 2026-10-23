/** neuro 窗口场景数据来源。
 *
 * 场景是**服务端的数据**（`neuro_sama/defaults/neuro_scenes.json`）：模块通过
 * `GET /ui/manifest` 下发完整清单，窗口只负责渲染。当前两个场景：
 * - `normal`：常态（房间背景 + 立绘 + 字幕），启动即落在这里；
 * - `intro`：开场（视频 + 立绘 rise_in），放完由后端脚本自动回 `normal`。
 *
 * 服务端读同一份数据做校验与后端脚本调度（`neuro_sama/scenes.py`）——
 * **加一个 neuro 场景只改这一处**。
 */

import type { SceneManifest } from '../engine/schema'
import { apiUrl } from '../engine/base'

export async function fetchScenes (): Promise<SceneManifest> {
  const resp = await fetch(apiUrl('ui/manifest'), { cache: 'no-store' })
  if (!resp.ok) {
    throw new Error(`场景清单读取失败: HTTP ${resp.status}`)
  }
  const data = (await resp.json()) as SceneManifest
  if (!Array.isArray(data.scenes) || !data.default_scene) {
    throw new Error('场景清单结构不合法（缺 scenes / default_scene）')
  }
  return data
}
