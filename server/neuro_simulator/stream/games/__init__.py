"""游戏子系统：直播里能被 Neuro 用工具驱动的本地小游戏。

设计要点（与"多客户端同步"直接相关）：**服务端是唯一权威**。
- 游戏状态只活在 stream 进程里（一个场次同一时间至多一个活跃游戏）。
- Neuro 的工具调用打到 stream 的 `/game/{id}/action`，游戏改状态 → 这里 publish
  `element_state` 全量快照给画面，同时把可读的结果文本回给 Neuro。
- OBS 浏览器源、dashboard 观看页、中途重连的客户端都只是渲染器：
  实时收 `element_state`，重连时从 snapshot 的 `elements` 里拿同一份全量状态。
  动画（翻牌、逐字）是渲染细节，由组件对比前后状态自己推导。

新增一个游戏 = 写一个 Game 子类（自带 register）+ 画面侧加一个元素组件
+ 场景数据里给它一层元素（见 ui/public/scenes.json）。
"""

from .base import GAMES, Game, GameError, GameHub, register
from .wordle import WordleGame  # 导入即注册

__all__ = ["GAMES", "Game", "GameError", "GameHub", "WordleGame", "register"]
