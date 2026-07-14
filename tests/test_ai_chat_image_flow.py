from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from utils.ai_chat_image_flow import AiChatImageFlowMixin
from utils.ai_imagine_client import ImagineResult
from utils.image_reference_resolver import ImageReferenceCandidate
from utils.json_response_protocol import ImageGenerationBlock, ParsedAIResponse


class AiChatImageFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_edit_resolves_only_model_selected_candidates(self):
        client = Mock()
        client.generate.return_value = ImagineResult("prompt", [], [Path("tmp/edit.png")], operation="edit")
        cog = _cog(client)
        candidates = [_candidate("current:0", b"first"), _candidate("reply:90:0", b"second")]
        parsed = ParsedAIResponse(
            reply_text="ok",
            image_generation=ImageGenerationBlock(
                needed=True,
                prompt="Change the shirt to blue.",
                operation="edit",
                source_image_ids=("reply:90:0",),
            ),
        )

        paths, error = await cog._maybe_generate_image(parsed, SimpleNamespace(), candidates)

        self.assertEqual(paths, [Path("tmp/edit.png")])
        self.assertEqual(error, "")
        kwargs = client.generate.call_args.kwargs
        self.assertEqual(kwargs["operation"], "edit")
        self.assertEqual(len(kwargs["source_images"]), 1)
        self.assertEqual(kwargs["source_images"][0].data, b"second")

    async def test_missing_selected_candidate_does_not_fallback_to_create(self):
        client = Mock()
        cog = _cog(client)
        parsed = ParsedAIResponse(
            reply_text="ok",
            image_generation=ImageGenerationBlock(
                needed=True,
                prompt="Change it.",
                operation="edit",
                source_image_ids=("recent:404:0",),
            ),
        )

        paths, error = await cog._maybe_generate_image(parsed, SimpleNamespace(), [])

        self.assertEqual(paths, [])
        self.assertIn("重新附圖", error)
        client.generate.assert_not_called()

    async def test_persona_edit_forwards_authoritative_identity_constraints(self):
        client = Mock()
        client.generate.return_value = ImagineResult("prompt", [], [Path("tmp/edit.png")], operation="edit")
        cog = _cog(client)
        parsed = ParsedAIResponse(
            reply_text="ok",
            image_generation=ImageGenerationBlock(
                needed=True,
                prompt="Place the active persona in this scene.",
                operation="edit",
                source_image_ids=("current:0",),
                use_persona_identity=True,
            ),
        )

        await cog._maybe_generate_image(parsed, SimpleNamespace(), [_candidate("current:0", b"scene")])

        generated_prompt = client.generate.call_args.args[0]
        self.assertIn("persona constraints", generated_prompt)
        self.assertIn("authoritative character identity", generated_prompt)

    def test_record_image_reference_uses_requesting_user_as_owner(self):
        store = Mock()
        cog = _cog(Mock(), store=store)
        sent_message = SimpleNamespace(id=500, attachments=[SimpleNamespace(content_type="image/png")])

        cog._record_image_reference(sent_message, owner_id=42)

        store.record_message.assert_called_once_with(sent_message, owner_id=42)


def _cog(client, store=None):
    cog = AiChatImageFlowMixin()
    cog.image_generation_enabled = True
    cog.persona_image_prompt_store = SimpleNamespace(get_prompt=lambda persona: "persona constraints")
    cog._get_imagine_client = lambda: client
    cog.image_reference_store = store
    return cog


def _candidate(candidate_id: str, data: bytes):
    return ImageReferenceCandidate(
        candidate_id=candidate_id,
        source="test",
        message_id="90",
        attachment_id="900",
        filename="source.png",
        mime_type="image/png",
        data=data,
    )


if __name__ == "__main__":
    unittest.main()
