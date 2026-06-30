from __future__ import annotations

from utils.browser_client import BrowserToolError
from utils.browser_result_types import BrowserFetchResult


async def fetch_browser_results(
    browser_client,
    urls: list[str],
    search_queries: list[str],
    find_requests: list,
    logger,
    include_images: bool = False,
):
    try:
        return await browser_client.fetch_urls_and_searches(
            urls,
            search_queries,
            find_requests,
            include_images=include_images,
        )
    except BrowserToolError as exc:
        logger.warning("ai_chat.browser_tool_failed error=%s", exc)
        return build_browser_error_results(urls, search_queries, find_requests, str(exc))
    except Exception as exc:
        logger.warning("ai_chat.browser_unexpected_failed error_type=%s error=%s", type(exc).__name__, exc)
        error = f"{type(exc).__name__}: {exc}"
        return build_browser_error_results(urls, search_queries, find_requests, error[:500])


def build_browser_error_results(urls: list[str], search_queries: list[str], find_requests: list, error: str):
    results = [BrowserFetchResult(requested_url=query, source_type="search", query=query, error=error) for query in search_queries]
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


def format_browser_notice_targets(urls: list[str], search_queries: list[str], find_requests: list) -> str:
    targets = [f"搜尋: {query}" for query in search_queries if str(query).strip()]
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
