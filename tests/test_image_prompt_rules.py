from __future__ import annotations

import unittest
from unittest.mock import patch

from utils.json_response_protocol import build_repair_instruction
from utils.persona_image_prompt import IMAGE_STYLE_POLICY, IMAGE_TEXT_POLICY, merge_persona_image_prompt
from utils.persona_store import Persona, PersonaPromptBuilder


class ImagePromptRuleTests(unittest.TestCase):
    def test_system_prompt_requires_explicit_text_request_for_visible_text(self):
        persona = Persona(key="test", name="Test", data={"characterName": "Test"})

        with patch.dict("os.environ", {"AI_IMAGINE_ENABLED": "1"}):
            prompt = PersonaPromptBuilder().build_system_prompt(persona)

        self.assertIn("除非使用者明確指示在圖片中加入特定文字", prompt)
        self.assertIn("否則不要加入", prompt)
        self.assertIn("文字", prompt)

    def test_system_prompt_routes_reference_images_to_edit_protocol(self):
        persona = Persona(key="test", name="Test", data={"characterName": "Test"})

        with patch.dict("os.environ", {"AI_IMAGINE_ENABLED": "1"}):
            prompt = PersonaPromptBuilder().build_system_prompt(persona)

        self.assertIn('operation: "create" | "edit" | "variation"', prompt)
        self.assertIn("sourceImageIds", prompt)
        self.assertIn("usePersonaIdentity", prompt)
        self.assertIn("人設角色", prompt)
        self.assertIn("不得混合", prompt)
        self.assertIn("不可改成從零生圖", prompt)
        self.assertIn("要求使用者重新附圖或直接回覆原圖", prompt)

    def test_system_prompt_omits_image_generation_protocol_when_disabled(self):
        persona = Persona(key="test", name="Test", data={"characterName": "Test"})

        with patch.dict("os.environ", {"AI_IMAGINE_ENABLED": "0"}):
            prompt = PersonaPromptBuilder().build_system_prompt(persona)

        self.assertNotIn("imageGeneration", prompt)
        self.assertNotIn("需要生圖", prompt)

    def test_system_prompt_requires_fast_browser_search_before_persona_reply(self):
        persona = Persona(key="test", name="Test", data={"characterName": "Test"})

        prompt = PersonaPromptBuilder().build_system_prompt(persona)

        self.assertIn("需要網頁搜尋", prompt)
        self.assertIn("不要先輸出 replyText", prompt)
        self.assertIn("browser.searchQuery", prompt)
        self.assertIn("browser.youtubeSearchQuery", prompt)
        self.assertIn("siteDomains", prompt)
        self.assertIn("desiredSources", prompt)
        self.assertIn("sourceProfile", prompt)
        self.assertIn("Discord Markdown", prompt)
        self.assertIn("[有意義的顯示文字](https://example.com)", prompt)
        self.assertIn("每個 query 都必須脫離目前對話後仍能單獨理解", prompt)
        self.assertIn("多個合理指涉對象", prompt)

    def test_system_prompt_requires_cross_language_media_search_queries(self):
        persona = Persona(key="test", name="Test", data={"characterName": "Test"})

        prompt = PersonaPromptBuilder().build_system_prompt(persona)

        self.assertIn("海外人物、遊戲、實況主、影片、梗圖或片段", prompt)
        self.assertIn("常用英文名稱", prompt)
        self.assertIn("最多 3 個 query", prompt)
        self.assertIn("第一個 query 必須是最精準", prompt)
        self.assertIn("可單獨執行", prompt)
        self.assertIn("短暱稱、多義詞或單字代稱太泛", prompt)
        self.assertIn("不要自行猜測", prompt)
        self.assertIn("英文常見說法與同義詞", prompt)
        self.assertIn("只有搜尋結果中出現 YouTube watch", prompt)

    def test_repair_instruction_keeps_image_text_rule(self):
        with patch.dict("os.environ", {"AI_IMAGINE_ENABLED": "1"}):
            instruction = build_repair_instruction()

        self.assertIn("除非使用者明確指示在圖片中加入特定文字", instruction)
        self.assertIn("imageGeneration.prompt 不要加入明文文字", instruction)

    def test_repair_instruction_keeps_image_operation_contract(self):
        with patch.dict("os.environ", {"AI_IMAGINE_ENABLED": "1"}):
            instruction = build_repair_instruction()

        self.assertIn("operation", instruction)
        self.assertIn("sourceImageIds", instruction)
        self.assertIn("usePersonaIdentity", instruction)
        self.assertIn("人設身份", instruction)
        self.assertIn("edit 或 variation", instruction)

    def test_repair_instruction_omits_image_generation_protocol_when_disabled(self):
        with patch.dict("os.environ", {"AI_IMAGINE_ENABLED": "0"}):
            instruction = build_repair_instruction()

        self.assertNotIn("imageGeneration", instruction)
        self.assertNotIn("生圖", instruction)

    def test_repair_instruction_keeps_fast_browser_search_rule(self):
        instruction = build_repair_instruction()

        self.assertIn("需要網頁搜尋", instruction)
        self.assertIn("不要先輸出 replyText", instruction)
        self.assertIn("browser.searchQuery", instruction)
        self.assertIn("browser.youtubeSearchQuery", instruction)
        self.assertIn("Discord Markdown", instruction)
        self.assertIn("[有意義的顯示文字](https://example.com)", instruction)

    def test_repair_instruction_keeps_cross_language_media_search_rule(self):
        instruction = build_repair_instruction()

        self.assertIn("海外人物", instruction)
        self.assertIn("英文別名", instruction)
        self.assertIn("最多三個查詢關鍵字", instruction)

    def test_merge_prompt_adds_text_policy_without_persona_reference(self):
        prompt = merge_persona_image_prompt("", "a clean portrait")

        self.assertIn(IMAGE_TEXT_POLICY, prompt)
        self.assertIn("User image request:\na clean portrait", prompt)

    def test_merge_prompt_adds_modern_anime_style_policy(self):
        prompt = merge_persona_image_prompt("", "a clean portrait")

        self.assertIn(IMAGE_STYLE_POLICY, prompt)
        self.assertIn("polished modern anime illustration style", prompt)
        self.assertIn("soft directional light", prompt)
        self.assertIn("Only use dramatic shadows", prompt)
        self.assertLess(prompt.index("polished modern anime illustration style"), prompt.index("User image request:"))

    def test_edit_prompt_preserves_unspecified_details_without_forcing_create_style(self):
        prompt = merge_persona_image_prompt(
            "character identity constraints",
            "Change only the shirt color to blue.",
            operation="edit",
        )

        self.assertNotIn(IMAGE_STYLE_POLICY, prompt)
        self.assertNotIn("character identity constraints", prompt)
        self.assertIn("Preserve every detail", prompt)
        self.assertIn("Change only the shirt color to blue.", prompt)

    def test_persona_edit_makes_persona_identity_authoritative_without_restyling_scene(self):
        prompt = merge_persona_image_prompt(
            "silver hair; red eyes; black sailor uniform",
            "Place the active persona in the source cafe scene.",
            operation="edit",
            use_persona_identity=True,
        )

        self.assertNotIn(IMAGE_STYLE_POLICY, prompt)
        self.assertIn("silver hair; red eyes; black sailor uniform", prompt)
        self.assertIn("authoritative character identity", prompt)
        self.assertIn("Do not blend", prompt)
        self.assertIn("scene, composition, camera angle, pose", prompt)
        self.assertIn("except conflicting character identity", prompt)

    def test_variation_prompt_preserves_identity_and_uses_reference(self):
        prompt = merge_persona_image_prompt(
            "character identity constraints",
            "Try a different pose.",
            operation="variation",
            use_persona_identity=True,
        )

        self.assertIn("distinct visual variation", prompt)
        self.assertIn("recognizable identity", prompt)
        self.assertIn("character identity constraints", prompt)

    def test_generic_variation_does_not_inject_active_persona_identity(self):
        prompt = merge_persona_image_prompt(
            "character identity constraints",
            "Try a different pose.",
            operation="variation",
        )

        self.assertNotIn("character identity constraints", prompt)

    def test_merge_prompt_adds_text_policy_with_persona_reference(self):
        prompt = merge_persona_image_prompt("silver hair; red eyes", "standing in a cafe")

        self.assertIn("silver hair; red eyes", prompt)
        self.assertIn(IMAGE_TEXT_POLICY, prompt)
        self.assertIn("User image request:\nstanding in a cafe", prompt)


if __name__ == "__main__":
    unittest.main()
