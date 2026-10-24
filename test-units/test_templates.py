"""消息模板：Jinja 命名空间、沙箱、渲染兜底、校验移除与 preview 的非阻塞提示。"""

import unittest
from unittest.mock import patch

from neuro_simulator.stream import settings as stream_settings
from neuro_simulator.stream.persona import build_template_store
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
            "metadata": {"title": "Testing", "category": "Just Chatting"},
            "scene": {"desc": "A cozy room", "prompt_context": "You are playing Wordle."},
            "games": {
                "active": "wordle",
                "wordle": {"phase": "typing", "summary": "playing, 2 of 6 guesses used"},
            },
        },
        "neuro_status": {"turn": {"silent_s": 90, "idle": True}},
        "neuro_said": "I think the word is crane.",
        "messages": "- alice: hello\n- bob: LUL",
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
                    default="default {{ messages }}",
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
            "{% if neuro_status.turn.silent_s and neuro_status.turn.silent_s > 45 %}"
            "Neuro has gone quiet.{% endif %}\n"
            "{% if neuro_said %}Neuro said: {{ neuro_said }}{% endif %}\n"
            "{{ messages }}"
        )
        out = self.store.render("neuro_user", _context())
        self.assertIn("Testing [Just Chatting]", out)
        self.assertIn("You are playing Wordle.", out)
        self.assertIn("Game: playing, 2 of 6 guesses used", out)
        self.assertIn("Neuro has gone quiet.", out)
        self.assertIn("Neuro said: I think the word is crane.", out)
        self.assertIn("- alice: hello", out)

    async def test_undefined_paths_render_empty_without_raising(self):
        await self._save(
            "A{{ stream_status.nope.deep }}B{{ neuro_status.missing }}C{{ nope }}D"
        )
        self.assertEqual(self.store.render("neuro_user", _context()), "ABCD")

    async def test_context_is_recursively_plain(self):
        ctx = _context()
        self.assertTrue(_plain(ctx), "context 里出现了业务对象")
        for value in ctx.values():
            self.assertIn(type(value), (dict, str, int, float, bool, type(None)))

    async def test_sandbox_blocks_attribute_escape(self):
        await self._save("{{ ''.__class__ }}")
        # 沙箱直接把它渲染成空（不会把对象泄露出去）
        out = self.store.render("neuro_user", {"messages": "hi"})
        self.assertNotIn("__class__", out)
        self.assertNotIn("str", out)
        preview = self.store.preview("neuro_user", {"messages": "hi"})
        self.assertTrue(preview["rendered"])
        self.assertNotIn("__class__", preview["prompt"])
        # 更深层的属性链同样拿不到对象
        await self._save("{{ ''.__class__.__mro__ }}")
        self.assertEqual(self.store.render("neuro_user", {}), "")

    async def test_unknown_global_renders_empty(self):
        await self._save("{{ config }}{{ secret }}{{ messages }}")
        self.assertEqual(self.store.render("neuro_user", {"messages": "ok"}), "ok")

    async def test_syntax_error_falls_back_to_last_success_then_recovers(self):
        await self._save("OK: {{ messages }}")
        self.assertEqual(self.store.render("neuro_user", {"messages": "one"}), "OK: one")
        # 保存坏模板本身允许（用户明确要求：编辑者自己负责）
        await self._save("BROKEN {{ messages ")
        self.assertEqual(
            self.store.render("neuro_user", {"messages": "two"}),
            "OK: one",
            "渲染失败必须退回上一版成功文本",
        )
        self.assertIsNotNone(self.store.template_error)
        self.assertEqual(self.store.template_error["name"], "neuro_user")
        # 下一轮成功后清除
        await self._save("FIXED: {{ messages }}")
        self.assertEqual(self.store.render("neuro_user", {"messages": "three"}), "FIXED: three")
        self.assertIsNone(self.store.template_error)

    async def test_never_rendered_falls_back_to_messages_fact(self):
        await self._save("BROKEN {{")
        self.assertEqual(
            self.store.render("neuro_user", {"messages": "- a: hi"}),
            "Recent viewer messages:\n- a: hi",
        )

    async def test_required_placeholders_no_longer_block_save(self):
        await self._save("No messages placeholder here.")
        self.assertEqual(self.store.content("neuro_user"), "No messages placeholder here.")
        with self.assertRaises(TemplateError) as ctx:
            await self._save("   ")
        self.assertEqual(ctx.exception.status, 400)

    async def test_preview_reports_unresolved_paths_and_expanded(self):
        await self._save(
            "{{ stream_status.scene.desc }} {{ stream_status.nope.x }} {{ messages }}"
        )
        preview = self.store.preview("neuro_user", _context())
        self.assertTrue(preview["rendered"])
        self.assertIsNone(preview["error"])
        self.assertEqual(preview["unresolved_paths"], ["stream_status.nope.x"])
        self.assertEqual(preview["expanded"]["messages"], len(_context()["messages"]))
        self.assertIn("A cozy room", preview["prompt"])

    async def test_preview_bad_draft_is_non_blocking(self):
        preview = self.store.preview("neuro_user", _context(), "{{ broken")
        self.assertFalse(preview["rendered"])
        self.assertTrue(preview["error"])
        # 草稿失败不能污染实况模板的兜底
        self.assertIsNone(self.store.template_error)


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
        self.assertIn("{{ messages }}", neuro)
        self.assertIn("stream_status.scene.prompt_context", neuro)
        self.assertIn("{{ messages }}", chatbot)
        self.assertIn("neuro_said", chatbot)
        self.assertIn("neuro_status.turn.silent_s", chatbot)
        for gone in ("stream_info", "{{title}}", "{{category}}", "{{scene}}", "{{round}}"):
            self.assertNotIn(gone, neuro)
            self.assertNotIn(gone, chatbot)


if __name__ == "__main__":
    unittest.main()
