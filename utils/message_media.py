from __future__ import annotations

import asyncio
import mimetypes
import re
from dataclasses import dataclass
from ipaddress import ip_address
from urllib.parse import urljoin, urlparse

import requests

MAX_IMAGE_URLS = 5
MAX_IMAGE_URL_LENGTH = 2000
MAX_ATTACHMENT_IMAGE_BYTES = 8 * 1024 * 1024
MAX_ATTACHMENT_VIDEO_BYTES = 50 * 1024 * 1024
MAX_MEDIA_PAGE_BYTES = 512 * 1024
IMAGE_URL_PATTERN = re.compile(r"https?://[^\s<>\"]+", re.IGNORECASE)
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif")
VIDEO_EXTENSIONS = (".mp4", ".mov", ".webm", ".m4v")
IMAGE_PUNCTUATION = ".,;:!?)]}'\""
SUPPORTED_MEDIA_PAGE_HOSTS = {"tenor.com", "www.tenor.com", "giphy.com", "www.giphy.com"}
META_TAG_PATTERN = re.compile(r"<meta\b[^>]*>", re.IGNORECASE)
HTML_ATTR_PATTERN = re.compile(r"([a-zA-Z_:.-]+)\s*=\s*([\"'])(.*?)\2", re.DOTALL)
JSON_IMAGE_URL_PATTERN = re.compile(r'"(?:contentUrl|thumbnailUrl)"\s*:\s*"([^"]+)"')


@dataclass(frozen=True)
class MessageMedia:
    image_urls: list[str]
    content_parts: list[dict]


def collect_message_image_urls(message, dialogue: str, limit: int = MAX_IMAGE_URLS) -> list[str]:
    attachment_urls = []
    for attachment in getattr(message, "attachments", []) or []:
        content_type = str(getattr(attachment, "content_type", "") or "").lower()
        url = str(getattr(attachment, "url", "") or "").strip()
        filename = str(getattr(attachment, "filename", "") or "").lower()
        if content_type.startswith("image/") or _has_image_extension(filename) or _has_image_extension(url):
            attachment_urls.append(url)
    urls = sanitize_image_urls(attachment_urls, limit=limit)
    remaining = max(0, limit - len(urls))
    for url in _collect_embed_image_urls(message, limit=remaining):
        if url not in urls:
            urls.append(url)
    remaining = max(0, limit - len(urls))
    for url in sanitize_image_urls(_extract_image_urls_from_text(dialogue), limit=remaining, require_image_hint=True):
        if url not in urls:
            urls.append(url)
    return urls


def collect_message_source_urls(message, dialogue: str, limit: int = MAX_IMAGE_URLS) -> list[str]:
    urls = collect_message_image_urls(message, dialogue, limit=limit)
    remaining = max(0, limit - len(urls))
    for url in _extract_supported_media_page_urls(dialogue, limit=remaining):
        if url not in urls:
            urls.append(url)
    return urls


async def collect_message_media(message, dialogue: str, limit: int = MAX_IMAGE_URLS) -> MessageMedia:
    image_urls = []
    content_parts = []
    for attachment in _iter_image_attachments(message):
        if len(content_parts) >= limit:
            break
        url = sanitize_image_urls([getattr(attachment, "url", "")], limit=1)
        if url and url[0] not in image_urls:
            image_urls.append(url[0])
        image_bytes = await _read_attachment_image_bytes(attachment)
        if image_bytes:
            content_parts.append({
                "type": "image_bytes",
                "image_bytes": {
                    "data": image_bytes,
                    "mime_type": _attachment_mime_type(attachment),
                },
            })
        elif url:
            content_parts.append(_image_url_part(url[0]))
    for attachment in _iter_video_attachments(message):
        if len(content_parts) >= limit:
            break
        video_bytes = await _read_attachment_video_bytes(attachment)
        if video_bytes:
            content_parts.append({
                "type": "video_bytes",
                "video_bytes": {
                    "data": video_bytes,
                    "mime_type": _attachment_mime_type(attachment),
                    "filename": str(getattr(attachment, "filename", "") or ""),
                },
            })
    remaining = max(0, limit - len(content_parts))
    for url in _collect_embed_image_urls(message, limit=remaining):
        if url in image_urls:
            continue
        image_urls.append(url)
        content_parts.append(_image_url_part(url))
    remaining = max(0, limit - len(content_parts))
    for url in sanitize_image_urls(_extract_image_urls_from_text(dialogue), limit=remaining, require_image_hint=True):
        if url in image_urls:
            continue
        image_urls.append(url)
        content_parts.append(_image_url_part(url))
    remaining = max(0, limit - len(content_parts))
    resolved_urls = await _resolve_media_page_urls(_extract_supported_media_page_urls(dialogue, limit=remaining), remaining)
    for url in resolved_urls:
        if url in image_urls:
            continue
        image_urls.append(url)
        content_parts.append(_image_url_part(url))
    return MessageMedia(image_urls=image_urls, content_parts=content_parts)


