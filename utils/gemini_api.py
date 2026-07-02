from __future__ import annotations

import logging
import mimetypes
import os
import time
from dataclasses import dataclass
from typing import Any

import requests

try:
    from google import genai
    from google.genai import types as genai_types
except ImportError as exc:
    genai = None
    genai_types = None
    GENAI_IMPORT_ERROR = exc
else:
    GENAI_IMPORT_ERROR = None

from utils.ai_api_logging import log_ai_api_event
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

DEFAULT_GEMINI_MODEL = "gemma-4-31b-it"
REQUEST_TIMEOUT_MS = 180_000
DEFAULT_CACHE_TTL = "31536000s"
PROJECT_CACHE_PREFIX = "discord-chatbot-persona:"
CACHE_ENABLED_ENV = "GEMINI_CACHE_ENABLED"
logger = logging.getLogger("discord.utils.gemini_api")


@dataclass
class GeminiResponse:
    raw_content: str
    visible_content: str
    thinking_content: str = ""
    delivery_mode: str = "complete"


class GeminiAPIError(Exception):
    pass


class GeminiChatClient:
    def __init__(self, api_key: str, model: str = DEFAULT_GEMINI_MODEL):
        if not api_key:
            raise GeminiAPIError("缺少 GEMINIAPIKEY")
        _ensure_genai_sdk()
        self.client = genai.Client(api_key=api_key)
        self.model = model

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float = 0.7,
        cached_content: str | None = None,
    ) -> GeminiResponse:
        system_instruction, contents = self._split_messages(messages)
        if cached_content:
            system_instruction = ""
        config = genai_types.GenerateContentConfig(
            systemInstruction=system_instruction or None,
            temperature=temperature,
            cachedContent=cached_content or None,
            httpOptions=genai_types.HttpOptions(timeout=REQUEST_TIMEOUT_MS),
        )
        started_at = time.monotonic()
        log_ai_api_event(
            "request",
            provider="gemini",
            operation="generate_content",
            model=self.model,
            request_body={
                "message_count": len(messages),
                "temperature": temperature,
                "uses_cached_content": bool(cached_content),
                "estimated_chars": _estimate_messages_chars(messages),
            },
        )
        try:
            response = self.client.models.generate_content(model=self.model, contents=contents, config=config)
        except Exception as exc:
            log_ai_api_event(
                "error",
                provider="gemini",
                operation="generate_content",
                model=self.model,
                elapsed_ms=round((time.monotonic() - started_at) * 1000, 3),
                error_type=type(exc).__name__,
                error=str(exc),
            )
            raise
        visible_content, thinking_content = self._extract_response_texts(response)
        log_ai_api_event(
            "response",
            provider="gemini",
            operation="generate_content",
            model=self.model,
            elapsed_ms=round((time.monotonic() - started_at) * 1000, 3),
            response={"visible_len": len(visible_content), "thinking_len": len(thinking_content)},
        )
        return GeminiResponse(raw_content=visible_content, visible_content=visible_content, thinking_content=thinking_content)

    def refresh_persona_caches(self, prompts_by_key: dict[str, str]) -> dict[str, str]:
        cache_enabled, reason = _explicit_cache_enabled(self.model)
        if not cache_enabled:
            logger.info("gemini.cache_skipped model=%s reason=%s", self.model, reason)
            return {}
        self.delete_project_caches()
        cache_names = {}
        for key, prompt in prompts_by_key.items():
            if not prompt.strip():
                continue
            try:
                cache = self.create_system_prompt_cache(key, prompt)
            except Exception as exc:
                if _is_explicit_cache_unsupported_error(exc):
                    logger.info(
                        "gemini.cache_skipped model=%s reason=create_cached_content_unsupported error_type=%s",
                        self.model,
                        type(exc).__name__,
                    )
                    return cache_names
                logger.warning(
                    "gemini.cache_create_failed persona=%s error_type=%s error=%s",
                    key,
                    type(exc).__name__,
                    exc,
                )
                continue
            cache_name = getattr(cache, "name", "")
            if cache_name:
                cache_names[key] = cache_name
        logger.info("gemini.cache_refresh_complete count=%s", len(cache_names))
        return cache_names

    def delete_project_caches(self) -> None:
        try:
            caches = list(self.client.caches.list())
        except Exception as exc:
            logger.warning("gemini.cache_list_failed error_type=%s error=%s", type(exc).__name__, exc)
            return
        deleted_count = 0
        for cache in caches:
            display_name = getattr(cache, "display_name", None) or getattr(cache, "displayName", None) or ""
            name = getattr(cache, "name", "")
            if not name or not str(display_name).startswith(PROJECT_CACHE_PREFIX):
                continue
            try:
                self.client.caches.delete(name=name)
                deleted_count += 1
            except Exception as exc:
                logger.warning(
                    "gemini.cache_delete_failed name=%s error_type=%s error=%s",
                    name,
                    type(exc).__name__,
                    exc,
                )
        logger.info("gemini.cache_delete_complete count=%s", deleted_count)

    def create_system_prompt_cache(self, persona_key: str, system_prompt: str):
        ttl = os.getenv("GEMINI_CACHE_TTL", DEFAULT_CACHE_TTL).strip() or DEFAULT_CACHE_TTL
        config = genai_types.CreateCachedContentConfig(
            displayName=f"{PROJECT_CACHE_PREFIX}{persona_key}",
            systemInstruction=system_prompt,
            contents=[genai_types.UserContent(parts=[genai_types.Part.from_text(text="Initialize cached persona instructions.")])],
            ttl=ttl,
            httpOptions=genai_types.HttpOptions(timeout=REQUEST_TIMEOUT_MS),
        )
        return self.client.caches.create(model=self.model, config=config)

    def _split_messages(self, messages: list[dict[str, Any]]):
        if not messages:
            raise GeminiAPIError("Gemini 請求不可為空")
        system_parts = []
        conversation = []
        for message in messages:
            role = message.get("role")
            if role == "system":
                content = message.get("content")
                if isinstance(content, str) and content.strip():
                    system_parts.append(content.strip())
                continue
            conversation.append(message)
        if not conversation or conversation[-1].get("role") != "user":
            raise GeminiAPIError("Gemini 請求最後一則訊息必須是 user")
        return "\n\n".join(system_parts), [self._build_content(message) for message in conversation]

    def _build_content(self, message: dict[str, Any]):
        role = message.get("role")
        parts = self._convert_parts(message.get("content"))
        if role == "assistant":
            return genai_types.ModelContent(parts=parts)
        if role == "user":
            return genai_types.UserContent(parts=parts)
        raise GeminiAPIError(f"Gemini 不支援的訊息角色: {role}")

    def _convert_parts(self, content) -> list:
        if isinstance(content, str):
            return [genai_types.Part.from_text(text=content)]
        if not isinstance(content, list):
            return [genai_types.Part.from_text(text=str(content))]
        parts = []
        for item in content:
            if not isinstance(item, dict):
                parts.append(genai_types.Part.from_text(text=str(item)))
                continue
            if item.get("type") == "text":
                parts.append(genai_types.Part.from_text(text=item.get("text", "")))
            elif item.get("type") == "image_url":
                image_url = item.get("image_url", {}).get("url")
                if image_url:
                    parts.extend(self._safe_download_image_parts(image_url))
            elif item.get("type") == "image_bytes":
                parts.extend(_image_bytes_parts(item.get("image_bytes", {})))
            elif item.get("type") == "video_bytes":
                parts.extend(_video_bytes_parts(item.get("video_bytes", {})))
        return parts or [genai_types.Part.from_text(text="")]

    def _safe_download_image_parts(self, url: str) -> list:
        try:
            return self._download_image_parts(url)
        except requests.RequestException as exc:
            logger.warning("gemini.image_download_failed error_type=%s error=%s", type(exc).__name__, exc)
            return []

    def _download_image_parts(self, url: str) -> list:
        response = requests.get(url, timeout=(10, 60))
        response.raise_for_status()
        mime_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip()
        if not mime_type:
            mime_type = mimetypes.guess_type(url)[0] or "application/octet-stream"
        return _image_data_parts(response.content, mime_type, source_label=url)

    @staticmethod
    def _extract_response_texts(response) -> tuple[str, str]:
        visible_chunks = []
        thinking_chunks = []
        for candidate in getattr(response, "candidates", []) or []:
            content = getattr(candidate, "content", None)
            for part in getattr(content, "parts", []) or []:
                text = getattr(part, "text", None)
                if not text:
                    continue
                if getattr(part, "thought", False):
                    thinking_chunks.append(text)
                else:
                    visible_chunks.append(text)
        if visible_chunks or thinking_chunks:
            return "".join(visible_chunks), "".join(thinking_chunks)
        try:
            return getattr(response, "text", "") or "", ""
        except ValueError:
            return "", ""


