"""Wordle 游戏逻辑与 GameHub：规则判定、状态快照、场景/事件联动。

游戏状态权威在 stream 侧，画面与 Neuro 都只看这里产出的东西：
`state()`（全量快照，广播给画面）与 result_text（给 LLM 读的文本）。
"""

import json
import unittest
from pathlib import Path

from neuro_simulator.stream import scenes as scene_catalog
from neuro_simulator.stream.games import GAMES, ChessGame, GameError, GameHub, WordleGame
from neuro_simulator.stream.games.wordle import MAX_ROWS, evaluate
from neuro_simulator.stream.games.words import load_banks


def game_with_answer(answer: str) -> WordleGame:
    """造一局答案确定的游戏（构造时会随机开局，这里直接改写局面）。"""
    hub = GameHub(publish=lambda ev: None, set_scene=lambda sid: None)
    game = WordleGame(hub)
    game.new_game()
    game.answer = answer.upper()
    game.word_len = len(game.answer)
    game.neuro_word = False
    return game


class TestEvaluate(unittest.TestCase):
    def test_positions(self):
        self.assertEqual(evaluate("CRANE", "CRANE"), ["correct"] * 5)
        # SLATE vs CRANE：A 与 E 位置都对，S/L/T 不在词里
        self.assertEqual(evaluate("SLATE", "CRANE"), ["absent", "absent", "correct", "absent", "correct"])

    def test_duplicate_letters_counted_once(self):
        # answer 只有两个 E：第三个 E 不能也算 present
        self.assertEqual(evaluate("EERIE", "SWEET"), ["present", "present", "absent", "absent", "absent"])
        # answer=ASSOC：位置对的先占额度，剩下的同字母才降级为 present/absent
        self.assertEqual(evaluate("SASSY", "ASSOC"), ["present", "present", "correct", "absent", "absent"])

    def test_correct_beats_present(self):
        # 位置对的先占额度，剩下的同字母才是 present/absent
        self.assertEqual(evaluate("LLAMA", "ALPHA"), ["absent", "correct", "present", "absent", "correct"])


