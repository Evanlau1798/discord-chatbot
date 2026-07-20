from __future__ import annotations

import logging

from utils.gif_frame_sampler import (
    is_apng_mime_type,
    is_gif_mime_type,
    is_webp_mime_type,
    sample_apng_frames,
    sample_gif_frames,
    sample_webp_frames,
)
from utils.media_frame_presentation import present_media_frames
from utils.video_frame_splitter import split_video_bytes

logger = logging.getLogger("discord.utils.chat_media")


def prepare_image_bytes(payload, *, source_label: str = "") -> list[dict]:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if not isinstance(data, (bytes, bytearray)) or not data:
        return []
    mime_type = _normalize_mime_type(payload.get("mime_type"))
    return prepare_image_data(bytes(data), mime_type, source_label=source_label)


def prepare_video_bytes(payload) -> list[dict]:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if not isinstance(data, (bytes, bytearray)) or not data:
        return []
    mime_type = _normalize_mime_type(payload.get("mime_type"))
    filename = str(payload.get("filename") or "").strip()
    try:
        split_result = split_video_bytes(bytes(data), mime_type, filename=filename)
    except Exception as exc:
        logger.warning("chat_media.video_sampling_failed error_type=%s", type(exc).__name__)
        return []
    if split_result is None:
        logger.warning("chat_media.video_sampling_failed source=%s", _safe_source_label(filename or mime_type))
        return []
    note = _video_sampling_note(split_result, filename)
    return _present_frames(split_result.frames, note) or [
        _text_part(note),
        *(_bytes_part(frame.data, frame.mime_type) for frame in split_result.frames),
    ]


def prepare_image_data(data: bytes, mime_type: str, *, source_label: str = "") -> list[dict]:
    normalized_mime_type = _normalize_mime_type(mime_type)
    sampling = None
    media_kind = ""
    sampler = None
    if is_gif_mime_type(normalized_mime_type):
        media_kind, sampler = "GIF", sample_gif_frames
    elif is_webp_mime_type(normalized_mime_type):
        media_kind, sampler = "WebP", sample_webp_frames
    elif is_apng_mime_type(normalized_mime_type):
        media_kind, sampler = "APNG", sample_apng_frames
    if sampler is not None:
        try:
            sampling = sampler(data)
        except Exception as exc:
            logger.warning(
                "chat_media.image_sampling_failed kind=%s source=%s error_type=%s",
                media_kind.lower(),
                _safe_source_label(source_label),
                type(exc).__name__,
            )
            if media_kind == "GIF":
                return []
        if sampling is None and media_kind == "GIF":
            logger.warning(
                "chat_media.image_sampling_failed kind=gif source=%s",
                _safe_source_label(source_label),
            )
            return []
    if sampling is None:
        return [_bytes_part(data, normalized_mime_type)]
    note = _animation_sampling_note(sampling, media_kind)
    return _present_frames(sampling.frames, note) or [
        _text_part(note),
        *(_bytes_part(frame.data, frame.mime_type) for frame in sampling.frames),
    ]


def _present_frames(frames, note_text: str) -> list[dict]:
    try:
        presentation = present_media_frames(tuple(frames or ()))
    except Exception as exc:
        logger.warning("chat_media.frame_presentation_failed error_type=%s", type(exc).__name__)
        return []
    if presentation is None:
        return []
    note = (
        f"{note_text} The following {len(presentation.sheets)} image parts are contact sheet summaries "
        "of the sampled frames, not separate single-frame images. Read each contact sheet left-to-right, "
        f"top-to-bottom; sheets are chronological. sampled_frame_parts={presentation.input_frame_count}, "
        f"kept_after_dedupe={presentation.kept_frame_count}, "
        f"dropped_similar_frames={presentation.dropped_similar_count}."
    )
    return [_text_part(note), *(_bytes_part(sheet.data, sheet.mime_type) for sheet in presentation.sheets)]


def _animation_sampling_note(sampling, media_kind: str) -> str:
    mode = "all" if sampling.sampled_all else "sampled"
    temporal = "Use them together as a temporal sequence."
    if media_kind == "WebP":
        temporal = "Use them together as a temporal sequence when multiple frames are present."
    return (
        f"The following {len(sampling.frames)} image parts are {mode} sampled image frames from one "
        f"{media_kind} image or animation, in chronological order. Original {media_kind} "
        f"frame_count={sampling.frame_count}, duration_ms={sampling.duration_ms}. {temporal}"
    )


def _video_sampling_note(split_result, filename: str = "") -> str:
    mode = "all" if split_result.sampled_all else "sampled"
    source = f" filename={filename}." if filename else "."
    return (
        f"The following image parts are {mode} JPEG frames sampled from one video,{source} "
        f"Original sampled_frame_count={len(split_result.frames)}, original video "
        f"frame_count={split_result.frame_count}, duration_ms={split_result.duration_ms}. "
        "Use them together as a temporal sequence."
    )


def _text_part(text: str) -> dict:
    return {"type": "text", "text": text}


def _bytes_part(data: bytes, mime_type: str) -> dict:
    return {"type": "image_bytes", "image_bytes": {"data": data, "mime_type": mime_type}}


def _normalize_mime_type(value) -> str:
    return str(value or "application/octet-stream").split(";", 1)[0].strip() or "application/octet-stream"


def _safe_source_label(value: str) -> str:
    return str(value or "")[:120]
