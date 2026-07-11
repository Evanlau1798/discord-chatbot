from __future__ import annotations

import asyncio
import os

from utils.browser_actions import (
    build_url_target as _build_url_target,
    dedupe_targets as _dedupe_targets,
    normalize_search_queries as _normalize_search_queries,
    normalize_search_query as _normalize_search_query,
    normalize_url as _normalize_url,
)
from utils.browser_challenge_detector import (
    ANTI_BOT_ERROR,
    EMPTY_PAGE_ERROR,
    UNRELIABLE_PAGE_ERROR,
    ReliablePageContent,
    build_reliable_content as _build_reliable_content,
    detect_captcha_challenge,
    normalize_text as _normalize_text,
)
from utils.browser_result_types import BrowserFetchResult, BrowserToolError
from utils.browser_text_quality import (
    SPARSE_PAGE_ERROR,
    is_useful_final_text,
    is_useful_http_text,
    prepare_browser_text,
)
from utils.http_page_fetcher import HttpPageText
from utils.message_media import sanitize_image_urls

MAX_BROWSER_TEXT_CHARS = 6000
DEFAULT_BROWSER_TIMEOUT_MS = 30000
DEFAULT_BROWSER_WAIT_UNTIL = "domcontentloaded"
DEFAULT_NETWORK_IDLE_TIMEOUT_MS = 5000
PATCHRIGHT_CHANNEL_ENV = "PATCHRIGHT_BROWSER_CHANNEL"
PATCHRIGHT_HEADLESS_ENV = "PATCHRIGHT_HEADLESS"
DEFAULT_CONTEXT_OPTIONS = {
    "ignore_https_errors": True,
    "locale": "zh-TW",
    "timezone_id": "Asia/Taipei",
    "viewport": {"width": 1365, "height": 768},
    "extra_http_headers": {
        "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    },
}


class PatchrightBrowserClient:
    def __init__(self, timeout_ms: int = DEFAULT_BROWSER_TIMEOUT_MS):
        self.timeout_ms = timeout_ms
        self.headless = _parse_bool_env(PATCHRIGHT_HEADLESS_ENV, default=True)
        self.browser_channel = os.getenv(PATCHRIGHT_CHANNEL_ENV, "").strip() or None

    async def fetch_many(self, urls: list[str], include_images: bool = False) -> list[BrowserFetchResult]:
        targets = [_build_url_target(url) for url in urls if str(url or "").strip()]
        return await self.fetch_targets(targets, include_images=include_images)

    async def fetch_targets(self, targets, include_images: bool = False) -> list[BrowserFetchResult]:
        if not targets:
            return []
        try:
            from patchright.async_api import Error as PatchrightError
            from patchright.async_api import TimeoutError as PatchrightTimeoutError
            from patchright.async_api import async_playwright
        except ImportError as exc:
            raise BrowserToolError("缺少 patchright，請安裝套件並執行 patchright install chromium。") from exc

        async with async_playwright() as patchright:
            browser = await patchright.chromium.launch(**self._launch_options())
            try:
                context = await browser.new_context(**DEFAULT_CONTEXT_OPTIONS)
                try:
                    results = []
                    for target in targets:
                        results.append(await self._fetch_single(
                            context,
                            target,
                            PatchrightError,
                            PatchrightTimeoutError,
                            include_images,
                        ))
                    return results
                finally:
                    await context.close()
            finally:
                await browser.close()

    def _launch_options(self) -> dict:
        options = {"headless": self.headless}
        if self.browser_channel:
            options["channel"] = self.browser_channel
        return options

    async def _fetch_single(
        self,
        context,
        target: dict[str, str],
        browser_error,
        browser_timeout_error,
        include_images: bool,
    ) -> BrowserFetchResult:
        url = target["url"]
        page = await context.new_page()
        try:
            try:
                await page.goto(url, wait_until=DEFAULT_BROWSER_WAIT_UNTIL, timeout=self.timeout_ms)
                await page.wait_for_load_state("networkidle", timeout=DEFAULT_NETWORK_IDLE_TIMEOUT_MS)
            except browser_timeout_error:
                return await self._build_partial_result(page, target, "頁面載入逾時", include_images)
            except browser_error as exc:
                return _build_error_result(target, str(exc)[:500])
            except Exception as exc:
                return _build_error_result(target, f"{type(exc).__name__}: {exc}"[:500])
            result = await self._build_partial_result(page, target, "", include_images)
            return result
        finally:
            await page.close()

    async def _build_partial_result(self, page, target: dict[str, str], error: str, include_images: bool) -> BrowserFetchResult:
        title = await _safe_page_call(page.title)
        text = await _extract_page_text(page)
        image_urls = await _extract_page_image_urls(page) if include_images else []
        has_captcha_challenge = await detect_captcha_challenge(page)
        content = _build_reliable_content(title, text, has_captcha_challenge=has_captcha_challenge)
        final_error = error or content.error
        prepared_text = prepare_browser_text(content.text)
        clipped_text, total_chars, next_start_char = _clip_browser_text(prepared_text)
        if content.text and not is_useful_final_text(prepared_text):
            final_error = final_error or SPARSE_PAGE_ERROR
        return BrowserFetchResult(
            requested_url=target["url"],
            source_type=target["source_type"],
            query=target["query"],
            final_url=str(getattr(page, "url", "") or ""),
            title=title,
            text=clipped_text,
            error=final_error,
            image_urls=tuple(image_urls),
            content_format="html",
            total_chars=total_chars,
            next_start_char=next_start_char,
            diagnostics=("browser_rendered",),
        )


HeadlessBrowserClient = PatchrightBrowserClient


def _build_error_result(target: dict[str, str], error: str) -> BrowserFetchResult:
    return BrowserFetchResult(
        requested_url=target.get("url", ""),
        source_type=target.get("source_type", "url"),
        query=target.get("query", ""),
        error=error,
    )


def _build_http_fallback_result(target: dict[str, str], page: HttpPageText, include_images: bool = False) -> BrowserFetchResult:
    image_urls = tuple(page.image_urls) if include_images else ()
    if page.error and image_urls:
        return BrowserFetchResult(
            requested_url=target.get("url", ""),
            source_type=target.get("source_type", "url"),
            query=target.get("query", ""),
            final_url=page.final_url,
            title=page.title,
            image_urls=image_urls,
            content_format="image",
            media_notes=("Direct image URL",),
        )
    if page.error:
        return _build_error_result(target, page.error)
    content = _build_reliable_content(page.title, page.text)
    if image_urls and not content.text:
        return BrowserFetchResult(
            requested_url=target.get("url", ""),
            source_type=target.get("source_type", "url"),
            query=target.get("query", ""),
            final_url=page.final_url,
            title=page.title,
            image_urls=image_urls,
            content_format="html",
            media_notes=("Image-only page result",),
        )
    error = content.error
    prepared_text = prepare_browser_text(content.text)
    clipped_text, total_chars, next_start_char = _clip_browser_text(prepared_text)
    if content.text and not image_urls and not is_useful_http_text(prepared_text):
        error = SPARSE_PAGE_ERROR
    return BrowserFetchResult(
        requested_url=target.get("url", ""),
        source_type=target.get("source_type", "url"),
        query=target.get("query", ""),
        final_url=page.final_url,
        title=page.title,
        text=clipped_text,
        error=error,
        image_urls=image_urls,
        content_format="html",
        total_chars=total_chars,
        next_start_char=next_start_char,
        diagnostics=("http_first",),
    )


async def _extract_page_text(page) -> str:
    text = await _safe_page_call(lambda: page.locator("body").inner_text(timeout=3000))
    if text:
        return text
    return await _safe_page_call(page.content)


async def _extract_page_image_urls(page) -> list[str]:
    script = """
    () => {
      const urls = [];
      const add = value => {
        if (!value) return;
        try { urls.push(new URL(value, document.baseURI).href); } catch (_) {}
      };
      document.querySelectorAll('meta[property="og:image"], meta[property="og:image:url"], meta[name="twitter:image"], meta[name="twitter:image:src"]')
        .forEach(node => add(node.getAttribute('content')));
      document.querySelectorAll('img').forEach(node => {
        add(node.currentSrc || node.src || node.getAttribute('src'));
        const srcset = node.getAttribute('srcset') || '';
        srcset.split(',').forEach(item => add(item.trim().split(/\\s+/, 1)[0]));
      });
      document.querySelectorAll('source[srcset]').forEach(node => {
        node.getAttribute('srcset').split(',').forEach(item => add(item.trim().split(/\\s+/, 1)[0]));
      });
      return urls;
    }
    """
    try:
        urls = page.evaluate(script)
        if asyncio.iscoroutine(urls):
            urls = await urls
    except Exception:
        return []
    return sanitize_image_urls(urls if isinstance(urls, list) else [], limit=10)


async def _safe_page_call(callable_object) -> str:
    try:
        value = callable_object()
        if asyncio.iscoroutine(value):
            value = await value
        return str(value or "")
    except Exception:
        return ""


def _parse_bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _clip_browser_text(text: str) -> tuple[str, int, int | None]:
    total_chars = len(text or "")
    next_start_char = MAX_BROWSER_TEXT_CHARS if total_chars > MAX_BROWSER_TEXT_CHARS else None
    return (text or "")[:MAX_BROWSER_TEXT_CHARS], total_chars, next_start_char
