from __future__ import annotations

import asyncio
from collections.abc import Callable

from utils.browser_actions import BrowserTarget, build_url_target, dedupe_targets
from utils.browser_client import (
    DEFAULT_BROWSER_TIMEOUT_MS,
    PatchrightBrowserClient,
    _build_http_fallback_result,
)
from utils.browser_result_types import BrowserFetchResult
from utils.browser_search import SearchPlanner
from utils.http_page_fetcher import HttpPageText, fetch_http_page_text
from utils.json_response_protocol import BrowserFindRequest
from utils.url_readers import read_special_url
from utils.youtube_search_reader import (
    plan_youtube_search_queries_from_env,
    search_youtube_videos,
    youtube_query_cooldown_seconds_from_env,
)

Target = BrowserTarget
HttpFetcher = Callable[[str, int], HttpPageText]
YoutubeSearcher = Callable[[str, int], BrowserFetchResult]
FIND_EXCERPT_CHARS = 1200


class WebToolClient:
    def __init__(
        self,
        timeout_ms: int = DEFAULT_BROWSER_TIMEOUT_MS,
        *,
        browser_client: PatchrightBrowserClient | None = None,
        search_planner: SearchPlanner | None = None,
        http_fetcher: HttpFetcher = fetch_http_page_text,
        youtube_searcher: YoutubeSearcher = search_youtube_videos,
    ):
        self.timeout_ms = timeout_ms
        self.browser_client = browser_client or PatchrightBrowserClient(timeout_ms)
        self.search_planner = search_planner or SearchPlanner(timeout_ms)
        self.http_fetcher = http_fetcher
        self.youtube_searcher = youtube_searcher

    async def fetch_many(self, urls: list[str]) -> list[BrowserFetchResult]:
        return await self.fetch_urls_and_searches(urls, [])

    async def search_many(self, queries: list[str]) -> list[BrowserFetchResult]:
        return await self.fetch_urls_and_searches([], queries)

    async def fetch_urls_and_searches(
        self,
        urls: list[str],
        search_queries: list[str],
        find_requests: list[BrowserFindRequest] | None = None,
        include_images: bool = False,
        youtube_search_queries: list[str] | None = None,
    ) -> list[BrowserFetchResult]:
        youtube_results = await self._fetch_youtube_searches(youtube_search_queries or [])
        search_results = await self.search_planner.search_many(search_queries)
        explicit_url_targets = [build_url_target(url) for url in urls if str(url or "").strip()]
        targets = dedupe_targets(explicit_url_targets)
        http_results, browser_targets = await self._fetch_http_first_targets(targets, include_images)
        browser_results = (
            await self.browser_client.fetch_targets(browser_targets, include_images=include_images)
            if browser_targets
            else []
        )
        find_results = await self._fetch_find_requests(find_requests or [])
        return [*youtube_results, *search_results, *http_results, *browser_results, *find_results]

    async def _fetch_youtube_searches(self, queries: list[str]) -> list[BrowserFetchResult]:
        results = []
        planned_queries = plan_youtube_search_queries_from_env(queries)
        cooldown_seconds = youtube_query_cooldown_seconds_from_env()
        for index, query in enumerate(planned_queries):
            if index > 0 and cooldown_seconds > 0:
                await asyncio.sleep(cooldown_seconds)
            result = await asyncio.to_thread(self.youtube_searcher, query, self.timeout_ms)
            results.append(result)
        return results

    async def _fetch_http_first_targets(self, targets: list[Target], include_images: bool) -> tuple[list[BrowserFetchResult], list[Target]]:
        http_results = []
        browser_targets = []
        for target in targets:
            if target.get("source_type") != "url":
                browser_targets.append(target)
                continue
            special_result = await self._fetch_special_target(target, include_images=include_images)
            if special_result is not None and _is_complete_http_result(special_result):
                http_results.append(special_result)
                continue
            result = await self._fetch_http_target(target, include_images=include_images)
            if _is_complete_http_result(result):
                http_results.append(result)
            else:
                browser_targets.append(target)
        return http_results, browser_targets

    async def _fetch_special_target(self, target: Target, include_images: bool = False) -> BrowserFetchResult | None:
        result = await asyncio.to_thread(read_special_url, target["url"], self.timeout_ms, include_images=include_images)
        return result

    async def _fetch_http_target(self, target: Target, include_images: bool = False) -> BrowserFetchResult:
        page = await asyncio.to_thread(self.http_fetcher, target["url"], self.timeout_ms)
        return _build_http_fallback_result(target, page, include_images=include_images)

    async def _fetch_find_requests(self, requests: list[BrowserFindRequest]) -> list[BrowserFetchResult]:
        results = []
        for request in requests:
            target = build_url_target(request.url)
            page_result = await self._fetch_url_target_with_fallback(target)
            results.append(_build_find_result(page_result, request.pattern))
        return results

    async def _fetch_url_target_with_fallback(self, target: Target) -> BrowserFetchResult:
        result = await self._fetch_http_target(target)
        if result.text and not result.error:
            return result
        browser_results = await self.browser_client.fetch_targets([target])
        return browser_results[0] if browser_results else result


HeadlessBrowserClient = WebToolClient


def _dedupe_targets(targets: list[Target]) -> list[Target]:
    return dedupe_targets(targets)


def _is_complete_http_result(result: BrowserFetchResult) -> bool:
    return bool((result.text and not result.error) or (result.image_urls and not result.error))


def _build_find_result(page_result: BrowserFetchResult, pattern: str) -> BrowserFetchResult:
    normalized_pattern = str(pattern or "").strip()
    if not page_result.text:
        return BrowserFetchResult(
            requested_url=page_result.requested_url,
            source_type="find",
            query=normalized_pattern,
            final_url=page_result.final_url,
            title=page_result.title,
            error=page_result.error or "頁面沒有可搜尋的文字。",
        )
    excerpt = _find_text_excerpt(page_result.text, normalized_pattern)
    if not excerpt:
        return BrowserFetchResult(
            requested_url=page_result.requested_url,
            source_type="find",
            query=normalized_pattern,
            final_url=page_result.final_url,
            title=page_result.title,
            error=f"找不到指定文字: {normalized_pattern}",
        )
    return BrowserFetchResult(
        requested_url=page_result.requested_url,
        source_type="find",
        query=normalized_pattern,
        final_url=page_result.final_url,
        title=page_result.title,
        text=excerpt,
    )


def _find_text_excerpt(text: str, pattern: str) -> str:
    normalized_text = str(text or "")
    normalized_pattern = str(pattern or "").strip()
    if not normalized_text or not normalized_pattern:
        return ""
    index = normalized_text.lower().find(normalized_pattern.lower())
    if index < 0:
        return ""
    start = max(0, index - FIND_EXCERPT_CHARS // 2)
    end = min(len(normalized_text), index + len(normalized_pattern) + FIND_EXCERPT_CHARS // 2)
    excerpt = normalized_text[start:end].strip()
    if start > 0:
        excerpt = f"...{excerpt}"
    if end < len(normalized_text):
        excerpt = f"{excerpt}..."
    return excerpt
