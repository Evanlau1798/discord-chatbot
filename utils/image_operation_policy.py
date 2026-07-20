from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, replace
from typing import Any, Iterable

from utils.json_response_protocol import ParsedAIResponse

EDIT_REQUEST_FAILED_MESSAGE = "這次無法安全建立圖片編輯請求，請再試一次。"
MISSING_EDIT_SOURCE_MESSAGE = "找不到你指定的原圖，請重新附圖或直接回覆原圖後再試一次。"

_DIRECT_EDIT_PATTERN = re.compile(
    r"(?:修圖|改圖|圖片編輯|編輯圖片|照片編輯|編輯照片|擴圖|局部重繪|去背)|"
    r"\b(?:edit|modify|inpaint|outpaint|retouch)\b",
    re.IGNORECASE,
)
_EDIT_ACTION_PATTERN = re.compile(
    r"(?:修改|改成|改為|換成|換掉|變成|調整|移除|刪除|去除|加上|加入|補上|延伸|擴展|"
    r"合併|融合|保留|套用|上色|換背景|參考|仿照|照(?:著)?|依照|根據|使用|採用)|"
    r"\b(?:change|replace|remove|delete|add|extend|merge|combine|preserve|use|reference)\b",
    re.IGNORECASE,
)
_IMAGE_REFERENCE_PATTERN = re.compile(
    r"(?:(?:這|那|上|前|原|剛才|剛剛|附件)[一個]?張?(?:圖|圖片|照片|影像))|"
    r"(?:來源圖|參考圖|原圖|附圖|回覆的圖|回覆圖片)|"
    r"\b(?:this|that|previous|last|source|attached|reference)\s+(?:image|picture|photo)\b",
    re.IGNORECASE,
)
_EXPLICIT_CREATE_PATTERN = re.compile(
    r"(?:從零|全新圖片|全新一張|另畫一張|不要參考|不用原圖|不使用原圖)|"
    r"\b(?:from scratch|brand[- ]new image|without (?:using )?the (?:source|reference) image)\b",
    re.IGNORECASE,
)
_VISUAL_PART_TYPES = frozenset({"image_url", "image_bytes", "video_bytes"})


@dataclass(frozen=True)
class ImageOperationPolicy:
    candidate_ids: tuple[str, ...] = ()
    requires_edit: bool = False
    signal: str = "none"

    def to_prompt_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "requiredOperation": "edit",
            "allowedSourceImageIds": list(self.candidate_ids),
        }
        if not self.candidate_ids:
            payload["missingSourceAction"] = "omit imageGeneration and ask the user to attach or reply to the source image"
        return payload


def infer_image_operation_policy(dialogue: str, candidates: Iterable[Any] = ()) -> ImageOperationPolicy:
    candidate_ids = _candidate_ids(candidates)
    normalized = unicodedata.normalize("NFKC", str(dialogue or "")).strip()
    if not normalized or _EXPLICIT_CREATE_PATTERN.search(normalized):
        return ImageOperationPolicy(candidate_ids=candidate_ids)
    if _DIRECT_EDIT_PATTERN.search(normalized):
        return ImageOperationPolicy(candidate_ids=candidate_ids, requires_edit=True, signal="explicit_image_edit")
    if _EDIT_ACTION_PATTERN.search(normalized) and (candidate_ids or _IMAGE_REFERENCE_PATTERN.search(normalized)):
        signal = "candidate_edit_action" if candidate_ids else "referenced_image_edit"
        return ImageOperationPolicy(candidate_ids=candidate_ids, requires_edit=True, signal=signal)
    return ImageOperationPolicy(candidate_ids=candidate_ids)


def image_operation_violation(parsed: ParsedAIResponse, policy: ImageOperationPolicy | None) -> str:
    if policy is None or parsed.image_generation is None:
        return ""
    block = parsed.image_generation
    if policy.requires_edit and block.operation == "create":
        return "edit_source_missing" if not policy.candidate_ids else "edit_required"
    if block.operation == "edit" and any(source_id not in policy.candidate_ids for source_id in block.source_image_ids):
        return "unknown_source_id"
    return ""


def build_image_operation_repair_instruction(policy: ImageOperationPolicy, violation: str) -> str:
    candidate_json = json.dumps(list(policy.candidate_ids), ensure_ascii=False)
    if violation == "edit_source_missing" or not policy.candidate_ids:
        return (
            "本輪是修改既有圖片的請求，但沒有可用的原圖候選。不得改用 create。"
            "請省略 imageGeneration，並在 replyText 要求使用者重新附圖或直接回覆原圖。"
        )
    return (
        "上一輪圖片操作不符合本輪約束。本輪是修改既有圖片的請求，imageGeneration.operation 必須是 edit，"
        f"sourceImageIds 只能從以下候選選擇：{candidate_json}。不得改用 create、不得編造 ID。"
        "請保留其他有效 JSON 欄位，只修正圖片操作並只輸出單一 JSON 物件。"
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
