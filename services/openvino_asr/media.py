from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import wave
from array import array
from dataclasses import dataclass
from pathlib import Path

from services.openvino_asr.config import ASRConfig
from services.openvino_asr.protocol import ASRServiceError

ALLOWED_EXTENSIONS = {".aac", ".flac", ".m4a", ".m4v", ".mov", ".mp3", ".mp4", ".oga", ".ogg", ".opus", ".wav", ".webm"}
ALLOWED_MIME_PREFIXES = ("audio/", "video/")
ALLOWED_MIME_TYPES = {"application/octet-stream", "application/ogg"}


@dataclass(frozen=True)
class DecodedAudio:
    samples: tuple[float, ...]
    duration_seconds: float


def decode_media(data: bytes, filename: str, content_type: str, config: ASRConfig) -> DecodedAudio:
    _validate_upload(data, filename, content_type, config.max_upload_bytes)
    ffmpeg = shutil.which(config.ffmpeg_bin)
    ffprobe = shutil.which(config.ffprobe_bin)
    if not ffmpeg or not ffprobe:
        raise ASRServiceError("decoder_unavailable", "Media decoder is unavailable.", status=503)
    config.temp_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="request-", dir=str(config.temp_dir)) as temp_dir:
        root = Path(temp_dir)
        suffix = Path(Path(filename).name).suffix.lower() or ".bin"
        source = root / f"input{suffix}"
        output = root / "audio.wav"
        source.write_bytes(data)
        probed_duration = _probe_duration(source, ffprobe, config.request_timeout_seconds)
        if probed_duration > config.max_duration_seconds:
            raise _duration_error(config.max_duration_seconds)
        _decode_to_wav(source, output, ffmpeg, config)
        return _read_wav(output, config.max_duration_seconds)


def _validate_upload(data: bytes, filename: str, content_type: str, max_bytes: int) -> None:
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise ASRServiceError("empty_upload", "Uploaded media is empty.", status=400)
    if len(data) > max_bytes:
        raise ASRServiceError("upload_too_large", "Uploaded media exceeds the configured size limit.", status=413)
    suffix = Path(Path(str(filename or "")).name).suffix.lower()
    mime = str(content_type or "").split(";", 1)[0].strip().lower()
    if suffix not in ALLOWED_EXTENSIONS and not mime.startswith(ALLOWED_MIME_PREFIXES) and mime not in ALLOWED_MIME_TYPES:
        raise ASRServiceError("unsupported_media", "Uploaded media format is not supported.", status=415)


def _probe_duration(path: Path, ffprobe: str, timeout_seconds: float) -> float:
    try:
        completed = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise ASRServiceError("decode_timeout", "Media probing timed out.", status=408) from exc
    if completed.returncode != 0:
        return 0.0
    try:
        return max(0.0, float(json.loads(completed.stdout or "{}").get("format", {}).get("duration") or 0.0))
    except (TypeError, ValueError, json.JSONDecodeError):
        return 0.0


def _decode_to_wav(source: Path, output: Path, ffmpeg: str, config: ASRConfig) -> None:
    decode_cap = config.max_duration_seconds + 0.25
    try:
        completed = subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(source),
                "-vn",
                "-t",
                f"{decode_cap:.3f}",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "pcm_s16le",
                str(output),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=config.request_timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise ASRServiceError("decode_timeout", "Media decoding timed out.", status=408) from exc
    if completed.returncode != 0 or not output.is_file():
        raise ASRServiceError("decode_failed", "Media audio could not be decoded.", status=422)


def _read_wav(path: Path, max_duration_seconds: float) -> DecodedAudio:
    try:
        with wave.open(str(path), "rb") as wav_file:
            if wav_file.getframerate() != 16_000 or wav_file.getnchannels() != 1 or wav_file.getsampwidth() != 2:
                raise ASRServiceError("decode_failed", "Decoded audio format is invalid.", status=422)
            frame_count = wav_file.getnframes()
            duration = frame_count / 16_000
            if duration > max_duration_seconds + (1 / 16_000):
                raise _duration_error(max_duration_seconds)
            pcm = array("h")
            pcm.frombytes(wav_file.readframes(frame_count))
    except (EOFError, wave.Error) as exc:
        raise ASRServiceError("decode_failed", "Decoded audio is invalid.", status=422) from exc
    if sys.byteorder != "little":
        pcm.byteswap()
    if not pcm:
        raise ASRServiceError("no_audio", "Uploaded media does not contain audio.", status=422)
    return DecodedAudio(
        samples=tuple(sample / 32768.0 for sample in pcm),
        duration_seconds=duration,
    )


def _duration_error(limit: float) -> ASRServiceError:
    return ASRServiceError(
        "duration_exceeded",
        f"Media duration exceeds the configured {limit:g} second limit.",
        status=422,
    )
