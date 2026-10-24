"""消息模板：Jinja 沙箱、三道关卡（G1 编译 / G2 最严苛 / G3 现实）、形状漂移。

G1/G2/静态路径在保存时拒绝；G3 只在 preview 里展示。运行期仍保留最小兜底，
但不再记住"上一版成功文本"。
"""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from neuro_simulator.neuro_sama import server as neuro_server
from neuro_simulator.neuro_sama import scenes as neuro_scenes
from neuro_simulator.neuro_sama.ui_state import UiState
from neuro_simulator.stream import scenes as stream_scenes
from neuro_simulator.stream import settings as stream_settings
from neuro_simulator.stream import status as stream_status
from neuro_simulator.stream.audience import AudienceTracker
from neuro_simulator.stream.persona import build_template_store
from neuro_simulator.template_shape import (
    NEURO_SHAPE,
    RUNTIME_KEY,
    STREAM_SHAPE,
)
from neuro_simulator.templates import TemplateError, TemplateSpec, TemplateStore


class _Storage:
    """只实现 TemplateStore 用到的两件事（不碰 SQLite）。"""

    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}
        self.seq = 0

    async def get_prompt(self, name: str):
        return self.rows.get(name)

    async def upsert_prompt(self, content: str, name: str):
        self.seq += 1
        self.rows[name] = {"content": content, "updated_at": f"t{self.seq}"}


def _context() -> dict:
    return {
        "stream_status": {
            "loop": {"state": "live", "round": 3, "intro": None},
            "metadata": {"title": "Testing", "category": "Just Chatting", "tags": [], "live": True},
            "scene": {
                "id": "play_wordle",
                "label": "Wordle",
                "icon": None,
                "desc": "A cozy room",
                "prompt_context": "You are playing Wordle.",
                "duration": 120.0,
                "elapsed": 10.5,
            },
            "games": {
                "active": "wordle",
                "wordle": {"phase": "typing", "summary": "playing, 2 of 6 guesses used"},
            },
        },
        "neuro_status": {"turn": {"silent_s": 90, "idle": True}},
        "neuro_said": "I think the word is crane.",
        "chat_messages": "- alice: hello\n- bob: LUL",
        "datetime": "2026-01-01 12:00:00",
    }


