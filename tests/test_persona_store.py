from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from utils.ai_chat_settings import AiChatUserSettingsStore
from utils.persona_store import PersonaPromptBuilder, PersonaStore


class PersonaStoreTests(unittest.TestCase):
    def test_default_persona_uses_repo_example_when_env_unset(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            persona_dir = Path(temp_dir)
            (persona_dir / "example.json").write_text(
                json.dumps({"characterName": "Example Bot"}, ensure_ascii=False),
                encoding="utf-8",
            )

            with patch.dict(os.environ, {}, clear=True):
                persona = PersonaStore(persona_dir).default_persona()

        self.assertIsNotNone(persona)
        self.assertEqual(persona.key, "example")

    def test_default_persona_uses_env_key_when_set(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            persona_dir = Path(temp_dir)
            (persona_dir / "example.json").write_text(
                json.dumps({"characterName": "Example Bot"}, ensure_ascii=False),
                encoding="utf-8",
            )
            (persona_dir / "sakura.json").write_text(
                json.dumps({"characterName": "Sakura"}, ensure_ascii=False),
                encoding="utf-8",
            )

            with patch.dict(os.environ, {"DEFAULT_PERSONA_KEY": "sakura"}, clear=True):
                persona = PersonaStore(persona_dir).default_persona()

        self.assertIsNotNone(persona)
        self.assertEqual(persona.key, "sakura")

    def test_private_persona_file_resolves_without_private_suffix(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            persona_dir = Path(temp_dir)
            (persona_dir / "sakura.private.json").write_text(
                json.dumps({"characterName": "Sakura Private"}, ensure_ascii=False),
                encoding="utf-8",
            )

            with patch.dict(os.environ, {"DEFAULT_PERSONA_KEY": "sakura"}, clear=True):
                persona = PersonaStore(persona_dir).default_persona()

        self.assertIsNotNone(persona)
        self.assertEqual(persona.key, "sakura")
        self.assertEqual(persona.name, "Sakura Private")

    def test_missing_default_persona_message_mentions_unconfigured_persona(self):
        with self.assertRaisesRegex(ValueError, "尚未設定人設資訊"):
            PersonaPromptBuilder().build_system_prompt(None)

    def test_default_user_setting_uses_repo_example_when_env_unset(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(os.environ, {}, clear=True):
                store = AiChatUserSettingsStore(path=str(Path(temp_dir) / "settings.pickle"))
                self.assertEqual(store.get(user_id="user-1")["persona"], "example")

    def test_default_user_setting_uses_env_key_when_set(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(os.environ, {"DEFAULT_PERSONA_KEY": "sakura"}, clear=True):
                store = AiChatUserSettingsStore(path=str(Path(temp_dir) / "settings.pickle"))
                self.assertEqual(store.get(user_id="user-1")["persona"], "sakura")


if __name__ == "__main__":
    unittest.main()
