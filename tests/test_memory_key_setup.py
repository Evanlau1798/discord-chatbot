from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from utils.memory_key_setup import generate_memory_key, inspect_memory_key
from utils.memory_store import MemoryStore


class MemoryKeySetupTests(unittest.TestCase):
    def test_inspection_distinguishes_missing_blank_invalid_and_duplicate_values(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            env_path = Path(tmp_dir) / ".env"
            self.assertEqual(inspect_memory_key(env_path), "env_missing")

            env_path.write_text("DISCORD_BOT_TOKEN=x\n", encoding="utf-8")
            self.assertEqual(inspect_memory_key(env_path), "missing")

            env_path.write_text("MEMORY_ENCRYPTION_KEY= # generated on first run\n", encoding="utf-8")
            self.assertEqual(inspect_memory_key(env_path), "blank")

            env_path.write_text("MEMORY_ENCRYPTION_KEY=ordinary-password\n", encoding="utf-8")
            self.assertEqual(inspect_memory_key(env_path), "invalid")

            env_path.write_text("MEMORY_ENCRYPTION_KEY=\nexport MEMORY_ENCRYPTION_KEY=\n", encoding="utf-8")
            self.assertEqual(inspect_memory_key(env_path), "duplicate")

    def test_generation_replaces_blank_value_without_changing_other_settings(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            env_path = Path(tmp_dir) / ".env"
            env_path.write_text(
                "DISCORD_BOT_TOKEN=token\nMEMORY_ENCRYPTION_KEY=\nAI_CHAT_PROVIDER=nvidia\n",
                encoding="utf-8",
            )

            generate_memory_key(env_path)
            updated = env_path.read_text(encoding="utf-8")

        self.assertEqual(inspect_memory_key_from_text(updated), "valid")
        self.assertIn("DISCORD_BOT_TOKEN=token\n", updated)
        self.assertIn("AI_CHAT_PROVIDER=nvidia\n", updated)
        self.assertNotIn("ordinary-password", updated)

    def test_generation_appends_missing_field_and_refuses_existing_invalid_key(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            env_path = Path(tmp_dir) / ".env"
            env_path.write_text("AI_CHAT_PROVIDER=nvidia", encoding="utf-8")
            generate_memory_key(env_path)
            self.assertEqual(inspect_memory_key(env_path), "valid")
            self.assertIn("\nMEMORY_ENCRYPTION_KEY=", env_path.read_text(encoding="utf-8"))

            env_path.write_text("MEMORY_ENCRYPTION_KEY=do-not-overwrite\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "state is invalid"):
                generate_memory_key(env_path)
            self.assertEqual(
                env_path.read_text(encoding="utf-8"),
                "MEMORY_ENCRYPTION_KEY=do-not-overwrite\n",
            )

    def test_generated_key_encrypts_and_decrypts_memory_store_records(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            env_path = root / ".env"
            env_path.write_text("MEMORY_ENCRYPTION_KEY=\n", encoding="utf-8")
            generate_memory_key(env_path)
            generated_key = env_path.read_text(encoding="utf-8").split("=", 1)[1].strip()
            store = MemoryStore(root / "memory.db", key=generated_key)

            store.set_memory("user", "stable preference")

            self.assertEqual(store.get_memory("user"), "stable preference")


def inspect_memory_key_from_text(text: str) -> str:
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = Path(tmp_dir) / ".env"
        path.write_text(text, encoding="utf-8")
        return inspect_memory_key(path)


if __name__ == "__main__":
    unittest.main()
