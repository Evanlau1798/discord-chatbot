from __future__ import annotations

from utils.browser_client import BrowserToolError
from utils.browser_result_types import BrowserFetchResult
from utils.openserp_search import SearchOptions, plan_search_queries_from_env
from utils.youtube_search_reader import plan_youtube_search_queries_from_env

CAPTCHA_MARKERS = ("captcha", "反機器人", "anti-bot", "challenge")


async def fetch_browser_results(
    browser_client,
    urls: list[str],
    search_queries: list[str],
    find_requests: list,
    logger,
    include_images: bool = False,
    youtube_search_queries: list[str] | None = None,
    search_options: SearchOptions | None = None,
):
    try:
        return await browser_client.fetch_urls_and_searches(
            urls,
            search_queries,
            find_requests,
            include_images=include_images,
            youtube_search_queries=youtube_search_queries or [],
            search_options=search_options,
        )
    except BrowserToolError as exc:
        logger.warning("ai_chat.browser_tool_failed error=%s", exc)
        return build_browser_error_results(urls, search_queries, find_requests, str(exc), youtube_search_queries)
    except Exception as exc:
        logger.warning("ai_chat.browser_unexpected_failed error_type=%s error=%s", type(exc).__name__, exc)
        error = f"{type(exc).__name__}: {exc}"
        return build_browser_error_results(urls, search_queries, find_requests, error[:500], youtube_search_queries)


def build_browser_error_results(
    urls: list[str],
    search_queries: list[str],
    find_requests: list,
    error: str,
    youtube_search_queries: list[str] | None = None,
):
    results = [BrowserFetchResult(requested_url=query, source_type="search", query=query, error=error) for query in search_queries]
    results.extend(
        BrowserFetchResult(requested_url=query, source_type="youtube_search", query=query, error=error)
        for query in (youtube_search_queries or [])
    )
    results.extend(BrowserFetchResult(requested_url=url, source_type="url", error=error) for url in urls)
    results.extend(
        BrowserFetchResult(
            requested_url=request.url,
            source_type="find",
            query=request.pattern,
            error=error,
        )
        for request in find_requests
    )
    return results


def search_fallback_allowed(results: list[BrowserFetchResult], attempted_queries: list[str]) -> bool:
    if not attempted_queries or any(_has_readable_content(result) for result in results):
        return False
    failure_summary = " ".join(
        str(value or "")
        for result in results
        for value in (result.error, *result.diagnostics)
    )
    return not any(marker in failure_summary.lower() for marker in CAPTCHA_MARKERS)


def new_fallback_queries(candidates: list[str], attempted_queries: list[str]) -> list[str]:
    attempted = {_query_identity(query) for query in attempted_queries}
    unique = []
    for query in candidates:
        normalized = str(query or "").strip()
        identity = _query_identity(normalized)
        if normalized and identity not in attempted and identity not in {_query_identity(item) for item in unique}:
            unique.append(normalized)
    return unique[:3]


def _has_readable_content(result: BrowserFetchResult) -> bool:
    return bool(str(result.text or "").strip() or result.image_urls)


def _query_identity(query: str) -> str:
    return " ".join(str(query or "").lower().split())


def format_browser_notice_targets(
    urls: list[str],
    search_queries: list[str],
    find_requests: list,
    *,
    youtube_search_queries: list[str] | None = None,
) -> str:
    notice_search_queries = plan_search_queries_from_env(search_queries)
    targets = [f"搜尋: {query}" for query in notice_search_queries if str(query).strip()]
    targets.extend(f"YouTube搜尋: {query}" for query in plan_youtube_search_queries_from_env(youtube_search_queries or []))
    targets.extend(str(url).strip() for url in urls if str(url).strip())
    targets.extend(
        f"尋找: {request.pattern} @ {request.url}"
        for request in find_requests
        if str(request.pattern).strip() and str(request.url).strip()
    )
    text = ", ".join(targets)
    if len(text) > 1500:
        return f"{text[:1500]}..."
    return text