def _ensure_genai_sdk():
    if genai is None or genai_types is None:
        raise GeminiAPIError("缺少 google-genai 套件") from GENAI_IMPORT_ERROR


def _image_bytes_parts(payload) -> list:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if not isinstance(data, (bytes, bytearray)) or not data:
        return []
    mime_type = str(payload.get("mime_type") or "application/octet-stream").strip() or "application/octet-stream"
    return _image_data_parts(bytes(data), mime_type)


def _video_bytes_parts(payload) -> list:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if not isinstance(data, (bytes, bytearray)) or not data:
        return []
    mime_type = str(payload.get("mime_type") or "application/octet-stream").strip() or "application/octet-stream"
    filename = str(payload.get("filename") or "").strip()
    try:
        split_result = split_video_bytes(bytes(data), mime_type, filename=filename)
    except Exception as exc:
        logger.warning("gemini.video_sampling_failed error_type=%s error=%s", type(exc).__name__, exc)
        return []
    if split_result is None:
        logger.warning("gemini.video_sampling_failed source=%s", _safe_source_label(filename or mime_type))
        return []
    presented_parts = _contact_sheet_parts(split_result.frames, _video_sampling_note(split_result, filename))
    if presented_parts:
        return presented_parts
    return [
        genai_types.Part.from_text(text=_video_sampling_note(split_result, filename)),
        *(genai_types.Part.from_bytes(data=frame.data, mime_type=frame.mime_type) for frame in split_result.frames),
    ]


