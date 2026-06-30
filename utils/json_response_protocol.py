from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

CODE_BLOCK_PATTERN = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)
FALLBACK_REPLY = "回覆格式不合法，這次無法正確處理模型回應。"


@dataclass(frozen=True)
class ImageGenerationBlock:
    needed: bool
    prompt: str = ""


@dataclass(frozen=True)
class MemoryBlock:
    update: bool
    content: str = ""


@dataclass(frozen=True)
class ImageUnderstandingBlock:
    summary: str
    visible_text: tuple[str, ...] = ()
    details: tuple[str, ...] = ()


@dataclass(frozen=True)
class BrowserFindRequest:
    url: str
    pattern: str


@dataclass(frozen=True)
class BrowserBlock:
    urls: list[str]
    search_queries: list[str]
    find_requests: list[BrowserFindRequest]
    include_images: bool = False

    @property
    def targets(self) -> list[str]:
        return [*self.search_queries, *self.urls, *(request.url for request in self.find_requests)]


@dataclass(frozen=True)
class ParsedAIResponse:
    reply_text: str
    image_generation: ImageGenerationBlock | None = None
    memory: MemoryBlock | None = None
    browser: BrowserBlock | None = None
    image_understanding: ImageUnderstandingBlock | None = None


def parse_model_response(text: str) -> ParsedAIResponse:
    payload = _load_payload(_strip_single_code_block(text))
    if payload is None:
        payload = _load_payload(_extract_first_json_object(text or ""))
    if payload is None:
        raise ValueError("model response is not a JSON object")
    return validate_payload(payload)


def build_fallback_response() -> ParsedAIResponse:
    return ParsedAIResponse(reply_text=FALLBACK_REPLY)


def build_repair_instruction() -> str:
    return (
        "你上一輪沒有正確遵守輸出格式。請只回傳單一 JSON 物件，不要 Markdown、不要說明文字。"
        "格式固定為 {\"replyText\":\"...\",\"imageGeneration\":{\"needed\":true,\"prompt\":\"...\"},"
        "\"memory\":{\"update\":true,\"content\":\"...\"},\"browser\":{\"searchQuery\":\"...\"}}。"
        "不需要生圖時省略 imageGeneration；不需要更新記憶時省略 memory；不需要上網時省略 browser。"
        "如果目前請求或前一輪請求包含圖片，請加入 imageUnderstanding: {\"summary\":\"...\",\"visibleText\":[\"...\"],\"details\":[\"...\"]}。"
        "除非使用者明確指示在圖片中加入特定文字，否則 imageGeneration.prompt 不要加入明文文字。"
        "需要網頁搜尋或最新資料時，不要先輸出 replyText，直接輸出 browser.searchQuery 的精簡查詢關鍵字；"
        "收到 browserResults 後才輸出具有人設語氣的 replyText。"
        "除非使用者明確提供 URL，否則上網請優先使用 browser.searchQuery；"
        "需要在指定網頁中尋找文字時可用 browser.find: {\"url\":\"...\",\"pattern\":\"...\"}。"
        "需要查看指定網頁內圖片時，可在 browser 中加入 includeImages: true。"
    )


def validate_payload(payload: dict[str, Any]) -> ParsedAIResponse:
    browser = _parse_browser(payload.get("browser"))
    reply_text = _optional_text(payload.get("replyText")) if browser is not None else _required_text(payload.get("replyText"), "replyText")
    image_generation = _parse_image_generation(payload.get("imageGeneration"))
    memory = _parse_memory(payload.get("memory"))
    image_understanding = _parse_image_understanding(payload.get("imageUnderstanding"))
    return ParsedAIResponse(
        reply_text=reply_text,
        image_generation=image_generation,
        memory=memory,
        browser=browser,
        image_understanding=image_understanding,
    )


def _parse_image_generation(value) -> ImageGenerationBlock | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("imageGeneration must be an object")
    needed = _optional_bool(value.get("needed", False), "imageGeneration.needed")
    if not needed:
        return None
    prompt = _required_text(value.get("prompt"), "imageGeneration.prompt")
    return ImageGenerationBlock(needed=True, prompt=prompt)


def _parse_memory(value) -> MemoryBlock | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("memory must be an object")
    update = _optional_bool(value.get("update", False), "memory.update")
    if not update:
        return None
    content = _required_text(value.get("content"), "memory.content")
    return MemoryBlock(update=True, content=content)


