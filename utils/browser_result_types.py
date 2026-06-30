from __future__ import annotations

from dataclasses import dataclass


class BrowserToolError(Exception):
    pass


@dataclass(frozen=True)
class BrowserFetchResult:
    requested_url: str
    source_type: str = "url"
    query: str = ""
    final_url: str = ""
    title: str = ""
    text: str = ""
    error: str = ""
    image_urls: tuple[str, ...] = ()
    content_format: str = ""
    total_chars: int = 0
    next_start_char: int | None = None
    diagnostics: tuple[str, ...] = ()
    media_notes: tuple[str, ...] = ()

    def to_payload(self) -> dict:
        payload = {
            "sourceType": self.source_type,
            "query": self.query,
            "requestedUrl": self.requested_url,
            "finalUrl": self.final_url,
            "title": self.title,
            "text": self.text,
            "error": self.error,
            "imageUrls": list(self.image_urls),
        }
        if self.content_format:
            payload["contentFormat"] = self.content_format
        if self.total_chars:
            payload["totalChars"] = self.total_chars
        if self.next_start_char is not None:
            payload["nextStartChar"] = self.next_start_char
        if self.diagnostics:
            payload["diagnostics"] = list(self.diagnostics)
        if self.media_notes:
            payload["mediaNotes"] = list(self.media_notes)
        return payload