def build_multimodal_content(
    text: str,
    image_urls: list[str] | tuple[str, ...] = (),
    image_parts: list[dict] | tuple[dict, ...] = (),
) -> str | list[dict]:
    normalized_text = str(text or "")
    sanitized_urls = sanitize_image_urls(image_urls, limit=MAX_IMAGE_URLS, require_image_hint=False)
    media_parts = list(image_parts or [])
    if not sanitized_urls and not media_parts:
        return normalized_text
    return [
        {"type": "text", "text": normalized_text},
        *media_parts,
        *(_image_url_part(url) for url in sanitized_urls if not _content_parts_include_url(media_parts, url)),
    ]


def sanitize_image_urls(
    urls,
    *,
    limit: int = MAX_IMAGE_URLS,
    require_image_hint: bool = False,
    base_url: str = "",
) -> list[str]:
    if limit <= 0:
        return []
    sanitized = []
    for url in urls or []:
        normalized = _normalize_image_url(url, base_url=base_url)
        if not normalized or normalized in sanitized:
            continue
        if require_image_hint and not _has_image_extension(normalized):
            continue
        sanitized.append(normalized)
        if len(sanitized) >= limit:
            break
    return sanitized


def _extract_image_urls_from_text(text: str) -> list[str]:
    urls = []
    for match in IMAGE_URL_PATTERN.findall(str(text or "")):
        normalized = match.rstrip(IMAGE_PUNCTUATION)
        if _has_image_extension(normalized):
            urls.append(normalized)
    return urls


def _extract_supported_media_page_urls(text: str, limit: int = MAX_IMAGE_URLS) -> list[str]:
    urls = []
    for match in IMAGE_URL_PATTERN.findall(str(text or "")):
        normalized = match.rstrip(IMAGE_PUNCTUATION)
        if _is_supported_media_page_url(normalized) and normalized not in urls:
            urls.append(normalized)
        if len(urls) >= limit:
            break
    return urls


def _iter_image_attachments(message) -> list:
    attachments = []
    for attachment in getattr(message, "attachments", []) or []:
        content_type = str(getattr(attachment, "content_type", "") or "").lower()
        url = str(getattr(attachment, "url", "") or "").strip()
        filename = str(getattr(attachment, "filename", "") or "").lower()
        if content_type.startswith("image/") or _has_image_extension(filename) or _has_image_extension(url):
            attachments.append(attachment)
    return attachments


def _iter_video_attachments(message) -> list:
    attachments = []
    for attachment in getattr(message, "attachments", []) or []:
        content_type = str(getattr(attachment, "content_type", "") or "").lower()
        url = str(getattr(attachment, "url", "") or "").strip()
        filename = str(getattr(attachment, "filename", "") or "").lower()
        if content_type.startswith("video/") or _has_video_extension(filename) or _has_video_extension(url):
            attachments.append(attachment)
    return attachments


def _collect_embed_image_urls(message, limit: int = MAX_IMAGE_URLS) -> list[str]:
    urls = []
    if limit <= 0:
        return urls
    for embed in getattr(message, "embeds", []) or []:
        for url, require_image_hint in _iter_embed_image_candidates(embed):
            for normalized in sanitize_image_urls([url], limit=1, require_image_hint=require_image_hint):
                if normalized in urls:
                    continue
                urls.append(normalized)
                if len(urls) >= limit:
                    return urls
    return urls


def _iter_embed_image_candidates(embed) -> list[tuple[str, bool]]:
    candidates = []
    for proxy_name in ("image", "thumbnail"):
        proxy = _get_embed_value(embed, proxy_name)
        candidates.extend((url, False) for url in _iter_proxy_urls(proxy))
    video = _get_embed_value(embed, "video")
    candidates.extend((url, True) for url in _iter_proxy_urls(video))
    candidates.append((str(_get_embed_value(embed, "url") or ""), True))
    return candidates


def _iter_proxy_urls(proxy) -> list[str]:
    if proxy is None:
        return []
    return [
        str(value or "").strip()
        for value in (_get_embed_value(proxy, "url"), _get_embed_value(proxy, "proxy_url"))
        if str(value or "").strip()
    ]


def _get_embed_value(value, key: str):
    if isinstance(value, dict):
        return value.get(key)
    return getattr(value, key, None)


async def _resolve_media_page_urls(urls: list[str], limit: int) -> list[str]:
    if limit <= 0:
        return []
    resolved = []
    for url in urls:
        resolved_url = await asyncio.to_thread(_resolve_media_page_image_url, url)
        for normalized in sanitize_image_urls([resolved_url], limit=1, require_image_hint=True):
            if normalized in resolved:
                continue
            resolved.append(normalized)
            if len(resolved) >= limit:
                return resolved
    return resolved


