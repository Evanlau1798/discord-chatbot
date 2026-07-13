from __future__ import annotations

import unittest

from utils.embedded_subtitles import parse_srt_segments


class EmbeddedSubtitleTests(unittest.TestCase):
    def test_parses_srt_into_timestamped_segments(self):
        segments = parse_srt_segments(
            "1\n00:00:00,100 --> 00:00:01,250\n第一行\n\n"
            "2\n00:00:01,300 --> 00:00:02,000\n第二行\n延續\n"
        )

        self.assertEqual(segments, (
            {"startSeconds": 0.1, "endSeconds": 1.25, "text": "第一行"},
            {"startSeconds": 1.3, "endSeconds": 2.0, "text": "第二行 延續"},
        ))

    def test_ignores_invalid_and_empty_cues(self):
        self.assertEqual(parse_srt_segments("not an srt"), ())


if __name__ == "__main__":
    unittest.main()
