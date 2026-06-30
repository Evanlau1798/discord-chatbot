from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from utils.browser_client import (
    ANTI_BOT_ERROR,
    BrowserFetchResult,
    BrowserToolError,
    EMPTY_PAGE_ERROR,
    PatchrightBrowserClient,
    UNRELIABLE_PAGE_ERROR,
    _build_reliable_content,
    _build_http_fallback_result,
    _format_searxng_results,
    _normalize_url,
    _normalize_text,
)
from utils.http_page_fetcher import HttpPageText
from utils.browser_search import SearchPlanner
from utils.search_provider_api import _searxng_json_request, fetch_searxng_search_result


class BrowserClientContentTests(unittest.TestCase):
    def test_normalize_text_removes_blank_lines_and_spaces(self):
        self.assertEqual(_normalize_text("  first  \n\n second\t\n"), "first\nsecond")

    def test_rejects_cloudflare_challenge_text(self):
        content = _build_reliable_content("Just a moment...", "Checking if the site connection is secure")

        self.assertEqual(content.text, "")
        self.assertEqual(content.error, ANTI_BOT_ERROR)

    def test_rejects_duckduckgo_duck_challenge_text(self):
        text = "Unfortunately, bots use DuckDuckGo too. Select all squares containing a duck."
        content = _build_reliable_content("DuckDuckGo", text)

        self.assertEqual(content.text, "")
        self.assertEqual(content.error, ANTI_BOT_ERROR)

    def test_rejects_google_unusual_traffic_text(self):
        text = "我們的系統偵測到您的電腦網路送出的流量有異常情況。"
        content = _build_reliable_content("Google Search", text)

        self.assertEqual(content.text, "")
        self.assertEqual(content.error, ANTI_BOT_ERROR)

    def test_rejects_bing_challenge_text(self):
        text = "最後一個步驟 請解決以下挑戰以繼續"
        content = _build_reliable_content("台北 天氣 - 搜尋", text)

        self.assertEqual(content.text, "")
        self.assertEqual(content.error, ANTI_BOT_ERROR)

    def test_rejects_empty_page_text(self):
        content = _build_reliable_content("Normal title", " \n\t ")

        self.assertEqual(content.text, "")
        self.assertEqual(content.error, EMPTY_PAGE_ERROR)

    def test_rejects_unreliable_404_page_text(self):
        content = _build_reliable_content("404 Not Found", "The requested URL was not found on this website.")

        self.assertEqual(content.text, "")
        self.assertEqual(content.error, UNRELIABLE_PAGE_ERROR)

    def test_accepts_regular_page_text(self):
        content = _build_reliable_content("Docs", "Install patchright\nUse async API")

        self.assertEqual(content.text, "Install patchright\nUse async API")
        self.assertEqual(content.error, "")

    def test_builds_reliable_http_fallback_result(self):
        target = {"source_type": "url", "query": "", "url": "https://example.test/page"}
        page = HttpPageText(
            final_url="https://example.test/page",
            title="Codex for Open Source",
            text=(
                "Codex for Open Source 計畫旨在支援關鍵開放原始碼軟體維護者。\n"
                "維護者可以使用這些工具處理程式碼審查、問題分流與發布流程。\n"
                "這段內容足夠描述頁面主旨，不只是導覽列或選單。"
            ),
        )

        result = _build_http_fallback_result(target, page)

        self.assertEqual(result.error, "")
        self.assertEqual(result.title, "Codex for Open Source")
        self.assertIn("開放原始碼", result.text)

    def test_rejects_credential_url(self):
        with self.assertRaises(BrowserToolError):
            _normalize_url("https://user:password@example.test/page")

    def test_rejects_localhost_url(self):
        with self.assertRaises(BrowserToolError):
            _normalize_url("http://127.0.0.1:8080/admin")


