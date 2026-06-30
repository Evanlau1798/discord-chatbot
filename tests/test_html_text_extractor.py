from __future__ import annotations

import unittest

from utils.html_text_extractor import extract_html_text


class HtmlTextExtractorTests(unittest.TestCase):
    def test_extracts_title_and_body_without_scripts(self):
        html = """
        <html>
          <head>
            <title>Codex for Open Source</title>
            <style>.hidden { display: none; }</style>
            <script>window.secret = "do not include";</script>
          </head>
          <body>
            <nav>登入 試用 ChatGPT</nav>
            <main>
              <h1>Codex for Open Source</h1>
              <p>計畫旨在支援關鍵開放原始碼軟體維護者。</p>
            </main>
            <footer>OpenAI footer links</footer>
          </body>
        </html>
        """

        extracted = extract_html_text(html)

        self.assertEqual(extracted.title, "Codex for Open Source")
        self.assertIn("計畫旨在支援", extracted.text)
        self.assertNotIn("do not include", extracted.text)
        self.assertNotIn("display", extracted.text)
        self.assertNotIn("登入", extracted.text)
        self.assertNotIn("footer", extracted.text)

    def test_extracts_page_image_urls(self):
        html = """
        <html>
          <head>
            <meta property="og:image" content="/images/cover.jpg">
          </head>
          <body>
            <main>
              <img src="photo.png" alt="cover">
              <img srcset="small.webp 480w, large.webp 960w">
            </main>
          </body>
        </html>
        """

        extracted = extract_html_text(html, base_url="https://example.test/posts/1")

        self.assertEqual(extracted.image_urls[0], "https://example.test/images/cover.jpg")
        self.assertIn("https://example.test/posts/photo.png", extracted.image_urls)
        self.assertIn("https://example.test/posts/large.webp", extracted.image_urls)

    def test_body_fallback_ignores_common_navigation_chrome(self):
        html = """
        <html>
          <head><title>Article</title></head>
          <body>
            <nav>登入 選單 首頁</nav>
            <article><h1>Article</h1><p>這是主要文章內容。</p></article>
            <footer>頁尾連結 隱私權</footer>
          </body>
        </html>
        """

        extracted = extract_html_text(html)

        self.assertIn("主要文章內容", extracted.text)
        self.assertNotIn("登入", extracted.text)
        self.assertNotIn("頁尾", extracted.text)


if __name__ == "__main__":
    unittest.main()
