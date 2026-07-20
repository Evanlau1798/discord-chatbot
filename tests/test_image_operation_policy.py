from __future__ import annotations

import unittest
from types import SimpleNamespace

from utils.image_operation_policy import (
    EDIT_REQUEST_FAILED_MESSAGE,
    MISSING_EDIT_SOURCE_MESSAGE,
    ImageOperationPolicy,
    build_candidate_prompt_payloads,
    build_image_operation_policy,
    build_image_operation_repair_instruction,
    image_operation_violation,
    label_candidate_content_parts,
    suppress_unsafe_image_operation,
)
from utils.json_response_protocol import ImageGenerationBlock, ParsedAIResponse


class ImageOperationPolicyTests(unittest.TestCase):
    def test_policy_collects_only_unique_trusted_candidate_ids(self):
        policy = build_image_operation_policy([
            _candidate("current:0", "current_attachment"),
            _candidate("current:0", "current_attachment"),
            _candidate("reply:90:0", "discord_reply"),
        ])

        self.assertEqual(policy.candidate_ids, ("current:0", "reply:90:0"))

    def test_create_is_not_keyword_classified_as_a_violation(self):
        parsed = ParsedAIResponse(
            reply_text="ok",
            image_generation=ImageGenerationBlock(needed=True, prompt="draw", operation="create"),
        )

        self.assertEqual(image_operation_violation(parsed, ImageOperationPolicy(("current:0",))), "")

    def test_edit_rejects_candidate_id_not_in_current_request(self):
        parsed = ParsedAIResponse(
            reply_text="ok",
            image_generation=ImageGenerationBlock(
                needed=True,
                prompt="change hair",
                operation="edit",
                source_image_ids=("history:404:0",),
            ),
        )

        self.assertEqual(
            image_operation_violation(parsed, ImageOperationPolicy(("reply:90:0",))),
            "unknown_source_id",
        )

    def test_candidate_payload_maps_ids_to_zero_based_visual_inputs(self):
        candidates = [
            _candidate("current:0", "current_attachment"),
            _candidate("reply:90:0", "discord_reply"),
        ]
        media_parts = [
            {"type": "image_bytes"},
            {"type": "text", "text": "transcript"},
            {"type": "video_bytes"},
        ]

        payloads = build_candidate_prompt_payloads(candidates, media_parts)

        self.assertEqual(payloads[0]["visualIndex"], 0)
        self.assertEqual(payloads[1]["visualIndex"], 2)

    def test_candidate_labels_stay_adjacent_to_visual_inputs(self):
        current = _candidate("current:0", "current_attachment")
        referenced = _candidate("reply:90:0", "discord_reply")
        current.to_content_part = lambda: {"type": "image_bytes", "image_bytes": {"data": b"current"}}
        referenced.to_content_part = lambda: {"type": "image_bytes", "image_bytes": {"data": b"reply"}}

        labeled_media, appended = label_candidate_content_parts(
            [current, referenced],
            [{"type": "image_bytes", "image_bytes": {"data": b"current"}}],
        )

        self.assertIn('id="current:0"', labeled_media[0]["text"])
        self.assertEqual(labeled_media[1]["image_bytes"]["data"], b"current")
        self.assertIn('id="reply:90:0"', appended[0]["text"])
        self.assertEqual(appended[1]["image_bytes"]["data"], b"reply")

    def test_repair_instruction_only_corrects_untrusted_source_ids(self):
        instruction = build_image_operation_repair_instruction(ImageOperationPolicy(("current:0",)))

        self.assertIn('"current:0"', instruction)
        self.assertIn("不得編造 ID", instruction)
        self.assertNotIn("必須是 edit", instruction)
        self.assertNotIn("不得改用 create", instruction)

    def test_failed_unknown_source_repair_is_suppressed(self):
        parsed = ParsedAIResponse(
            reply_text="處理中。",
            image_generation=ImageGenerationBlock(
                needed=True,
                prompt="draw",
                operation="edit",
                source_image_ids=("history:404:0",),
            ),
        )

        safe = suppress_unsafe_image_operation(parsed, ImageOperationPolicy(("current:0",)))

        self.assertIsNone(safe.image_generation)
        self.assertIn(EDIT_REQUEST_FAILED_MESSAGE, safe.reply_text)

    def test_missing_source_suppression_asks_for_source_image(self):
        safe = suppress_unsafe_image_operation(
            ParsedAIResponse(reply_text="處理中。"),
            ImageOperationPolicy(()),
        )

        self.assertIn(MISSING_EDIT_SOURCE_MESSAGE, safe.reply_text)


def _candidate(candidate_id: str, source: str):
    return SimpleNamespace(
        candidate_id=candidate_id,
        source=source,
        to_prompt_payload=lambda visual_index=None: {
            "id": candidate_id,
            "source": source,
            "visualIndex": visual_index,
        },
        to_content_part=lambda: {"type": "image_bytes", "image_bytes": {"data": b"image"}},
    )


if __name__ == "__main__":
    unittest.main()