def _image_data_parts(data: bytes, mime_type: str, *, source_label: str = "") -> list:
    normalized_mime_type = str(mime_type or "application/octet-stream").split(";", 1)[0].strip() or "application/octet-stream"
    if is_gif_mime_type(normalized_mime_type):
        try:
            sampling = sample_gif_frames(data)
        except Exception as exc:
            logger.warning(
                "gemini.gif_sampling_failed source=%s error_type=%s error=%s",
                _safe_source_label(source_label),
                type(exc).__name__,
                exc,
            )
            return []
        if sampling is None:
            logger.warning("gemini.gif_sampling_failed source=%s", _safe_source_label(source_label))
            return []
        presented_parts = _contact_sheet_parts(sampling.frames, _gif_sampling_note(sampling))
        if presented_parts:
            return presented_parts
        return [
            genai_types.Part.from_text(text=_gif_sampling_note(sampling)),
            *(genai_types.Part.from_bytes(data=frame.data, mime_type=frame.mime_type) for frame in sampling.frames),
        ]
    if is_webp_mime_type(normalized_mime_type):
        try:
            sampling = sample_webp_frames(data)
        except Exception as exc:
            logger.warning(
                "gemini.webp_sampling_failed source=%s error_type=%s error=%s",
                _safe_source_label(source_label),
                type(exc).__name__,
                exc,
            )
            sampling = None
        if sampling is not None:
            presented_parts = _contact_sheet_parts(sampling.frames, _webp_sampling_note(sampling))
            if presented_parts:
                return presented_parts
            return [
                genai_types.Part.from_text(text=_webp_sampling_note(sampling)),
                *(genai_types.Part.from_bytes(data=frame.data, mime_type=frame.mime_type) for frame in sampling.frames),
            ]
    if is_apng_mime_type(normalized_mime_type):
        try:
            sampling = sample_apng_frames(data)
        except Exception as exc:
            logger.warning(
                "gemini.apng_sampling_failed source=%s error_type=%s error=%s",
                _safe_source_label(source_label),
                type(exc).__name__,
                exc,
            )
            sampling = None
        if sampling is not None:
            presented_parts = _contact_sheet_parts(sampling.frames, _apng_sampling_note(sampling))
            if presented_parts:
                return presented_parts
            return [
                genai_types.Part.from_text(text=_apng_sampling_note(sampling)),
                *(genai_types.Part.from_bytes(data=frame.data, mime_type=frame.mime_type) for frame in sampling.frames),
            ]
    return [genai_types.Part.from_bytes(data=data, mime_type=normalized_mime_type)]

