"""Persona 三层测试：默认播种、core memory 人工 CRUD、占位符渲染与预览。"""

import tempfile
import unittest
from pathlib import Path

from neuro_simulator.neuro_sama.memory import MemoryStore
from neuro_simulator.neuro_sama.persona import (
    CORE_PLACEHOLDER,
    SHORT_TERM_PLACEHOLDER,
    PersonaError,
    PersonaStore,
)
from neuro_simulator.neuro_sama.storage import Storage


class TestPersona(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "data.db"
        self.storage = Storage(self.db)
        await self.storage.init()
        self.persona = PersonaStore(self.storage)
        await self.persona.load()
        self.memory = MemoryStore(self.storage, 4000)
        await self.memory.load()

    async def asyncTearDown(self):
        await self.storage.close()
        self.tmp.cleanup()

    async def test_fresh_db_seeds_prompt_and_core_memory(self):
        self.assertGreater(len(self.persona.prompt), 1000)
        titles = [b.title for b in self.persona.blocks]
        self.assertEqual(
            titles,
            ["Persona", "Vedal", "Evil", "Anny", "Chat", "Other People", "Lore & Running Jokes"],
        )
        self.assertIn(CORE_PLACEHOLDER, self.persona.prompt)
        self.assertIn(SHORT_TERM_PLACEHOLDER, self.persona.prompt)

    async def test_seed_is_not_repeated_after_deleting_everything(self):
        for block in list(self.persona.blocks):
            await self.persona.delete_core(block.id)
        self.assertEqual(self.persona.blocks, [])

        # 同一 DB 重新打开：meta.persona_seeded 已置位，不能把默认内容塞回来
        await self.storage.close()
        self.storage = Storage(self.db)
        await self.storage.init()
        reloaded = PersonaStore(self.storage)
        await reopened_load(reloaded)
        self.assertEqual(reloaded.blocks, [])

    async def test_core_memory_crud_and_duplicate_title(self):
        block = await self.persona.add_core("Test", "line one\nline two")
        self.assertEqual(block.title, "Test")

        with self.assertRaises(PersonaError) as ctx:
            await self.persona.add_core("test", "duplicate")
        self.assertEqual(ctx.exception.status, 409)

        updated = await self.persona.update_core(block.id, "Test 2", "updated")
        self.assertEqual(updated.title, "Test 2")
        self.assertEqual(updated.content, "updated")

        await self.persona.delete_core(block.id)
        self.assertNotIn(block.id, [b.id for b in self.persona.blocks])

        with self.assertRaises(PersonaError) as ctx:
            await self.persona.delete_core(block.id)
        self.assertEqual(ctx.exception.status, 404)

    async def test_empty_core_memory_is_rejected(self):
        with self.assertRaises(PersonaError):
            await self.persona.add_core("   ", "content")
        with self.assertRaises(PersonaError):
            await self.persona.add_core("Title", "   ")

    async def test_reset_core_replaces_custom_blocks(self):
        for block in list(self.persona.blocks):
            await self.persona.delete_core(block.id)
        await self.persona.add_core("My Own Block", "hand written")
        self.assertFalse(self.persona.is_default_core)

        await self.persona.reset_core()

        self.assertEqual(
            [b.title for b in self.persona.blocks],
            ["Persona", "Vedal", "Evil", "Anny", "Chat", "Other People", "Lore & Running Jokes"],
        )
        self.assertTrue(self.persona.is_default_core)
        self.assertNotIn("My Own Block", [b.title for b in self.persona.blocks])

    async def test_reset_all_restores_prompt_and_core_memory(self):
        await self.persona.save_prompt("Custom prompt {{core_memory}}")
        await self.persona.add_core("Extra", "extra content")
        self.assertFalse(self.persona.is_default_prompt)
        self.assertFalse(self.persona.is_default_core)

        await self.persona.reset_all()

        self.assertTrue(self.persona.is_default_prompt)
        self.assertTrue(self.persona.is_default_core)
        self.assertIn(SHORT_TERM_PLACEHOLDER, self.persona.prompt)
        self.assertNotIn("Extra", [b.title for b in self.persona.blocks])

    async def test_render_replaces_placeholders(self):
        await self.memory.add_entry("Chat likes cookies.")
        rendered = self.persona.render(self.memory.render())

        self.assertIn("### Persona", rendered)
        self.assertIn("### Vedal", rendered)
        self.assertIn("## Short-term Memory", rendered)
        self.assertIn("Chat likes cookies.", rendered)
        self.assertNotIn(CORE_PLACEHOLDER, rendered)
        self.assertNotIn(SHORT_TERM_PLACEHOLDER, rendered)

    async def test_missing_placeholders_are_appended(self):
        await self.persona.save_prompt("Custom prompt without placeholders.")
        rendered = self.persona.render(self.memory.render())

        self.assertTrue(rendered.startswith("Custom prompt without placeholders."))
        self.assertIn("## Core Memory", rendered)
        self.assertIn("## Short-term Memory", rendered)

    async def test_preview_with_draft_does_not_persist(self):
        saved = self.persona.prompt
        preview = self.persona.preview(self.memory.render(), "Draft {{core_memory}}")

        self.assertEqual(self.persona.prompt, saved)
        self.assertIn("Draft ## Core Memory", preview["prompt"])
        self.assertEqual(preview["chars"]["template"], len("Draft {{core_memory}}"))
        self.assertGreater(preview["estimated_tokens"], 0)

    async def test_empty_prompt_is_rejected(self):
        with self.assertRaises(PersonaError) as ctx:
            await self.persona.save_prompt("   \n ")
        self.assertEqual(ctx.exception.status, 400)


async def reopened_load(persona: PersonaStore) -> None:
    """辅助：在 async 测试里重新加载 store。"""
    await persona.load()


if __name__ == "__main__":
    unittest.main()
