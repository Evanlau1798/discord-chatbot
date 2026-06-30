from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from utils.ai_chat_settings import AiChatUserSettingsStore
from utils.persona_store import PersonaPromptBuilder, PersonaStore


class PersonaStoreTests(unittest.TestCase):
    def test_default_persona_uses_akira_when_available(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            persona_dir = Path(temp_dir)
            (persona_dir / "akira.json").write_text(
                json.dumps({"characterName": "Akira"}, ensure_ascii=False),
                encoding="utf-8",
            )

            persona = PersonaStore(persona_dir).default_persona()

        self.assertIsNotNone(persona)
        self.assertEqual(persona.key, "akira")

    def test_only_example_persona_is_not_treated_as_configured_default(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            persona_dir = Path(temp_dir)
            (persona_dir / "example.json").write_text(
                json.dumps({"characterName": "Example Bot"}, ensure_ascii=False),
                encoding="utf-8",
            )
            store = PersonaStore(persona_dir)

            persona = store.default_persona()

        self.assertIsNone(persona)

    def test_private_persona_file_resolves_without_private_suffix(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            persona_dir = Path(temp_dir)
            (persona_dir / "akira.private.json").write_text(
                json.dumps({"characterName": "Akira Private"}, ensure_ascii=False),
                encoding="utf-8",
            )

            persona = PersonaStore(persona_dir).default_persona()

        self.assertIsNotNone(persona)
        self.assertEqual(persona.key, "akira")
        self.assertEqual(persona.name, "Akira Private")

    def test_missing_default_persona_message_mentions_unconfigured_persona(self):
        with self.assertRaisesRegex(ValueError, "尚未設定人設資訊"):
            PersonaPromptBuilder().build_system_prompt(None)

    def test_default_user_setting_uses_akira(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = AiChatUserSettingsStore(path=str(Path(temp_dir) / "settings.pickle"))

            self.assertEqual(store.get(user_id="user-1")["persona"], "akira")


if __name__ == "__main__":
    unittest.main()
