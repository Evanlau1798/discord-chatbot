from __future__ import annotations

import mimetypes
import math
from pathlib import Path
from urllib.parse import urlparse

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif")
VIDEO_EXTENSIONS = (".mp4", ".mov", ".webm", ".m4v", ".mkv")
AUDIO_EXTENSIONS = (".aac", ".flac", ".m4a", ".mp3", ".oga", ".ogg", ".opus", ".wav", ".webm")


def message_has_video_attachment(message) -> bool:
    return bool(iter_video_attachments(message))


def message_is_voice_message(message) -> bool:
    flags = getattr(message, "flags", None)
    return bool(getattr(flags, "is_voice_message", False))


def iter_image_attachments(message) -> list:
    return [attachment for attachment in _attachments(message) if _is_image(attachment)]


def iter_video_attachments(message) -> list:
    if message_is_voice_message(message):
        return []
    attachments = []
    for attachment in _attachments(message):
        content_type = _content_type(attachment)
        if content_type.startswith("audio/"):
            continue
        if content_type.startswith("video/") or _has_extension(attachment, VIDEO_EXTENSIONS):
            attachments.append(attachment)
    return attachments


def iter_audio_attachments(message) -> list:
    voice_message = message_is_voice_message(message)
    videos = {id(attachment) for attachment in iter_video_attachments(message)}
    attachments = []
    for attachment in _attachments(message):
        if id(attachment) in videos or _is_image(attachment):
            continue
        content_type = _content_type(attachment)
        if voice_message or content_type.startswith("audio/") or _has_extension(attachment, AUDIO_EXTENSIONS):
            attachments.append(attachment)
    return attachments


async def read_attachment_bytes(attachment, *, max_bytes: int) -> bytes:
    size = int(getattr(attachment, "size", 0) or 0)
    if size > max_bytes:
        return b""
    read = getattr(attachment, "read", None)
    if not callable(read):
        return b""
    try:
        data = await read(use_cached=True)
    except TypeError:
        data = await read()
    except Exception:
        return b""
    if not isinstance(data, (bytes, bytearray)) or not data or len(data) > max_bytes:
        return b""
    return bytes(data)


def attachment_mime_type(attachment) -> str:
    content_type = _content_type(attachment)
    if content_type.startswith(("image/", "audio/", "video/")) or content_type == "application/ogg":
        return content_type
    guessed = (
        mimetypes.guess_type(str(getattr(attachment, "filename", "") or ""))[0]
        or mimetypes.guess_type(str(getattr(attachment, "url", "") or ""))[0]
    )
    return guessed or "application/octet-stream"


def attachment_duration_seconds(attachment) -> float | None:
    try:
        duration = float(getattr(attachment, "duration_secs", None))
    except (TypeError, ValueError):
        return None
    return max(0.0, duration) if math.isfinite(duration) else None


def _attachments(message) -> list:
    return list(getattr(message, "attachments", []) or [])


def _is_image(attachment) -> bool:
    return _content_type(attachment).startswith("image/") or _has_extension(attachment, IMAGE_EXTENSIONS)


def _content_type(attachment) -> str:
    return str(getattr(attachment, "content_type", "") or "").split(";", 1)[0].strip().lower()


def _has_extension(attachment, extensions: tuple[str, ...]) -> bool:
    filename = Path(str(getattr(attachment, "filename", "") or "")).name.lower()
    url_path = urlparse(str(getattr(attachment, "url", "") or "").lower()).path
    return filename.endswith(extensions) or url_path.endswith(extensions)
