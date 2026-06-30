from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from utils.ai_chat_settings import AiChatUserSettingsStore
from utils.persona_store import PersonaStore


class PersonaStoreTests(unittest.TestCase):
    def test_default_persona_uses_open_source_example(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            persona_dir = Path(temp_dir)
            (persona_dir / "example.json").write_text(
                json.dumps({"characterName": "Example Bot"}, ensure_ascii=False),
                encoding="utf-8",
            )

            persona = PersonaStore(persona_dir).default_persona()

        self.assertIsNotNone(persona)
        self.assertEqual(persona.key, "example")

    def test_default_user_setting_uses_open_source_example(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = AiChatUserSettingsStore(path=str(Path(temp_dir) / "settings.pickle"))

            self.assertEqual(store.get(user_id="user-1")["persona"], "example")


if __name__ == "__main__":
    unittest.main()
