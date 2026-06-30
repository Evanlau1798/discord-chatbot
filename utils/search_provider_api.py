from __future__ import annotations

import json
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from utils.browser_result_types import BrowserFetchResult

SEARXNG_BASE_URL_ENV = "SEARXNG_BASE_URL"
DEFAULT_SEARXNG_BASE_URL = "http://127.0.0.1:19183"
SEARXNG_CATEGORIES_ENV = "SEARXNG_CATEGORIES"
SEARXNG_ENGINES_ENV = "SEARXNG_ENGINES"
DEFAULT_SEARXNG_ENGINES = "google,bing"
SEARXNG_LANGUAGE_ENV = "SEARXNG_LANGUAGE"
SEARXNG_TIME_RANGE_ENV = "SEARXNG_TIME_RANGE"
SEARXNG_RESULT_LIMIT = 8
SEARXNG_EMPTY_RESULT_RETRIES = 1
SEARXNG_EMPTY_RESULT_RETRY_DELAY_SECONDS = 0.5


def fetch_searxng_search_result(
    query: str,
    base_url: str,
    timeout_ms: int,
    *,
    categories: str = "",
    engines: str = "",
    language: str = "zh-TW",
    time_range: str = "",
) -> BrowserFetchResult:
    normalized_base_url = normalize_searxng_base_url(base_url)
    request_url = normalized_base_url or base_url
    for attempt in range(SEARXNG_EMPTY_RESULT_RETRIES + 1):
        try:
            request_url, payload = _request_searxng_search(
                query,
                normalized_base_url,
                timeout_ms,
                categories=categories,
                engines=engines,
                language=language,
                time_range=time_range,
            )
        except (HTTPError, URLError, TimeoutError, ValueError) as exc:
            return _provider_error_result(query, normalized_base_url or base_url, "SearXNG Search", exc)
        text = format_searxng_results(payload)
        if text:
            return BrowserFetchResult(
                requested_url=query,
                source_type="search",
                query=query,
                final_url=request_url,
                title="SearXNG Search",
                text=text,
            )
        if attempt < SEARXNG_EMPTY_RESULT_RETRIES:
            time.sleep(SEARXNG_EMPTY_RESULT_RETRY_DELAY_SECONDS)
    return BrowserFetchResult(
        requested_url=query,
        source_type="search",
        query=query,
        final_url=request_url,
        title="SearXNG Search",
        error=_empty_result_error(payload),
    )


def normalize_searxng_base_url(base_url: str) -> str:
    normalized = str(base_url or "").strip().rstrip("/")
    if not normalized:
        return ""
    if "://" not in normalized:
        normalized = f"http://{normalized}"
    return normalized


def format_searxng_results(payload: dict) -> str:
    results = payload.get("results")
    if not isinstance(results, list):
        return ""
    blocks = []
    for item in sorted(results, key=_searxng_engine_priority)[:SEARXNG_RESULT_LIMIT]:
        if not isinstance(item, dict):
            continue
        title = _normalize_provider_text(item.get("title", ""))
        link = _normalize_provider_text(item.get("url", ""))
        content = _normalize_provider_text(item.get("content", ""))
        engine = _format_searxng_engine(item)
        block = "\n".join(part for part in (title, link, content, engine) if part)
        if block:
            blocks.append(block)
    return "\n\n".join(blocks)


def _request_searxng_search(
    query: str,
    base_url: str,
    timeout_ms: int,
    *,
    categories: str,
    engines: str,
    language: str,
    time_range: str,
) -> tuple[str, dict]:
    if not base_url:
        raise ValueError("missing SearXNG base URL")
    parameters = {
        "q": query,
        "format": "json",
        "language": language or "zh-TW",
    }
    if categories:
        parameters["categories"] = categories
    if engines:
        parameters["engines"] = engines
    if time_range:
        parameters["time_range"] = time_range
    request_url = f"{base_url}/search?{urlencode(parameters)}"
    with urlopen(_searxng_json_request(request_url), timeout=_timeout_seconds(timeout_ms)) as response:
        return request_url, json.loads(response.read().decode("utf-8"))


def _searxng_json_request(url: str) -> Request:
    headers = {"Accept": "application/json", "User-Agent": "discord-chatbot/1.0"}
    if _is_local_url(url):
        headers["X-Real-IP"] = "127.0.0.1"
    return Request(url, headers=headers)


def _is_local_url(url: str) -> bool:
    hostname = (urlparse(url).hostname or "").lower()
    return hostname in {"localhost", "127.0.0.1", "::1"}


def _timeout_seconds(timeout_ms: int) -> float:
    return max(1.0, float(timeout_ms) / 1000)


def _provider_error_result(query: str, final_url: str, title: str, exc: Exception) -> BrowserFetchResult:
    return BrowserFetchResult(
        requested_url=query,
        source_type="search",
        query=query,
        final_url=final_url,
        title=title,
        error=f"{title} failed: {type(exc).__name__}",
    )


def _empty_result_error(payload: dict) -> str:
    engines = _format_unresponsive_engines(payload.get("unresponsive_engines"))
    if engines:
        return f"SearXNG 搜尋引擎暫時不可用: {engines}"
    return "SearXNG 未回傳可讀搜尋結果。"


def _format_unresponsive_engines(value) -> str:
    if not isinstance(value, list):
        return ""
    formatted = []
    for item in value:
        if isinstance(item, list) and item:
            engine = _normalize_provider_text(item[0])
            reason = _normalize_provider_text(item[1]) if len(item) > 1 else ""
            if engine and reason:
                formatted.append(f"{engine} ({reason})")
            elif engine:
                formatted.append(engine)
        elif isinstance(item, str):
            normalized = _normalize_provider_text(item)
            if normalized:
                formatted.append(normalized)
    return "; ".join(formatted[:5])


def _format_searxng_engine(item: dict) -> str:
    engines = item.get("engines")
    if isinstance(engines, list):
        normalized = ", ".join(str(engine) for engine in engines if str(engine).strip())
        return f"來源引擎: {normalized}" if normalized else ""
    engine = _normalize_provider_text(item.get("engine", ""))
    return f"來源引擎: {engine}" if engine else ""


def _searxng_engine_priority(item) -> int:
    if not isinstance(item, dict):
        return 3
    engines = item.get("engines")
    values = engines if isinstance(engines, list) else [item.get("engine", "")]
    normalized = {str(engine).strip().lower() for engine in values if str(engine).strip()}
    if any("google" in engine for engine in normalized):
        return 0
    if any("bing" in engine for engine in normalized):
        return 1
    return 2


def _normalize_provider_text(text) -> str:
    return "\n".join(line.strip() for line in str(text or "").splitlines() if line.strip())
