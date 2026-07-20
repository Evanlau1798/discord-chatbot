from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from utils.imagine_config import is_image_generation_enabled
from utils.openserp_search import SearchOptions

CODE_BLOCK_PATTERN = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)
FALLBACK_REPLY = "回覆格式不合法，這次無法正確處理模型回應。"
SEARCH_FAILURE_REPLY = (
    "目前仍無法取得足夠可靠的資料。請補充更完整的名稱、所在地區、時間範圍或指定網站，"
    "我可以依這些線索換個方向搜尋。"
)
SEARCH_SOURCE_PROFILES = frozenset({"mixed", "official", "news", "technical", "reviews", "local"})
IMAGE_GENERATION_OPERATIONS = frozenset({"create", "edit"})
IMAGE_SOURCE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*(?::[A-Za-z0-9_-]+)+$")
MAX_IMAGE_SOURCE_IDS = 16


@dataclass(frozen=True)
class ImageGenerationBlock:
    needed: bool
    prompt: str = ""
    operation: str = "create"
    source_image_ids: tuple[str, ...] = ()
    use_persona_identity: bool = False


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
    youtube_search_queries: list[str]
    find_requests: list[BrowserFindRequest]
    include_images: bool = False
    search_options: SearchOptions = SearchOptions()

    @property
    def targets(self) -> list[str]:
        return [
            *self.search_queries,
            *self.youtube_search_queries,
            *self.urls,
            *(request.url for request in self.find_requests),
        ]


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


def build_search_failure_response() -> ParsedAIResponse:
    return ParsedAIResponse(reply_text=SEARCH_FAILURE_REPLY)


def build_repair_instruction() -> str:
    image_generation_enabled = is_image_generation_enabled()
    schema = '{"replyText":"..."'
    if image_generation_enabled:
        schema += ',"imageGeneration":{"needed":true,"operation":"create|edit","prompt":"...","sourceImageIds":["..."],"usePersonaIdentity":false}'
    schema += ',"memory":{"update":true,"content":"..."},"browser":{"search":{"queries":["..."],"language":"zh-TW","region":"TW","sourceProfile":"mixed","desiredSources":3},"youtubeSearchQuery":"..."}}'
    parts = [
        "你上一輪沒有正確遵守輸出格式。請只回傳單一 JSON 物件，不要 Markdown、不要說明文字。"
        f"格式固定為 {schema}。",
        "replyText 內需要提供 URL 時，請使用 Discord Markdown 格式 [有意義的顯示文字](https://example.com)，"
        "連結 URL 必須是實際來源，不要放在反引號中。",
    ]
    if image_generation_enabled:
        parts.append("不需要生圖時省略 imageGeneration；")
    parts.extend([
        "不需要更新記憶時省略 memory；不需要上網時省略 browser。"
        "如果目前請求或前一輪請求包含圖片，請加入 imageUnderstanding: {\"summary\":\"...\",\"visibleText\":[\"...\"],\"details\":[\"...\"]}。"
    ])
    if image_generation_enabled:
        parts.append("除非使用者明確指示在圖片中加入特定文字，否則 imageGeneration.prompt 不要加入明文文字。")
        parts.append(
            "imageGeneration.operation 使用 create 時不得輸出 sourceImageIds；使用 edit 時必須從 "
            "payload.imageGenerationCandidates 選擇一個或多個 sourceImageIds。若找不到使用者指稱的原圖，"
            "請在 replyText 要求使用者重新附圖或直接回覆原圖，並省略 imageGeneration。"
        )
        parts.append(
            "edit 需要讓目前人設角色出現在結果中時，設定 usePersonaIdentity: true；"
            "人設身份必須優先於來源圖片中的人物特徵且不得混合。一般圖片修改則省略或設為 false。"
        )
    parts.extend([
        "需要網頁搜尋或最新資料時，不要先輸出 replyText，直接輸出 browser.search.queries 的精簡查詢關鍵字；可選擇提供 language、region、timeRange、siteDomains、sourceProfile 與 3 到 5 的 desiredSources；"
        "需要搜尋 YouTube 影片、yt 影片、shorts 或剪輯連結時，優先輸出 browser.youtubeSearchQuery；"
        "收到 browserResults 後才輸出具有人設語氣的 replyText。"
        "若前一輪需要搜尋海外人物、遊戲、實況主、影片、梗圖或片段，請使用英文別名、常見英文說法與最多三個查詢關鍵字，"
        "不要只輸出使用者原文；第一個 query 必須是可單獨執行的最精準主查詢。"
        "若使用者指定 YouTube 或影片，請使用 browser.youtubeSearchQuery 並加入可能的英文標題線索。"
        "除非使用者明確提供 URL，否則上網請優先使用 browser.searchQuery；"
        "需要在指定網頁中尋找文字時可用 browser.find: {\"url\":\"...\",\"pattern\":\"...\"}。"
        "需要查看指定網頁內圖片時，可在 browser 中加入 includeImages: true。"
    ])
    return "".join(parts)


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
    operation = _required_text(value.get("operation"), "imageGeneration.operation").lower()
    if operation not in IMAGE_GENERATION_OPERATIONS:
        raise ValueError("imageGeneration.operation must be create or edit")
    source_image_ids = _parse_image_source_ids(value.get("sourceImageIds"))
    use_persona_identity = _optional_bool(value.get("usePersonaIdentity", False), "imageGeneration.usePersonaIdentity")
    if operation == "create" and source_image_ids:
        raise ValueError("imageGeneration.sourceImageIds must be omitted for create")
    if operation == "edit" and not source_image_ids:
        raise ValueError("imageGeneration.sourceImageIds is required for edit")
    return ImageGenerationBlock(
        needed=True,
        prompt=prompt,
        operation=operation,
        source_image_ids=source_image_ids,
        use_persona_identity=use_persona_identity,
    )


