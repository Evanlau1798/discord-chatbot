from __future__ import annotations

import os

from utils.async_cooldown_queue import AsyncCooldownQueue

YTDLP_REQUEST_COOLDOWN_SECONDS_ENV = "YTDLP_REQUEST_COOLDOWN_SECONDS"
LEGACY_YOUTUBE_SEARCH_COOLDOWN_ENV = "YOUTUBE_SEARCH_QUERY_COOLDOWN_SECONDS"
DEFAULT_YTDLP_REQUEST_COOLDOWN_SECONDS = 1.0
YTDLP_REQUEST_QUEUE = AsyncCooldownQueue()


def ytdlp_request_cooldown_seconds_from_env() -> float:
    raw_value = os.getenv(YTDLP_REQUEST_COOLDOWN_SECONDS_ENV)
    if raw_value is None:
        raw_value = os.getenv(LEGACY_YOUTUBE_SEARCH_COOLDOWN_ENV)
    if raw_value is None:
        return DEFAULT_YTDLP_REQUEST_COOLDOWN_SECONDS
    try:
        return min(max(float(raw_value.strip()), 0.0), 30.0)
    except ValueError:
        return DEFAULT_YTDLP_REQUEST_COOLDOWN_SECONDS
