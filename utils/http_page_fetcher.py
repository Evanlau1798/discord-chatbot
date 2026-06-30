from __future__ import annotations

from dataclasses import dataclass

import requests

from utils.html_text_extractor import extract_html_text


@dataclass(frozen=True)
class HttpPageText:
    final_url: str
    title: str
    text: str
    error: str = ""
    image_urls: tuple[str, ...] = ()


HTTP_MAX_BYTES = 2 * 1024 * 1024
HTML_CONTENT_TYPES = ("application/xhtml+xml", "text/html", "text/plain")
HTTP_BROWSER_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "Cache-Control": "no-cache",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0 Safari/537.36"
    ),
}


def fetch_http_page_text(url: str, timeout_ms: int) -> HttpPageText:
    try:
        response = requests.get(
            url,
            headers=HTTP_BROWSER_HEADERS,
            timeout=max(1.0, float(timeout_ms) / 1000),
            allow_redirects=True,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        return HttpPageText(final_url=url, title="", text="", error=f"HTTP fallback failed: {type(exc).__name__}")

    content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type.startswith("image/"):
        return HttpPageText(final_url=response.url, title="", text="", error=f"Unsupported content type: {content_type}", image_urls=(response.url,))
    if content_type and content_type not in HTML_CONTENT_TYPES:
        return HttpPageText(final_url=response.url, title="", text="", error=f"Unsupported content type: {content_type}")

    html = response.content[:HTTP_MAX_BYTES].decode(response.encoding or response.apparent_encoding or "utf-8", errors="replace")
    extracted = extract_html_text(html, base_url=response.url)
    return HttpPageText(final_url=response.url, title=extracted.title, text=extracted.text, image_urls=tuple(extracted.image_urls))
