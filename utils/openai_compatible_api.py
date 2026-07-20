from __future__ import annotations

import base64
import logging
import time
from typing import Any
from urllib.parse import urlparse

import requests

from utils.ai_api_logging import log_ai_api_event
from utils.chat_client import ChatAPIError, ChatClient, ChatClientConfigError, ChatResponse
from utils.chat_media import prepare_image_bytes, prepare_video_bytes

REQUEST_TIMEOUT = (10, 180)
logger = logging.getLogger("discord.utils.openai_compatible_api")


class OpenAIMessageAdapter:
    def __init__(self, provider: str):
        self.provider = str(provider or "openai_compatible")

    def adapt(self, messages: list[dict[str, Any]]) -> list[dict]:
        if not messages:
            raise ChatAPIError(f"{self.provider} 請求不可為空", provider=self.provider)
        converted = []
        for message in messages:
            role = str(message.get("role") or "").strip()
            if role not in {"system", "user", "assistant"}:
                raise ChatAPIError(f"{self.provider} 不支援的訊息角色: {role}", provider=self.provider)
            converted.append({"role": role, "content": self.convert_content(message.get("content"))})
        if converted[-1]["role"] != "user":
            raise ChatAPIError(f"{self.provider} 請求最後一則訊息必須是 user", provider=self.provider)
        return converted

    def convert_content(self, content):
        if isinstance(content, str):
            return content
        if not isinstance(content, list):
            return str(content)
        parts = []
        for item in content:
            if not isinstance(item, dict):
                parts.append({"type": "text", "text": str(item)})
                continue
            part_type = item.get("type")
            if part_type == "text":
                parts.append({"type": "text", "text": str(item.get("text") or "")})
            elif part_type == "image_url":
                url = str(item.get("image_url", {}).get("url") or "").strip()
                if url:
                    parts.append({"type": "image_url", "image_url": {"url": url}})
            elif part_type == "image_bytes":
                parts.extend(openai_media_parts(prepare_image_bytes(item.get("image_bytes", {}))))
            elif part_type == "video_bytes":
                parts.extend(openai_media_parts(prepare_video_bytes(item.get("video_bytes", {}))))
        return parts or [{"type": "text", "text": ""}]


class OpenAICompatibleChatClient(ChatClient):
    provider_name = "openai_compatible"

    def __init__(self, *, base_url: str, model: str, api_key: str = "", provider_name: str | None = None):
        self.base_url = validated_base_url(base_url)
        self.model = str(model or "").strip()
        self.api_key = str(api_key or "").strip()
        self.provider_name = str(provider_name or self.provider_name).strip()
        if not self.model:
            raise ChatClientConfigError(f"缺少 {self.provider_name} 模型名稱")
        self.message_adapter = OpenAIMessageAdapter(self.provider_name)

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float = 0.7,
        persona_key: str | None = None,
    ) -> ChatResponse:
        del persona_key
        request_messages = self._convert_messages(messages)
        endpoint = f"{self.base_url}/chat/completions"
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload = {
            "model": self.model,
            "messages": request_messages,
            "temperature": temperature,
            "stream": False,
        }
        started_at = time.monotonic()
        log_ai_api_event(
            "request",
            provider=self.provider_name,
            operation="chat_completions",
            model=self.model,
            request_body={
                "message_count": len(request_messages),
                "temperature": temperature,
                "estimated_chars": estimate_messages_chars(messages),
                "image_part_count": count_image_parts(request_messages),
            },
            request_meta={"url": endpoint},
        )
        try:
            response = requests.post(endpoint, headers=headers, json=payload, timeout=REQUEST_TIMEOUT)
            response_payload = load_success_payload(response, self.provider_name)
        except ChatAPIError as exc:
            self._log_error(started_at, exc)
            raise
        except requests.RequestException as exc:
            wrapped = connection_error(self.provider_name)
            self._log_error(started_at, wrapped)
            raise wrapped from exc
        finally:
            if "response" in locals():
                response.close()
        try:
            visible_content, thinking_content = extract_response_texts(response_payload, self.provider_name)
        except ChatAPIError as exc:
            self._log_error(started_at, exc)
            raise
        log_chat_response(self.provider_name, self.model, started_at, response_payload, visible_content, thinking_content)
        return ChatResponse(
            raw_content=visible_content,
            visible_content=visible_content,
            thinking_content=thinking_content,
        )

    def _convert_messages(self, messages: list[dict[str, Any]]) -> list[dict]:
        return self.message_adapter.adapt(messages)

    def _convert_content(self, content):
        return self.message_adapter.convert_content(content)

    def _log_error(self, started_at: float, exc: ChatAPIError) -> None:
        log_chat_error(self.provider_name, self.model, started_at, exc)


