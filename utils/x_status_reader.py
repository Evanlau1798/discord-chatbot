from __future__ import annotations

import re
from dataclasses import dataclass
from html import unescape
from typing import Callable
from urllib.parse import urlparse

import requests

from utils.browser_client import MAX_BROWSER_TEXT_CHARS
from utils.browser_result_types import BrowserFetchResult
from utils.http_page_fetcher import HTTP_BROWSER_HEADERS
from utils.message_media import sanitize_image_urls

X_HOSTS = {"x.com", "www.x.com", "twitter.com", "www.twitter.com", "mobile.twitter.com"}
STATUS_PATTERN = re.compile(r"/status(?:es)?/(?P<id>\d+)")
META_TAG_PATTERN = re.compile(r"<meta\b[^>]*>", re.IGNORECASE)
ATTR_PATTERN = re.compile(r"([a-zA-Z_:.-]+)\s*=\s*([\"'])(.*?)\2", re.DOTALL)


@dataclass(frozen=True)
class FetchedStatusPage:
    final_url: str
    html: str
    error: str = ""


StatusFetcher = Callable[[str, int], FetchedStatusPage]


def parse_x_status_url(url: str) -> str:
    try:
        parsed = urlparse(str(url or "").strip())
    except ValueError:
        return ""
    if (parsed.hostname or "").lower() not in X_HOSTS:
        return ""
    matched = STATUS_PATTERN.search(parsed.path or "")
    return matched.group("id") if matched else ""


def read_x_status_url(
    url: str,
    timeout_ms: int,
    *,
    fetch_page: StatusFetcher | None = None,
) -> BrowserFetchResult | None:
    status_id = parse_x_status_url(url)
    if not status_id:
        return None
    fetcher = fetch_page or _fetch_page
    page = fetcher(url, timeout_ms)
    if page.error or not page.html:
        return None
    meta = _extract_meta(page.html)
    title = _first_meta(meta, ("og:title", "twitter:title"))
    description = _first_meta(meta, ("og:description", "twitter:description"))
    image_urls = tuple(sanitize_image_urls(_meta_values(meta, ("og:image", "twitter:image", "twitter:image:src")), limit=5))
    video_urls = _meta_values(meta, ("og:video", "og:video:url", "twitter:player:stream"))
    text = _build_status_text(title, description)
    if not text and not image_urls:
        return None
    clipped, total_chars, next_start = _truncate_text(text or "X/Twitter status media")
    return BrowserFetchResult(
        requested_url=url,
        source_type="url",
        final_url=page.final_url or url,
        title=title or "X/Twitter status",
        text=clipped,
        image_urls=image_urls,
        content_format="x_status",
        total_chars=total_chars,
        next_start_char=next_start,
        media_notes=_media_notes(bool(video_urls)),
    )


def _fetch_page(url: str, timeout_ms: int) -> FetchedStatusPage:
    try:
        response = requests.get(url, headers=HTTP_BROWSER_HEADERS, timeout=max(1.0, timeout_ms / 1000))
        response.raise_for_status()
    except requests.RequestException as exc:
        return FetchedStatusPage(final_url=url, html="", error=type(exc).__name__)
    return FetchedStatusPage(final_url=response.url, html=response.text)


def _extract_meta(html: str) -> dict[str, list[str]]:
    meta: dict[str, list[str]] = {}
    for tag in META_TAG_PATTERN.findall(html or ""):
        attrs = {key.lower(): unescape(value.strip()) for key, _, value in ATTR_PATTERN.findall(tag)}
        name = (attrs.get("property") or attrs.get("name") or "").lower()
        content = attrs.get("content", "").strip()
        if not name or not content:
            continue
        meta.setdefault(name, [])
        if content not in meta[name]:
            meta[name].append(content)
    return meta


def _first_meta(meta: dict[str, list[str]], names: tuple[str, ...]) -> str:
    values = _meta_values(meta, names)
    return values[0] if values else ""


def _meta_values(meta: dict[str, list[str]], names: tuple[str, ...]) -> list[str]:
    values = []
    for name in names:
        for value in meta.get(name, []):
            if value and value not in values:
                values.append(value)
    return values


def _build_status_text(title: str, description: str) -> str:
    parts = ["X/Twitter status"]
    if title:
        parts.append(f"Title: {title}")
    if description and description != title:
        parts.append(description)
    return "\n".join(parts).strip()


def _media_notes(has_video: bool) -> tuple[str, ...]:
    if not has_video:
        return ()
    return ("此 X/Twitter 貼文包含影片；目前僅提供文字與圖片/封面資訊，不送完整影片。",)


def _truncate_text(text: str) -> tuple[str, int, int | None]:
    total_chars = len(text)
    next_start = MAX_BROWSER_TEXT_CHARS if total_chars > MAX_BROWSER_TEXT_CHARS else None
    return text[:MAX_BROWSER_TEXT_CHARS], total_chars, next_start
