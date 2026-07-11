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
    "使用網路資料形成的事實必須在 replyText 以 Discord Markdown 格式 [來源名稱](finalUrl) 引用來源，"
    "而且只能引用 browserResults 的實際 finalUrl；不可捏造或改寫 URL，也不要把 URL 放在反引號中。"
    "不要提及已省略的搜尋失敗、CAPTCHA、反機器人驗證、工具錯誤或網站阻擋。"
)
RELIABLE_RESULT_INSTRUCTION = (
    "請只根據 browserResults 中可讀取的網頁內容與已提供的圖片產生最終 JSON 回覆。"
    "不要再次輸出 browser；必要時仍可輸出 memory。"
    "若使用者要求找 YouTube 影片或影片連結，只有結果中出現 YouTube watch、youtu.be 或明確影片頁面時才算找到；"
    "如果只找到論壇、社群或討論串，請把它當作線索並說明尚未確認 direct video URL。"
    "使用網路資料形成的事實必須在 replyText 以 Discord Markdown 格式 [來源名稱](finalUrl) 引用來源，"
    "而且只能引用 browserResults 的實際 finalUrl；不可捏造或改寫 URL，也不要把 URL 放在反引號中。"
    "不要提及已省略的搜尋失敗、CAPTCHA、反機器人驗證、工具錯誤或網站阻擋。"
)
NO_RELIABLE_RESULT_INSTRUCTION = (
    "browserResults 為空，表示這次無法取得可靠網頁內容。"
    "請不要編造查詢結果，也不要提及 CAPTCHA、反機器人驗證、工具錯誤或網站阻擋；"
    "請用自然語氣說明目前無法可靠查到，並提供一到三個具體替代搜尋關鍵字，"
    "或指出需要使用者補充的完整名稱、地點、時間範圍或指定網站；不要只說找不到。"
)
SEARCH_RETRY_INSTRUCTION = (
    "browserResults 為空，表示第一次搜尋沒有取得可靠網頁內容。"
    "你有且只有一次改寫搜尋的機會；若能從對話確認目標，請只輸出 browser.search.queries 的一到三個新查詢，不要輸出 replyText。"
    "新查詢不可重複先前 query，且必須具體調整人物或產品全名、原文別名、地點、時間範圍或來源方向；不可只改標點或詞序。"
    "若目標仍有歧義，請直接在 replyText 詢問缺少的資訊，不要再次搜尋。"
)
INLINE_BROWSER_CONTEXT_INSTRUCTION = (
    "prefetchedBrowserContext 是系統已根據本輪使用者明確 URL 預先讀取的網頁附件。"
    "若 browserResults 內容足以回答，請直接輸出最終 JSON replyText，不要再對相同 URL 輸出 browser；"
    "若 browserResults 為空或內容不足、需要搜尋、指定文字或網頁圖片，仍可輸出 browser。"
)


def build_browser_followup_payload(
    results: list[BrowserFetchResult], *, allow_search_retry: bool = False
) -> dict:
    return {
        "inputType": "browser_results",
        "payload": _build_context_payload(
            results, lambda readable: _browser_instruction(readable, allow_search_retry)
        ),
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


def build_browser_followup_content(
    results: list[BrowserFetchResult], *, allow_search_retry: bool = False
) -> str | list[dict]:
    payload = build_browser_followup_payload(results, allow_search_retry=allow_search_retry)
    image_urls = collect_browser_result_image_urls(results)
    return build_multimodal_content(json.dumps(payload, ensure_ascii=False), image_urls)


def _browser_instruction(readable_results: list[BrowserFetchResult], allow_search_retry: bool = False) -> str:
    if readable_results:
        if is_image_generation_enabled():
            return RELIABLE_RESULT_INSTRUCTION_WITH_IMAGE_GENERATION
        return RELIABLE_RESULT_INSTRUCTION
    return SEARCH_RETRY_INSTRUCTION if allow_search_retry else NO_RELIABLE_RESULT_INSTRUCTION


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
