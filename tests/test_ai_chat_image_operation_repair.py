from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from extensions.AIChat import AiChat
from utils.image_operation_policy import EDIT_REQUEST_FAILED_MESSAGE, ImageOperationPolicy


class AiChatImageOperationRepairTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_with_available_candidate_is_left_to_model_semantics(self):
        cog = _cog(_response(_create_payload()))

        parsed, _ = await cog._complete_and_parse_with_raw(
            [],
            object(),
            None,
            image_operation_policy=ImageOperationPolicy(("current:0",)),
        )

        self.assertEqual(parsed.image_generation.operation, "create")
        self.assertEqual(cog._complete_with_retry.await_count, 1)

    async def test_unknown_edit_source_gets_one_candidate_id_repair(self):
        cog = _cog(
            _response(_edit_payload("history:404:0")),
            _response(_edit_payload("current:0")),
        )

        parsed, raw = await cog._complete_and_parse_with_raw(
            [],
            object(),
            None,
            image_operation_policy=ImageOperationPolicy(("current:0",)),
        )

        self.assertEqual(parsed.image_generation.source_image_ids, ("current:0",))
        self.assertEqual(raw, _edit_payload("current:0"))
        repair_messages = cog._complete_with_retry.await_args_list[1].args[0]
        self.assertIn('"current:0"', repair_messages[-1]["content"])
        self.assertNotIn("必須是 edit", repair_messages[-1]["content"])

    async def test_failed_unknown_source_repair_suppresses_image_action(self):
        cog = _cog(
            _response(_edit_payload("history:404:0")),
            _response(_edit_payload("history:405:0")),
        )

        parsed, _ = await cog._complete_and_parse_with_raw(
            [],
            object(),
            None,
            image_operation_policy=ImageOperationPolicy(("current:0",)),
        )

        self.assertIsNone(parsed.image_generation)
        self.assertIn(EDIT_REQUEST_FAILED_MESSAGE, parsed.reply_text)
        self.assertEqual(cog._complete_with_retry.await_count, 2)

    async def test_missing_operation_still_gets_generic_schema_repair(self):
        cog = _cog(
            _response('{"replyText":"處理中。","imageGeneration":{"needed":true,"prompt":"change hair"}}'),
            _response(_edit_payload("reply:90:0")),
        )

        with patch.dict("os.environ", {"AI_IMAGINE_ENABLED": "1"}):
            parsed, _ = await cog._complete_and_parse_with_raw(
                [],
                object(),
                None,
                image_operation_policy=ImageOperationPolicy(("reply:90:0",)),
            )

        self.assertEqual(parsed.image_generation.operation, "edit")
        repair_messages = cog._complete_with_retry.await_args_list[1].args[0]
        self.assertIn("operation", repair_messages[-1]["content"])


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
