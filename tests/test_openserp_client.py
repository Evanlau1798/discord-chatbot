import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from utils.openserp_client import OpenSerpClient, OpenSerpSearchRequest


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class OpenSerpClientTests(unittest.TestCase):
    def test_search_sends_balanced_multi_engine_extraction_request(self):
        payload = {
            "meta": {"engines_failed": ["google"], "engine_errors": {"google": "captcha_detected"}},
            "results": [
                {
                    "title": "Official docs",
                    "url": "https://example.com/docs",
                    "snippet": "Documentation",
                    "engine": "bing",
                    "domain_info": {"category": "gov"},
                    "classification": {"source_hint": "document"},
                    "extracted": {"content": "Useful extracted documentation", "format": "markdown"},
                }
            ],
            "clusters": [{"canonical_url": "https://example.com/docs", "score": 1.5, "engines_count": 2}],
        }
        with patch("utils.openserp_client.urlopen", return_value=_Response(payload)) as urlopen:
            response = OpenSerpClient("http://127.0.0.1:17000", 4000).search(
                OpenSerpSearchRequest(
                    query="example docs",
                    language="zh-TW",
                    region="TW",
                    time_range="month",
                    site_domains=("example.com",),
                    desired_sources=3,
                )
            )

        request = urlopen.call_args.args[0]
        self.assertIn("/mega/search?", request.full_url)
        self.assertIn("engines=google%2Cbing%2Cduckduckgo%2Cecosia", request.full_url)
        self.assertIn("mode=balanced", request.full_url)
        self.assertIn("extract=3", request.full_url)
        self.assertIn("site=example.com", request.full_url)
        self.assertEqual(response.failed_engines, ("google",))
        self.assertEqual(response.sources[0].cluster_score, 1.5)
        self.assertEqual(response.sources[0].text, "Useful extracted documentation")
        self.assertEqual(response.sources[0].source_hint, "document")
        self.assertEqual(response.sources[0].source_category, "gov")

    def test_search_returns_partial_results_when_one_engine_hits_captcha(self):
        payload = {
            "meta": {"engines_failed": ["google"], "engine_errors": {"google": "captcha_detected"}},
            "results": [
                {
                    "title": "Bing result",
                    "url": "https://example.org/page",
                    "snippet": "Result summary",
                    "engine": "bing",
                    "extracted": {"content": "Readable result body"},
                }
            ],
        }
        with patch("utils.openserp_client.urlopen", return_value=_Response(payload)):
            response = OpenSerpClient("http://localhost:17000", 1000).search(OpenSerpSearchRequest("query"))

        self.assertEqual(len(response.sources), 1)
        self.assertEqual(response.sources[0].engines, ("bing",))
        self.assertIn("google:captcha_detected", response.diagnostics)

    def test_search_does_not_retry_provider_error(self):
        error = HTTPError("http://localhost:17000/mega/search", 502, "all engines failed", {}, None)
        with patch("utils.openserp_client.urlopen", side_effect=error) as urlopen:
            response = OpenSerpClient("http://localhost:17000", 1000).search(OpenSerpSearchRequest("query"))

        self.assertEqual(urlopen.call_count, 1)
        self.assertEqual(response.sources, ())
        self.assertEqual(response.error, "OpenSERP Search failed: HTTPError")


if __name__ == "__main__":
    unittest.main()
