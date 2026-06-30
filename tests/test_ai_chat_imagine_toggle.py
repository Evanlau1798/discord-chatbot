from __future__ import annotations

import unittest
from types import SimpleNamespace

from extensions.AIChat import AiChat
from utils.json_response_protocol import ImageGenerationBlock, ParsedAIResponse


class AiChatImagineToggleTests(unittest.IsolatedAsyncioTestCase):
    async def test_maybe_generate_image_ignores_model_request_when_disabled(self):
        cog = AiChat.__new__(AiChat)
        cog.image_generation_enabled = False
        cog._get_imagine_client = lambda: self.fail("Imagine client should not be created")
        parsed = ParsedAIResponse(
            reply_text="ok",
            image_generation=ImageGenerationBlock(needed=True, prompt="draw a cat"),
        )

        image_paths, image_error = await cog._maybe_generate_image(parsed, SimpleNamespace())

        self.assertEqual(image_paths, [])
        self.assertFalse(image_error)


if __name__ == "__main__":
    unittest.main()
