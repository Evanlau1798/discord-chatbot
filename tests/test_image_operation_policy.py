from __future__ import annotations

import unittest
from types import SimpleNamespace

from utils.image_operation_policy import (
    EDIT_REQUEST_FAILED_MESSAGE,
    MISSING_EDIT_SOURCE_MESSAGE,
    ImageOperationPolicy,
    build_candidate_prompt_payloads,
    build_image_operation_repair_instruction,
    image_operation_violation,
    infer_image_operation_policy,
    label_candidate_content_parts,
    suppress_unsafe_image_operation,
)
from utils.json_response_protocol import ImageGenerationBlock, ParsedAIResponse


class ImageOperationPolicyTests(unittest.TestCase):
    def test_candidate_and_edit_action_require_edit(self):
        policy = infer_image_operation_policy("把她的頭髮換成紅色", [_candidate("reply:90:0", "discord_reply")])

        self.assertTrue(policy.requires_edit)
        self.assertEqual(policy.signal, "candidate_edit_action")
        self.assertEqual(policy.candidate_ids, ("reply:90:0",))

    def test_explicit_image_edit_requires_edit_without_candidate(self):
        policy = infer_image_operation_policy("請幫我修改上一張圖片", [])

        self.assertTrue(policy.requires_edit)
        self.assertEqual(policy.signal, "referenced_image_edit")
        self.assertEqual(policy.to_prompt_payload()["allowedSourceImageIds"], [])
        self.assertIn("missingSourceAction", policy.to_prompt_payload())

    def test_using_attached_image_as_basis_requires_edit(self):
        policy = infer_image_operation_policy(
            "使用這張圖的構圖，把角色換成你",
            [_candidate("current:0", "current_attachment")],
        )

        self.assertTrue(policy.requires_edit)

    def test_explicit_from_scratch_request_overrides_incidental_edit_words(self):
        policy = infer_image_operation_policy(
            "請從零畫一張全新圖片，頭髮改成紅色",
            [_candidate("reply:90:0", "discord_reply")],
        )

        self.assertFalse(policy.requires_edit)

    def test_create_is_rejected_for_explicit_edit_intent(self):
        parsed = ParsedAIResponse(
            reply_text="ok",
            image_generation=ImageGenerationBlock(needed=True, prompt="change hair", operation="create"),
        )
        policy = ImageOperationPolicy(("reply:90:0",), True, "candidate_edit_action")

        self.assertEqual(image_operation_violation(parsed, policy), "edit_required")

    def test_edit_rejects_candidate_id_not_in_current_request(self):
        parsed = ParsedAIResponse(
            reply_text="ok",
            image_generation=ImageGenerationBlock(
                needed=True,
                prompt="change hair",
                operation="edit",
                source_image_ids=("recent:404:0",),
            ),
        )
        policy = ImageOperationPolicy(("reply:90:0",), True, "candidate_edit_action")

        self.assertEqual(image_operation_violation(parsed, policy), "unknown_source_id")

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

    def test_candidate_labels_stay_adjacent_to_current_and_referenced_images(self):
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

    def test_repair_instruction_contains_only_allowed_candidate_ids(self):
        policy = ImageOperationPolicy(("current:0",), True, "candidate_edit_action")

        instruction = build_image_operation_repair_instruction(policy, "edit_required")

        self.assertIn('"current:0"', instruction)
        self.assertIn("必須是 edit", instruction)
        self.assertIn("不得改用 create", instruction)

    def test_unsafe_edit_is_suppressed_after_failed_repair(self):
        parsed = ParsedAIResponse(
            reply_text="處理中。",
            image_generation=ImageGenerationBlock(needed=True, prompt="draw", operation="create"),
        )

        safe = suppress_unsafe_image_operation(parsed, ImageOperationPolicy(("current:0",), True))

        self.assertIsNone(safe.image_generation)
        self.assertIn(EDIT_REQUEST_FAILED_MESSAGE, safe.reply_text)

    def test_missing_source_suppression_asks_for_source_image(self):
        parsed = ParsedAIResponse(
            reply_text="處理中。",
            image_generation=ImageGenerationBlock(needed=True, prompt="draw", operation="create"),
        )

        safe = suppress_unsafe_image_operation(parsed, ImageOperationPolicy((), True))

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
