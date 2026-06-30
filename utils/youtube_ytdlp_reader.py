from __future__ import annotations

import html
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Callable, Optional

from utils.browser_result_types import BrowserFetchResult
from utils.message_media import sanitize_image_urls
from utils.youtube_transcript_reader import (
    FetchedText,
    PREFERRED_LANGUAGES,
    TranscriptSegment,
    build_youtube_metadata_result,
    build_youtube_transcript_result,
    parse_json3_transcript,
    parse_srv3_transcript,
    parse_youtube_video_id,
    timedtext_url_matches_video,
    _fetch_text,
)

YT_DLP_BIN_ENV = "YT_DLP_BIN"
DEFAULT_YT_DLP_BIN = "yt-dlp"
WATCH_URL = "https://www.youtube.com/watch?v={video_id}"
PREFERRED_CAPTION_EXTENSIONS = ("json3", "srv3", "vtt", "srt", "ttml")
TIMESTAMP_PATTERN = re.compile(
    r"(?P<start>\d{1,2}:\d{2}(?::\d{2})?[.,]\d{1,3})\s+-->\s+"
    r"(?P<end>\d{1,2}:\d{2}(?::\d{2})?[.,]\d{1,3})"
)
TAG_PATTERN = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class YtdlpMetadataResult:
    data: Optional[dict] = None
    error: str = ""


@dataclass(frozen=True)
class CaptionChoice:
    language: str
    kind: str
    entries: tuple[dict, ...]


MetadataFetcher = Callable[[str, int], YtdlpMetadataResult]


def read_youtube_transcript_url(
    url: str,
    timeout_ms: int,
    *,
    include_images: bool = False,
    fetch_text: Callable[[str, int], FetchedText] | None = None,
    fetch_metadata: MetadataFetcher | None = None,
) -> BrowserFetchResult | None:
    video_id = parse_youtube_video_id(url)
    if not video_id:
        return None
    metadata_fetcher = fetch_metadata or fetch_ytdlp_metadata
    metadata_result = metadata_fetcher(url, timeout_ms)
    if metadata_result.error or not metadata_result.data:
        return _build_ytdlp_unavailable_result(url, video_id, metadata_result.error)

    metadata = metadata_result.data
    title = _clean_text(metadata.get("title")) or "YouTube video"
    final_url = _metadata_final_url(metadata, video_id)
    image_urls = _metadata_image_urls(metadata) if include_images else ()
    for choice in iter_ytdlp_caption_choices(metadata, video_id):
        segments = _fetch_caption_segments(choice.entries, timeout_ms, fetch_text or _fetch_text)
        if segments:
            return build_youtube_transcript_result(
                url,
                final_url,
                title,
                choice.language,
                choice.kind,
                segments,
                image_urls,
            )
    return build_youtube_metadata_result(
        url,
        final_url,
        title,
        _metadata_description(metadata),
        image_urls,
        diagnostics=("youtube_transcript_unavailable", "youtube_ytdlp"),
    )


