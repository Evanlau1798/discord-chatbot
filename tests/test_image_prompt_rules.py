from __future__ import annotations

import unittest

from utils.json_response_protocol import build_repair_instruction
from utils.persona_image_prompt import IMAGE_TEXT_POLICY, merge_persona_image_prompt
from utils.persona_store import Persona, PersonaPromptBuilder


class ImagePromptRuleTests(unittest.TestCase):
    def test_system_prompt_requires_explicit_text_request_for_visible_text(self):
        persona = Persona(key="test", name="Test", data={"characterName": "Test"})

        prompt = PersonaPromptBuilder().build_system_prompt(persona)

        self.assertIn("除非使用者明確指示在圖片中加入特定文字，否則不要加入明文的文字", prompt)

    def test_system_prompt_requires_fast_browser_search_before_persona_reply(self):
        persona = Persona(key="test", name="Test", data={"characterName": "Test"})

        prompt = PersonaPromptBuilder().build_system_prompt(persona)

        self.assertIn("需要網頁搜尋", prompt)
        self.assertIn("不要先輸出 replyText", prompt)
        self.assertIn("browser.searchQuery", prompt)

    def test_repair_instruction_keeps_image_text_rule(self):
        instruction = build_repair_instruction()

        self.assertIn("除非使用者明確指示在圖片中加入特定文字", instruction)
        self.assertIn("imageGeneration.prompt 不要加入明文文字", instruction)

    def test_repair_instruction_keeps_fast_browser_search_rule(self):
        instruction = build_repair_instruction()

        self.assertIn("需要網頁搜尋", instruction)
        self.assertIn("不要先輸出 replyText", instruction)
        self.assertIn("browser.searchQuery", instruction)

    def test_merge_prompt_adds_text_policy_without_persona_reference(self):
        prompt = merge_persona_image_prompt("", "a clean portrait")

        self.assertIn(IMAGE_TEXT_POLICY, prompt)
        self.assertIn("User image request:\na clean portrait", prompt)

    def test_merge_prompt_adds_text_policy_with_persona_reference(self):
        prompt = merge_persona_image_prompt("silver hair; red eyes", "standing in a cafe")

        self.assertIn("silver hair; red eyes", prompt)
        self.assertIn(IMAGE_TEXT_POLICY, prompt)
        self.assertIn("User image request:\nstanding in a cafe", prompt)


if __name__ == "__main__":
    unittest.main()
