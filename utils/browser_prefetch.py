from __future__ import annotations

import logging
import re
from collections.abc import Iterable

from utils.ai_chat_browser import fetch_browser_results
from utils.browser_actions import BrowserToolError, normalize_url
from utils.browser_result_types import BrowserFetchResult
from utils.message_media import sanitize_image_urls

MAX_PREFETCH_URLS = 3
URL_PATTERN = re.compile(r"https?://[^\s<>\")\]]+", re.IGNORECASE)
URL_PUNCTUATION = ".,;:!?)]}'\""

logger = logging.getLogger("discord.extensions.AIChat")


def extract_prefetch_web_urls(
    text: str,
    *,
    excluded_urls: Iterable[str] = (),
    limit: int = MAX_PREFETCH_URLS,
) -> list[str]:
    if limit <= 0:
        return []
    excluded = _normalized_url_set(excluded_urls)
    urls = []
    for match in URL_PATTERN.findall(str(text or "")):
        try:
            normalized = normalize_url(match.rstrip(URL_PUNCTUATION))
        except BrowserToolError:
            continue
        if normalized in excluded or _is_direct_image_url(normalized):
            continue
        if normalized not in urls:
            urls.append(normalized)
        if len(urls) >= limit:
            break
    return urls


async def prefetch_explicit_web_urls(
    browser_client,
    dialogue: str,
    *,
    excluded_urls: Iterable[str] = (),
) -> list[BrowserFetchResult]:
    if browser_client is None:
        return []
    urls = extract_prefetch_web_urls(dialogue, excluded_urls=excluded_urls)
    if not urls:
        return []
    return await fetch_browser_results(browser_client, urls, [], [], logger, include_images=False)


def _normalized_url_set(urls: Iterable[str]) -> set[str]:
    normalized_urls = set()
    for url in urls or ():
        text = str(url or "").strip()
        if not text:
            continue
        normalized_urls.add(text)
        try:
            normalized_urls.add(normalize_url(text))
        except BrowserToolError:
            continue
    return normalized_urls


def _is_direct_image_url(url: str) -> bool:
    return bool(sanitize_image_urls([url], limit=1, require_image_hint=True))
