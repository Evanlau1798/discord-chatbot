from __future__ import annotations

import asyncio
import os

from utils.browser_actions import normalize_search_queries
from utils.browser_result_types import BrowserFetchResult
from utils.search_provider_api import (
    DEFAULT_SEARXNG_BASE_URL,
    DEFAULT_SEARXNG_ENGINES,
    SEARXNG_BASE_URL_ENV,
    SEARXNG_CATEGORIES_ENV,
    SEARXNG_ENGINES_ENV,
    SEARXNG_LANGUAGE_ENV,
    SEARXNG_TIME_RANGE_ENV,
    fetch_searxng_search_result,
)

SEARXNG_MAX_QUERIES_PER_TURN_ENV = "SEARXNG_MAX_QUERIES_PER_TURN"
SEARXNG_QUERY_COOLDOWN_SECONDS_ENV = "SEARXNG_QUERY_COOLDOWN_SECONDS"
SEARXNG_MERGE_QUERIES_ENV = "SEARXNG_MERGE_QUERIES"
DEFAULT_SEARXNG_MAX_QUERIES_PER_TURN = 3
DEFAULT_SEARXNG_QUERY_COOLDOWN_SECONDS = 1.0
MAX_MERGED_QUERY_TERMS = 5
MAX_MERGED_QUERY_CHARS = 500
MERGED_QUERY_SEPARATOR = ", "


class SearchPlanner:
    def __init__(self, timeout_ms: int):
        self.timeout_ms = timeout_ms
        self.searxng_base_url = os.getenv(SEARXNG_BASE_URL_ENV, DEFAULT_SEARXNG_BASE_URL).strip()
        self.searxng_base_url = self.searxng_base_url or DEFAULT_SEARXNG_BASE_URL
        self.searxng_categories = os.getenv(SEARXNG_CATEGORIES_ENV, "").strip()
        self.searxng_engines = os.getenv(SEARXNG_ENGINES_ENV, DEFAULT_SEARXNG_ENGINES).strip()
        self.searxng_engines = self.searxng_engines or DEFAULT_SEARXNG_ENGINES
        self.searxng_language = os.getenv(SEARXNG_LANGUAGE_ENV, "zh-TW").strip() or "zh-TW"
        self.searxng_time_range = os.getenv(SEARXNG_TIME_RANGE_ENV, "").strip()
        self.merge_queries = _parse_bool_env(SEARXNG_MERGE_QUERIES_ENV, default=False)
        self.max_queries_per_turn = _parse_int_env(
            SEARXNG_MAX_QUERIES_PER_TURN_ENV,
            default=DEFAULT_SEARXNG_MAX_QUERIES_PER_TURN,
            minimum=1,
            maximum=10,
        )
        self.query_cooldown_seconds = _parse_float_env(
            SEARXNG_QUERY_COOLDOWN_SECONDS_ENV,
            default=DEFAULT_SEARXNG_QUERY_COOLDOWN_SECONDS,
            minimum=0.0,
            maximum=30.0,
        )

    async def search_many(self, queries: list[str]) -> list[BrowserFetchResult]:
        planned_queries = self.plan_queries(queries)
        if not planned_queries:
            return []
        return await self._fetch_searxng_searches(planned_queries)

    def plan_queries(self, queries: list[str]) -> list[str]:
        return plan_search_queries(
            queries,
            merge_queries=self.merge_queries,
            max_queries=self.max_queries_per_turn,
        )

    async def _fetch_searxng_searches(self, queries: list[str]) -> list[BrowserFetchResult]:
        results = []
        for index, query in enumerate(queries):
            if index > 0 and self.query_cooldown_seconds > 0:
                await asyncio.sleep(self.query_cooldown_seconds)
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


def plan_search_queries_from_env(queries: list[str]) -> list[str]:
    merge_queries = _parse_bool_env(SEARXNG_MERGE_QUERIES_ENV, default=False)
    max_queries = _parse_int_env(
        SEARXNG_MAX_QUERIES_PER_TURN_ENV,
        default=DEFAULT_SEARXNG_MAX_QUERIES_PER_TURN,
        minimum=1,
        maximum=10,
    )
    return plan_search_queries(queries, merge_queries=merge_queries, max_queries=max_queries)


def plan_search_queries(queries: list[str], *, merge_queries: bool, max_queries: int) -> list[str]:
    normalized_queries = normalize_search_queries(queries)
    if not normalized_queries:
        return []
    if merge_queries and len(normalized_queries) > 1:
        merged_query = _merge_search_queries(normalized_queries[:MAX_MERGED_QUERY_TERMS])
        return [merged_query] if merged_query else []
    return normalized_queries[:max(1, max_queries)]


def _merge_search_queries(queries: list[str]) -> str:
    merged = ""
    for query in queries:
        candidate = query if not merged else f"{merged}{MERGED_QUERY_SEPARATOR}{query}"
        if len(candidate) > MAX_MERGED_QUERY_CHARS:
            return merged or query[:MAX_MERGED_QUERY_CHARS].rstrip()
        merged = candidate
    return merged


def _parse_bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _parse_int_env(name: str, *, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)).strip())
    except ValueError:
        return default
    return min(max(value, minimum), maximum)


def _parse_float_env(name: str, *, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)).strip())
    except ValueError:
        return default
    return min(max(value, minimum), maximum)