def _parse_image_understanding(value) -> ImageUnderstandingBlock | None:
    if value is None:
        return None
    if isinstance(value, str):
        summary = value.strip()
        return ImageUnderstandingBlock(summary=summary[:1200]) if summary else None
    if not isinstance(value, dict):
        return None
    visible_text = _optional_text_tuple(value.get("visibleText"), "imageUnderstanding.visibleText")
    details = _optional_text_tuple(value.get("details"), "imageUnderstanding.details")
    summary = _first_optional_text(value, ("summary", "description", "caption", "text"))
    summary = summary or _first_tuple_text(details) or _first_tuple_text(visible_text)
    if not summary:
        return None
    return ImageUnderstandingBlock(
        summary=summary[:1200],
        visible_text=visible_text,
        details=details,
    )


def _parse_browser(value) -> BrowserBlock | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("browser must be an object")
    urls = _collect_browser_urls(value)
    search_queries = _collect_browser_search_queries(value)
    find_requests = _collect_browser_find_requests(value)
    include_images = _optional_bool(value.get("includeImages", False), "browser.includeImages")
    if not urls and not search_queries and not find_requests:
        return None
    return BrowserBlock(
        urls=urls[:5],
        search_queries=search_queries[:5],
        find_requests=find_requests[:5],
        include_images=include_images,
    )


def _collect_browser_urls(value: dict) -> list[str]:
    raw_values = []
    for key in ("links", "urls"):
        item = value.get(key)
        if isinstance(item, list):
            raw_values.extend(item)
    for key in ("link", "url"):
        item = value.get(key)
        if isinstance(item, str):
            raw_values.append(item)
    urls = []
    for item in raw_values:
        if not isinstance(item, str):
            raise ValueError("browser links must be strings")
        normalized = item.strip()
        if normalized and normalized not in urls:
            urls.append(normalized)
    return urls


def _collect_browser_search_queries(value: dict) -> list[str]:
    raw_values = []
    for key in ("searchQueries", "queries"):
        item = value.get(key)
        if isinstance(item, list):
            raw_values.extend(item)
    for key in ("searchQuery", "query"):
        item = value.get(key)
        if isinstance(item, str):
            raw_values.append(item)
    queries = []
    for item in raw_values:
        if not isinstance(item, str):
            raise ValueError("browser search queries must be strings")
        normalized = item.strip()
        if normalized and normalized not in queries:
            queries.append(normalized)
    return queries


def _collect_browser_find_requests(value: dict) -> list[BrowserFindRequest]:
    raw_values = []
    for key in ("finds", "findInPages"):
        item = value.get(key)
        if isinstance(item, list):
            raw_values.extend(item)
    for key in ("find", "findInPage"):
        item = value.get(key)
        if isinstance(item, dict):
            raw_values.append(item)
    requests = []
    seen = set()
    for item in raw_values:
        if not isinstance(item, dict):
            raise ValueError("browser find requests must be objects")
        url = _first_text(item, ("url", "link"))
        pattern = _first_text(item, ("pattern", "text"))
        if not url or not pattern:
            continue
        identity = (url, pattern)
        if identity in seen:
            continue
        seen.add(identity)
        requests.append(BrowserFindRequest(url=url, pattern=pattern))
    return requests


def _first_text(value: dict, keys: tuple[str, ...]) -> str:
    for key in keys:
        item = value.get(key)
        if item is None:
            continue
        if not isinstance(item, str):
            raise ValueError("browser find url and pattern must be strings")
        normalized = item.strip()
        if normalized:
            return normalized
    return ""


def _optional_bool(value, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    raise ValueError(f"{field_name} must be a boolean")


def _required_text(value, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


def _optional_text(value) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("replyText must be a string")
    return value.strip()


def _optional_text_tuple(value, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        normalized = value.strip()
        return (normalized[:300],) if normalized else ()
    if not isinstance(value, list):
        return ()
    items = []
    for item in value:
        if not isinstance(item, str):
            continue
        normalized = item.strip()
        if normalized and normalized not in items:
            items.append(normalized[:300])
        if len(items) >= 10:
            break
    return tuple(items)


def _first_optional_text(value: dict, keys: tuple[str, ...]) -> str:
    for key in keys:
        item = value.get(key)
        if isinstance(item, str) and item.strip():
            return item.strip()
    return ""


def _first_tuple_text(values: tuple[str, ...]) -> str:
    return values[0] if values else ""


def _strip_single_code_block(text: str) -> str:
    normalized = (text or "").strip()
    matched = CODE_BLOCK_PATTERN.match(normalized)
    return matched.group(1).strip() if matched else normalized


def _load_payload(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    try:
        payload = json.loads(text)
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def _extract_first_json_object(text: str) -> str:
    start = text.find("{")
    if start < 0:
        return ""
    depth = 0
    inside_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if inside_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                inside_string = False
            continue
        if char == '"':
            inside_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start:index + 1]
    return ""