class TestWordleGame(unittest.TestCase):
    def test_type_truncates_at_word_length(self):
        game = game_with_answer("CRANE")
        text = game.type_letters("cranes")
        self.assertEqual("".join(game.typing), "CRANE")
        self.assertIn("ignored", text)

    def test_type_ignores_non_letters(self):
        game = game_with_answer("CRANE")
        game.type_letters("c-r,a!n")
        self.assertEqual("".join(game.typing), "CRAN")

    def test_delete(self):
        game = game_with_answer("CRANE")
        game.type_letters("CRANE")
        game.delete_letters(2)
        self.assertEqual("".join(game.typing), "CRA")
        game.delete_letters(9)
        self.assertEqual(game.typing, [])

    def test_submit_requires_full_row(self):
        game = game_with_answer("CRANE")
        game.type_letters("CR")
        text = game.submit()
        self.assertIn("Cannot submit", text)
        self.assertEqual(game.guesses_used, 0)
        self.assertIsNone(game.statuses[0])

    def test_submit_requires_real_word(self):
        game = game_with_answer("CRANE")
        game.type_letters("XQZZZ")
        text = game.submit()
        self.assertIn("not in the word list", text)
        self.assertEqual(game.guesses_used, 0)
        self.assertEqual("".join(game.typing), "XQZZZ")  # 行内容保留，改一改还能交

    def test_submit_advances_and_colors_keyboard(self):
        game = game_with_answer("CRANE")
        game.type_letters("SLATE")
        text = game.submit()
        self.assertEqual(game.statuses[0], ["absent", "absent", "correct", "absent", "correct"])
        self.assertEqual(game.row, 1)
        self.assertIn("5 guesses left", text)
        self.assertEqual(game.keyboard["A"], "correct")
        self.assertEqual(game.keyboard["E"], "correct")
        self.assertEqual(game.keyboard["S"], "absent")

    def test_keyboard_keeps_best_status(self):
        # MAMBO：A 有但位置不对 → present；ALARM 同一行里 A 既 absent(0) 又 correct(2)，
        # 键盘必须取最好的那个（present→correct 可升级，correct 不会被 absent 降级）
        game = game_with_answer("CRANE")
        game.type_letters("MAMBO")
        game.submit()
        self.assertEqual(game.keyboard["A"], "present")
        game.type_letters("ALARM")
        game.submit()
        self.assertEqual(game.keyboard["A"], "correct")
        self.assertEqual(game.keyboard["R"], "present")
        self.assertEqual(game.keyboard["M"], "absent")

    def test_win_reveals_answer(self):
        game = game_with_answer("CRANE")
        game.type_letters("CRANE")
        text = game.submit()
        self.assertTrue(game.done and game.win)
        self.assertEqual(game.status, "won")
        self.assertIn("YOU GOT IT", text)
        self.assertEqual(game.state()["answer"], "CRANE")

    def test_lose_on_last_row(self):
        game = game_with_answer("CRANE")
        for guess in ("SLATE", "DUMBS", "FIGHT", "VENOM", "BOOZY"):
            game.type_letters(guess)
            game.submit()
        self.assertFalse(game.done)
        self.assertEqual(game.row, MAX_ROWS - 1)
        game.type_letters("BREAD")
        text = game.submit()
        self.assertEqual(game.status, "lost")
        self.assertIn("Out of guesses", text)
        self.assertIn("CRANE", text)

    def test_actions_after_game_over_are_refused(self):
        game = game_with_answer("CRANE")
        game.type_letters("CRANE")
        game.submit()
        self.assertIn("game is over", game.type_letters("XXXXX"))
        self.assertIn("game is over", game.submit())

    def test_restart_resets_board(self):
        game = game_with_answer("CRANE")
        game.type_letters("SLATE")
        game.submit()
        game.new_game()
        self.assertEqual(game.guesses_used, 0)
        self.assertEqual(game.rows, [[] for _ in range(MAX_ROWS)])
        self.assertEqual(game.keyboard, {})
        self.assertFalse(game.done)

    def test_narrative_is_one_line_without_board_details(self):
        game = game_with_answer("CRANE")
        self.assertEqual(game.narrative(game.state()), "playing, 0 of 6 guesses used")
        game.type_letters("SLATE")
        game.submit()
        self.assertEqual(game.narrative(game.state()), "playing, 1 of 6 guesses used")
        game.type_letters("CRANE")
        game.submit()
        self.assertIn("won in 2 of 6 guesses", game.narrative(game.state()))

    def test_state_snapshot_hides_answer_while_playing(self):
        game = game_with_answer("CRANE")
        game.type_letters("SL")
        state = game.state()
        self.assertIsNone(state["answer"])  # 别从画面事件流里漏答案
        self.assertEqual(state["status"], "playing")
        self.assertEqual(state["word_len"], 5)
        self.assertEqual(state["rows"][0], ["S", "L"])
        self.assertEqual(len(state["rows"]), MAX_ROWS)
        self.assertIsNone(state["statuses"][0])

    def test_variable_length_neuro_word(self):
        game = game_with_answer("GIGANEURO")
        game.type_letters("GIGANEURO")
        game.submit()
        self.assertTrue(game.win)
        self.assertEqual(len(game.state()["rows"][0]), 9)

    def test_short_answer_still_spellchecked(self):
        game = game_with_answer("DUCK")
        game.type_letters("QQQQ")
        self.assertIn("not in the word list", game.submit())
        game.delete_letters(4)
        game.type_letters("BOOM")
        game.submit()
        self.assertEqual(game.guesses_used, 1)


