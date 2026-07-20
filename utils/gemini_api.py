from __future__ import annotations

import logging
import mimetypes
import os
import time
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
from utils.chat_client import (
    ChatAPIError,
    ChatClient,
    ChatClientConfigError,
    ChatResponse,
    is_retryable_api_error,
)
from utils.chat_media import prepare_image_bytes, prepare_image_data, prepare_video_bytes

DEFAULT_GEMINI_MODEL = "gemma-4-31b-it"
REQUEST_TIMEOUT_MS = 180_000
DEFAULT_CACHE_TTL = "31536000s"
PROJECT_CACHE_PREFIX = "discord-chatbot-persona:"
CACHE_ENABLED_ENV = "GEMINI_CACHE_ENABLED"
logger = logging.getLogger("discord.utils.gemini_api")


GeminiResponse = ChatResponse


class GeminiAPIError(ChatClientConfigError):
    pass


class GeminiChatClient(ChatClient):
    provider_name = "gemini"

    def __init__(self, api_key: str, model: str = DEFAULT_GEMINI_MODEL):
        if not api_key:
            raise GeminiAPIError("缺少 GEMINI_API_KEY 或 GEMINIAPIKEY")
        _ensure_genai_sdk()
        self.client = genai.Client(api_key=api_key)
        self.model = model
        self.persona_cache_names: dict[str, str] = {}

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float = 0.7,
        persona_key: str | None = None,
    ) -> GeminiResponse:
        cached_content = self.persona_cache_names.get(str(persona_key or ""))
        try:
            return self._complete_once(messages, temperature=temperature, cached_content=cached_content)
        except Exception as exc:
            if cached_content and not is_retryable_api_error(exc):
                logger.warning(
                    "gemini.cached_content_failed_fallback error_type=%s",
                    type(exc).__name__,
                )
                try:
                    return self._complete_once(messages, temperature=temperature, cached_content=None)
                except Exception as fallback_exc:
                    raise _chat_api_error(fallback_exc) from fallback_exc
            raise _chat_api_error(exc) from exc

    def _complete_once(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float,
        cached_content: str | None,
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
            self.persona_cache_names = {}
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
                    self.persona_cache_names = cache_names
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
        self.persona_cache_names = cache_names
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
                parts.extend(_genai_parts(prepare_image_bytes(item.get("image_bytes", {}))))
            elif item.get("type") == "video_bytes":
                parts.extend(_genai_parts(prepare_video_bytes(item.get("video_bytes", {}))))
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
        return _genai_parts(prepare_image_data(response.content, mime_type, source_label=url))

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


def _genai_parts(parts: list[dict]) -> list:
    converted = []
    for part in parts:
        if part.get("type") == "text":
            converted.append(genai_types.Part.from_text(text=str(part.get("text") or "")))
            continue
        payload = part.get("image_bytes", {})
        data = payload.get("data")
        if isinstance(data, (bytes, bytearray)) and data:
            mime_type = str(payload.get("mime_type") or "application/octet-stream")
            converted.append(genai_types.Part.from_bytes(data=bytes(data), mime_type=mime_type))
    return converted


def _chat_api_error(exc: Exception) -> ChatAPIError:
    if isinstance(exc, ChatAPIError):
        return exc
    return ChatAPIError(
        "Gemini API 請求失敗",
        provider="gemini",
        status_code=getattr(exc, "status_code", None) or getattr(exc, "code", None),
        retryable=is_retryable_api_error(exc),
    )


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
    return is_retryable_api_error(exc)