def fetch_ytdlp_metadata(url: str, timeout_ms: int) -> YtdlpMetadataResult:
    executable = os.getenv(YT_DLP_BIN_ENV, "").strip() or shutil.which(DEFAULT_YT_DLP_BIN) or DEFAULT_YT_DLP_BIN
    timeout_seconds = max(5.0, timeout_ms / 1000)
    try:
        completed = subprocess.run(
            [executable, "-J", "--skip-download", "--no-warnings", "--no-playlist", url],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except FileNotFoundError:
        return YtdlpMetadataResult(error="yt_dlp_missing")
    except subprocess.TimeoutExpired:
        return YtdlpMetadataResult(error="yt_dlp_timeout")
    if completed.returncode != 0:
        return YtdlpMetadataResult(error="yt_dlp_failed")
    try:
        payload = json.loads(completed.stdout or "")
    except ValueError:
        return YtdlpMetadataResult(error="yt_dlp_invalid_json")
    return YtdlpMetadataResult(data=payload if isinstance(payload, dict) else None)


def select_ytdlp_caption_choice(metadata: dict, video_id: str) -> CaptionChoice | None:
    for choice in iter_ytdlp_caption_choices(metadata, video_id):
        return choice
    return None


def iter_ytdlp_caption_choices(metadata: dict, video_id: str) -> list[CaptionChoice]:
    manual = _caption_collection(metadata.get("subtitles"))
    automatic = _caption_collection(metadata.get("automatic_captions"))
    choices = []
    seen = set()
    for language in PREFERRED_LANGUAGES:
        for collection, kind in ((manual, "manual"), (automatic, "asr")):
            matched = _find_language_entries(collection, language, exact=True, video_id=video_id)
            if matched is not None:
                _append_choice(choices, seen, CaptionChoice(matched[0], kind, matched[1]))
    for language in PREFERRED_LANGUAGES:
        for collection, kind in ((manual, "manual"), (automatic, "asr")):
            matched = _find_language_entries(collection, language, exact=False, video_id=video_id)
            if matched is not None:
                _append_choice(choices, seen, CaptionChoice(matched[0], kind, matched[1]))
    for collection, kind in ((manual, "manual"), (automatic, "asr")):
        for language, entries in collection.items():
            valid_entries = _valid_caption_entries(entries, video_id)
            if valid_entries:
                _append_choice(choices, seen, CaptionChoice(language, kind, valid_entries))
    return choices


def _append_choice(choices: list[CaptionChoice], seen: set[tuple[str, str]], choice: CaptionChoice) -> None:
    key = (choice.kind, choice.language.lower())
    if key in seen:
        return
    seen.add(key)
    choices.append(choice)


def _fetch_caption_segments(
    entries: tuple[dict, ...],
    timeout_ms: int,
    fetch_text: Callable[[str, int], FetchedText],
) -> list[TranscriptSegment]:
    for entry in _sort_caption_entries(entries):
        url = str(entry.get("url") or "")
        fetched = fetch_text(url, timeout_ms)
        if fetched.error or not fetched.text:
            continue
        segments = _parse_caption_text(fetched.text, str(entry.get("ext") or ""))
        if segments:
            return segments
    return []


def _parse_caption_text(text: str, extension: str) -> list[TranscriptSegment]:
    normalized = extension.lower()
    if normalized == "json3":
        return parse_json3_transcript(text)
    if normalized in {"srv1", "srv2", "srv3", "ttml"}:
        return parse_srv3_transcript(text)
    if normalized in {"vtt", "srt"}:
        return parse_vtt_or_srt_transcript(text)
    return parse_json3_transcript(text) or parse_srv3_transcript(text) or parse_vtt_or_srt_transcript(text)


def parse_vtt_or_srt_transcript(text: str) -> list[TranscriptSegment]:
    lines = [line.strip() for line in str(text or "").splitlines()]
    segments = []
    index = 0
    while index < len(lines):
        matched = TIMESTAMP_PATTERN.search(lines[index])
        if not matched:
            index += 1
            continue
        start = _parse_timestamp(matched.group("start"))
        end = _parse_timestamp(matched.group("end"))
        index += 1
        payload_lines = []
        while index < len(lines) and lines[index]:
            payload_lines.append(lines[index])
            index += 1
        line = _clean_caption_line(" ".join(payload_lines))
        if line:
            segments.append(TranscriptSegment(start=start, end=max(start, end), text=line))
    return segments


def _caption_collection(value) -> dict:
    return value if isinstance(value, dict) else {}


def _find_language_entries(
    collection: dict,
    language: str,
    *,
    exact: bool,
    video_id: str,
) -> tuple[str, tuple[dict, ...]] | None:
    for got, entries in collection.items():
        if not _language_matches(str(got), language, exact=exact):
            continue
        valid_entries = _valid_caption_entries(entries, video_id)
        if valid_entries:
            return str(got), valid_entries
    return None


def _valid_caption_entries(entries, video_id: str) -> tuple[dict, ...]:
    if not isinstance(entries, list):
        return ()
    valid = []
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("url"):
            continue
        if not _caption_url_matches_video(str(entry.get("url") or ""), video_id):
            continue
        valid.append(entry)
    return tuple(valid)


def _caption_url_matches_video(url: str, video_id: str) -> bool:
    if "/api/timedtext" not in url:
        return True
    return timedtext_url_matches_video(url, video_id)


def _sort_caption_entries(entries: tuple[dict, ...]) -> list[dict]:
    return sorted(entries, key=lambda entry: _extension_rank(str(entry.get("ext") or "")))


def _extension_rank(extension: str) -> int:
    try:
        return PREFERRED_CAPTION_EXTENSIONS.index(extension.lower())
    except ValueError:
        return len(PREFERRED_CAPTION_EXTENSIONS)


def _language_matches(got: str, wanted: str, *, exact: bool) -> bool:
    normalized_got = got.lower()
    normalized_wanted = wanted.lower()
    if normalized_got == normalized_wanted:
        return True
    return not exact and (
        normalized_got.startswith(normalized_wanted + "-")
        or normalized_wanted.startswith(normalized_got + "-")
    )


def _metadata_final_url(metadata: dict, video_id: str) -> str:
    return _clean_text(metadata.get("webpage_url")) or WATCH_URL.format(video_id=video_id)


def _metadata_description(metadata: dict) -> str:
    return _clean_text(metadata.get("description") or metadata.get("fulltitle") or "")


def _metadata_image_urls(metadata: dict) -> tuple[str, ...]:
    urls = []
    thumbnail = _clean_text(metadata.get("thumbnail"))
    if thumbnail:
        urls.append(thumbnail)
    thumbnails = metadata.get("thumbnails")
    if isinstance(thumbnails, list):
        urls.extend(item.get("url", "") for item in thumbnails if isinstance(item, dict))
    return tuple(sanitize_image_urls(urls, limit=1))


def _build_ytdlp_unavailable_result(requested_url: str, video_id: str, error: str) -> BrowserFetchResult:
    text = "YouTube metadata unavailable via yt-dlp."
    final_url = WATCH_URL.format(video_id=video_id)
    return build_youtube_metadata_result(
        requested_url,
        final_url,
        "YouTube video",
        text if not error else f"{text} Diagnostic: {error}.",
        (),
        diagnostics=("youtube_ytdlp_failed",),
    )


def _parse_timestamp(value: str) -> float:
    parts = value.replace(",", ".").split(":")
    try:
        if len(parts) == 3:
            hours, minutes, seconds = parts
            return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
        if len(parts) == 2:
            minutes, seconds = parts
            return int(minutes) * 60 + float(seconds)
    except ValueError:
        return 0.0
    return 0.0


def _clean_caption_line(value: str) -> str:
    without_tags = TAG_PATTERN.sub("", str(value or ""))
    return " ".join(html.unescape(without_tags).split())


def _clean_text(value) -> str:
    return " ".join(str(value or "").split())