class TestGameHub(unittest.IsolatedAsyncioTestCase):
    def make_hub(self):
        events, scenes = [], []
        return GameHub(publish=events.append, set_scene=scenes.append), events, scenes

    async def test_start_switches_scene_and_broadcasts(self):
        hub, events, scenes = self.make_hub()
        result = await hub.start("wordle")
        self.assertEqual(scenes, ["play_wordle"])
        self.assertEqual(events[0]["type"], "element_state")
        self.assertEqual(events[0]["id"], "game_wordle")
        self.assertEqual(events[0]["payload"]["status"], "playing")
        self.assertIn("Wordle started", result["result_text"])
        self.assertEqual(hub.element_states()["game_wordle"]["game"], "wordle")

    async def test_unknown_game(self):
        hub, _, _ = self.make_hub()
        with self.assertRaises(GameError):
            await hub.start("checkers")

    async def test_act_without_game(self):
        hub, _, _ = self.make_hub()
        with self.assertRaises(GameError):
            await hub.act("wordle", "submit", {})

    async def test_wrong_game_name(self):
        hub, _, _ = self.make_hub()
        await hub.start("wordle")
        with self.assertRaises(GameError):
            await hub.act("lichess", "submit", {})

    async def test_exit_returns_to_room_and_clears(self):
        hub, events, scenes = self.make_hub()
        await hub.start("wordle")
        await hub.act("wordle", "type", {"letters": "CR"})
        result = await hub.act("wordle", "exit", {})
        self.assertIsNone(hub.active)
        self.assertEqual(scenes[-1], "room")
        self.assertEqual(events[-1]["payload"]["status"], "idle")
        self.assertIn("Wordle", result["result_text"])

    async def test_clear_leaves_no_active_game(self):
        hub, _, _ = self.make_hub()
        await hub.start("wordle")
        hub.clear()
        self.assertIsNone(hub.active)

    async def test_async_clear_leaves_no_active_game(self):
        hub, _, _ = self.make_hub()
        await hub.start("wordle")
        await hub.aclear()
        self.assertIsNone(hub.active)

    async def test_only_one_active_game(self):
        hub, _, _ = self.make_hub()
        await hub.start("wordle")
        await hub.start("wordle")  # 重开顶掉旧的
        self.assertEqual(hub.active_id, "wordle")


class TestWordBanks(unittest.TestCase):
    def test_common_answers_are_valid_words_of_five_letters(self):
        banks = load_banks()
        self.assertGreater(len(banks.answers), 1500)
        bad = [w for w in banks.answers if len(w) != 5 or not banks.valid(w)]
        self.assertEqual(bad, [])

    def test_dictionary_is_indexed_by_length(self):
        banks = load_banks()
        for n in (3, 4, 5, 6, 7, 8, 9, 10):
            self.assertTrue(banks.covers(n), n)
        self.assertFalse(banks.covers(16))

    def test_dictionary_rejects_nonsense(self):
        banks = load_banks()
        self.assertTrue(banks.valid("crane"))
        self.assertFalse(banks.valid("xqzzz"))
        self.assertFalse(banks.valid("abcd"))
        self.assertTrue(banks.valid("quilt"))  # 5 字母之外的词长也能校验
        self.assertFalse(banks.valid("qqqq"))

    def test_neuro_words_accepted_as_guesses(self):
        banks = load_banks()
        for w in ("NEURO", "VEDAL", "FILTERED"):
            self.assertTrue(banks.valid(w), w)

    def test_uncovered_length_passes_through(self):
        banks = load_banks()
        self.assertTrue(banks.valid("A" * 16))  # 16 字母没有词表，只能放行


