from __future__ import annotations

import json
import subprocess
import unittest
from unittest.mock import patch

from utils.youtube_search_reader import (
    plan_youtube_search_queries_from_env,
    search_youtube_videos,
    youtube_query_cooldown_seconds_from_env,
)


class YoutubeSearchReaderTests(unittest.TestCase):
    def test_plan_youtube_search_queries_uses_one_query_by_default(self):
        with patch.dict("os.environ", {}, clear=True):
            queries = plan_youtube_search_queries_from_env(["first", "second", "third"])

        self.assertEqual(queries, ["first"])

    def test_plan_youtube_search_queries_can_be_raised_by_env(self):
        env = {"YOUTUBE_SEARCH_MAX_QUERIES_PER_TURN": "2"}
        with patch.dict("os.environ", env, clear=True):
            queries = plan_youtube_search_queries_from_env(["first", "second", "third"])

        self.assertEqual(queries, ["first", "second"])

    def test_youtube_query_cooldown_defaults_to_one_second(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(youtube_query_cooldown_seconds_from_env(), 1.0)

    def test_search_youtube_videos_formats_flat_playlist_results(self):
        payload = {
            "entries": [
                {
                    "id": "jJoe2D8zet8",
                    "title": "From Subs to Mic-Eating",
                    "url": "https://www.youtube.com/watch?v=jJoe2D8zet8",
                    "channel": "Apex Clips",
                    "duration": 95,
                    "view_count": 12345,
                    "description": "Hal reacts to the mic moment.",
                }
            ]
        }

        def runner(command, **kwargs):
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")

        result = search_youtube_videos("Apex Hal eating microphone", 1000, runner=runner)

        self.assertEqual(result.source_type, "youtube_search")
        self.assertEqual(result.query, "Apex Hal eating microphone")
        self.assertEqual(result.content_format, "youtube_search_results")
        self.assertIn("From Subs to Mic-Eating", result.text)
        self.assertIn("https://www.youtube.com/watch?v=jJoe2D8zet8", result.text)
        self.assertIn("Channel: Apex Clips", result.text)
        self.assertIn("Duration: 1:35", result.text)
        self.assertIn("Views: 12345", result.text)
        self.assertIn("youtube_ytdlp_search", result.diagnostics)
        self.assertEqual(result.error, "")

    def test_search_youtube_videos_uses_bounded_limit_and_sleep_requests(self):
        captured = {}

        def runner(command, **kwargs):
            captured["command"] = command
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps({"entries": []}), stderr="")

        search_youtube_videos("query", 1000, limit=99, runner=runner)

        self.assertIn("--playlist-end", captured["command"])
        self.assertIn("10", captured["command"])
        self.assertIn("--sleep-requests", captured["command"])
        self.assertIn("ytsearch10:query", captured["command"])

    def test_search_youtube_videos_builds_watch_url_from_flat_url_id(self):
        payload = {
            "entries": [
                {
                    "title": "Flat result",
                    "url": "abc123xyz00",
                }
            ]
        }

        def runner(command, **kwargs):
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")

        result = search_youtube_videos("query", 1000, runner=runner)

        self.assertIn("https://www.youtube.com/watch?v=abc123xyz00", result.text)

    def test_search_youtube_videos_returns_error_result_on_failure(self):
        def runner(command, **kwargs):
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="temporary unavailable")

        result = search_youtube_videos("Apex Hal", 1000, runner=runner)

        self.assertEqual(result.source_type, "youtube_search")
        self.assertEqual(result.query, "Apex Hal")
        self.assertEqual(result.text, "")
        self.assertIn("yt-dlp YouTube search failed", result.error)
        self.assertIn("youtube_ytdlp_search_failed", result.diagnostics)


if __name__ == "__main__":
    unittest.main()
