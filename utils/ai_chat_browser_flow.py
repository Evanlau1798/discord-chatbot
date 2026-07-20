from __future__ import annotations

import asyncio
import logging

from utils.ai_chat_browser import fetch_browser_results, new_fallback_queries, search_fallback_allowed
from utils.browser_result_payload import build_browser_followup_content
from utils.json_response_protocol import build_search_failure_response

logger = logging.getLogger("discord.extensions.AIChat")


class AiChatBrowserFlowMixin:
    async def _complete_after_browser(
        self,
        request_messages: list[dict],
        raw_response: str,
        urls: list[str],
        search_queries: list[str],
        youtube_search_queries: list[str],
        find_requests: list,
        include_images: bool,
        search_options,
        message,
        persona_key: str | None,
        request_status=None,
    ):
        browser_notice = await self._send_browser_notice(
            message, urls, search_queries, youtube_search_queries, find_requests
        )
        notice_sent_at = asyncio.get_running_loop().time() if browser_notice is not None else None
        browser_results = await self._fetch_browser_round(
            urls, search_queries, youtube_search_queries, find_requests, include_images, search_options
        )
        await self._set_browser_reading_notice(browser_notice, notice_sent_at)
        retry_allowed = search_fallback_allowed(browser_results, search_queries)
        followup_messages = request_messages + [
            {"role": "assistant", "content": raw_response},
            {"role": "user", "content": build_browser_followup_content(browser_results, allow_search_retry=retry_allowed)},
        ]
        parsed, fallback_raw = await self._complete_and_parse_with_raw(
            followup_messages, message, persona_key, request_status
        )
        if parsed.browser is None:
            return parsed, browser_notice
        fallback_queries = self._valid_fallback_queries(parsed.browser, search_queries) if retry_allowed else []
        if not fallback_queries:
            return build_search_failure_response(), browser_notice
        fallback_results = await self._fetch_browser_round(
            [], fallback_queries, [], [], False, parsed.browser.search_options
        )
        final_messages = followup_messages + [
            {"role": "assistant", "content": fallback_raw},
            {"role": "user", "content": build_browser_followup_content(fallback_results, allow_search_retry=False)},
        ]
        final, _ = await self._complete_and_parse_with_raw(final_messages, message, persona_key, request_status)
        return (build_search_failure_response() if final.browser is not None else final), browser_notice

    async def _fetch_browser_round(
        self, urls, search_queries, youtube_search_queries, find_requests, include_images, search_options
    ):
        return await fetch_browser_results(
            self.browser_client,
            urls,
            search_queries,
            find_requests,
            logger,
            include_images,
            youtube_search_queries=youtube_search_queries,
            search_options=search_options,
        )

    @staticmethod
    def _valid_fallback_queries(browser, attempted_queries: list[str]) -> list[str]:
        if browser.urls or browser.youtube_search_queries or browser.find_requests:
            return []
        return new_fallback_queries(browser.search_queries, attempted_queries)
