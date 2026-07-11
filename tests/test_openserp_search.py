import asyncio
import os
import unittest
from unittest.mock import patch

from utils.openserp_client import OpenSerpResponse, OpenSerpSource
from utils.openserp_search import SearchPlanner, canonicalize_source_url, select_reliable_sources


class _Client:
    def __init__(self, responses):
        self.responses = responses
        self.requests = []
        self.active = 0
        self.max_active = 0

    def search(self, request):
        self.requests.append(request)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            return self.responses[request.query]
        finally:
            self.active -= 1


class OpenSerpQualityTests(unittest.TestCase):
    def test_canonical_url_removes_fragment_tracking_and_default_port(self):
        result = canonicalize_source_url("HTTPS://Example.COM:443/docs/?utm_source=x&b=2&a=1#top")
        self.assertEqual(result, "https://example.com/docs?a=1&b=2")

    def test_selection_deduplicates_and_prioritizes_explicit_site(self):
        sources = [
            ("pricing", OpenSerpSource("Mirror", "https://blog.test/post", text="x" * 300, cluster_score=2.0)),
            ("pricing", OpenSerpSource("Official", "https://openai.com/api/pricing", text="y" * 300, cluster_score=0.5)),
            ("pricing", OpenSerpSource("Duplicate", "https://openai.com/api/pricing?utm_medium=x", text="z" * 300)),
        ]
        selected = select_reliable_sources(sources, desired_sources=3, site_domains=("openai.com",))

        self.assertEqual([item.url for item in selected], ["https://openai.com/api/pricing", "https://blog.test/post"])

    def test_selection_requires_extracted_content_and_clips_budgets(self):
        sources = [
            ("query", OpenSerpSource("One", "https://one.test/a", text="a" * 20_000)),
            ("query", OpenSerpSource("No extraction", "https://two.test/b", snippet="snippet only")),
            ("query", OpenSerpSource("Three", "https://three.test/c", text="c" * 20_000)),
            ("query", OpenSerpSource("Four", "https://four.test/d", text="d" * 20_000)),
        ]
        selected = select_reliable_sources(sources, desired_sources=3, per_source_chars=10_000, total_chars=25_000)

        self.assertEqual([len(item.text) for item in selected], [10_000, 10_000, 5_000])
        self.assertNotIn("https://two.test/b", [item.url for item in selected])


class OpenSerpPlannerTests(unittest.IsolatedAsyncioTestCase):
    async def test_planner_limits_queries_and_returns_individual_citable_sources(self):
        response = OpenSerpResponse(
            sources=(
                OpenSerpSource("One", "https://one.test", text="one " * 100, engines=("bing",)),
                OpenSerpSource("Two", "https://two.test", text="two " * 100, engines=("duckduckgo",)),
            )
        )
        client = _Client({"q1": response, "q2": response, "q3": response})
        with patch.dict(os.environ, {"OPENSERP_MAX_QUERIES_PER_TURN": "3"}, clear=True):
            planner = SearchPlanner(timeout_ms=1000, client=client)
            results = await planner.search_many(["q1", "q2", "q3", "q4"])

        self.assertEqual([request.query for request in client.requests], ["q1", "q2", "q3"])
        self.assertEqual([result.final_url for result in results], ["https://one.test", "https://two.test"])
        self.assertTrue(all(result.source_type == "search" for result in results))

    async def test_planner_returns_no_reliable_content_when_only_one_untrusted_source_exists(self):
        response = OpenSerpResponse(sources=(OpenSerpSource("One", "https://one.test", text="one " * 100),))
        planner = SearchPlanner(timeout_ms=1000, client=_Client({"q": response}))

        results = await planner.search_many(["q"])

        self.assertEqual(len(results), 1)
        self.assertFalse(results[0].text)
        self.assertIn("可靠來源不足", results[0].error)

    async def test_planners_share_global_three_request_limit(self):
        class SlowClient(_Client):
            def search(self, request):
                self.requests.append(request)
                self.active += 1
                self.max_active = max(self.max_active, self.active)
                try:
                    import time
                    time.sleep(0.02)
                    return self.responses[request.query]
                finally:
                    self.active -= 1

        response = OpenSerpResponse(sources=())
        client = SlowClient({f"q{i}": response for i in range(6)})
        planners = [SearchPlanner(timeout_ms=1000, client=client) for _ in range(6)]

        await asyncio.gather(*(planner.search_many([f"q{i}"]) for i, planner in enumerate(planners)))

        self.assertEqual(client.max_active, 3)


if __name__ == "__main__":
    unittest.main()
