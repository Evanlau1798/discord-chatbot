from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import requests

logger = logging.getLogger("discord.utils.local_asr_client")


class LocalASRError(RuntimeError):
    def __init__(self, code: str, message: str, *, status: int = 0):
        super().__init__(message)
        self.code = str(code)
        self.status = int(status)


@dataclass(frozen=True)
class LocalASRSettings:
    enabled: bool
    port: int
    max_duration_seconds: float
    request_timeout_seconds: float
    config_error: str = ""

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "LocalASRSettings":
        import os

        values = os.environ if env is None else env
        try:
            enabled = _boolean(values.get("LOCAL_ASR_ENABLED", "1"))
            port = _integer(values.get("LOCAL_ASR_PORT", "18765"), 1024, 65535)
            duration = _number(values.get("LOCAL_ASR_MAX_DURATION_SECONDS", "60"), minimum=0.001)
            timeout = _number(values.get("LOCAL_ASR_REQUEST_TIMEOUT_SECONDS", "45"), minimum=0.001)
            return cls(enabled=enabled, port=port, max_duration_seconds=duration, request_timeout_seconds=timeout)
        except ValueError as exc:
            logger.error("local_asr.config_invalid error=%s", exc)
            return cls(enabled=False, port=18765, max_duration_seconds=60.0, request_timeout_seconds=45.0, config_error=str(exc))


@dataclass(frozen=True)
class LocalASRTranscript:
    text: str
    language: str
    duration_seconds: float
    segments: tuple[dict, ...]
    backend: str
    device: str


class LocalASRClient:
    def __init__(self, settings: LocalASRSettings | None = None, *, session=None):
        self.settings = settings or LocalASRSettings.from_env()
        self._session = session or requests.Session()

    async def transcribe(self, data: bytes, *, filename: str, content_type: str) -> LocalASRTranscript:
        if not self.settings.enabled:
            raise LocalASRError("service_disabled", "Local ASR service is disabled.", status=503)
        return await asyncio.to_thread(self._transcribe_sync, bytes(data), filename, content_type)

    def _transcribe_sync(self, data: bytes, filename: str, content_type: str) -> LocalASRTranscript:
        safe_filename = Path(str(filename or "upload.bin")).name or "upload.bin"
        safe_type = str(content_type or "application/octet-stream").split(";", 1)[0].strip()
        try:
            response = self._session.post(
                f"{self.settings.base_url}/v1/transcriptions",
                files={"file": (safe_filename, data, safe_type)},
                timeout=(2.0, self.settings.request_timeout_seconds),
            )
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise LocalASRError("service_unavailable", "Local ASR service is unavailable.", status=503) from exc
        if response.status_code != 200:
            error = payload.get("error") if isinstance(payload, dict) else None
            code = str(error.get("code") or "request_failed") if isinstance(error, dict) else "request_failed"
            message = str(error.get("message") or "Local ASR request failed.") if isinstance(error, dict) else "Local ASR request failed."
            raise LocalASRError(code, message, status=response.status_code)
        return _parse_transcript(payload)


def _parse_transcript(payload) -> LocalASRTranscript:
    if not isinstance(payload, dict) or payload.get("schemaVersion") != 1:
        raise LocalASRError("invalid_response", "Local ASR response schema is invalid.", status=502)
    required = ("text", "language", "durationSeconds", "segments", "backend", "device")
    if any(key not in payload for key in required) or not isinstance(payload.get("segments"), list):
        raise LocalASRError("invalid_response", "Local ASR response fields are invalid.", status=502)
    segments = []
    previous_end = 0.0
    for segment in payload["segments"]:
        if not isinstance(segment, dict) or any(key not in segment for key in ("startSeconds", "endSeconds", "text")):
            raise LocalASRError("invalid_response", "Local ASR segments are invalid.", status=502)
        try:
            start = float(segment["startSeconds"])
            end = float(segment["endSeconds"])
        except (TypeError, ValueError) as exc:
            raise LocalASRError("invalid_response", "Local ASR segment timestamps are invalid.", status=502) from exc
        if not all(math.isfinite(value) for value in (start, end)) or start + 0.001 < previous_end or end < start:
            raise LocalASRError("invalid_response", "Local ASR segment timestamps are invalid.", status=502)
        segments.append({
            "startSeconds": start,
            "endSeconds": end,
            "text": str(segment["text"]),
        })
        previous_end = end
    try:
        duration = float(payload["durationSeconds"])
    except (TypeError, ValueError) as exc:
        raise LocalASRError("invalid_response", "Local ASR duration is invalid.", status=502) from exc
    if (
        not math.isfinite(duration)
        or duration < 0
        or previous_end > duration + 0.001
        or str(payload["backend"]) != "openvino-genai"
        or not str(payload["device"]).upper().startswith("GPU")
    ):
        raise LocalASRError("invalid_response", "Local ASR device or duration is invalid.", status=502)
    return LocalASRTranscript(
        text=str(payload["text"]),
        language=str(payload["language"]),
        duration_seconds=duration,
        segments=tuple(segments),
        backend=str(payload["backend"]),
        device=str(payload["device"]),
    )


def _boolean(value) -> bool:
    normalized = str(value or "").strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError("LOCAL_ASR_ENABLED must be a boolean")


def _integer(value, minimum: int, maximum: int) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError("LOCAL_ASR_PORT must be an integer") from exc
    if not minimum <= parsed <= maximum:
        raise ValueError("LOCAL_ASR_PORT is outside the allowed range")
    return parsed


def _number(value, *, minimum: float) -> float:
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError("LOCAL_ASR numeric setting is invalid") from exc
    if not math.isfinite(parsed) or parsed < minimum:
        raise ValueError("LOCAL_ASR numeric setting is outside the allowed range")
    return parsed
