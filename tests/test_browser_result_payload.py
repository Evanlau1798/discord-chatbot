from __future__ import annotations

import unittest
from unittest.mock import patch

from utils.browser_client import BrowserFetchResult
from utils.browser_result_payload import (
    build_browser_followup_content,
    build_browser_followup_payload,
    build_inline_browser_context,
)


class BrowserResultPayloadTests(unittest.TestCase):
    def test_followup_requires_citations_from_returned_urls(self):
        result = BrowserFetchResult(
            requested_url="query",
            source_type="search",
            final_url="https://example.test/source",
            title="Source",
            text="Reliable source content",
        )

        payload = build_browser_followup_payload([result])

        self.assertIn("引用", payload["payload"]["instruction"])
        self.assertIn("finalUrl", payload["payload"]["instruction"])

    def test_filters_failed_results_when_readable_result_exists(self):
        payload = build_browser_followup_payload([
            BrowserFetchResult(
                requested_url="http://127.0.0.1:19183/search?q=test",
                source_type="search",
                query="台北 天氣",
                error="頁面顯示 CAPTCHA 或反機器人驗證，無法可靠讀取內容。",
            ),
            BrowserFetchResult(
                requested_url="https://www.cwa.gov.tw/V8/C/W/County/County.html?CID=63",
                source_type="url",
                title="臺北市 - 縣市預報",
                text="臺北市 今日白天 27 - 29 70% 悶熱",
            ),
        ])

        results = payload["payload"]["browserResults"]

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "臺北市 - 縣市預報")
        self.assertEqual(results[0]["error"], "")
        self.assertEqual(payload["payload"]["omittedFailedResultCount"], 1)
        self.assertIn("不要提及", payload["payload"]["instruction"])

    def test_keeps_text_result_but_removes_partial_error(self):
        payload = build_browser_followup_payload([
            BrowserFetchResult(
                requested_url="https://example.com",
                source_type="url",
                title="Example",
                text="Readable content",
                error="頁面載入逾時",
                content_format="html",
                total_chars=16,
                next_start_char=None,
                diagnostics=("http_first",),
            ),
        ])

        result = payload["payload"]["browserResults"][0]

        self.assertEqual(result["text"], "Readable content")
        self.assertEqual(result["error"], "")
        self.assertEqual(result["contentFormat"], "html")
        self.assertEqual(result["totalChars"], 16)
        self.assertEqual(result["diagnostics"], ["http_first"])

    def test_empty_browser_results_when_all_results_failed(self):
        payload = build_browser_followup_payload([
            BrowserFetchResult(
                requested_url="https://duckduckgo.com",
                source_type="search",
                query="台北 天氣",
                error="頁面顯示 CAPTCHA 或反機器人驗證，無法可靠讀取內容。",
            ),
        ])

        self.assertEqual(payload["payload"]["browserResults"], [])
        self.assertEqual(payload["payload"]["omittedFailedResultCount"], 1)
        self.assertIn("無法取得可靠網頁內容", payload["payload"]["instruction"])
        self.assertNotIn("不要嘲諷", payload["payload"]["instruction"])
        self.assertNotIn("冷門", payload["payload"]["instruction"])
        self.assertNotIn("不要宣稱目標內容不存在", payload["payload"]["instruction"])
        self.assertIn("具體替代搜尋關鍵字", payload["payload"]["instruction"])

    def test_empty_results_can_allow_one_controlled_search_rewrite(self):
        payload = build_browser_followup_payload(
            [BrowserFetchResult(requested_url="query", source_type="search", error="insufficient")],
            allow_search_retry=True,
        )

        instruction = payload["payload"]["instruction"]
        self.assertIn("有且只有一次", instruction)
        self.assertIn("不可重複", instruction)
        self.assertIn("不要輸出 replyText", instruction)

    def test_followup_instruction_requires_direct_youtube_url_for_video_requests(self):
        payload = build_browser_followup_payload([
            BrowserFetchResult(
                requested_url="apex hal 吃麥克風 youtube",
                source_type="search",
                query="apex hal 吃麥克風 youtube",
                title="OpenSERP Search",
                text="巴哈姆特討論串\nhttps://forum.gamer.com.tw/C.php?bsn=36072&snA=8654",
            ),
        ])

        instruction = payload["payload"]["instruction"]

        self.assertIn("YouTube watch", instruction)
        self.assertIn("論壇、社群或討論串", instruction)
        self.assertIn("線索", instruction)

    def test_keeps_image_only_result(self):
        payload = build_browser_followup_payload([
            BrowserFetchResult(
                requested_url="https://example.test/article",
                source_type="url",
                final_url="https://example.test/article",
                title="Article",
                image_urls=("https://example.test/cover.jpg",),
            ),
        ])

        results = payload["payload"]["browserResults"]

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["imageUrls"], ["https://example.test/cover.jpg"])
        self.assertIn("圖片", payload["payload"]["instruction"])

    def test_followup_instruction_omits_image_generation_when_disabled(self):
        with patch.dict("os.environ", {"AI_IMAGINE_ENABLED": "0"}):
            payload = build_browser_followup_payload([
                BrowserFetchResult(
                    requested_url="https://example.test/article",
                    source_type="url",
                    title="Article",
                    text="Readable content",
                ),
            ])

        self.assertNotIn("imageGeneration", payload["payload"]["instruction"])
        self.assertIn("memory", payload["payload"]["instruction"])

    def test_followup_content_includes_image_url_parts(self):
        content = build_browser_followup_content([
            BrowserFetchResult(
                requested_url="https://example.test/article",
                source_type="url",
                final_url="https://example.test/article",
                title="Article",
                text="Article text",
                image_urls=("https://example.test/cover.jpg",),
            ),
        ])

        self.assertEqual(content[0]["type"], "text")
        self.assertEqual(content[1], {"type": "image_url", "image_url": {"url": "https://example.test/cover.jpg"}})

    def test_inline_browser_context_allows_browser_when_prefetch_is_insufficient(self):
        payload = build_inline_browser_context([
            BrowserFetchResult(
                requested_url="https://example.test/article",
                source_type="url",
                title="Article",
                text="Readable content.",
            ),
        ])

        self.assertEqual(payload["browserResults"][0]["title"], "Article")
        self.assertIn("prefetchedBrowserContext", payload["instruction"])
        self.assertIn("仍可輸出 browser", payload["instruction"])


if __name__ == "__main__":
    unittest.main()
