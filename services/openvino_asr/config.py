from __future__ import annotations

import os
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


class ASRConfigError(ValueError):
    pass


@dataclass(frozen=True)
class ASRConfig:
    enabled: bool
    port: int
    max_concurrency: int
    max_duration_seconds: float
    max_queue_size: int
    queue_timeout_seconds: float
    request_timeout_seconds: float
    max_upload_bytes: int
    device: str
    model_id: str
    model_revision: str
    model_dir: Path
    cache_dir: Path
    temp_dir: Path
    hotwords_enabled: bool
    max_hotwords: int
    ffmpeg_bin: str
    ffprobe_bin: str

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "ASRConfig":
        values = os.environ if env is None else env
        device = _text(values, "LOCAL_ASR_DEVICE", "GPU").upper()
        if not device.startswith("GPU"):
            raise ASRConfigError("LOCAL_ASR_DEVICE must explicitly select an Intel GPU")
        return cls(
            enabled=_boolean(values, "LOCAL_ASR_ENABLED", True),
            port=_integer(values, "LOCAL_ASR_PORT", 18765, minimum=1024, maximum=65535),
            max_concurrency=_integer(values, "LOCAL_ASR_MAX_CONCURRENCY", 1, minimum=1),
            max_duration_seconds=_number(values, "LOCAL_ASR_MAX_DURATION_SECONDS", 60.0, minimum=0.001),
            max_queue_size=_integer(values, "LOCAL_ASR_MAX_QUEUE_SIZE", 2, minimum=0),
            queue_timeout_seconds=_number(values, "LOCAL_ASR_QUEUE_TIMEOUT_SECONDS", 20.0, minimum=0.0),
            request_timeout_seconds=_number(values, "LOCAL_ASR_REQUEST_TIMEOUT_SECONDS", 45.0, minimum=0.001),
            max_upload_bytes=_integer(values, "LOCAL_ASR_MAX_UPLOAD_BYTES", 50 * 1024 * 1024, minimum=1),
            device=device,
            model_id=_text(values, "LOCAL_ASR_MODEL_ID", "OpenVINO/whisper-small-fp16-ov"),
            model_revision=_text(
                values,
                "LOCAL_ASR_MODEL_REVISION",
                "2410d022171ca8a97343182f88eec8807a324db9",
            ),
            model_dir=Path(_text(values, "LOCAL_ASR_MODEL_DIR", "./tmp/openvino-asr/models/whisper-small-fp16-ov")),
            cache_dir=Path(_text(values, "LOCAL_ASR_CACHE_DIR", "./tmp/openvino-asr/cache")),
            temp_dir=Path(_text(values, "LOCAL_ASR_TMP_DIR", "/tmp/openvino-asr")),
            hotwords_enabled=_boolean(values, "LOCAL_ASR_HOTWORDS_ENABLED", False),
            max_hotwords=_integer(values, "LOCAL_ASR_MAX_HOTWORDS", 8, minimum=0, maximum=32),
            ffmpeg_bin=_text(values, "FFMPEG_BIN", "ffmpeg"),
            ffprobe_bin=_text(values, "FFPROBE_BIN", "ffprobe"),
        )


def _text(env: Mapping[str, str], key: str, default: str) -> str:
    value = str(env.get(key, default) or "").strip()
    if not value:
        raise ASRConfigError(f"{key} must not be empty")
    return value


def _boolean(env: Mapping[str, str], key: str, default: bool) -> bool:
    raw = str(env.get(key, "1" if default else "0") or "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise ASRConfigError(f"{key} must be a boolean")


def _integer(
    env: Mapping[str, str],
    key: str,
    default: int,
    *,
    minimum: int,
    maximum: int | None = None,
) -> int:
    try:
        value = int(str(env.get(key, default)).strip())
    except (TypeError, ValueError) as exc:
        raise ASRConfigError(f"{key} must be an integer") from exc
    if value < minimum or (maximum is not None and value > maximum):
        raise ASRConfigError(f"{key} is outside the allowed range")
    return value


def _number(env: Mapping[str, str], key: str, default: float, *, minimum: float) -> float:
    try:
        value = float(str(env.get(key, default)).strip())
    except (TypeError, ValueError) as exc:
        raise ASRConfigError(f"{key} must be numeric") from exc
    if not math.isfinite(value) or value < minimum:
        raise ASRConfigError(f"{key} is outside the allowed range")
    return value
