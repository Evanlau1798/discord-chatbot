import asyncio
from datetime import date
import os
import unittest
from unittest.mock import patch

from utils.openserp_client import OpenSerpResponse, OpenSerpSource
from utils.openserp_search import (
    SearchOptions,
    SearchPlanner,
    canonicalize_source_url,
    normalize_openserp_time_range,
    select_reliable_sources,
)


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
            ("pricing", OpenSerpSource("Pricing mirror", "https://blog.test/post", text="x" * 300, cluster_score=2.0)),
            ("pricing", OpenSerpSource("Official pricing", "https://openai.com/api/pricing", text="y" * 300, cluster_score=0.5)),
            ("pricing", OpenSerpSource("Pricing duplicate", "https://openai.com/api/pricing?utm_medium=x", text="z" * 300)),
        ]
        selected = select_reliable_sources(sources, desired_sources=3, site_domains=("openai.com",))

        self.assertEqual([item.url for item in selected], ["https://openai.com/api/pricing", "https://blog.test/post"])

    def test_selection_requires_extracted_content_and_clips_budgets(self):
        sources = [
            ("query", OpenSerpSource("Query one", "https://one.test/a", text="a" * 20_000)),
            ("query", OpenSerpSource("No extraction", "https://two.test/b", snippet="snippet only")),
            ("query", OpenSerpSource("Query three", "https://three.test/c", text="c" * 20_000)),
            ("query", OpenSerpSource("Query four", "https://four.test/d", text="d" * 20_000)),
        ]
        selected = select_reliable_sources(sources, desired_sources=3, per_source_chars=10_000, total_chars=25_000)

        self.assertEqual([len(item.text) for item in selected], [10_000, 10_000, 5_000])
        self.assertNotIn("https://two.test/b", [item.url for item in selected])

    def test_selection_rejects_adult_and_unsafe_domains(self):
        sources = [
            ("discord.py changelog", OpenSerpSource("Changelog", "https://discordpy.readthedocs.io/change_log.html", text="a" * 300)),
            ("discord.py changelog", OpenSerpSource("Unrelated", "https://hqtube.xxx/discord", text="b" * 300, cluster_score=9)),
            ("discord.py changelog", OpenSerpSource("Best restaurants", "https://tasteatlas.com/copycat", text="d" * 300, cluster_score=8)),
            ("discord.py changelog", OpenSerpSource("discord.py Release", "https://github.com/Rapptz/discord.py/releases", text="c" * 300)),
        ]

        selected = select_reliable_sources(sources, desired_sources=3)

        self.assertEqual(
            [item.url for item in selected],
            ["https://discordpy.readthedocs.io/change_log.html", "https://github.com/Rapptz/discord.py/releases"],
        )

    def test_selection_does_not_apply_product_specific_hostname_classifier(self):
        sources = [
            ("discord.py changelog", OpenSerpSource("Discord.py fork changelog", "https://discordpy-self.readthedocs.io/changelog", text="e" * 300)),
        ]

        selected = select_reliable_sources(sources, desired_sources=3)

        self.assertEqual(len(selected), 1)

    def test_reviews_profile_prioritizes_community_over_official_source(self):
        sources = [
            ("headphones review", OpenSerpSource("Official headphones", "https://brand.test/product", text="a" * 300)),
            ("headphones review", OpenSerpSource("Headphones owner review", "https://forum.test/thread", text="b" * 300, source_hint="social_forum", source_category="forum")),
        ]

        selected = select_reliable_sources(sources, desired_sources=3, source_profile="reviews")

        self.assertEqual(selected[0].url, "https://forum.test/thread")

    def test_official_profile_prioritizes_government_source(self):
        sources = [
            ("typhoon warning", OpenSerpSource("Community report", "https://forum.test/post", text="a" * 300, source_hint="social_forum", source_category="forum")),
            ("typhoon warning", OpenSerpSource("Official warning", "https://weather.gov.test/warning", text="b" * 300, source_category="gov")),
        ]

        selected = select_reliable_sources(sources, desired_sources=3, source_profile="official")

        self.assertEqual(selected[0].url, "https://weather.gov.test/warning")

    def test_official_profile_excludes_lower_trust_sources_when_authority_exists(self):
        sources = [
            (
                "台北 天氣 降雨機率",
                OpenSerpSource(
                    "臺北市縣市預報",
                    "https://weather.gov.test/taipei",
                    snippet="臺北市今日天氣與降雨機率",
                    text="official " * 100,
                    source_category="gov",
                ),
            ),
            (
                "台北 天氣 降雨機率",
                OpenSerpSource(
                    "臺北市一週預報",
                    "https://weather.gov.test/taipei/week",
                    snippet="臺北市一週天氣與降雨機率",
                    text="official week " * 100,
                    source_category="gov",
                ),
            ),
            (
                "台北 天氣 降雨機率",
                OpenSerpSource(
                    "報天氣社群貼文",
                    "https://social.test/weather",
                    snippet="台北今日天氣與降雨機率",
                    text="social " * 100,
                    source_category="social_media",
                ),
            ),
        ]

        selected = select_reliable_sources(sources, desired_sources=3, source_profile="official")

        self.assertEqual([source.url for source in selected], ["https://weather.gov.test/taipei"])

    def test_chinese_relevance_rejects_location_only_encyclopedia(self):
        query = "台北2026-07-20 天氣 降雨機率"
        sources = [
            (
                query,
                OpenSerpSource(
                    "臺北市縣市預報",
                    "https://weather.gov.test/taipei",
                    snippet="臺北市今日天氣，多雲且有降雨機率",
                    text="forecast " * 100,
                    source_category="gov",
                ),
            ),
            (
                query,
                OpenSerpSource(
                    "臺北市",
                    "https://zh.wikipedia.org/wiki/taipei",
                    snippet="臺北市是位於臺灣北部的城市",
                    text="encyclopedia " * 100,
                    source_hint="encyclopedia",
                ),
            ),
        ]

        selected = select_reliable_sources(sources, desired_sources=3)

        self.assertEqual([source.url for source in selected], ["https://weather.gov.test/taipei"])

    def test_time_range_normalizes_relative_and_iso_date_values_for_openserp(self):
        today = date(2026, 7, 20)

        self.assertEqual(normalize_openserp_time_range("today", today=today), "20260720..20260720")
        self.assertEqual(normalize_openserp_time_range("2026-07-01..2026-07-20", today=today), "20260701..20260720")
        self.assertEqual(normalize_openserp_time_range("2026-07-20", today=today), "20260720..20260720")
        self.assertEqual(normalize_openserp_time_range("invalid", today=today), "")

    def test_technical_profile_infers_documentation_when_openserp_hint_is_empty(self):
        sources = [
            ("python threading documentation", OpenSerpSource("Python discussion", "https://forum.test/python", text="a" * 300, source_category="forum")),
            ("python threading documentation", OpenSerpSource("Python threading documentation", "https://docs.python.org/3/howto/free-threading-python.html", text="b" * 300)),
        ]

        selected = select_reliable_sources(sources, desired_sources=3, source_profile="technical")

        self.assertEqual(selected[0].url, "https://docs.python.org/3/howto/free-threading-python.html")

    def test_selection_diversifies_domains_before_repeated_pages(self):
        sources = [
            ("python release", OpenSerpSource("Python one", "https://python.test/one", text="a" * 300, cluster_score=3)),
            ("python release", OpenSerpSource("Python two", "https://python.test/two", text="b" * 300, cluster_score=2)),
            ("python release", OpenSerpSource("Independent Python", "https://independent.test/python", text="c" * 300, cluster_score=1)),
        ]

        selected = select_reliable_sources(sources, desired_sources=3)

        self.assertEqual(
            [source.url for source in selected],
            ["https://python.test/one", "https://independent.test/python", "https://python.test/two"],
        )


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

    async def test_planner_accepts_single_authoritative_source_for_official_profile(self):
        response = OpenSerpResponse(sources=(
            OpenSerpSource(
                "Official warning",
                "https://weather.gov.test/warning",
                text="official warning " * 100,
                source_category="gov",
            ),
        ))
        planner = SearchPlanner(timeout_ms=1000, client=_Client({"warning": response}))

        results = await planner.search_many(["warning"], options=SearchOptions(source_profile="official"))

        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].text)
        self.assertFalse(results[0].error)

    async def test_planner_requires_two_distinct_domains_for_cross_checking(self):
        response = OpenSerpResponse(sources=(
            OpenSerpSource("One", "https://same.test/one", text="one " * 100),
            OpenSerpSource("Two", "https://same.test/two", text="two " * 100),
        ))
        planner = SearchPlanner(timeout_ms=1000, client=_Client({"q": response}))

        results = await planner.search_many(["q"])

        self.assertEqual(len(results), 1)
        self.assertFalse(results[0].text)
        self.assertIn("可靠來源不足", results[0].error)

    async def test_planner_logs_only_safe_result_diagnostics(self):
        response = OpenSerpResponse(sources=(
            OpenSerpSource(
                "One",
                "https://same.test/one",
                snippet="private query source",
                text="private result text " * 100,
                engines=("bing",),
            ),
            OpenSerpSource(
                "Two",
                "https://same.test/two",
                snippet="private query source",
                text="another private result " * 100,
                engines=("duckduckgo",),
            ),
        ))
        planner = SearchPlanner(timeout_ms=1000, client=_Client({"private query": response}))

        with self.assertLogs("utils.openserp_search", level="INFO") as captured:
            await planner.search_many(["private query"])

        log_output = "\n".join(captured.output)
        self.assertIn("selected_chars=", log_output)
        self.assertIn("distinct_domains=1", log_output)
        self.assertIn("returned_readable=0", log_output)
        self.assertNotIn("private query", log_output)
        self.assertNotIn("private result text", log_output)

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
