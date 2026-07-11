from __future__ import annotations

import unittest

from utils.ai_chat_browser import new_fallback_queries, search_fallback_allowed
from utils.ai_chat_browser_flow import AiChatBrowserFlowMixin
from utils.browser_result_types import BrowserFetchResult
from utils.json_response_protocol import BrowserBlock, ParsedAIResponse
from utils.openserp_search import SearchOptions


class _BrowserClient:
    def __init__(self, rounds):
        self.rounds = list(rounds)
        self.calls = []

    async def fetch_urls_and_searches(
        self, urls, search_queries, find_requests, *, include_images, youtube_search_queries, search_options
    ):
        self.calls.append((urls, search_queries, search_options))
        return self.rounds.pop(0)


class _Flow(AiChatBrowserFlowMixin):
    def __init__(self, rounds, model_results):
        self.browser_client = _BrowserClient(rounds)
        self.model_results = list(model_results)

    async def _send_browser_notice(self, *args):
        return None

    async def _set_browser_reading_notice(self, *args):
        return None

    async def _complete_and_parse_with_raw(self, *args):
        return self.model_results.pop(0)


class SearchFallbackPolicyTests(unittest.TestCase):
    def test_allows_fallback_for_insufficient_non_captcha_results(self):
        results = [BrowserFetchResult(requested_url="query", source_type="search", error="可靠來源不足")]

        self.assertTrue(search_fallback_allowed(results, ["original query"]))

    def test_rejects_fallback_after_captcha(self):
        results = [BrowserFetchResult(requested_url="query", source_type="search", error="CAPTCHA challenge")]

        self.assertFalse(search_fallback_allowed(results, ["original query"]))

    def test_rejects_fallback_when_any_readable_result_exists(self):
        results = [BrowserFetchResult(requested_url="query", source_type="search", text="readable")]

        self.assertFalse(search_fallback_allowed(results, ["original query"]))

    def test_fallback_queries_must_be_new_and_are_limited(self):
        queries = new_fallback_queries(
            [" Original   Query ", "specific query", "specific query", "third", "fourth"],
            ["original query"],
        )

        self.assertEqual(queries, ["specific query", "third", "fourth"])


class SearchFallbackFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_flow_performs_only_one_new_search_round(self):
        first = [BrowserFetchResult(requested_url="original", source_type="search", error="insufficient")]
        second = [BrowserFetchResult(
            requested_url="specific", source_type="search", final_url="https://source.test",
            title="Specific result", text="reliable content",
        )]
        fallback_request = ParsedAIResponse(
            reply_text="",
            browser=BrowserBlock([], ["specific query"], [], [], search_options=SearchOptions(source_profile="official")),
        )
        flow = _Flow([first, second], [(fallback_request, "fallback raw"), (ParsedAIResponse("done"), "final raw")])

        parsed, _ = await flow._complete_after_browser(
            [], "initial raw", [], ["original query"], [], [], False, SearchOptions(), object(), None
        )

        self.assertEqual(parsed.reply_text, "done")
        self.assertEqual(len(flow.browser_client.calls), 2)
        self.assertEqual(flow.browser_client.calls[1][1], ["specific query"])
        self.assertEqual(flow.browser_client.calls[1][2].source_profile, "official")

    async def test_flow_rejects_a_third_search_request(self):
        failed = [BrowserFetchResult(requested_url="query", source_type="search", error="insufficient")]
        fallback_request = ParsedAIResponse(
            reply_text="", browser=BrowserBlock([], ["specific query"], [], [])
        )
        another_request = ParsedAIResponse(
            reply_text="", browser=BrowserBlock([], ["third query"], [], [])
        )
        flow = _Flow([failed, failed], [(fallback_request, "fallback raw"), (another_request, "third raw")])

        parsed, _ = await flow._complete_after_browser(
            [], "initial raw", [], ["original query"], [], [], False, SearchOptions(), object(), None
        )

        self.assertIn("補充更完整的名稱", parsed.reply_text)
        self.assertEqual(len(flow.browser_client.calls), 2)


if __name__ == "__main__":
    unittest.main()
