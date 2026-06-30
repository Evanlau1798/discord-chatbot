from __future__ import annotations

import unittest

from utils.browser_prefetch import extract_prefetch_web_urls


class BrowserPrefetchTests(unittest.TestCase):
    def test_extracts_only_explicit_web_page_urls(self):
        urls = extract_prefetch_web_urls(
            "請整理 https://example.test/article ，圖片 https://example.test/a.jpg 還有 https://tenor.com/view/test-gif-123",
            excluded_urls=["https://tenor.com/view/test-gif-123"],
        )

        self.assertEqual(urls, ["https://example.test/article"])

    def test_rejects_private_or_duplicate_urls(self):
        urls = extract_prefetch_web_urls(
            "http://127.0.0.1:8080/page https://example.test/a https://example.test/a"
        )

        self.assertEqual(urls, ["https://example.test/a"])

    def test_extracts_markdown_url_when_label_is_same_url(self):
        urls = extract_prefetch_web_urls(
            "[https://youtu.be/DYhzv0bOsPo](https://youtu.be/DYhzv0bOsPo)"
        )

        self.assertEqual(urls, ["https://youtu.be/DYhzv0bOsPo"])


if __name__ == "__main__":
    unittest.main()
