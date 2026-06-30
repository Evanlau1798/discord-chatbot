from __future__ import annotations

import json
import unittest

from utils.youtube_transcript_reader import (
    FetchedText,
    parse_json3_transcript,
    parse_srv3_transcript,
    parse_youtube_video_id,
    read_youtube_transcript_url,
    select_caption_track,
    timedtext_url_matches_video,
)


class YoutubeTranscriptReaderTests(unittest.TestCase):
    def test_parse_video_id_supports_common_youtube_urls(self):
        cases = {
            "https://www.youtube.com/watch?v=abc123xyz00": "abc123xyz00",
            "https://youtu.be/abc123xyz00?t=30": "abc123xyz00",
            "https://www.youtube.com/shorts/abc123xyz00": "abc123xyz00",
            "https://www.youtube.com/embed/abc123xyz00": "abc123xyz00",
            "https://www.youtube.com/live/abc123xyz00": "abc123xyz00",
        }

        for url, expected in cases.items():
            with self.subTest(url=url):
                self.assertEqual(parse_youtube_video_id(url), expected)

    def test_timedtext_url_requires_exact_current_video_id(self):
        self.assertTrue(timedtext_url_matches_video("https://www.youtube.com/api/timedtext?v=abc&lang=en", "abc"))
        self.assertFalse(timedtext_url_matches_video("https://www.youtube.com/api/timedtext?v=abcd&lang=en", "abc"))
        self.assertFalse(timedtext_url_matches_video("https://www.youtube.com/api/timedtext?v=prev&lang=en", "abc"))

    def test_select_caption_track_prefers_chinese_then_english_then_manual(self):
        tracks = [
            {"languageCode": "ja", "baseUrl": "https://example.test/ja", "name": {"simpleText": "Japanese"}},
            {"languageCode": "en", "baseUrl": "https://example.test/en", "name": {"simpleText": "English"}},
            {"languageCode": "zh-Hant", "baseUrl": "https://example.test/zh", "name": {"simpleText": "Chinese"}},
        ]

        track = select_caption_track(tracks)

        self.assertEqual(track["languageCode"], "zh-Hant")

    def test_parse_json3_transcript_segments(self):
        payload = json.dumps({
            "events": [
                {"tStartMs": 1000, "dDurationMs": 1200, "segs": [{"utf8": "hello "}, {"utf8": "world"}]},
                {"tStartMs": 3000, "dDurationMs": 800, "segs": [{"utf8": "\n"}]},
            ]
        })

        segments = parse_json3_transcript(payload)

        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0].start, 1.0)
        self.assertEqual(segments[0].end, 2.2)
        self.assertEqual(segments[0].text, "hello world")

    def test_parse_srv3_transcript_segments(self):
        xml = '<timedtext><body><p t="1000" d="2000">hello <s>world</s></p></body></timedtext>'

        segments = parse_srv3_transcript(xml)

        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0].start, 1.0)
        self.assertEqual(segments[0].end, 3.0)
        self.assertEqual(segments[0].text, "hello world")

    def test_read_youtube_transcript_returns_grouped_browser_result(self):
        watch_html = _watch_html(
            caption_tracks=[
                {
                    "languageCode": "en",
                    "kind": "",
                    "baseUrl": "https://www.youtube.com/api/timedtext?v=abc123xyz00&lang=en",
                    "name": {"simpleText": "English"},
                }
            ],
        )
        json3 = json.dumps({
            "events": [
                {"tStartMs": 1000, "dDurationMs": 1000, "segs": [{"utf8": "First sentence."}]},
                {"tStartMs": 2500, "dDurationMs": 1000, "segs": [{"utf8": "Second sentence."}]},
            ]
        })
        calls = []

        def fetch_text(url: str, timeout_ms: int) -> FetchedText:
            calls.append(url)
            if "watch?" in url:
                return FetchedText(final_url=url, text=watch_html)
            return FetchedText(final_url=url, text=json3)

        result = read_youtube_transcript_url("https://youtu.be/abc123xyz00", 1000, fetch_text=fetch_text)

        self.assertIsNotNone(result)
        self.assertEqual(result.content_format, "youtube_transcript")
        self.assertEqual(result.title, "Demo Video")
        self.assertIn("[0:01] First sentence.", result.text)
        self.assertIn("[0:02] Second sentence.", result.text)
        self.assertTrue(any("fmt=json3" in url for url in calls))

    def test_read_youtube_transcript_falls_back_to_metadata_when_no_captions(self):
        watch_html = _watch_html(caption_tracks=[])

        def fetch_text(url: str, timeout_ms: int) -> FetchedText:
            return FetchedText(final_url=url, text=watch_html)

        result = read_youtube_transcript_url("https://www.youtube.com/watch?v=abc123xyz00", 1000, fetch_text=fetch_text)

        self.assertIsNotNone(result)
        self.assertEqual(result.content_format, "youtube_metadata")
        self.assertIn("Demo Video", result.text)
        self.assertIn("youtube_transcript_unavailable", result.diagnostics)


def _watch_html(caption_tracks: list[dict]) -> str:
    player = {
        "videoDetails": {
            "title": "Demo Video",
            "shortDescription": "A useful demo description.",
            "thumbnail": {"thumbnails": [{"url": "https://i.ytimg.com/vi/abc123xyz00/maxresdefault.jpg"}]},
        },
        "captions": {"playerCaptionsTracklistRenderer": {"captionTracks": caption_tracks}},
    }
    return f"<html><script>var ytInitialPlayerResponse = {json.dumps(player)};</script></html>"


if __name__ == "__main__":
    unittest.main()
