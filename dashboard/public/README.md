# public/ 资产说明（deprecated 标记）

自画布编辑第二批（第 5 期，资产后端化）起，**场景素材归后端**（设计定稿 §5
"资产二分"）：

| 文件 | 状态 | 新位置 |
| --- | --- | --- |
| `no_source.webp` | **deprecated** | stream 包内模板 `server/neuro_simulator/stream/assets/images/` → 工作目录 `assets/`，经 `GET /ui/assets/no_source.webp` |
| `room_v1_bg_media.webp` | **deprecated** | 同上 |
| `room_v2_bg.webp` | **deprecated** | 同上；neuro 侧在 `neuro_sama/assets/` |
| `neurosama.png` | **deprecated** | neuro 包内 `neuro_sama/assets/` → `GET /manage/neuro_sama/ui/assets/neurosama.png` |
| `sc_purple.png` / `sc_pink.png` | **deprecated** | 同上（highlight 浮层） |

这些文件**保留不删**：client（观看页）与旧构建/旧引用的兜底，以及 vedal 根路径
`/avatar.webp` 的相邻静态服务先例。新代码不得再引用它们；字体、`avatar.webp`、
twitch 图标等"什么情况都不变"的前端资产仍留在这里（二分原则的另一半）。
