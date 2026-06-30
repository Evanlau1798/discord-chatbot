from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from typing import Callable
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse
from xml.etree import ElementTree

import requests

from utils.browser_client import MAX_BROWSER_TEXT_CHARS
from utils.browser_result_types import BrowserFetchResult
from utils.http_page_fetcher import HTTP_BROWSER_HEADERS
from utils.message_media import sanitize_image_urls

PREFERRED_LANGUAGES = ("zh-TW", "zh-Hant", "zh", "en")
WATCH_URL = "https://www.youtube.com/watch?v={video_id}"
YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}
VIDEO_PATH_PATTERN = re.compile(r"^/(shorts|embed|live|v)/([^/?#]+)")


@dataclass(frozen=True)
class FetchedText:
    final_url: str
    text: str
    error: str = ""


@dataclass(frozen=True)
class TranscriptSegment:
    start: float
    end: float
    text: str


TextFetcher = Callable[[str, int], FetchedText]


def parse_youtube_video_id(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = urlparse(text if "://" in text else f"https://{text}")
    except ValueError:
        return ""
    host = (parsed.hostname or "").lower()
    if host not in YOUTUBE_HOSTS:
        return ""
    if host == "youtu.be":
        return parsed.path.strip("/").split("/", 1)[0]
    query_id = parse_qs(parsed.query).get("v", [""])[0].strip()
    if query_id:
        return query_id
    matched = VIDEO_PATH_PATTERN.match(parsed.path or "")
    return matched.group(2).strip() if matched else ""


def timedtext_url_matches_video(url: str, video_id: str) -> bool:
    try:
        parsed = urlparse(str(url or ""))
    except ValueError:
        return False
    return parse_qs(parsed.query).get("v", [""])[0] == video_id


def select_caption_track(tracks: list[dict]) -> dict | None:
    normalized = [track for track in tracks if isinstance(track, dict) and track.get("baseUrl")]
    if not normalized:
        return None
    for language in PREFERRED_LANGUAGES:
        matched = _find_track_by_language(normalized, language, exact=True)
        if matched is not None:
            return matched
    for language in PREFERRED_LANGUAGES:
        matched = _find_track_by_language(normalized, language, exact=False)
        if matched is not None:
            return matched
    for track in normalized:
        if track.get("kind") != "asr":
            return track
    return normalized[0]


def parse_json3_transcript(text: str) -> list[TranscriptSegment]:
    try:
        payload = json.loads(text or "")
    except ValueError:
        return []
    events = payload.get("events")
    if not isinstance(events, list):
        return []
    segments = []
    for event in events:
        if not isinstance(event, dict):
            continue
        line = _json3_event_text(event)
        if not line:
            continue
        start_ms = _float_value(event.get("tStartMs"))
        duration_ms = _float_value(event.get("dDurationMs"))
        segments.append(TranscriptSegment(start=start_ms / 1000, end=(start_ms + duration_ms) / 1000, text=line))
    return segments


def parse_srv3_transcript(xml: str) -> list[TranscriptSegment]:
    try:
        root = ElementTree.fromstring(xml or "")
    except ElementTree.ParseError:
        return []
    segments = []
    for element in [*root.findall(".//p"), *root.findall(".//text")]:
        line = _normalize_caption_text("".join(element.itertext()))
        if not line:
            continue
        if element.tag == "p":
            start = _float_value(element.attrib.get("t")) / 1000
            duration = _float_value(element.attrib.get("d")) / 1000
        else:
            start = _float_value(element.attrib.get("start"))
            duration = _float_value(element.attrib.get("dur"))
        segments.append(TranscriptSegment(start=start, end=start + duration, text=line))
    return segments


def read_youtube_transcript_url(
    url: str,
    timeout_ms: int,
    *,
    include_images: bool = False,
    fetch_text: TextFetcher | None = None,
    fetch_metadata: Callable | None = None,
) -> BrowserFetchResult | None:
    from utils.youtube_ytdlp_reader import read_youtube_transcript_url as read_with_ytdlp

    return read_with_ytdlp(
        url,
        timeout_ms,
        include_images=include_images,
        fetch_text=fetch_text,
        fetch_metadata=fetch_metadata,
    )


def _fetch_text(url: str, timeout_ms: int) -> FetchedText:
    try:
        response = requests.get(url, headers=HTTP_BROWSER_HEADERS, timeout=max(1.0, timeout_ms / 1000))
        response.raise_for_status()
    except requests.RequestException as exc:
        return FetchedText(final_url=url, text="", error=type(exc).__name__)
    return FetchedText(final_url=response.url, text=response.text)


def _caption_tracks(player: dict) -> list[dict]:
    renderer = player.get("captions", {}).get("playerCaptionsTracklistRenderer", {})
    tracks = renderer.get("captionTracks", [])
    return tracks if isinstance(tracks, list) else []


def _fetch_caption_segments(track: dict, video_id: str, timeout_ms: int, fetcher: TextFetcher) -> list[TranscriptSegment]:
    base_url = str(track.get("baseUrl") or "")
    if not timedtext_url_matches_video(base_url, video_id):
        return []
    for caption_url, parser in (
        (_caption_url_with_format(base_url, "json3"), parse_json3_transcript),
        (_caption_url_with_format(base_url, "srv3"), parse_srv3_transcript),
        (base_url, parse_srv3_transcript),
    ):
        fetched = fetcher(caption_url, timeout_ms)
        if fetched.error or not fetched.text:
            continue
        segments = parser(fetched.text)
        if segments:
            return segments
    return []


def build_youtube_transcript_result(
    requested_url: str,
    final_url: str,
    title: str,
    language: str,
    kind: str,
    segments: list[TranscriptSegment],
    image_urls: tuple[str, ...] = (),
) -> BrowserFetchResult:
    return _build_transcript_result(
        requested_url,
        final_url,
        title,
        {"languageCode": language, "kind": kind},
        segments,
        image_urls,
    )


def _build_transcript_result(
    requested_url: str,
    final_url: str,
    title: str,
    track: dict,
    segments: list[TranscriptSegment],
    image_urls: tuple[str, ...],
) -> BrowserFetchResult:
    language = _clean_text(track.get("languageCode"))
    kind = _clean_text(track.get("kind")) or "manual"
    body = "\n".join((
        "YouTube transcript",
        f"Title: {title}",
        f"Language: {language or 'unknown'} ({kind})",
        "",
        _format_segments(segments),
    )).strip()
    text, total_chars, next_start = _truncate_text(body)
    return BrowserFetchResult(
        requested_url=requested_url,
        source_type="url",
        final_url=final_url,
        title=title,
        text=text,
        image_urls=image_urls,
        content_format="youtube_transcript",
        total_chars=total_chars,
        next_start_char=next_start,
        media_notes=(f"YouTube captions language={language or 'unknown'} kind={kind}",),
    )


def _build_metadata_result(
    requested_url: str,
    final_url: str,
    title: str,
    details: dict,
    image_urls: tuple[str, ...],
) -> BrowserFetchResult | None:
    description = _clean_text(details.get("shortDescription"))
    return build_youtube_metadata_result(requested_url, final_url, title, description, image_urls)


def build_youtube_metadata_result(
    requested_url: str,
    final_url: str,
    title: str,
    description: str,
    image_urls: tuple[str, ...] = (),
    *,
    diagnostics: tuple[str, ...] = ("youtube_transcript_unavailable",),
) -> BrowserFetchResult | None:
    clean_title = _clean_text(title)
    clean_description = _clean_text(description)
    if not clean_title and not clean_description:
        return None
    body = "\n".join(
        part
        for part in ("YouTube video metadata", f"Title: {clean_title}" if clean_title else "", clean_description)
        if part
    )
    text, total_chars, next_start = _truncate_text(body)
    return BrowserFetchResult(
        requested_url=requested_url,
        source_type="url",
        final_url=final_url,
        title=clean_title,
        text=text,
        image_urls=image_urls,
        content_format="youtube_metadata",
        total_chars=total_chars,
        next_start_char=next_start,
        diagnostics=diagnostics,
    )


def _format_segments(segments: list[TranscriptSegment]) -> str:
    return "\n".join(f"[{_format_time(segment.start)}] {segment.text}" for segment in segments)


def _format_time(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _extract_json_assignment(source: str, keys: tuple[str, ...]) -> dict | None:
    for key in keys:
        for marker in (f"var {key} = ", f"window[\"{key}\"] = ", f"window.{key} = ", f"{key} = "):
            marker_index = source.find(marker)
            if marker_index < 0:
                continue
            parsed = _parse_json_object_at(source, source.find("{", marker_index + len(marker)))
            if parsed is not None:
                return parsed
    return None


def _parse_json_object_at(source: str, start: int) -> dict | None:
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaping = False
    for index in range(start, len(source)):
        char = source[index]
        if in_string:
            if escaping:
                escaping = False
            elif char == "\\":
                escaping = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    payload = json.loads(source[start:index + 1])
                except ValueError:
                    return None
                return payload if isinstance(payload, dict) else None
    return None


def _caption_url_with_format(url: str, fmt: str) -> str:
    parsed = urlparse(url)
    params = parse_qs(parsed.query, keep_blank_values=True)
    params["fmt"] = [fmt]
    query = urlencode([(key, value) for key, values in params.items() for value in values])
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, query, parsed.fragment))