class SearxngSearchTests(unittest.IsolatedAsyncioTestCase):
    def test_format_searxng_results(self):
        payload = {
            "results": [
                {
                    "title": "焦點新聞",
                    "url": "https://example.test/news",
                    "content": "今日新聞摘要",
                    "engine": "bing news",
                }
            ]
        }

        text = _format_searxng_results(payload)

        self.assertIn("焦點新聞", text)
        self.assertIn("https://example.test/news", text)
        self.assertIn("今日新聞摘要", text)
        self.assertIn("來源引擎: bing news", text)

    async def test_default_search_uses_local_searxng(self):
        with patch.dict(os.environ, {}, clear=True):
            search_planner = SearchPlanner(timeout_ms=1000)
            with patch("utils.browser_search.fetch_searxng_search_result") as fetch_searxng:
                fetch_searxng.return_value = BrowserFetchResult(
                    requested_url="竹北 天氣",
                    source_type="search",
                    query="竹北 天氣",
                    final_url="http://127.0.0.1:19183/search?q=test",
                    title="SearXNG Search",
                    text="竹北天氣\nhttps://example.test/weather",
                )
                search_results = await search_planner.search_many(["竹北 天氣"])

        self.assertEqual(search_results[0].title, "SearXNG Search")
        self.assertEqual(fetch_searxng.call_args.args[1], "http://127.0.0.1:19183")

    async def test_obsolete_search_provider_env_is_ignored(self):
        env = {"BROWSER_SEARCH_PROVIDER": "bing"}
        with patch.dict(os.environ, env, clear=True):
            search_planner = SearchPlanner(timeout_ms=1000)
            with patch("utils.browser_search.fetch_searxng_search_result") as fetch_searxng:
                fetch_searxng.return_value = BrowserFetchResult(
                    requested_url="python asyncio",
                    source_type="search",
                    query="python asyncio",
                    final_url="http://127.0.0.1:19183/search?q=python+asyncio&format=json",
                    title="SearXNG Search",
                    text="asyncio docs\nhttps://docs.python.org/3/library/asyncio.html",
                )
                search_results = await search_planner.search_many(["python asyncio"])

        self.assertEqual(search_results[0].title, "SearXNG Search")
        fetch_searxng.assert_called_once()
        self.assertEqual(fetch_searxng.call_args.args[1], "http://127.0.0.1:19183")

    async def test_searxng_failure_returns_error_without_browser_target(self):
        error_result = BrowserFetchResult(
            requested_url="python asyncio",
            source_type="search",
            query="python asyncio",
            final_url="http://127.0.0.1:19183",
            title="SearXNG Search",
            error="SearXNG failed",
        )
        with patch.dict(os.environ, {}, clear=True):
            search_planner = SearchPlanner(timeout_ms=1000)
            with patch("utils.browser_search.fetch_searxng_search_result", return_value=error_result):
                search_results = await search_planner.search_many(["python asyncio"])

        self.assertEqual(search_results, [error_result])

    async def test_searxng_base_url_env_uses_configured_api(self):
        env = {
            "SEARXNG_BASE_URL": "http://localhost:8080",
        }
        api_result = BrowserFetchResult(
            requested_url="python asyncio",
            source_type="search",
            query="python asyncio",
            final_url="http://localhost:8080/search?q=python+asyncio&format=json",
            title="SearXNG Search",
            text="asyncio docs\nhttps://docs.python.org/3/library/asyncio.html",
        )
        with patch.dict(os.environ, env, clear=True):
            search_planner = SearchPlanner(timeout_ms=1000)
            with patch("utils.browser_search.fetch_searxng_search_result", return_value=api_result):
                search_results = await search_planner.search_many(["python asyncio"])

        self.assertEqual(search_results, [api_result])

    def test_searxng_categories_are_omitted_by_default(self):
        env = {
            "SEARXNG_BASE_URL": "http://localhost:8080",
        }
        with patch.dict(os.environ, env, clear=True):
            search_planner = SearchPlanner(timeout_ms=1000)

        self.assertEqual(search_planner.searxng_categories_for_query("今日新聞 焦點"), "")

    def test_searxng_categories_honor_explicit_env(self):
        env = {
            "SEARXNG_BASE_URL": "http://localhost:8080",
            "SEARXNG_CATEGORIES": "news",
        }
        with patch.dict(os.environ, env, clear=True):
            search_planner = SearchPlanner(timeout_ms=1000)

        self.assertEqual(search_planner.searxng_categories_for_query("台北 天氣"), "news")

    def test_local_searxng_request_adds_real_ip_header(self):
        request = _searxng_json_request("http://127.0.0.1:19183/search?q=test&format=json")

        self.assertEqual(request.headers["X-real-ip"], "127.0.0.1")

    def test_remote_searxng_request_does_not_spoof_real_ip(self):
        request = _searxng_json_request("https://searx.example.test/search?q=test&format=json")

        self.assertNotIn("X-real-ip", request.headers)

    def test_searxng_provider_retries_empty_result_once(self):
        empty_payload = {"results": []}
        valid_payload = {
            "results": [
                {
                    "title": "臺北市天氣",
                    "url": "https://example.test/weather",
                    "content": "今日天氣摘要",
                    "engine": "google",
                }
            ]
        }

        with patch("utils.search_provider_api.time.sleep"), patch(
            "utils.search_provider_api._request_searxng_search",
            side_effect=[
                ("http://localhost:8080/search?q=test", empty_payload),
                ("http://localhost:8080/search?q=test", valid_payload),
            ],
        ) as request_search:
            result = fetch_searxng_search_result("台北 天氣", "http://localhost:8080", 1000)

        self.assertEqual(request_search.call_count, 2)
        self.assertEqual(result.error, "")
        self.assertIn("臺北市天氣", result.text)


class PatchrightBrowserClientConfigTests(unittest.TestCase):
    def test_launch_options_default_to_headless(self):
        with patch.dict(os.environ, {}, clear=True):
            client = PatchrightBrowserClient()

        self.assertEqual(client._launch_options(), {"headless": True})

    def test_launch_options_honor_channel_and_headless_env(self):
        env = {
            "PATCHRIGHT_BROWSER_CHANNEL": "chrome",
            "PATCHRIGHT_HEADLESS": "false",
        }
        with patch.dict(os.environ, env, clear=True):
            client = PatchrightBrowserClient()

        self.assertEqual(client._launch_options(), {"headless": False, "channel": "chrome"})


if __name__ == "__main__":
    unittest.main()