def _resolve_media_page_image_url(url: str) -> str:
    if not _is_supported_media_page_url(url):
        return ""
    try:
        response = requests.get(
            url,
            headers={"User-Agent": "discord-chatbot/1.0"},
            timeout=(5, 10),
        )
        response.raise_for_status()
    except requests.RequestException:
        return ""
    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
    if content_type.startswith("image/"):
        return response.url
    if content_type and content_type not in {"text/html", "application/xhtml+xml"}:
        return ""
    html = response.text[:MAX_MEDIA_PAGE_BYTES]
    for candidate in _extract_html_image_candidates(html, response.url):
        if _has_image_extension(candidate):
            return candidate
    return ""


def _extract_html_image_candidates(html: str, base_url: str) -> list[str]:
    candidates = []
    for tag in META_TAG_PATTERN.findall(html or ""):
        attrs = {key.lower(): value.strip() for key, _, value in HTML_ATTR_PATTERN.findall(tag)}
        name = (attrs.get("property") or attrs.get("name") or "").lower()
        if name in {"og:image", "og:image:url", "twitter:image", "twitter:image:src"}:
            url = urljoin(base_url, attrs.get("content", ""))
            if url and url not in candidates:
                candidates.append(url)
    for matched in JSON_IMAGE_URL_PATTERN.findall(html or ""):
        url = urljoin(base_url, matched.encode("utf-8").decode("unicode_escape"))
        if url and url not in candidates:
            candidates.append(url)
    return candidates


async def _read_attachment_image_bytes(attachment) -> bytes:
    size = int(getattr(attachment, "size", 0) or 0)
    if size > MAX_ATTACHMENT_IMAGE_BYTES:
        return b""
    read = getattr(attachment, "read", None)
    if not callable(read):
        return b""
    try:
        data = await read(use_cached=True)
    except TypeError:
        data = await read()
    except Exception:
        return b""
    if not isinstance(data, (bytes, bytearray)) or not data:
        return b""
    if len(data) > MAX_ATTACHMENT_IMAGE_BYTES:
        return b""
    return bytes(data)


async def _read_attachment_video_bytes(attachment) -> bytes:
    size = int(getattr(attachment, "size", 0) or 0)
    if size > MAX_ATTACHMENT_VIDEO_BYTES:
        return b""
    read = getattr(attachment, "read", None)
    if not callable(read):
        return b""
    try:
        data = await read(use_cached=True)
    except TypeError:
        data = await read()
    except Exception:
        return b""
    if not isinstance(data, (bytes, bytearray)) or not data:
        return b""
    if len(data) > MAX_ATTACHMENT_VIDEO_BYTES:
        return b""
    return bytes(data)


def _attachment_mime_type(attachment) -> str:
    content_type = str(getattr(attachment, "content_type", "") or "").split(";", 1)[0].strip().lower()
    if content_type.startswith("image/"):
        return content_type
    guessed = (
        mimetypes.guess_type(str(getattr(attachment, "filename", "") or ""))[0]
        or mimetypes.guess_type(str(getattr(attachment, "url", "") or ""))[0]
    )
    return guessed or "application/octet-stream"


def _image_url_part(url: str) -> dict:
    return {"type": "image_url", "image_url": {"url": url}}


def _content_parts_include_url(parts: list[dict], url: str) -> bool:
    return any(part.get("type") == "image_url" and part.get("image_url", {}).get("url") == url for part in parts)


def _normalize_image_url(url, *, base_url: str = "") -> str:
    normalized = str(url or "").strip()
    if not normalized or len(normalized) > MAX_IMAGE_URL_LENGTH:
        return ""
    if base_url:
        normalized = urljoin(base_url, normalized)
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    if parsed.username or parsed.password:
        return ""
    if _is_private_or_local_host(parsed.hostname or ""):
        return ""
    return normalized


def _has_image_extension(value: str) -> bool:
    parsed = urlparse(str(value or "").strip().lower())
    return parsed.path.endswith(IMAGE_EXTENSIONS)


def _has_video_extension(value: str) -> bool:
    parsed = urlparse(str(value or "").strip().lower())
    return parsed.path.endswith(VIDEO_EXTENSIONS)


def _is_supported_media_page_url(value: str) -> bool:
    parsed = urlparse(str(value or "").strip().lower())
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in SUPPORTED_MEDIA_PAGE_HOSTS:
        return False
    return not _has_image_extension(value)


def _is_private_or_local_host(hostname: str) -> bool:
    normalized = str(hostname or "").strip().lower().strip("[]")
    if not normalized:
        return True
    if normalized in {"localhost", "localhost.localdomain"} or normalized.endswith(".localhost"):
        return True
    try:
        address = ip_address(normalized)
    except ValueError:
        return False
    return any((
        address.is_loopback,
        address.is_private,
        address.is_link_local,
        address.is_reserved,
        address.is_multicast,
        address.is_unspecified,
    ))
