from __future__ import annotations

import json

from utils.browser_result_types import BrowserFetchResult
from utils.imagine_config import is_image_generation_enabled
from utils.message_media import build_multimodal_content, sanitize_image_urls

RELIABLE_RESULT_INSTRUCTION_WITH_IMAGE_GENERATION = (
    "請只根據 browserResults 中可讀取的網頁內容與已提供的圖片產生最終 JSON 回覆。"
    "不要再次輸出 browser；必要時仍可輸出 imageGeneration 或 memory。"
    "若使用者要求找 YouTube 影片或影片連結，只有結果中出現 YouTube watch、youtu.be 或明確影片頁面時才算找到；"
    "如果只找到論壇、社群或討論串，請把它當作線索並說明尚未確認 direct video URL。"
    "不要提及已省略的搜尋失敗、CAPTCHA、反機器人驗證、工具錯誤或網站阻擋。"
)
RELIABLE_RESULT_INSTRUCTION = (
    "請只根據 browserResults 中可讀取的網頁內容與已提供的圖片產生最終 JSON 回覆。"
    "不要再次輸出 browser；必要時仍可輸出 memory。"
    "若使用者要求找 YouTube 影片或影片連結，只有結果中出現 YouTube watch、youtu.be 或明確影片頁面時才算找到；"
    "如果只找到論壇、社群或討論串，請把它當作線索並說明尚未確認 direct video URL。"
    "不要提及已省略的搜尋失敗、CAPTCHA、反機器人驗證、工具錯誤或網站阻擋。"
)
NO_RELIABLE_RESULT_INSTRUCTION = (
    "browserResults 為空，表示這次無法取得可靠網頁內容。"
    "請不要編造查詢結果，也不要提及 CAPTCHA、反機器人驗證、工具錯誤或網站阻擋；"
    "請用自然語氣簡短說明目前無法可靠查到。"
)
INLINE_BROWSER_CONTEXT_INSTRUCTION = (
    "prefetchedBrowserContext 是系統已根據本輪使用者明確 URL 預先讀取的網頁附件。"
    "若 browserResults 內容足以回答，請直接輸出最終 JSON replyText，不要再對相同 URL 輸出 browser；"
    "若 browserResults 為空或內容不足、需要搜尋、指定文字或網頁圖片，仍可輸出 browser。"
)


def build_browser_followup_payload(results: list[BrowserFetchResult]) -> dict:
    return {
        "inputType": "browser_results",
        "payload": _build_context_payload(results, _browser_instruction),
    }


def build_inline_browser_context(results: list[BrowserFetchResult]) -> dict:
    return _build_context_payload(results, lambda _: INLINE_BROWSER_CONTEXT_INSTRUCTION)


def _build_context_payload(results: list[BrowserFetchResult], instruction_builder) -> dict:
    readable_results = [result for result in results if _has_readable_content(result)]
    omitted_failed_count = len(results) - len(readable_results)
    return {
        "instruction": instruction_builder(readable_results),
        "browserResults": [_sanitized_payload(result) for result in readable_results],
        "omittedFailedResultCount": omitted_failed_count,
    }


def build_browser_followup_content(results: list[BrowserFetchResult]) -> str | list[dict]:
    payload = build_browser_followup_payload(results)
    image_urls = collect_browser_result_image_urls(results)
    return build_multimodal_content(json.dumps(payload, ensure_ascii=False), image_urls)


def _browser_instruction(readable_results: list[BrowserFetchResult]) -> str:
    if readable_results:
        if is_image_generation_enabled():
            return RELIABLE_RESULT_INSTRUCTION_WITH_IMAGE_GENERATION
        return RELIABLE_RESULT_INSTRUCTION
    return NO_RELIABLE_RESULT_INSTRUCTION


def _sanitized_payload(result: BrowserFetchResult) -> dict:
    payload = result.to_payload()
    payload["error"] = ""
    return payload


def _has_readable_content(result: BrowserFetchResult) -> bool:
    return bool(str(result.text or "").strip() or result.image_urls)


def collect_browser_result_image_urls(results: list[BrowserFetchResult]) -> list[str]:
    urls = []
    for result in results:
        urls.extend(result.image_urls)
    return sanitize_image_urls(urls)
