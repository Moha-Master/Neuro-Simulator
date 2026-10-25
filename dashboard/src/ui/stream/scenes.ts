/** 场景数据来源。
 *
 * 场景本身是**服务端的数据**（`stream/defaults/scenes.json`）：模块通过
 * `GET /ui/manifest` 下发完整清单（元素/box/css/script 都在里面），画面只负责渲染。
 * 过去这份 json 放在前端 `public/` 里靠构建拷进 dist，服务端读它要依赖前端构建
 * 产物——那是反向耦合，现在归回服务端。
 *
 * 开播的开场动画归 neuro 窗口自己放（`POST /ui/intro`），但合成器也有一个配套的
 * 内部场景 `room_intro`：开场期间只有 neuro 全屏，**不叠 chat**
 * （真实开场没有弹幕盖在动画上）。她离开开场场景后循环才切到 `room`。
 * `starting_soon` / `room_intro` 都是 hidden 内部场景，不出现在 dashboard 清单。
 */

import type { SceneDef, SceneManifest } from '../engine/schema'
import { apiUrl } from '../engine/base'

export const SCENE_STARTING_SOON = 'starting_soon'
/** 开场期间的合成画面（hidden；neuro 全屏，chat 不参与）。 */
export const SCENE_ROOM_INTRO = 'room_intro'
export const SCENE_ROOM = 'room'

/** 拉取场景清单（走模块 API：直挂 / 代理 / dev 三种托管方式都成立）。 */
export async function fetchScenes (): Promise<SceneDef[]> {
  const resp = await fetch(apiUrl('ui/manifest'), { cache: 'no-store' })
  if (!resp.ok) {
    throw new Error(`场景清单读取失败: HTTP ${resp.status}`)
  }
  const data = (await resp.json()) as SceneManifest
  return data.scenes ?? []
}