def openai_media_parts(parts: list[dict]) -> list[dict]:
    converted = []
    for part in parts:
        if part.get("type") == "text":
            converted.append({"type": "text", "text": str(part.get("text") or "")})
            continue
        payload = part.get("image_bytes", {})
        data = payload.get("data")
        if not isinstance(data, (bytes, bytearray)) or not data:
            continue
        mime_type = str(payload.get("mime_type") or "application/octet-stream").split(";", 1)[0].strip()
        encoded = base64.b64encode(bytes(data)).decode("ascii")
        converted.append({"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{encoded}"}})
    return converted


def extract_response_texts(payload: dict, provider: str) -> tuple[str, str]:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ChatAPIError(f"{provider} API 未返回 choices", provider=provider)
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise ChatAPIError(f"{provider} API 未返回 assistant message", provider=provider)
    visible_content = content_text(message.get("content"))
    thinking_content = content_text(message.get("reasoning_content"))
    if not visible_content:
        raise ChatAPIError(f"{provider} API 未返回可見文字", provider=provider)
    return visible_content, thinking_content


def content_text(content) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "".join(str(item.get("text") or "") for item in content if isinstance(item, dict))


def load_success_payload(response, provider: str) -> dict:
    status_code = int(getattr(response, "status_code", 0) or 0)
    if status_code != 200:
        raise http_error(provider, status_code)
    try:
        payload = response.json()
    except (TypeError, ValueError) as exc:
        raise ChatAPIError(f"{provider} API 回應不是有效 JSON", provider=provider) from exc
    if not isinstance(payload, dict):
        raise ChatAPIError(f"{provider} API 回應格式錯誤", provider=provider)
    return payload


def http_error(provider: str, status_code: int) -> ChatAPIError:
    return ChatAPIError(
        f"{provider} API HTTP {status_code}",
        provider=provider,
        status_code=status_code,
        retryable=status_code in {429, 500, 502, 503, 504},
    )


def connection_error(provider: str) -> ChatAPIError:
    return ChatAPIError(f"{provider} API 連線失敗", provider=provider, retryable=True)


def validated_base_url(value: str) -> str:
    normalized = str(value or "").strip().rstrip("/")
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        raise ChatClientConfigError("OpenAI-compatible base URL 必須是有效的 HTTP(S) URL")
    if parsed.query or parsed.fragment:
        raise ChatClientConfigError("OpenAI-compatible base URL 不可包含 query 或 fragment")
    return normalized


def estimate_messages_chars(messages: list[dict[str, Any]]) -> int:
    total = 0
    for message in messages:
        content = message.get("content", "")
        if isinstance(content, str):
            total += len(content)
        elif isinstance(content, list):
            total += sum(len(str(item.get("text") or "")) for item in content if isinstance(item, dict))
    return total


def count_image_parts(messages: list[dict]) -> int:
    return sum(
        1
        for message in messages
        for item in (message.get("content") if isinstance(message.get("content"), list) else [])
        if isinstance(item, dict) and item.get("type") == "image_url"
    )


def log_chat_response(provider, model, started_at, payload, visible_content, thinking_content) -> None:
    choice = payload.get("choices", [{}])[0]
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    log_ai_api_event(
        "response",
        provider=provider,
        operation="chat_completions",
        model=model,
        elapsed_ms=round((time.monotonic() - started_at) * 1000, 3),
        response={
            "status_code": 200,
            "visible_len": len(visible_content),
            "thinking_len": len(thinking_content),
            "finish_reason": choice.get("finish_reason") if isinstance(choice, dict) else None,
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
        },
    )


def log_chat_error(provider: str, model: str, started_at: float, exc: ChatAPIError) -> None:
    log_ai_api_event(
        "error",
        provider=provider,
        operation="chat_completions",
        model=model,
        elapsed_ms=round((time.monotonic() - started_at) * 1000, 3),
        status_code=exc.status_code,
        retryable=exc.retryable,
        error_type=type(exc).__name__,
    )
