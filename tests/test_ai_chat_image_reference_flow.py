from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from utils.ai_chat_image_reference_flow import AiChatImageReferenceFlowMixin, IMAGE_REFERENCE_FAILURE_MESSAGE
from utils.image_reference_resolver import DeferredImageReference, ImageReferenceCandidate
from utils.json_response_protocol import ImageGenerationBlock, ImageReferenceRequest, ParsedAIResponse


class AiChatImageReferenceFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_allowed_history_request_loads_images_only_for_followup(self):
        candidate = _candidate()
        cog = AiChatImageReferenceFlowMixin()
        cog.image_reference_resolver = SimpleNamespace(resolve_requested=AsyncMock(return_value=[candidate]))
        final = ParsedAIResponse(
            reply_text="開始修改。",
            image_generation=ImageGenerationBlock(
                needed=True,
                operation="edit",
                prompt="copy the pose",
                source_image_ids=("history:80:0",),
            ),
        )
        cog._complete_and_parse_with_raw = AsyncMock(return_value=(final, "final-json"))

        result = await cog._complete_after_image_reference(
            [{"role": "system", "content": "rules"}],
            _request_json(),
            _request_response(),
            object(),
            "persona",
            None,
            [],
            [_reference()],
        )

        parsed, raw, messages, candidates, policy = result
        self.assertEqual(parsed.image_generation.source_image_ids, ("history:80:0",))
        self.assertEqual(raw, "final-json")
        self.assertEqual(policy.candidate_ids, ("history:80:0",))
        self.assertEqual(candidates[0].data, b"history-image")
        followup_content = messages[-1]["content"]
        self.assertIn("image_reference_result", followup_content[0]["text"])
        self.assertIn('id="history:80:0"', followup_content[1]["text"])
        self.assertEqual(followup_content[2]["image_bytes"]["data"], b"history-image")

    async def test_unadvertised_reference_is_rejected_without_fetch_or_model_call(self):
        cog = AiChatImageReferenceFlowMixin()
        cog.image_reference_resolver = SimpleNamespace(resolve_requested=AsyncMock())
        cog._complete_and_parse_with_raw = AsyncMock()
        parsed = ParsedAIResponse(
            reply_text="",
            image_reference=ImageReferenceRequest(("discord-message:10:20:999",)),
        )

        final, *_ = await cog._complete_after_image_reference(
            [], "raw", parsed, object(), None, None, [], [_reference()]
        )

        self.assertEqual(final.reply_text, IMAGE_REFERENCE_FAILURE_MESSAGE)
        cog.image_reference_resolver.resolve_requested.assert_not_awaited()
        cog._complete_and_parse_with_raw.assert_not_awaited()

    async def test_second_image_reference_request_is_not_executed(self):
        cog = AiChatImageReferenceFlowMixin()
        cog.image_reference_resolver = SimpleNamespace(resolve_requested=AsyncMock(return_value=[_candidate()]))
        cog._complete_and_parse_with_raw = AsyncMock(return_value=(_request_response(), "repeat"))

        final, *_ = await cog._complete_after_image_reference(
            [], _request_json(), _request_response(), object(), None, None, [], [_reference()]
        )

        self.assertEqual(final.reply_text, IMAGE_REFERENCE_FAILURE_MESSAGE)
        self.assertIsNone(final.image_reference)
        self.assertEqual(cog.image_reference_resolver.resolve_requested.await_count, 1)


def _reference():
    return DeferredImageReference(
        reference_id="discord-message:10:20:80",
        guild_id="10",
        channel_id="20",
        message_id="80",
        image_count=1,
    )


def _candidate():
    return ImageReferenceCandidate(
        candidate_id="history:80:0",
        source="history_request",
        message_id="80",
        attachment_id="800",
        filename="history.png",
        mime_type="image/png",
        data=b"history-image",
    )


def _request_response():
    return ParsedAIResponse(
        reply_text="",
        image_reference=ImageReferenceRequest(("discord-message:10:20:80",)),
    )


def _request_json():
    return '{"imageReference":{"messageReferenceIds":["discord-message:10:20:80"]}}'


if __name__ == "__main__":
    unittest.main()
