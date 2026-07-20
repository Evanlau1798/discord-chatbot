from __future__ import annotations

import json
import logging

from utils.image_operation_policy import (
    build_candidate_prompt_payloads,
    build_image_operation_policy,
    label_candidate_content_parts,
)
from utils.json_response_protocol import ParsedAIResponse
from utils.message_media import build_multimodal_content

logger = logging.getLogger("discord.extensions.AIChat")
IMAGE_REFERENCE_FAILURE_MESSAGE = "找不到可回溯的原圖，請重新附圖或直接回覆原圖後再試一次。"


class AiChatImageReferenceFlowMixin:
    async def _complete_after_image_reference(
        self,
        request_messages,
        raw_response,
        parsed,
        message,
        persona_key,
        request_status,
        explicit_candidates,
        historical_references,
    ):
        request = parsed.image_reference
        if request is None:
            return parsed, raw_response, request_messages, list(explicit_candidates), build_image_operation_policy(explicit_candidates)
        allowed_ids = {reference.reference_id for reference in historical_references}
        requested_ids = tuple(item for item in request.message_reference_ids if item in allowed_ids)
        resolver = getattr(self, "image_reference_resolver", None)
        loaded = (
            await resolver.resolve_requested(message, requested_ids, historical_references)
            if (
                resolver is not None
                and requested_ids
                and len(requested_ids) == len(request.message_reference_ids)
            )
            else []
        )
        logger.info(
            "ai_chat.image_reference_resolved requested_count=%s allowed_count=%s loaded_image_count=%s",
            len(request.message_reference_ids),
            len(requested_ids),
            len(loaded),
        )
        if not loaded:
            return build_image_reference_failure_response(), raw_response, request_messages, list(explicit_candidates), build_image_operation_policy(explicit_candidates)
        candidates = [*explicit_candidates, *loaded]
        policy = build_image_operation_policy(candidates)
        followup_messages = request_messages + [
            {"role": "assistant", "content": raw_response},
            {"role": "user", "content": _build_reference_followup_content(loaded)},
        ]
        final, final_raw = await self._complete_and_parse_with_raw(
            followup_messages,
            message,
            persona_key,
            request_status,
            image_operation_policy=policy,
        )
        if final.image_reference is not None:
            logger.warning("ai_chat.image_reference_repeat_rejected loaded_image_count=%s", len(loaded))
            final = build_image_reference_failure_response()
        return final, final_raw, followup_messages, candidates, policy


def _build_reference_followup_content(candidates):
    _, candidate_parts = label_candidate_content_parts(candidates, ())
    payload = {
        "inputType": "image_reference_result",
        "payload": {
            "imageGenerationCandidates": build_candidate_prompt_payloads(candidates, ()),
            "instruction": (
                "Requested historical images are now loaded as trusted candidates. "
                "Complete the original request and do not request imageReference again."
            ),
        },
    }
    return build_multimodal_content(
        json.dumps(payload, ensure_ascii=False),
        image_parts=candidate_parts,
    )


def build_image_reference_failure_response() -> ParsedAIResponse:
    return ParsedAIResponse(reply_text=IMAGE_REFERENCE_FAILURE_MESSAGE)