def _find_track_by_language(tracks: list[dict], language: str, *, exact: bool) -> dict | None:
    wanted = language.lower()
    for track in tracks:
        got = str(track.get("languageCode") or "").lower()
        if got == wanted or (not exact and (got.startswith(wanted + "-") or wanted.startswith(got + "-"))):
            return track
    return None


def _json3_event_text(event: dict) -> str:
    segs = event.get("segs")
    if not isinstance(segs, list):
        return ""
    return _normalize_caption_text("".join(str(seg.get("utf8", "")) for seg in segs if isinstance(seg, dict)))


def _extract_thumbnail_urls(details: dict) -> list[str]:
    thumbnails = details.get("thumbnail", {}).get("thumbnails", [])
    if not isinstance(thumbnails, list):
        return []
    return sanitize_image_urls([item.get("url", "") for item in thumbnails if isinstance(item, dict)], limit=1)


def _normalize_caption_text(value: str) -> str:
    return " ".join(html.unescape(str(value or "")).split())


def _clean_text(value) -> str:
    return " ".join(str(value or "").split())


def _float_value(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _truncate_text(text: str) -> tuple[str, int, int | None]:
    total_chars = len(text)
    next_start = MAX_BROWSER_TEXT_CHARS if total_chars > MAX_BROWSER_TEXT_CHARS else None
    return text[:MAX_BROWSER_TEXT_CHARS], total_chars, next_start