def _contact_sheet_parts(frames, note_text: str) -> list:
    try:
        presentation = present_media_frames(tuple(frames or ()))
    except Exception as exc:
        logger.warning("gemini.frame_presentation_failed error_type=%s error=%s", type(exc).__name__, exc)
        return []
    if presentation is None:
        return []
    return [
        genai_types.Part.from_text(text=f"{note_text} {_contact_sheet_note(presentation)}"),
        *(genai_types.Part.from_bytes(data=sheet.data, mime_type=sheet.mime_type) for sheet in presentation.sheets),
    ]


def _contact_sheet_note(presentation) -> str:
    return (
        f"The following {len(presentation.sheets)} image parts are contact sheet summaries of the sampled frames, "
        "not separate single-frame images. Read each contact sheet left-to-right, top-to-bottom; sheets are chronological. "
        f"sampled_frame_parts={presentation.input_frame_count}, kept_after_dedupe={presentation.kept_frame_count}, "
        f"dropped_similar_frames={presentation.dropped_similar_count}."
    )


def _gif_sampling_note(sampling) -> str:
    mode = "all" if sampling.sampled_all else "sampled"
    return (
        f"The following {len(sampling.frames)} image parts are {mode} sampled image frames from one animated GIF, "
        f"in chronological order. Original GIF frame_count={sampling.frame_count}, duration_ms={sampling.duration_ms}. "
        "Use them together as a temporal sequence."
    )


def _webp_sampling_note(sampling) -> str:
    mode = "all" if sampling.sampled_all else "sampled"
    return (
        f"The following {len(sampling.frames)} image parts are {mode} sampled image frames from one WebP image or animation, "
        f"in chronological order. Original WebP frame_count={sampling.frame_count}, duration_ms={sampling.duration_ms}. "
        "Use them together as a temporal sequence when multiple frames are present."
    )


def _apng_sampling_note(sampling) -> str:
    mode = "all" if sampling.sampled_all else "sampled"
    return (
        f"The following {len(sampling.frames)} image parts are {mode} sampled image frames from one APNG animation, "
        f"in chronological order. Original APNG frame_count={sampling.frame_count}, duration_ms={sampling.duration_ms}. "
        "Use them together as a temporal sequence."
    )


def _video_sampling_note(split_result, filename: str = "") -> str:
    mode = "all" if split_result.sampled_all else "sampled"
    source = f" filename={filename}." if filename else "."
    return (
        f"The following image parts are {mode} JPEG frames sampled from one video,"
        f"{source} Original sampled_frame_count={len(split_result.frames)}, original video frame_count={split_result.frame_count}, "
        f"duration_ms={split_result.duration_ms}. Use them together as a temporal sequence."
    )


def _safe_source_label(value: str) -> str:
    return str(value or "")[:120]


def _estimate_messages_chars(messages: list[dict[str, Any]]) -> int:
    return sum(_estimate_content_chars(message.get("content", "")) for message in messages)


def _estimate_content_chars(content) -> int:
    if isinstance(content, str):
        return len(content)
    if not isinstance(content, list):
        return len(str(content))
    total = 0
    for item in content:
        if not isinstance(item, dict):
            total += len(str(item))
        elif item.get("type") == "text":
            total += len(str(item.get("text", "")))
        elif item.get("type") == "image_url":
            total += len(str(item.get("image_url", {}).get("url", "")))
    return total


def _explicit_cache_enabled(model: str) -> tuple[bool, str]:
    setting = os.getenv(CACHE_ENABLED_ENV, "").strip().lower()
    if setting in {"1", "true", "yes", "on"}:
        return True, "forced_by_env"
    if setting in {"0", "false", "no", "off"}:
        return False, "disabled_by_env"
    if _is_gemma_model(model):
        return False, "gemma_model_does_not_support_explicit_cache"
    return True, "auto"


def _is_gemma_model(model: str) -> bool:
    normalized = str(model or "").strip().lower()
    if normalized.startswith("models/"):
        normalized = normalized.removeprefix("models/")
    return normalized.startswith("gemma-")


def _is_explicit_cache_unsupported_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return (
        "createcachedcontent" in message
        or "cached content" in message
        or "supported methods" in message
        or ("not found" in message and "model" in message)
    )


def _is_retryable_api_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return (
        "500" in message
        or "internal" in message
        or "502" in message
        or "bad gateway" in message
        or "503" in message
        or "unavailable" in message
        or "504" in message
        or "gateway timeout" in message
        or "429" in message
        or "rate limit" in message
        or "high demand" in message
        or "temporarily" in message
        or "timeout" in message
    )