def _parse_image_source_ids(value) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError("imageGeneration.sourceImageIds must be an array")
    source_ids = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError("imageGeneration.sourceImageIds must contain strings")
        normalized = item.strip()
        if not IMAGE_SOURCE_ID_PATTERN.fullmatch(normalized):
            raise ValueError("imageGeneration.sourceImageIds contains an invalid candidate ID")
        if normalized not in source_ids:
            source_ids.append(normalized)
        if len(source_ids) > MAX_IMAGE_SOURCE_IDS:
            raise ValueError("imageGeneration.sourceImageIds exceeds the maximum")
    return tuple(source_ids)


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
    youtube_search_queries = _collect_browser_youtube_search_queries(value)
    find_requests = _collect_browser_find_requests(value)
    include_images = _optional_bool(value.get("includeImages", False), "browser.includeImages")
    search_options = _parse_search_options(value.get("search"))
    if not urls and not search_queries and not youtube_search_queries and not find_requests:
        return None
    return BrowserBlock(
        urls=urls[:5],
        search_queries=search_queries[:5],
        youtube_search_queries=youtube_search_queries[:3],
        find_requests=find_requests[:5],
        include_images=include_images,
        search_options=search_options,
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
    search = value.get("search")
    if isinstance(search, dict):
        nested = search.get("queries")
        if isinstance(nested, list):
            raw_values.extend(nested)
        nested_query = search.get("query")
        if isinstance(nested_query, str):
            raw_values.append(nested_query)
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


def _parse_search_options(value) -> SearchOptions:
    if value is None:
        return SearchOptions()
    if not isinstance(value, dict):
        raise ValueError("browser.search must be an object")
    language = _optional_text(value.get("language")) or "zh-TW"
    region = _optional_text(value.get("region"))
    time_range = _optional_text(value.get("timeRange"))
    raw_domains = value.get("siteDomains", [])
    if isinstance(raw_domains, str):
        raw_domains = [raw_domains]
    if not isinstance(raw_domains, list) or any(not isinstance(item, str) for item in raw_domains):
        raise ValueError("browser.search.siteDomains must contain strings")
    domains = tuple(dict.fromkeys(item.strip().lower() for item in raw_domains if item.strip()))[:3]
    desired = value.get("desiredSources", 3)
    if not isinstance(desired, int) or isinstance(desired, bool):
        raise ValueError("browser.search.desiredSources must be an integer")
    source_profile = (_optional_text(value.get("sourceProfile")) or "mixed").lower()
    if source_profile not in SEARCH_SOURCE_PROFILES:
        raise ValueError("browser.search.sourceProfile is not supported")
    return SearchOptions(
        language=language[:20],
        region=region[:10],
        time_range=time_range[:20],
        site_domains=domains,
        desired_sources=min(max(desired, 3), 5),
        source_profile=source_profile,
    )


def _collect_browser_youtube_search_queries(value: dict) -> list[str]:
    raw_values = []
    for key in ("youtubeSearchQueries", "ytSearchQueries"):
        item = value.get(key)
        if isinstance(item, list):
            raw_values.extend(item)
    for key in ("youtubeSearchQuery", "ytSearchQuery"):
        item = value.get(key)
        if isinstance(item, str):
            raw_values.append(item)
    queries = []
    for item in raw_values:
        if not isinstance(item, str):
            raise ValueError("browser YouTube search queries must be strings")
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
