from __future__ import annotations

import json
import unittest

from utils.youtube_ytdlp_reader import YtdlpMetadataResult
from utils.youtube_transcript_reader import (
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

    def test_read_youtube_transcript_uses_ytdlp_caption_url(self):
        json3 = json.dumps({
            "events": [
                {"tStartMs": 1000, "dDurationMs": 1000, "segs": [{"utf8": "First sentence."}]},
                {"tStartMs": 2500, "dDurationMs": 1000, "segs": [{"utf8": "Second sentence."}]},
            ]
        })
        metadata_calls = []
        caption_calls = []

        def fetch_metadata(url: str, timeout_ms: int) -> YtdlpMetadataResult:
            metadata_calls.append(url)
            return YtdlpMetadataResult(data={
                "id": "abc123xyz00",
                "webpage_url": "https://www.youtube.com/watch?v=abc123xyz00",
                "title": "Demo Video",
                "description": "A useful demo description.",
                "thumbnail": "https://i.ytimg.com/vi/abc123xyz00/maxresdefault.jpg",
                "subtitles": {},
                "automatic_captions": {
                    "en": [
                        {"ext": "json3", "url": "https://caption.test/en.json3"},
                        {"ext": "srv3", "url": "https://caption.test/en.srv3"},
                    ],
                },
            })

        def fetch_text(url: str, timeout_ms: int):
            caption_calls.append(url)
            return type("Fetched", (), {"final_url": url, "text": json3, "error": ""})()

        result = read_youtube_transcript_url(
            "https://youtu.be/abc123xyz00",
            1000,
            include_images=True,
            fetch_text=fetch_text,
            fetch_metadata=fetch_metadata,
        )

        self.assertIsNotNone(result)
        self.assertEqual(result.content_format, "youtube_transcript")
        self.assertEqual(result.title, "Demo Video")
        self.assertIn("[0:01] First sentence.", result.text)
        self.assertIn("[0:02] Second sentence.", result.text)
        self.assertEqual(metadata_calls, ["https://youtu.be/abc123xyz00"])
        self.assertEqual(caption_calls, ["https://caption.test/en.json3"])
        self.assertEqual(result.image_urls, ("https://i.ytimg.com/vi/abc123xyz00/maxresdefault.jpg",))

    def test_read_youtube_transcript_uses_ytdlp_metadata_when_no_captions(self):
        def fetch_metadata(url: str, timeout_ms: int) -> YtdlpMetadataResult:
            return YtdlpMetadataResult(data={
                "id": "abc123xyz00",
                "webpage_url": "https://www.youtube.com/watch?v=abc123xyz00",
                "title": "Demo Video",
                "description": "A useful demo description.",
                "thumbnail": "https://i.ytimg.com/vi/abc123xyz00/maxresdefault.jpg",
                "subtitles": {},
                "automatic_captions": {},
            })

        result = read_youtube_transcript_url(
            "https://www.youtube.com/watch?v=abc123xyz00",
            1000,
            fetch_metadata=fetch_metadata,
        )

        self.assertIsNotNone(result)
        self.assertEqual(result.content_format, "youtube_metadata")
        self.assertIn("Demo Video", result.text)
        self.assertIn("youtube_transcript_unavailable", result.diagnostics)

    def test_read_youtube_transcript_tries_next_ytdlp_caption_when_preferred_is_empty(self):
        json3 = json.dumps({
            "events": [
                {"tStartMs": 1000, "dDurationMs": 1000, "segs": [{"utf8": "English fallback."}]},
            ]
        })

        def fetch_metadata(url: str, timeout_ms: int) -> YtdlpMetadataResult:
            return YtdlpMetadataResult(data={
                "id": "abc123xyz00",
                "webpage_url": "https://www.youtube.com/watch?v=abc123xyz00",
                "title": "Demo Video",
                "description": "A useful demo description.",
                "subtitles": {},
                "automatic_captions": {
                    "zh-Hant": [{"ext": "json3", "url": "https://caption.test/zh.json3"}],
                    "en": [{"ext": "json3", "url": "https://caption.test/en.json3"}],
                },
            })

        def fetch_text(url: str, timeout_ms: int):
            text = "" if url.endswith("/zh.json3") else json3
            return type("Fetched", (), {"final_url": url, "text": text, "error": ""})()

        result = read_youtube_transcript_url(
            "https://youtu.be/abc123xyz00",
            1000,
            fetch_text=fetch_text,
            fetch_metadata=fetch_metadata,
        )

        self.assertIsNotNone(result)
        self.assertEqual(result.content_format, "youtube_transcript")
        self.assertIn("English fallback.", result.text)
        self.assertIn("language=en", result.media_notes[0])


if __name__ == "__main__":
    unittest.main()
