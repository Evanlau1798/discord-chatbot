from __future__ import annotations

import unittest
from unittest.mock import patch

from utils.browser_result_types import BrowserFetchResult
from utils.http_page_fetcher import HttpPageText
from utils.web_tool_client import WebToolClient
from utils.json_response_protocol import BrowserFindRequest


class FakeBrowserClient:
    def __init__(
        self,
        *,
        browser_results: list[BrowserFetchResult] | None = None,
    ):
        self.browser_results = browser_results or []
        self.fetched_targets = None

    async def fetch_targets(self, targets: list[dict[str, str]], include_images: bool = False):
        self.fetched_targets = targets
        self.include_images = include_images
        return self.browser_results


class FakeSearchPlanner:
    def __init__(
        self,
        *,
        search_results: list[BrowserFetchResult] | None = None,
    ):
        self.search_results = search_results or []
        self.received_queries = []

    async def search_many(self, queries: list[str]):
        self.received_queries = queries
        return self.search_results


class WebToolClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_url_open_uses_http_first_without_browser(self):
        browser_client = FakeBrowserClient()

        def http_fetcher(url: str, timeout_ms: int) -> HttpPageText:
            return HttpPageText(
                final_url=url,
                title="Readable",
                text=(
                    "This page has enough readable article text for the model to use directly.\n"
                    "It includes concrete details, not only menu labels or navigation controls.\n"
                    "The HTTP extraction path should accept it without launching the browser."
                ),
            )

        client = WebToolClient(timeout_ms=1000, browser_client=browser_client, http_fetcher=http_fetcher)

        results = await client.fetch_urls_and_searches(["https://example.test/page"], [])

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].title, "Readable")
        self.assertIn("enough readable article text", results[0].text)
        self.assertIsNone(browser_client.fetched_targets)

    async def test_url_open_uses_special_reader_before_http(self):
        browser_client = FakeBrowserClient()
        special_result = BrowserFetchResult(
            requested_url="https://youtu.be/abc123xyz00",
            source_type="url",
            final_url="https://www.youtube.com/watch?v=abc123xyz00",
            title="Transcript",
            text="[0:01] hello",
            content_format="youtube_transcript",
        )

        def http_fetcher(url: str, timeout_ms: int) -> HttpPageText:
            raise AssertionError("special URL reader should run before generic HTTP")

        client = WebToolClient(timeout_ms=1000, browser_client=browser_client, http_fetcher=http_fetcher)

        with patch("utils.web_tool_client.read_special_url", return_value=special_result):
            results = await client.fetch_urls_and_searches(["https://youtu.be/abc123xyz00"], [])

        self.assertEqual(results, [special_result])
        self.assertIsNone(browser_client.fetched_targets)

    async def test_url_open_falls_back_to_browser_when_http_fails(self):
        browser_result = BrowserFetchResult(
            requested_url="https://example.test/page",
            source_type="url",
            final_url="https://example.test/page",
            title="Browser",
            text="Browser readable content",
        )
        browser_client = FakeBrowserClient(browser_results=[browser_result])

        def http_fetcher(url: str, timeout_ms: int) -> HttpPageText:
            return HttpPageText(final_url=url, title="", text="", error="HTTP failed")

        client = WebToolClient(timeout_ms=1000, browser_client=browser_client, http_fetcher=http_fetcher)

        results = await client.fetch_urls_and_searches(["https://example.test/page"], [])

        self.assertEqual(results, [browser_result])
        self.assertEqual(browser_client.fetched_targets[0]["url"], "https://example.test/page")
        self.assertEqual(browser_client.fetched_targets[0]["source_type"], "url")

    async def test_sparse_http_menu_text_falls_back_to_browser(self):
        browser_result = BrowserFetchResult(
            requested_url="https://example.test/app",
            source_type="url",
            final_url="https://example.test/app",
            title="Rendered App",
            text="Rendered browser content includes the actual article body after JavaScript loads.",
        )
        browser_client = FakeBrowserClient(browser_results=[browser_result])

        def http_fetcher(url: str, timeout_ms: int) -> HttpPageText:
            return HttpPageText(
                final_url=url,
                title="Static Shell",
                text="選擇縣市\n快速地點搜尋\n搜尋\n確定",
            )

        client = WebToolClient(timeout_ms=1000, browser_client=browser_client, http_fetcher=http_fetcher)

        results = await client.fetch_urls_and_searches(["https://example.test/app"], [])

        self.assertEqual(results, [browser_result])
        self.assertEqual(browser_client.fetched_targets[0]["url"], "https://example.test/app")

    async def test_search_uses_planner_without_url_fetch(self):
        search_result = BrowserFetchResult(
            requested_url="台北 天氣",
            source_type="search",
            query="台北 天氣",
            title="SearXNG Search",
            text="Search result text",
        )
        browser_client = FakeBrowserClient()
        search_planner = FakeSearchPlanner(search_results=[search_result])

        def http_fetcher(url: str, timeout_ms: int) -> HttpPageText:
            raise AssertionError("search query should not be treated as a URL")

        client = WebToolClient(
            timeout_ms=1000,
            browser_client=browser_client,
            search_planner=search_planner,
            http_fetcher=http_fetcher,
        )

        results = await client.fetch_urls_and_searches([], ["台北 天氣"])

        self.assertEqual(results, [search_result])
        self.assertEqual(search_planner.received_queries, ["台北 天氣"])
        self.assertIsNone(browser_client.fetched_targets)

    async def test_youtube_search_uses_ytdlp_searcher_without_searxng(self):
        youtube_result = BrowserFetchResult(
            requested_url="Apex Hal eating microphone",
            source_type="youtube_search",
            query="Apex Hal eating microphone",
            title="YouTube Search",
            text="1. Clip\nURL: https://www.youtube.com/watch?v=abc123xyz00",
            content_format="youtube_search_results",
        )
        browser_client = FakeBrowserClient()
        search_planner = FakeSearchPlanner()
        received_queries = []

        def youtube_searcher(query: str, timeout_ms: int) -> BrowserFetchResult:
            received_queries.append(query)
            return youtube_result

        client = WebToolClient(
            timeout_ms=1000,
            browser_client=browser_client,
            search_planner=search_planner,
            youtube_searcher=youtube_searcher,
        )

        results = await client.fetch_urls_and_searches([], [], youtube_search_queries=["Apex Hal eating microphone"])

        self.assertEqual(results, [youtube_result])
        self.assertEqual(received_queries, ["Apex Hal eating microphone"])
        self.assertEqual(search_planner.received_queries, [])
        self.assertIsNone(browser_client.fetched_targets)

    async def test_youtube_search_queries_are_limited_and_throttled(self):
        browser_client = FakeBrowserClient()
        search_planner = FakeSearchPlanner()
        received_queries = []

        def youtube_searcher(query: str, timeout_ms: int) -> BrowserFetchResult:
            received_queries.append(query)
            return BrowserFetchResult(
                requested_url=query,
                source_type="youtube_search",
                query=query,
                text=f"Result for {query}",
            )

        client = WebToolClient(
            timeout_ms=1000,
            browser_client=browser_client,
            search_planner=search_planner,
            youtube_searcher=youtube_searcher,
        )
        env = {
            "YOUTUBE_SEARCH_MAX_QUERIES_PER_TURN": "2",
            "YOUTUBE_SEARCH_QUERY_COOLDOWN_SECONDS": "1",
        }
        with patch.dict("os.environ", env, clear=True), patch("utils.web_tool_client.asyncio.sleep") as sleep:
            results = await client.fetch_urls_and_searches(
                [],
                [],
                youtube_search_queries=["first", "second", "third"],
            )

        self.assertEqual([result.query for result in results], ["first", "second"])
        self.assertEqual(received_queries, ["first", "second"])
        sleep.assert_called_once_with(1.0)
        self.assertEqual(search_planner.received_queries, [])

    async def test_search_result_does_not_force_browser_fetch(self):
        provider_result = BrowserFetchResult(
            requested_url="台北 天氣",
            source_type="search",
            query="台北 天氣",
            title="SearXNG Search",
            text="臺北市 - 縣市預報\n今日白天 27 - 29 70% 悶熱",
        )
        browser_client = FakeBrowserClient()
        search_planner = FakeSearchPlanner(search_results=[provider_result])
        client = WebToolClient(timeout_ms=1000, browser_client=browser_client, search_planner=search_planner)

        results = await client.fetch_urls_and_searches([], ["台北 天氣"])

        self.assertEqual(results, [provider_result])
        self.assertIsNone(browser_client.fetched_targets)

    async def test_find_request_returns_matching_excerpt_from_http_result(self):
        browser_client = FakeBrowserClient()

        def http_fetcher(url: str, timeout_ms: int) -> HttpPageText:
            return HttpPageText(
                final_url=url,
                title="Docs",
                text=(
                    "Install the package with pip before running the bot.\n"
                    "Configure the browser provider after installation.\n"
                    "Restart is not required for this documentation page."
                ),
            )

        client = WebToolClient(timeout_ms=1000, browser_client=browser_client, http_fetcher=http_fetcher)

        results = await client.fetch_urls_and_searches(
            [],
            [],
            [BrowserFindRequest(url="https://example.test/docs", pattern="browser provider")],
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].source_type, "find")
        self.assertEqual(results[0].query, "browser provider")
        self.assertIn("Configure the browser provider", results[0].text)
        self.assertIsNone(browser_client.fetched_targets)

    async def test_find_request_reports_empty_text_when_pattern_missing(self):
        browser_client = FakeBrowserClient()

        def http_fetcher(url: str, timeout_ms: int) -> HttpPageText:
            return HttpPageText(
                final_url=url,
                title="Docs",
                text="This document explains installation and configuration in detail.",
            )

        client = WebToolClient(timeout_ms=1000, browser_client=browser_client, http_fetcher=http_fetcher)

        results = await client.fetch_urls_and_searches(
            [],
            [],
            [BrowserFindRequest(url="https://example.test/docs", pattern="deployment")],
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].source_type, "find")
        self.assertEqual(results[0].text, "")
        self.assertIn("找不到", results[0].error)

    async def test_url_open_keeps_http_image_urls_when_requested(self):
        browser_client = FakeBrowserClient()

        def http_fetcher(url: str, timeout_ms: int) -> HttpPageText:
            return HttpPageText(
                final_url=url,
                title="Gallery",
                text="Cover image and gallery.",
                image_urls=("https://example.test/cover.jpg",),
            )

        client = WebToolClient(timeout_ms=1000, browser_client=browser_client, http_fetcher=http_fetcher)

        results = await client.fetch_urls_and_searches(["https://example.test/gallery"], [], include_images=True)

        self.assertEqual(results[0].image_urls, ("https://example.test/cover.jpg",))
        self.assertIsNone(browser_client.fetched_targets)

    async def test_image_only_http_result_is_complete_when_images_requested(self):
        browser_client = FakeBrowserClient()

        def http_fetcher(url: str, timeout_ms: int) -> HttpPageText:
            return HttpPageText(
                final_url=url,
                title="",
                text="",
                error="Unsupported content type: image/jpeg",
                image_urls=("https://example.test/photo.jpg",),
            )

        client = WebToolClient(timeout_ms=1000, browser_client=browser_client, http_fetcher=http_fetcher)

        results = await client.fetch_urls_and_searches(["https://example.test/photo.jpg"], [], include_images=True)

        self.assertEqual(results[0].image_urls, ("https://example.test/photo.jpg",))
        self.assertEqual(results[0].error, "")
        self.assertIsNone(browser_client.fetched_targets)


if __name__ == "__main__":
    unittest.main()