def _plain(value) -> bool:
    if value is None or isinstance(value, (bool, int, float, str)):
        return True
    if isinstance(value, dict):
        return all(isinstance(k, str) and _plain(v) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return all(_plain(v) for v in value)
    return False


def _new_store(default: str = "default {{ chat_messages }}") -> TemplateStore:
    return TemplateStore(
        _Storage(),
        [TemplateSpec(name="neuro_user", label="neuro", description="", default=default)],
    )


class TestGates(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.storage = _Storage()
        self.store = TemplateStore(
            self.storage,
            [
                TemplateSpec(
                    name="neuro_user",
                    label="neuro",
                    description="",
                    default="default {{ chat_messages }}",
                )
            ],
        )
        await self.store.load()

    async def _save(self, text: str) -> dict:
        return await self.store.save("neuro_user", text)

    async def _assert_rejected(self, text: str, *needles: str) -> None:
        with self.assertRaises(TemplateError) as ctx:
            await self._save(text)
        self.assertEqual(ctx.exception.status, 400)
        for needle in needles:
            self.assertIn(needle, ctx.exception.message)

    async def _assert_accepted(self, text: str) -> dict:
        return await self._save(text)

    async def test_g1_syntax_error_is_400_with_line_number(self):
        await self._assert_rejected("line one\n{{ broken", "模板语法错误", "第 2 行")

    async def test_g1_sandbox_escape_is_400(self):
        # 沙箱越权在编译期不发生，但要被 G2 的渲染挡住（SecurityError）
        await self._assert_rejected("{{ ''.__class__ }}", "SecurityError")

    async def test_g2_typo_field_is_400(self):
        await self._assert_rejected(
            "{{ stream_status.metadata.titel }}", "stream_status.metadata.titel", "不存在"
        )

    async def test_g2_old_messages_name_is_400(self):
        """`messages` 已改名 `chat_messages`，旧名必须被拒而不是静默变空。

        改名最大的风险是"库里的旧模板还写着旧名"：未知变量在实况下渲染成空串，
        Neuro 会安静地收不到任何弹幕。G2 用 StrictUndefined 风格的骨架，未知根变量
        当场拒绝，把这种漂移暴露到保存/preview 而不是直播现场。
        """
        await self._assert_rejected("{{ messages }}", "未知变量")
        await self._assert_accepted("{{ chat_messages }}")

    async def test_g2_unguarded_comparison_is_400(self):
        await self._assert_rejected(
            "{% if neuro_status.turn.silent_s > 45 %}asleep{% endif %}", "数据缺失"
        )

    async def test_g2_unguarded_comparison_in_later_branch_is_400(self):
        # 主骨架 neuro_said="" 会走第一个分支；G2 的第二遍用非空 neuro_said
        # 让 elif 也真的被渲染，避免"分支没走到"漏判。
        await self._assert_rejected(
            "{% if not neuro_said %}online"
            "{% elif neuro_status.turn.silent_s > 45 %}asleep{% endif %}",
            "数据缺失",
        )

    async def test_g2_sandbox_attribute_on_status_is_400(self):
        await self._assert_rejected("{{ stream_status.__class__ }}", "SecurityError")

    async def test_g2_does_not_reject_runtime_index(self):
        validation = await self._save(
            "{{ stream_status.games[stream_status.games.active].summary }}"
        )
        self.assertTrue(validation["gate"]["g2_ok"])
        self.assertTrue(
            any("cannot statically verify" in w for w in validation["warnings"])
        )

    async def test_g2_optional_root_requires_defence(self):
        await self._assert_rejected("{{ neuro_status.turn.silent_s }}", "数据缺失")
        for text in (
            "{{ neuro_status.turn.silent_s|default('') }}",
            "{% if neuro_status.turn %}{{ neuro_status.turn.silent_s }}{% endif %}",
            "{{ neuro_status.turn.silent_s|int(0) }}",
        ):
            with self.subTest(text=text):
                validation = await self._save(text)
                self.assertTrue(validation["gate"]["g2_ok"])

    async def test_unknown_root_variable_is_400(self):
        await self._assert_rejected("{{ config }}", "未知变量")
        await self._assert_rejected("{{ secret|default('') }}", "未知变量")

    async def test_unknown_section_is_400(self):
        await self._assert_rejected("{{ stream_status.nope.x }}", "stream_status.nope")


class TestJinjaRendering(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.storage = _Storage()
        self.store = TemplateStore(
            self.storage,
            [
                TemplateSpec(
                    name="neuro_user",
                    label="neuro",
                    description="",
                    default="default {{ chat_messages }}",
                )
            ],
        )
        await self.store.load()

    async def _save(self, text: str) -> None:
        await self.store.save("neuro_user", text)

    async def test_namespace_paths_and_conditionals(self):
        await self._save(
            "{{ stream_status.metadata.title }} [{{ stream_status.metadata.category }}]\n"
            "{{ stream_status.scene.prompt_context }}\n"
            "{% if stream_status.games.active %}Game: "
            "{{ stream_status.games[stream_status.games.active].summary }}{% endif %}\n"
            "{% if neuro_status.turn.silent_s|int(0) > 45 %}"
            "Neuro has gone quiet.{% endif %}\n"
            "{% if neuro_said %}Neuro said: {{ neuro_said }}{% endif %}\n"
            "{{ chat_messages }}"
        )
        out = self.store.render("neuro_user", _context())
        self.assertIn("Testing [Just Chatting]", out)
        self.assertIn("You are playing Wordle.", out)
        self.assertIn("Game: playing, 2 of 6 guesses used", out)
        self.assertIn("Neuro has gone quiet.", out)
        self.assertIn("Neuro said: I think the word is crane.", out)
        self.assertIn("- alice: hello", out)

    async def test_sandbox_blocks_attribute_escape(self):
        # 保存路径已经拒绝；这里验证 preview 的 G1/G2 报告不会泄露对象
        preview = self.store.preview("neuro_user", {"chat_messages": "hi"}, "{{ ''.__class__ }}")
        self.assertTrue(preview["gate"]["g1_ok"])
        self.assertFalse(preview["gate"]["g2_ok"])
        self.assertIn("SecurityError", preview["gate_errors"]["g2"])
        self.assertNotIn("__class__", preview["prompt"])

    async def test_context_is_recursively_plain(self):
        ctx = _context()
        self.assertTrue(_plain(ctx), "context 里出现了业务对象")
        for value in ctx.values():
            self.assertIn(type(value), (dict, str, int, float, bool, type(None)))

    async def test_bad_db_row_uses_minimal_fallback_and_recovers(self):
        """绕过保存直接写 DB 的坏模板：最小兜底 + template_error，成功即清除。"""
        await self.storage.upsert_prompt("BROKEN {{ messages ", "neuro_user")
        await self.store.load()
        self.assertEqual(
            self.store.render("neuro_user", {"chat_messages": "- a: hi"}),
            "Recent viewer messages:\n- a: hi",
        )
        self.assertIsNotNone(self.store.template_error)
        self.assertEqual(self.store.template_error["name"], "neuro_user")

        await self.storage.upsert_prompt("FIXED: {{ chat_messages }}", "neuro_user")
        await self.store.load()
        self.assertEqual(
            self.store.render("neuro_user", {"chat_messages": "three"}), "FIXED: three"
        )
        self.assertIsNone(self.store.template_error)

    async def test_bad_db_row_without_messages_returns_empty(self):
        await self.storage.upsert_prompt("BROKEN {{", "neuro_user")
        await self.store.load()
        self.assertEqual(self.store.render("neuro_user", {}), "")

    async def test_required_placeholders_no_longer_block_save(self):
        await self._save("No messages placeholder here.")
        self.assertEqual(self.store.content("neuro_user"), "No messages placeholder here.")
        with self.assertRaises(TemplateError) as ctx:
            await self._save("   ")
        self.assertEqual(ctx.exception.status, 400)

    async def test_preview_reports_gate_warnings_and_expanded(self):
        await self._save(
            "{{ stream_status.scene.desc }} "
            "{{ stream_status.games[stream_status.games.active].summary }} {{ chat_messages }}"
        )
        preview = self.store.preview("neuro_user", _context())
        self.assertTrue(preview["rendered"])
        self.assertIsNone(preview["error"])
        self.assertTrue(preview["gate"]["g1_ok"])
        self.assertTrue(preview["gate"]["g2_ok"])
        self.assertTrue(preview["gate"]["skeleton_rendered"])
        self.assertEqual(len(preview["warnings"]), 1)
        self.assertIn("cannot statically verify", preview["warnings"][0])
        self.assertEqual(preview["expanded"]["chat_messages"], len(_context()["chat_messages"]))
        self.assertIn("A cozy room", preview["prompt"])

    async def test_preview_bad_draft_is_non_blocking(self):
        preview = self.store.preview("neuro_user", _context(), "{{ broken")
        self.assertFalse(preview["rendered"])
        self.assertTrue(preview["error"])
        self.assertFalse(preview["gate"]["g1_ok"])
        # 草稿失败不能污染实况模板的错误状态
        self.assertIsNone(self.store.template_error)


class TestDefaultTemplates(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.store = build_template_store(_Storage())
        await self.store.load()

    def test_defaults_pass_g1_g2_g3(self):
        for name in ("neuro_user", "chatbot_user"):
            source = self.store.content(name)
            gate, warnings, errors = self.store._gate(source)
            self.assertTrue(gate["g1_ok"], name)
            self.assertTrue(gate["g2_ok"], f"{name}: {errors}")
            self.assertTrue(gate["skeleton_rendered"], name)
            with self.subTest(name=name):
                out = self.store.render(name, _context())
                self.assertTrue(out)

        chatbot = self.store.content("chatbot_user")
        self.assertIn("neuro_status.turn.silent_s|int(0) > 45", chatbot)
        # chatbot 不看观众消息：模板里既没有消息块，也没有"别复述"的反向暗示
        self.assertNotIn("chat_messages", chatbot)
        self.assertNotIn("Recent messages", chatbot)
        self.assertNotIn("real viewers", chatbot)

    def test_defaults_render_with_real_shape(self):
        out = self.store.render("chatbot_user", _context())
        self.assertIn("Neuro seems fall asleep on stream...", out)
        self.assertNotIn("- alice: hello", out)  # 观众消息不进 chatbot 的输入

        out = self.store.render("neuro_user", _context())
        self.assertIn("Now streaming: Testing [Just Chatting]", out)
        self.assertIn("You are playing Wordle.", out)
        self.assertIn("Game: playing, 2 of 6 guesses used", out)


class TestShapeDrift(unittest.TestCase):
    """形状是静态契约：provider 新增字段而形状没跟，测试必须失败。"""

    # 这几个分区依赖配置 / 场景存储（model/tts/caption/manifest）或只返回列表
    # （layers），不做 provider 级漂移对比；形状仍随代码手动维护。
    _NEURO_UNCOVERED = {"layers", "model", "tts", "caption", "manifest"}

    @staticmethod
    def _assert_section(name: str, actual: dict, shape=STREAM_SHAPE) -> None:
        declared = {k for k in shape.sections[name] if k != RUNTIME_KEY}
        if declared != set(actual):
            raise AssertionError(
                f"{name} 形状漂移：声明 {sorted(declared)}，provider 返回 {sorted(actual)}"
            )

    @staticmethod
    def _fake_loop() -> SimpleNamespace:
        return SimpleNamespace(
            state="live",
            session={
                "session_id": "s",
                "queue_id": 1,
                "channel": "c",
                "conversation_id": "v",
                "rounds": 2,
                "cursor": 3,
                "priority_cursor": 4,
                "started_at": "t",
            },
            intro_status=lambda: None,
            last_error=None,
            persisted=None,
            scene_snapshot=lambda: {"id": "room", "duration": 1.0, "elapsed": 0.5},
            session_metadata=lambda: ("T", "C"),
            session_tags=lambda: ["x"],
            queue_depth=1,
            priority_depth=2,
            last_message_id=3,
            chatbot=SimpleNamespace(in_flight=0),
            templates=SimpleNamespace(template_error=None),
            games=SimpleNamespace(active_id=None, active=None),
            neuro_link={"reachable": False, "observed_at": None, "url": "", "error": None},
        )

    @staticmethod
    def _chatbot_settings() -> SimpleNamespace:
        return SimpleNamespace(
            enabled=True,
            model=None,
            model_ref=None,
            interval_s=60.0,
            count=3,
            max_concurrent=1,
            interval_jitter=0.0,
        )

    def test_stream_sections_match_providers(self):
        loop = self._fake_loop()
        with patch.object(
            stream_settings,
            "load",
            return_value=SimpleNamespace(chatbot=self._chatbot_settings()),
        ), patch.object(stream_scenes, "prompt_context", return_value="p"), patch.object(
            stream_scenes, "label", return_value="L"
        ), patch.object(stream_scenes, "icon", return_value=None), patch.object(
            stream_scenes, "desc", return_value="D"
        ):
            providers = {
                "loop": stream_status._loop_current(loop),
                "scene": stream_status._scene_current(loop),
                "metadata": stream_status._metadata_current(loop),
                "audience": AudienceTracker().snapshot(),
                "chat": stream_status._chat_current(loop),
                "chatbot": stream_status._chatbot_current(loop),
                "games": stream_status._games_current(loop),
                "neuro_link": dict(loop.neuro_link),
            }
        for name, data in providers.items():
            with self.subTest(section=name):
                self._assert_section(name, data)

    def test_neuro_turn_matches_projection(self):
        projection = UiState().turn_projection("stream")
        self._assert_section("turn", projection, NEURO_SHAPE)

    def test_neuro_scene_matches_provider(self):
        ui = SimpleNamespace(
            scene_snapshot=lambda: {"id": "room", "duration": 1.0, "elapsed": 0.5}
        )
        with patch.object(neuro_scenes, "label", return_value="L"), patch.object(
            neuro_scenes, "desc", return_value="D"
        ), patch.object(
            neuro_scenes,
            "find",
            return_value={"icon": None, "state_script": {"steps": []}},
        ):
            data = neuro_server._scene_current(ui)
        self._assert_section("scene", data, NEURO_SHAPE)

    def test_all_sections_are_covered_or_allowlisted(self):
        """新增分区必须补漂移测试，或显式加进 allowlist 并说明原因。"""
        self.assertEqual(
            set(NEURO_SHAPE.sections) - {"turn", "scene"},
            self._NEURO_UNCOVERED,
        )
        self.assertEqual(
            set(STREAM_SHAPE.sections),
            {
                "loop",
                "scene",
                "metadata",
                "audience",
                "chat",
                "chatbot",
                "games",
                "neuro_link",
            },
        )

    def test_drift_guard_detects_a_new_field(self):
        """演示：给 turn_projection 加一个形状里没有的字段，守卫测试会失败。"""
        real = UiState().turn_projection("stream")

        def _with_new_field(self, channel=None):
            return {**real, "new_field": 1}

        with patch.object(UiState, "turn_projection", _with_new_field):
            with self.assertRaises(AssertionError):
                self._assert_section("turn", UiState().turn_projection("stream"), NEURO_SHAPE)


class TestChatbotSettingsMigration(unittest.IsolatedAsyncioTestCase):
    async def test_residual_sleep_key_is_ignored(self):
        class _Cfg:
            def module(self, name):
                return {
                    "neuro_url": "http://neuro.test",
                    "chatbot": {
                        "enabled": True,
                        "model": "",
                        # 老配置残留：未知键必须被静默忽略，不能 400
                        "neuro_sleep_timeout_s": 45.0,
                    },
                }

            def llm_service(self, ref):
                return None

        with patch.object(stream_settings, "get_config", return_value=_Cfg()):
            loaded = stream_settings.load()
        self.assertFalse(hasattr(loaded.chatbot, "neuro_sleep_timeout_s"))


class TestStreamTemplateSpecs(unittest.IsolatedAsyncioTestCase):
    async def test_default_templates_use_namespaces(self):
        store = build_template_store(_Storage())
        await store.load()
        neuro = store.content("neuro_user")
        chatbot = store.content("chatbot_user")
        self.assertIn("{{ chat_messages }}", neuro)
        self.assertIn("stream_status.scene.prompt_context", neuro)
        self.assertIn("neuro_said", chatbot)
        self.assertIn("neuro_status.turn.silent_s", chatbot)
        for gone in ("stream_info", "{{title}}", "{{category}}", "{{scene}}", "{{round}}"):
            self.assertNotIn(gone, neuro)
            self.assertNotIn(gone, chatbot)

    async def test_missing_placeholders_tolerates_whitespace(self) -> None:
        """`{{ chat_messages }}` 与 `{{chat_messages}}` 是同一个变量。

        曾经的裸字符串比对让**默认模板自己**在编辑器顶上亮一条
        "缺少占位符"——假的警告会把人推向"去改一个本来没坏的模板"。
        """
        store = build_template_store(_Storage())
        await store.load()
        for form in ("{{chat_messages}}", "{{ chat_messages }}", "{{  chat_messages  }}",
                     "{{\n  chat_messages \n}}", "{{- chat_messages -}}"):
            with self.subTest(form=repr(form)):
                info = store.preview("neuro_user", content=f"{form}\n{{ datetime }}")
                self.assertEqual(info["missing_placeholders"], [], form)

        info = store.preview("neuro_user", content="只有 datetime：{{ datetime }}")
        self.assertEqual(info["missing_placeholders"], ["chat_messages"])

    async def test_default_templates_report_no_missing_placeholder(self) -> None:
        """播种出来的两份默认模板必须自检干净（回归 2026-10-24 实机看到的假警告）。"""
        store = build_template_store(_Storage())
        await store.load()
        for name in ("neuro_user", "chatbot_user"):
            with self.subTest(name=name):
                info = store.preview(name, content=store.content(name))
                self.assertEqual(info["missing_placeholders"], [])


if __name__ == "__main__":
    unittest.main()
