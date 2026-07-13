from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping


class ASRServiceError(RuntimeError):
    def __init__(self, code: str, message: str, *, status: int):
        super().__init__(message)
        self.code = str(code)
        self.public_message = str(message)
        self.status = int(status)

    def to_payload(self) -> dict:
        return {
            "schemaVersion": 1,
            "error": {"code": self.code, "message": self.public_message},
        }


@dataclass(frozen=True)
class Transcription:
    text: str
    language: str
    duration_seconds: float
    segments: tuple[dict, ...]
    backend: str
    device: str

    def to_payload(self) -> dict:
        return {
            "schemaVersion": 1,
            "text": self.text,
            "language": self.language,
            "durationSeconds": round(max(0.0, self.duration_seconds), 3),
            "segments": list(self.segments),
            "backend": self.backend,
            "device": self.device,
        }


def normalize_segments(segments: Iterable, *, duration_seconds: float) -> tuple[dict, ...]:
    duration = round(max(0.0, _number(duration_seconds)), 3)
    previous_end = 0.0
    normalized = []
    for segment in segments or ():
        start = min(duration, max(previous_end, _number(_value(segment, "start", "start_ts"))))
        end = min(duration, max(start, _number(_value(segment, "end", "end_ts"))))
        text = str(_value(segment, "text") or "").strip()
        if not text:
            continue
        start = round(start, 3)
        end = round(max(start, end), 3)
        normalized.append({"startSeconds": start, "endSeconds": end, "text": text})
        previous_end = end
    return tuple(normalized)


def _value(value, *keys: str):
    if isinstance(value, Mapping):
        for key in keys:
            if key in value:
                return value[key]
        return None
    for key in keys:
        if hasattr(value, key):
            return getattr(value, key)
    return None


def _number(value) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
