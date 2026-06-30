from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from typing import Any

from utils.browser_result_types import BrowserFetchResult

YT_DLP_BIN_ENV = "YT_DLP_BIN"
YOUTUBE_SEARCH_LIMIT_ENV = "YOUTUBE_SEARCH_LIMIT"
YOUTUBE_SEARCH_MAX_QUERIES_PER_TURN_ENV = "YOUTUBE_SEARCH_MAX_QUERIES_PER_TURN"
YOUTUBE_SEARCH_QUERY_COOLDOWN_SECONDS_ENV = "YOUTUBE_SEARCH_QUERY_COOLDOWN_SECONDS"
YTDLP_SEARCH_SLEEP_REQUESTS_ENV = "YTDLP_SEARCH_SLEEP_REQUESTS"
DEFAULT_YT_DLP_BIN = "yt-dlp"
DEFAULT_YOUTUBE_SEARCH_LIMIT = 5
DEFAULT_YOUTUBE_SEARCH_MAX_QUERIES_PER_TURN = 1
DEFAULT_YOUTUBE_SEARCH_QUERY_COOLDOWN_SECONDS = 1.0
MAX_YOUTUBE_SEARCH_LIMIT = 10
DEFAULT_YTDLP_SEARCH_SLEEP_REQUESTS = 1.0
MAX_DESCRIPTION_CHARS = 220
MAX_TEXT_CHARS = 8000

Runner = Callable[..., subprocess.CompletedProcess]


def plan_youtube_search_queries_from_env(queries: list[str]) -> list[str]:
    max_queries = _bounded_int_from_env(
        YOUTUBE_SEARCH_MAX_QUERIES_PER_TURN_ENV,
        default=DEFAULT_YOUTUBE_SEARCH_MAX_QUERIES_PER_TURN,
        minimum=1,
        maximum=3,
    )
    return _dedupe_texts(queries)[:max_queries]


def youtube_query_cooldown_seconds_from_env() -> float:
    return _bounded_float_from_env(
        YOUTUBE_SEARCH_QUERY_COOLDOWN_SECONDS_ENV,
        default=DEFAULT_YOUTUBE_SEARCH_QUERY_COOLDOWN_SECONDS,
        minimum=0.0,
        maximum=30.0,
    )


def search_youtube_videos(
    query: str,
    timeout_ms: int,
    *,
    limit: int | None = None,
    runner: Runner = subprocess.run,
) -> BrowserFetchResult:
    normalized_query = str(query or "").strip()
    if not normalized_query:
        return _error_result(normalized_query, "YouTube search query is empty.", "youtube_ytdlp_search_empty")
    bounded_limit = _bounded_limit(limit)
    command = _build_ytdlp_search_command(normalized_query, bounded_limit)
    try:
        completed = runner(
            command,
            capture_output=True,
            text=True,
            timeout=max(10.0, timeout_ms / 1000),
            check=False,
        )
    except FileNotFoundError:
        return _error_result(normalized_query, "yt-dlp is not installed or not found.", "youtube_ytdlp_missing")
    except subprocess.TimeoutExpired:
        return _error_result(normalized_query, "yt-dlp YouTube search timed out.", "youtube_ytdlp_timeout")
    if completed.returncode != 0:
        detail = str(completed.stderr or completed.stdout or "").strip()
        return _error_result(
            normalized_query,
            _join_error("yt-dlp YouTube search failed.", detail),
            "youtube_ytdlp_search_failed",
        )
    try:
        payload = json.loads(completed.stdout or "{}")
    except ValueError:
        return _error_result(normalized_query, "yt-dlp returned invalid YouTube search JSON.", "youtube_ytdlp_invalid_json")
    entries = _collect_entries(payload)
    text = _format_search_results(normalized_query, entries[:bounded_limit])
    if not text:
        return _error_result(normalized_query, "No YouTube search results were returned.", "youtube_ytdlp_search_empty")
    return BrowserFetchResult(
        requested_url=normalized_query,
        source_type="youtube_search",
        query=normalized_query,
        title="YouTube Search",
        text=text[:MAX_TEXT_CHARS],
        content_format="youtube_search_results",
        total_chars=len(text),
        diagnostics=("youtube_ytdlp_search",),
    )


