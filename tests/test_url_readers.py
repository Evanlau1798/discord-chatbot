from __future__ import annotations

import unittest

from utils.url_readers import read_special_url
from utils.x_status_reader import FetchedStatusPage, parse_x_status_url, read_x_status_url


class UrlReadersTests(unittest.TestCase):
    def test_read_special_url_returns_none_for_regular_page(self):
        self.assertIsNone(read_special_url("https://example.test/article", 1000))

    def test_parse_x_status_url_accepts_twitter_and_x_hosts(self):
        self.assertEqual(parse_x_status_url("https://x.com/example/status/2071589845864943655"), "2071589845864943655")
        self.assertEqual(parse_x_status_url("https://twitter.com/example/status/2071589845864943655"), "2071589845864943655")

    def test_read_x_status_url_extracts_text_and_images_without_video_payload(self):
        html = """
        <html><head>
          <meta property="og:title" content="Bruno Simon on X">
          <meta property="og:description" content="A tiny interactive car experiment.">
          <meta property="og:image" content="https://pbs.twimg.com/media/example?format=jpg&name=large">
          <meta property="og:video" content="https://video.twimg.com/ext_tw_video/example/pu/vid/1280x720/video.mp4">
        </head></html>
        """

        def fetch_page(url: str, timeout_ms: int) -> FetchedStatusPage:
            return FetchedStatusPage(final_url=url, html=html)

        result = read_x_status_url(
            "https://x.com/bruno_simon/status/2071589845864943655",
            1000,
            fetch_page=fetch_page,
        )

        self.assertIsNotNone(result)
        self.assertEqual(result.content_format, "x_status")
        self.assertIn("A tiny interactive car experiment", result.text)
        self.assertEqual(result.image_urls, ("https://pbs.twimg.com/media/example?format=jpg&name=large",))
        self.assertIn("不送完整影片", result.media_notes[0])
        self.assertNotIn("video.twimg.com", result.to_payload().get("imageUrls", []))


if __name__ == "__main__":
    unittest.main()
