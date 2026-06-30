from __future__ import annotations

from utils.browser_result_types import BrowserFetchResult
from utils.x_status_reader import read_x_status_url
from utils.youtube_transcript_reader import read_youtube_transcript_url


def read_special_url(url: str, timeout_ms: int, *, include_images: bool = False) -> BrowserFetchResult | None:
    for reader in (
        lambda: read_youtube_transcript_url(url, timeout_ms, include_images=include_images),
        lambda: read_x_status_url(url, timeout_ms),
    ):
        result = reader()
        if result is not None:
            return result
    return None
