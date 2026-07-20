from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from extensions.AIChat import AiChat
from utils.image_operation_policy import EDIT_REQUEST_FAILED_MESSAGE, ImageOperationPolicy


class AiChatImageOperationRepairTests(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_create_for_edit_intent_gets_one_constrained_repair(self):
        cog = _cog(
            _response(_create_payload()),
            _response(_edit_payload("current:0")),
        )
        policy = ImageOperationPolicy(("current:0",), True, "candidate_edit_action")

        parsed, raw = await cog._complete_and_parse_with_raw(
            [{"role": "user", "content": "request"}],
            object(),
            "persona",
            image_operation_policy=policy,
        )

        self.assertEqual(parsed.image_generation.operation, "edit")
        self.assertEqual(parsed.image_generation.source_image_ids, ("current:0",))
        self.assertEqual(raw, _edit_payload("current:0"))
        self.assertEqual(cog._complete_with_retry.await_count, 2)
        repair_messages = cog._complete_with_retry.await_args_list[1].args[0]
        self.assertIn("必須是 edit", repair_messages[-1]["content"])
        self.assertIn('"current:0"', repair_messages[-1]["content"])

    async def test_missing_operation_repair_keeps_edit_constraint(self):
        cog = _cog(
            _response('{"replyText":"處理中。","imageGeneration":{"needed":true,"prompt":"change hair"}}'),
            _response(_edit_payload("reply:90:0")),
        )
        policy = ImageOperationPolicy(("reply:90:0",), True, "candidate_edit_action")

        parsed, _ = await cog._complete_and_parse_with_raw(
            [],
            object(),
            None,
            image_operation_policy=policy,
        )

        self.assertEqual(parsed.image_generation.operation, "edit")
        repair_messages = cog._complete_with_retry.await_args_list[1].args[0]
        self.assertIn("imageGeneration.operation 必須是 edit", repair_messages[-1]["content"])

    async def test_failed_operation_repair_never_falls_back_to_generation(self):
        cog = _cog(_response(_create_payload()), _response(_create_payload()))
        policy = ImageOperationPolicy(("current:0",), True, "candidate_edit_action")

        parsed, _ = await cog._complete_and_parse_with_raw(
            [],
            object(),
            None,
            image_operation_policy=policy,
        )

        self.assertIsNone(parsed.image_generation)
        self.assertIn(EDIT_REQUEST_FAILED_MESSAGE, parsed.reply_text)
        self.assertEqual(cog._complete_with_retry.await_count, 2)

    async def test_create_without_edit_constraint_is_not_repaired(self):
        cog = _cog(_response(_create_payload()))
        policy = ImageOperationPolicy(("reply:90:0",), False, "none")

        parsed, _ = await cog._complete_and_parse_with_raw(
            [],
            object(),
            None,
            image_operation_policy=policy,
        )

        self.assertEqual(parsed.image_generation.operation, "create")
        self.assertEqual(cog._complete_with_retry.await_count, 1)


def _cog(*responses):
    cog = AiChat.__new__(AiChat)
    cog._complete_with_retry = AsyncMock(side_effect=list(responses))
    return cog


def _response(content: str):
    return SimpleNamespace(visible_content=content)


def _create_payload() -> str:
    return '{"replyText":"處理中。","imageGeneration":{"needed":true,"operation":"create","prompt":"draw"}}'


def _edit_payload(source_id: str) -> str:
    return (
        '{"replyText":"處理中。","imageGeneration":{"needed":true,"operation":"edit",'
        f'"prompt":"change hair","sourceImageIds":["{source_id}"]}}}}'
    )


if __name__ == "__main__":
    unittest.main()