class TestChessMirror(unittest.TestCase):
    def make_game(self, moves: str = "") -> ChessGame:
        hub = GameHub(publish=lambda ev: None, set_scene=lambda sid: None)
        game = ChessGame(hub)
        game.game_id = "testgame"
        game.status = "playing"
        game.my_color = "white"
        game.color_known = True
        if moves:
            game._set_moves(moves)
        return game

    def test_color_is_detected_from_game_full(self):
        game = self.make_game()
        game.bot_username = "files-transfer-bot"
        game.color_known = False
        # AI 在白、我方（bot）在黑
        game._apply_game_full(
            {
                "id": "abcd1234",
                "initialFen": "startpos",
                "white": {"aiLevel": 3},
                "black": {"id": "files-transfer-bot", "name": "Files-Transfer-Bot", "title": "BOT"},
            }
        )
        self.assertEqual(game.my_color, "black")
        self.assertTrue(game.color_known)
        self.assertEqual(game.white_name, "Stockfish level 3")
        self.assertEqual(game.black_name, "Files-Transfer-Bot")

    def test_color_falls_back_to_bot_title(self):
        game = self.make_game()
        game.bot_username = None
        game.color_known = False
        game._apply_game_full(
            {
                "id": "abcd1234",
                "initialFen": "startpos",
                "white": {"id": "files-transfer-bot", "name": "Bot", "title": "BOT"},
                "black": {"aiLevel": 1},
            }
        )
        self.assertEqual(game.my_color, "white")
        self.assertTrue(game.color_known)

    def test_replay_updates_board_and_legal_moves(self):
        game = self.make_game("e2e4 e7e5")
        self.assertEqual(game.turn, "white")
        self.assertIn("f1b5", game._legal_uci())
        self.assertEqual(game.last_move["san"], "e5")
        self.assertEqual(game.state()["board"][4][4], "P")  # e4
        self.assertEqual(game.state()["board"][3][4], "p")  # e5
        self.assertEqual(game.state()["board"][7][4], "K")  # e1

    def test_resolves_san_and_number(self):
        game = self.make_game()
        self.assertEqual(game._resolve_move("Nf3"), "g1f3")
        self.assertEqual(game._resolve_move("1"), "a2a3")
        with self.assertRaises(GameError):
            game._resolve_move("e2e5")

    def test_state_is_full_snapshot(self):
        game = self.make_game("d2d4")
        state = game.state()
        self.assertEqual(state["game"], "chess")
        self.assertEqual(state["mode"], "lichess")
        self.assertEqual(state["turn"], "black")
        self.assertEqual(state["legal_move_count"], len(state["legal_moves"]))
        self.assertEqual(state["orientation"], "white")
        self.assertFalse(state["in_check"])
        self.assertEqual(state["moves"], ["d2d4"])
        self.assertIn("3P4", state["fen"])

    def test_narrative_has_no_fen_or_legal_moves(self):
        game = self.make_game("e2e4 e7e5")
        text = game.narrative(game.state())
        self.assertEqual(text, "White to move, 2 moves in, not in check")
        self.assertNotIn("e2e4", text)
        self.assertNotIn("Nf3", text)
        self.assertNotIn("/", text, "不能出现 FEN")

    def test_legal_moves_are_listed_as_san(self):
        game = self.make_game()
        sans = game._legal_san()
        self.assertIn("Nf3", sans)
        self.assertIn("e4", sans)
        # 列表里不应再出现 4 字符的 UCI 形式
        self.assertNotIn("g1f3", sans)
        self.assertEqual(game.state()["legal_moves_san"], sans)
        self.assertEqual(len(game.state()["legal_moves"]), len(sans))

    def test_castling_uses_standard_uci(self):
        game = self.make_game("e2e4 e7e5 g1f3 b8c6 f1c4 f8c5")
        self.assertIn("O-O", game._legal_san())
        self.assertIn("e1g1", game._legal_uci())  # 标准 UCI，不是 esca 内部的 e1h1
        uci = game._resolve_move("O-O")
        self.assertEqual(uci, "e1g1")
        # 0-0 / e1g1 两种输入都接受
        self.assertEqual(game._resolve_move("0-0"), "e1g1")
        self.assertEqual(game._resolve_move("e1g1"), "e1g1")
        info = game._move_info(game.board.position.parse_uci(uci))
        self.assertEqual(info["san"], "O-O")
        self.assertEqual(info["to"], "g1")  # 王的落点，不是车格 h1
        self.assertIsNone(info["captured"])  # 不能把自己的车当成被吃

    def test_black_castling(self):
        game = self.make_game()
        game.my_color = "black"
        game.initial_fen = "r3k2r/8/8/8/8/8/8/R3K2R b KQkq - 0 1"
        game._set_moves("")
        self.assertIn("O-O", game._legal_san())
        self.assertEqual(game._resolve_move("O-O"), "e8g8")

    def test_move_labels_capture_and_side(self):
        game = self.make_game("e2e4 d7d5 e4d5")
        game.black_name = "Stockfish level 3"
        text = game.describe()
        self.assertIn("You are White. Opponent: Stockfish level 3.", text)
        self.assertIn(
            "Last move: You played exd5, capturing Black's pawn on d5 (e4d5).", text
        )
        self.assertIn("It is Stockfish level 3's turn.", text)

    def test_describe_labels_opponent_move(self):
        game = self.make_game("e2e4 e7e5")
        game.black_name = "Stockfish level 3"
        text = game.describe()
        self.assertIn("Last move: Stockfish level 3 played e5 (e7e5).", text)
        self.assertIn("It is your turn to move.", text)

    def test_describe_omits_game_id_and_bot_name(self):
        game = self.make_game("e2e4 e7e5")
        game.game_id = "aCJXWdGy"
        game.white_name = "Files-Transfer-Bot"
        text = game.describe()
        self.assertNotIn("aCJXWdGy", text)
        self.assertNotIn("Files-Transfer-Bot", text)

    def test_illegal_move_reports_legal_san(self):
        game = self.make_game()
        with self.assertRaises(GameError) as ctx:
            game._resolve_move("Bg4")
        message = str(ctx.exception)
        self.assertIn("is not a legal move", message)
        self.assertIn("Nf3", message)
        self.assertNotIn("g1f3", message)


