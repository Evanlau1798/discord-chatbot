from __future__ import annotations

import unittest

from utils.browser_text_quality import is_useful_http_text, prepare_browser_text


class BrowserTextQualityTests(unittest.TestCase):
    def test_rejects_sparse_navigation_menu_text(self):
        text = "\n".join(["快速地點搜尋", "選擇縣市", "搜尋", "確定"])

        self.assertFalse(is_useful_http_text(text))

    def test_accepts_regular_article_text(self):
        text = "\n".join([
            "Open source maintainers review pull requests and triage issues every day.",
            "The page describes a concrete program, its eligibility, and benefits.",
            "This is enough content for the model to summarize without a browser fallback.",
        ])

        self.assertTrue(is_useful_http_text(text))

    def test_trims_leading_boilerplate_before_content(self):
        text = "\n".join([
            "登入",
            "會員資料",
            "隱私權政策",
            "今日焦點新聞包含多個具體事件與最新發展。",
            "第二段提供更多背景，方便模型根據內容回答。",
        ])

        prepared = prepare_browser_text(text)

        self.assertTrue(prepared.startswith("今日焦點新聞"))
        self.assertNotIn("會員資料", prepared)


if __name__ == "__main__":
    unittest.main()
