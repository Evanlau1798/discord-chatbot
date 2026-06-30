from __future__ import annotations

import unittest

from utils.json_response_protocol import BrowserFindRequest, ImageUnderstandingBlock, parse_model_response


class JsonResponseProtocolBrowserTests(unittest.TestCase):
    def test_browser_find_object_is_parsed(self):
        parsed = parse_model_response(
            '{"browser":{"find":{"url":"https://example.test/docs","pattern":"install"}}}'
        )

        self.assertEqual(parsed.reply_text, "")
        self.assertEqual(
            parsed.browser.find_requests,
            [BrowserFindRequest(url="https://example.test/docs", pattern="install")],
        )

    def test_browser_find_list_is_deduped_and_limited(self):
        parsed = parse_model_response(
            """
            {
              "browser": {
                "finds": [
                  {"url": "https://example.test/a", "pattern": "alpha"},
                  {"url": "https://example.test/a", "pattern": "alpha"},
                  {"url": "https://example.test/b", "pattern": "beta"},
                  {"url": "https://example.test/c", "pattern": "gamma"},
                  {"url": "https://example.test/d", "pattern": "delta"},
                  {"url": "https://example.test/e", "pattern": "epsilon"},
                  {"url": "https://example.test/f", "pattern": "zeta"}
                ]
              }
            }
            """
        )

        self.assertEqual(len(parsed.browser.find_requests), 5)
        self.assertEqual(parsed.browser.find_requests[0].url, "https://example.test/a")
        self.assertEqual(parsed.browser.find_requests[1].pattern, "beta")

    def test_browser_find_requires_strings(self):
        with self.assertRaises(ValueError):
            parse_model_response('{"browser":{"find":{"url":"https://example.test","pattern":123}}}')

    def test_browser_include_images_is_parsed(self):
        parsed = parse_model_response(
            '{"browser":{"link":"https://example.test/article","includeImages":true}}'
        )

        self.assertEqual(parsed.browser.urls, ["https://example.test/article"])
        self.assertTrue(parsed.browser.include_images)

    def test_image_understanding_is_parsed(self):
        parsed = parse_model_response(
            """
            {
              "replyText": "我看到了。",
              "imageUnderstanding": {
                "summary": "一張反應梗圖。",
                "visibleText": ["不要瞎掰"],
                "details": ["角色看起來在吐槽"]
              }
            }
            """
        )

        self.assertEqual(parsed.reply_text, "我看到了。")
        self.assertEqual(
            parsed.image_understanding,
            ImageUnderstandingBlock(
                summary="一張反應梗圖。",
                visible_text=("不要瞎掰",),
                details=("角色看起來在吐槽",),
            ),
        )

    def test_image_understanding_accepts_string_details(self):
        parsed = parse_model_response(
            """
            {
              "replyText": "我看到了。",
              "imageUnderstanding": {
                "summary": "一張 GIF 動圖。",
                "visibleText": "",
                "details": "角色比出大拇指。"
              }
            }
            """
        )

        self.assertEqual(parsed.image_understanding.visible_text, ())
        self.assertEqual(parsed.image_understanding.details, ("角色比出大拇指。",))

    def test_image_understanding_accepts_string_summary_block(self):
        parsed = parse_model_response(
            '{"replyText":"我看到了。","imageUnderstanding":"一張短 GIF 動圖。"}'
        )

        self.assertEqual(parsed.reply_text, "我看到了。")
        self.assertEqual(parsed.image_understanding.summary, "一張短 GIF 動圖。")

    def test_invalid_image_understanding_does_not_break_reply_text(self):
        parsed = parse_model_response(
            '{"replyText":"我看到了。","imageUnderstanding":{"summary":123,"details":[456]}}'
        )

        self.assertEqual(parsed.reply_text, "我看到了。")
        self.assertIsNone(parsed.image_understanding)


if __name__ == "__main__":
    unittest.main()