class TestSceneCatalogContract(unittest.TestCase):
    """游戏与场景数据的契约：scene_id / element_id 必须在 scenes.json 里存在。"""

    @classmethod
    def setUpClass(cls):
        path = Path("server/neuro_simulator/stream/defaults/scenes.json")
        if not path.is_file():  # 兼容从别处跑测试
            path = (
                Path(scene_catalog.__file__).parent
                / "ui"
                / "public"
                / "scenes.json"
            )
        cls.manifest = json.loads(path.read_text(encoding="utf-8"))
        cls.scenes = {s["id"]: s for s in cls.manifest["scenes"]}

    def test_registry_matches_scene_manifest(self):
        for gid, cls in GAMES.items():
            with self.subTest(game=gid):
                self.assertIn(cls.scene_id, self.scenes, "游戏指向的场景不存在")
                ids = [e["id"] for e in self.scenes[cls.scene_id]["elements"]]
                self.assertIn(cls.element_id, ids, "场景里没有该游戏的元素层")
                self.assertEqual(len(ids), len(set(ids)), "场景内元素 id 重复")

    def test_every_scene_has_a_background(self):
        for sid, scene in self.scenes.items():
            with self.subTest(scene=sid):
                kinds = [(e["content"] or {}).get("kind") for e in scene["elements"]]
                has_bg = bool({"image", "color"} & set(kinds))
                # room 的背景由 neuro 窗口自己的 bg_room 提供（合成器不铺背景）；
                # 媒体场景则是合成器背景 + 隐藏 neuro bg_room。
                neuro = scene.get("neuro") or {}
                has_neuro_bg = bool(neuro) and not neuro.get("hidden", False)
                self.assertTrue(has_bg or has_neuro_bg, f"{sid} 没有背景层")

    def test_element_ids_unique_within_scene(self):
        for sid, scene in self.scenes.items():
            ids = [e["id"] for e in scene["elements"]]
            self.assertEqual(len(ids), len(set(ids)), sid)


if __name__ == "__main__":
    unittest.main()
