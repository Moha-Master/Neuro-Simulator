"""游戏基类与中枢：活跃游戏的唯一持有者 + 状态广播。"""

import inspect
from abc import ABC, abstractmethod
from typing import Any, Awaitable, Callable, Dict, Optional, Type, Union

# 游戏注册表：子类在各自模块里 register()（避免 __init__ 与子模块循环导入）。
GAMES: Dict[str, Type["Game"]] = {}


def register(cls: Type["Game"]) -> Type["Game"]:
    if not cls.id:
        raise ValueError("游戏必须有 id")
    GAMES[cls.id] = cls
    return cls


class GameError(Exception):
    """面向调用方（Neuro 的工具结果）的错误：不是崩溃，是"这步不能做"。"""


class Game(ABC):
    """一个可被工具驱动的游戏。

    - `scene_id`：玩这个游戏时画面该切到的场景（元素清单里含本游戏的棋盘层）。
    - `element_id`：本游戏在场景里的元素 id，状态就以它为 key 广播。
    - `state()` 必须是**全量快照**：画面组件拿它就能画出当前局面（幂等，不做增量）。
    - `start()` / `act()` / `exit()` 可以返回字符串，也可以返回 awaitable 字符串；
      在线游戏（如 Lichess）需要异步 HTTP，本地游戏保持同步即可。
    """

    id: str = ""
    label: str = ""
    scene_id: str = ""
    element_id: str = ""

    def __init__(self, hub: "GameHub") -> None:
        self.hub = hub

    @abstractmethod
    def start(self) -> Union[str, Awaitable[str]]:
        """开一局（返回给 LLM 的文本）。"""

    @abstractmethod
    def act(self, action: str, args: Dict[str, Any]) -> Union[str, Awaitable[str]]:
        """执行一个动作（返回给 LLM 的文本）。未知动作抛 GameError。"""

    @abstractmethod
    def state(self) -> Dict[str, Any]:
        """全量状态快照（广播给画面）。"""

    def exit(self) -> Union[str, Awaitable[str]]:
        return f"Left the {self.label} game."


class GameHub:
    """同时至多一个活跃游戏；每次动作后广播 element_state 全量快照。"""

    def __init__(
        self,
        publish: Callable[[dict], None],
        set_scene: Callable[[str], None],
    ) -> None:
        self._publish = publish
        self._set_scene = set_scene
        self.active: Optional[Game] = None

    # ---------- 查询 ----------

    @property
    def active_id(self) -> Optional[str]:
        return self.active.id if self.active else None

    def element_states(self) -> Dict[str, Any]:
        """snapshot 用：当前活跃游戏的元素状态（无游戏 = 空）。"""
        if not self.active:
            return {}
        return {self.active.element_id: self.active.state()}

    # ---------- 生命周期 ----------

    async def start(self, game_id: str) -> Dict[str, Any]:
        cls = GAMES.get(game_id)
        if cls is None:
            raise GameError(
                f"Unknown game {game_id!r}. Available: {', '.join(sorted(GAMES)) or 'none'}."
            )
        if self.active:
            await self.aclear()
        game = cls(self)
        text = await self._resolve(game.start())
        self.active = game
        self._set_scene(game.scene_id)
        return self._finish(text)

    async def act(self, game_id: str, action: str, args: Dict[str, Any]) -> Dict[str, Any]:
        if action == "exit":
            return await self.exit(game_id)
        game = self._require(game_id)
        text = await self._resolve(game.act(action, args or {}))
        return self._finish(text)

    async def exit(self, game_id: Optional[str] = None) -> Dict[str, Any]:
        game = self._require(game_id)
        text = await self._resolve(game.exit())
        scene = "room"
        self.active = None
        self._publish(
            {"type": "element_state", "id": game.element_id, "payload": {"game": game.id, "status": "idle"}}
        )
        self._set_scene(scene)
        return {"ok": True, "result_text": text, "game": None, "state": None}

    def clear(self) -> None:
        """场次结束：丢掉活跃游戏（不广播场景切换，session_end 已让画面回黑）。"""
        self.active = None

    async def aclear(self) -> None:
        """异步清理：给在线游戏机会结束远端对局/关闭长连接；不广播场景切换。"""
        game = self.active
        if not game:
            return
        self.active = None
        try:
            await self._resolve(game.exit())
        except Exception:
            pass

    # ---------- 内部 ----------

    @staticmethod
    async def _resolve(value: Union[str, Awaitable[str]]) -> str:
        if inspect.isawaitable(value):
            return await value
        return str(value)

    def _require(self, game_id: Optional[str] = None) -> Game:
        if not self.active:
            raise GameError("No game is running right now. Start one first.")
        if game_id and self.active.id != game_id:
            raise GameError(
                f"The active game is {self.active.id!r}, not {game_id!r}. Exit it first."
            )
        return self.active

    def _finish(self, text: str) -> Dict[str, Any]:
        game = self.active
        assert game is not None
        state = game.state()
        self._publish({"type": "element_state", "id": game.element_id, "payload": state})
        return {"ok": True, "result_text": text, "game": game.id, "state": state}
