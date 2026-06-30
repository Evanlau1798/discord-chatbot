from __future__ import annotations

import asyncio
import os

from utils.browser_actions import normalize_search_queries
from utils.browser_result_types import BrowserFetchResult
from utils.search_provider_api import (
    DEFAULT_SEARXNG_BASE_URL,
    SEARXNG_BASE_URL_ENV,
    SEARXNG_CATEGORIES_ENV,
    SEARXNG_ENGINES_ENV,
    SEARXNG_LANGUAGE_ENV,
    SEARXNG_TIME_RANGE_ENV,
    fetch_searxng_search_result,
)


class SearchPlanner:
    def __init__(self, timeout_ms: int):
        self.timeout_ms = timeout_ms
        self.searxng_base_url = os.getenv(SEARXNG_BASE_URL_ENV, DEFAULT_SEARXNG_BASE_URL).strip()
        self.searxng_base_url = self.searxng_base_url or DEFAULT_SEARXNG_BASE_URL
        self.searxng_categories = os.getenv(SEARXNG_CATEGORIES_ENV, "").strip()
        self.searxng_engines = os.getenv(SEARXNG_ENGINES_ENV, "").strip()
        self.searxng_language = os.getenv(SEARXNG_LANGUAGE_ENV, "zh-TW").strip() or "zh-TW"
        self.searxng_time_range = os.getenv(SEARXNG_TIME_RANGE_ENV, "").strip()

    async def search_many(self, queries: list[str]) -> list[BrowserFetchResult]:
        normalized_queries = normalize_search_queries(queries)
        if not normalized_queries:
            return []
        return await self._fetch_searxng_searches(normalized_queries)

    async def _fetch_searxng_searches(self, queries: list[str]) -> list[BrowserFetchResult]:
        results = []
        for query in queries:
            result = await asyncio.to_thread(
                fetch_searxng_search_result,
                query,
                self.searxng_base_url,
                self.timeout_ms,
                categories=self.searxng_categories_for_query(query),
                engines=self.searxng_engines,
                language=self.searxng_language,
                time_range=self.searxng_time_range,
            )
            if result.text or result.error:
                results.append(result)
        return results

    def searxng_categories_for_query(self, query: str) -> str:
        if self.searxng_categories:
            return self.searxng_categories
        return ""