def _build_ytdlp_search_command(query: str, limit: int) -> list[str]:
    command = [
        os.getenv(YT_DLP_BIN_ENV, DEFAULT_YT_DLP_BIN).strip() or DEFAULT_YT_DLP_BIN,
        "--dump-single-json",
        "--flat-playlist",
        "--playlist-end",
        str(limit),
        "--no-warnings",
        "--skip-download",
    ]
    sleep_requests = _sleep_requests_from_env()
    if sleep_requests > 0:
        command.extend(["--sleep-requests", _format_seconds(sleep_requests)])
    command.append(f"ytsearch{limit}:{query}")
    return command


def _bounded_limit(limit: int | None) -> int:
    raw_value = limit if limit is not None else _int_from_env(YOUTUBE_SEARCH_LIMIT_ENV, DEFAULT_YOUTUBE_SEARCH_LIMIT)
    return max(1, min(MAX_YOUTUBE_SEARCH_LIMIT, int(raw_value or DEFAULT_YOUTUBE_SEARCH_LIMIT)))


def _sleep_requests_from_env() -> float:
    raw_value = os.getenv(YTDLP_SEARCH_SLEEP_REQUESTS_ENV)
    if raw_value is None:
        return DEFAULT_YTDLP_SEARCH_SLEEP_REQUESTS
    try:
        return max(0.0, float(raw_value))
    except ValueError:
        return DEFAULT_YTDLP_SEARCH_SLEEP_REQUESTS


def _int_from_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _bounded_int_from_env(name: str, *, default: int, minimum: int, maximum: int) -> int:
    return min(max(_int_from_env(name, default), minimum), maximum)


def _bounded_float_from_env(name: str, *, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)).strip())
    except ValueError:
        return default
    return min(max(value, minimum), maximum)


def _dedupe_texts(values: list[str]) -> list[str]:
    results = []
    for value in values:
        normalized = str(value or "").strip()
        if normalized and normalized not in results:
            results.append(normalized)
    return results


def _collect_entries(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict) and isinstance(payload.get("entries"), list):
        return [entry for entry in payload["entries"] if isinstance(entry, dict)]
    if isinstance(payload, dict):
        return [payload]
    return []


def _format_search_results(query: str, entries: list[dict[str, Any]]) -> str:
    blocks = []
    for index, entry in enumerate(entries, start=1):
        title = _clean_text(entry.get("title")) or "Untitled YouTube result"
        url = _entry_url(entry)
        if not url:
            continue
        lines = [f"{index}. {title}", f"URL: {url}"]
        channel = _clean_text(entry.get("channel") or entry.get("uploader") or entry.get("creator"))
        if channel:
            lines.append(f"Channel: {channel}")
        duration = _format_duration(entry.get("duration"))
        if duration:
            lines.append(f"Duration: {duration}")
        view_count = entry.get("view_count")
        if isinstance(view_count, int):
            lines.append(f"Views: {view_count}")
        description = _clean_text(entry.get("description"))
        if description:
            lines.append(f"Description: {description[:MAX_DESCRIPTION_CHARS]}")
        blocks.append("\n".join(lines))
    if not blocks:
        return ""
    return f"YouTube search results for: {query}\n\n" + "\n\n".join(blocks)


def _entry_url(entry: dict[str, Any]) -> str:
    fallback_video_id = ""
    for key in ("webpage_url", "original_url", "url"):
        value = _clean_text(entry.get(key))
        if value.startswith("http://") or value.startswith("https://"):
            return value
        if key == "url" and value:
            fallback_video_id = value
    video_id = _clean_text(entry.get("id"))
    video_id = video_id or fallback_video_id
    if video_id:
        return f"https://www.youtube.com/watch?v={video_id}"
    return ""


def _format_duration(value: Any) -> str:
    if not isinstance(value, (int, float)) or value <= 0:
        return ""
    total_seconds = int(value)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def _format_seconds(value: float) -> str:
    return str(int(value)) if value.is_integer() else str(value)


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _error_result(query: str, error: str, diagnostic: str) -> BrowserFetchResult:
    return BrowserFetchResult(
        requested_url=query,
        source_type="youtube_search",
        query=query,
        title="YouTube Search",
        error=error[:500],
        content_format="youtube_search_results",
        diagnostics=(diagnostic,),
    )


def _join_error(message: str, detail: str) -> str:
    return f"{message} {detail[:300]}".strip()
