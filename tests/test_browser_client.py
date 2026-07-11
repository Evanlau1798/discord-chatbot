from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from utils.browser_client import (
    ANTI_BOT_ERROR,
    BrowserToolError,
    EMPTY_PAGE_ERROR,
    PatchrightBrowserClient,
    UNRELIABLE_PAGE_ERROR,
    _build_reliable_content,
    _build_http_fallback_result,
    _normalize_url,
    _normalize_text,
)
from utils.http_page_fetcher import HttpPageText


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
