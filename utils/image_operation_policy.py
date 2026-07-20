from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Any, Iterable

from utils.json_response_protocol import ParsedAIResponse

EDIT_REQUEST_FAILED_MESSAGE = "這次無法安全建立圖片編輯請求，請再試一次。"
MISSING_EDIT_SOURCE_MESSAGE = "找不到你指定的原圖，請重新附圖或直接回覆原圖後再試一次。"

_VISUAL_PART_TYPES = frozenset({"image_url", "image_bytes", "video_bytes"})


@dataclass(frozen=True)
class ImageOperationPolicy:
    candidate_ids: tuple[str, ...] = ()


def build_image_operation_policy(candidates: Iterable[Any] = ()) -> ImageOperationPolicy:
    return ImageOperationPolicy(candidate_ids=_candidate_ids(candidates))


def image_operation_violation(parsed: ParsedAIResponse, policy: ImageOperationPolicy | None) -> str:
    if policy is None or parsed.image_generation is None:
        return ""
    block = parsed.image_generation
    if block.operation == "edit" and any(source_id not in policy.candidate_ids for source_id in block.source_image_ids):
        return "unknown_source_id"
    return ""


def build_image_operation_repair_instruction(policy: ImageOperationPolicy) -> str:
    candidate_json = json.dumps(list(policy.candidate_ids), ensure_ascii=False)
    if not policy.candidate_ids:
        return (
            "上一輪 edit 使用了本輪不存在的來源圖片，而且目前沒有可用的圖片候選。"
            "請省略 imageGeneration，並在 replyText 要求使用者重新附圖或直接回覆原圖。"
        )
    return (
        "上一輪 edit 使用了本輪不存在的來源圖片 ID。"
        f"sourceImageIds 只能從以下候選選擇：{candidate_json}，不得編造 ID。"
        "請保留使用者原本的圖片操作意圖與其他有效 JSON 欄位，只修正來源 ID 並輸出單一 JSON 物件。"
    )


def suppress_unsafe_image_operation(
    parsed: ParsedAIResponse,
    policy: ImageOperationPolicy,
) -> ParsedAIResponse:
    notice = MISSING_EDIT_SOURCE_MESSAGE if not policy.candidate_ids else EDIT_REQUEST_FAILED_MESSAGE
    reply_text = str(parsed.reply_text or "").strip()
    if notice not in reply_text:
        reply_text = f"{reply_text}\n\n{notice}".strip()
    return replace(parsed, reply_text=reply_text, image_generation=None)


def build_candidate_prompt_payloads(candidates: Iterable[Any], media_parts: Iterable[dict]) -> list[dict]:
    candidates = list(candidates or ())
    media_visual_count = sum(
        1 for part in media_parts or () if isinstance(part, dict) and part.get("type") in _VISUAL_PART_TYPES
    )
    current_index = 0
    appended_index = media_visual_count
    payloads = []
    for candidate in candidates:
        if str(getattr(candidate, "source", "")) == "current_attachment":
            visual_index = current_index
            current_index += 1
        else:
            visual_index = appended_index
            appended_index += 1
        payloads.append(candidate.to_prompt_payload(visual_index=visual_index))
    return payloads


def label_candidate_content_parts(
    candidates: Iterable[Any],
    media_parts: Iterable[dict],
) -> tuple[list[dict], list[dict]]:
    candidates = list(candidates or ())
    media_parts = list(media_parts or ())
    payloads = build_candidate_prompt_payloads(candidates, media_parts)
    payload_by_id = {str(item.get("id") or ""): item for item in payloads}
    current_candidates = [item for item in candidates if getattr(item, "source", "") == "current_attachment"]
    labeled_media = []
    current_index = 0
    for part in media_parts:
        if (
            isinstance(part, dict)
            and part.get("type") in _VISUAL_PART_TYPES
            and current_index < len(current_candidates)
        ):
            candidate = current_candidates[current_index]
            labeled_media.append(_candidate_label(payload_by_id.get(candidate.candidate_id, {})))
            current_index += 1
        labeled_media.append(part)
    appended_candidates = []
    for candidate in candidates:
        if getattr(candidate, "source", "") == "current_attachment":
            continue
        appended_candidates.extend([
            _candidate_label(payload_by_id.get(candidate.candidate_id, {})),
            candidate.to_content_part(),
        ])
    return labeled_media, appended_candidates


def _candidate_ids(candidates: Iterable[Any]) -> tuple[str, ...]:
    values = []
    for candidate in candidates or ():
        candidate_id = str(getattr(candidate, "candidate_id", "") or "").strip()
        if candidate_id and candidate_id not in values:
            values.append(candidate_id)
    return tuple(values)


def _candidate_label(payload: dict) -> dict:
    candidate_id = str(payload.get("id") or "")
    visual_index = payload.get("visualIndex")
    return {
        "type": "text",
        "text": (
            f'<trusted_image_candidate id="{candidate_id}" visualIndex="{visual_index}">'
            "The immediately following visual input is this trusted image editing candidate."
            "</trusted_image_candidate>"
        ),
    }
