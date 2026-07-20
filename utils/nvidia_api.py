from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from numbers import Real
from typing import Any

import requests

from utils.ai_api_logging import log_ai_api_event
from utils.chat_client import ChatAPIError, ChatClient, ChatClientConfigError, ChatResponse
from utils.nvidia_assets import NvidiaAssetConfig, NvidiaAssetManager
from utils.openai_compatible_api import (
    OpenAIMessageAdapter,
    REQUEST_TIMEOUT,
    connection_error,
    content_text,
    count_image_parts,
    estimate_messages_chars,
    extract_response_texts,
    load_success_payload,
    log_chat_error,
    log_chat_response,
    validated_base_url,
)

DEFAULT_NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
DEFAULT_MAX_POLL_ATTEMPTS = 36
POLL_SECONDS = "5"
logger = logging.getLogger("discord.utils.nvidia_api")


class NvidiaMessageStrategy(str, Enum):
    PRESERVE = "preserve"
    USER_PREFIX = "user_prefix"


@dataclass(frozen=True)
class NvidiaRequestOptions:
    max_tokens: int | None = None
    top_p: float | None = None
    seed: int | None = None
    enable_thinking: bool | None = None

    def __post_init__(self):
        if self.max_tokens is not None and (
            isinstance(self.max_tokens, bool) or not isinstance(self.max_tokens, int) or self.max_tokens <= 0
        ):
            raise ChatClientConfigError("NVIDIA_MAX_TOKENS 必須是正整數")
        if self.top_p is not None and (
            isinstance(self.top_p, bool) or not isinstance(self.top_p, Real) or not 0 < float(self.top_p) <= 1
        ):
            raise ChatClientConfigError("NVIDIA_TOP_P 必須大於 0 且小於等於 1")
        if self.seed is not None and (isinstance(self.seed, bool) or not isinstance(self.seed, int)):
            raise ChatClientConfigError("NVIDIA_SEED 必須是整數")
        if self.enable_thinking is not None and not isinstance(self.enable_thinking, bool):
            raise ChatClientConfigError("NVIDIA_ENABLE_THINKING 必須是布林值")


@dataclass(frozen=True)
class NvidiaChatConfig:
    api_key: str
    model: str
    base_url: str = DEFAULT_NVIDIA_BASE_URL
    message_strategy: NvidiaMessageStrategy = NvidiaMessageStrategy.PRESERVE
    request_options: NvidiaRequestOptions = field(default_factory=NvidiaRequestOptions)
    asset_config: NvidiaAssetConfig = field(default_factory=NvidiaAssetConfig)

    def __post_init__(self):
        api_key = str(self.api_key or "").strip()
        model = str(self.model or "").strip()
        if not api_key:
            raise ChatClientConfigError("缺少 NVIDIA_API_KEY")
        if not model:
            raise ChatClientConfigError("缺少 NVIDIA_MODEL")
        try:
            strategy = (
                self.message_strategy
                if isinstance(self.message_strategy, NvidiaMessageStrategy)
                else NvidiaMessageStrategy(str(self.message_strategy).strip().lower())
            )
        except ValueError as exc:
            raise ChatClientConfigError("NVIDIA_MESSAGE_STRATEGY 只支援 preserve 或 user_prefix") from exc
        if not isinstance(self.request_options, NvidiaRequestOptions):
            raise ChatClientConfigError("NVIDIA request options 設定格式錯誤")
        if not isinstance(self.asset_config, NvidiaAssetConfig):
            raise ChatClientConfigError("NVIDIA asset 設定格式錯誤")
        object.__setattr__(self, "api_key", api_key)
        object.__setattr__(self, "model", model)
        object.__setattr__(self, "base_url", validated_base_url(self.base_url))
        object.__setattr__(self, "message_strategy", strategy)


class NvidiaMessageAdapter:
    def __init__(self, strategy: NvidiaMessageStrategy):
        try:
            self.strategy = strategy if isinstance(strategy, NvidiaMessageStrategy) else NvidiaMessageStrategy(strategy)
        except ValueError as exc:
            raise ChatClientConfigError("NVIDIA_MESSAGE_STRATEGY 只支援 preserve 或 user_prefix") from exc
        self.openai_adapter = OpenAIMessageAdapter("nvidia")

    def adapt(self, messages: list[dict[str, Any]]) -> list[dict]:
        converted = self.openai_adapter.adapt(messages)
        if self.strategy is NvidiaMessageStrategy.PRESERVE:
            return converted
        system_texts = []
        remaining = []
        for message in converted:
            if message["role"] == "system":
                text = content_text(message["content"]).strip()
                if text:
                    system_texts.append(text)
                continue
            remaining.append(message)
        if not system_texts:
            return remaining
        protocol = "<application_protocol>\n" + "\n\n".join(system_texts) + "\n</application_protocol>"
        if remaining and remaining[0]["role"] == "user":
            remaining[0]["content"] = _prefix_content(remaining[0]["content"], protocol)
        else:
            remaining.insert(0, {"role": "user", "content": protocol})
        return remaining


class NvidiaRequestBuilder:
    def __init__(self, config: NvidiaChatConfig):
        self.config = config

    def build(self, messages: list[dict], temperature: float) -> tuple[dict, dict]:
        payload = {
            "model": self.config.model,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }
        options = self.config.request_options
        if options.max_tokens is not None:
            payload["max_tokens"] = options.max_tokens
        if options.top_p is not None:
            payload["top_p"] = float(options.top_p)
        if options.seed is not None:
            payload["seed"] = options.seed
        if options.enable_thinking is not None:
            payload["chat_template_kwargs"] = {"enable_thinking": options.enable_thinking}
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config.api_key}",
        }
        return payload, headers


class NvidiaTransport:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        session=None,
        timeout=REQUEST_TIMEOUT,
        max_poll_attempts: int = DEFAULT_MAX_POLL_ATTEMPTS,
    ):
        self.base_url = validated_base_url(base_url)
        self.api_key = str(api_key or "").strip()
        self.session = session or requests
        self.timeout = timeout
        self.max_poll_attempts = max_poll_attempts

    def send(self, endpoint: str, headers: dict, payload: dict) -> dict:
        deadline = time.monotonic() + _read_timeout_seconds(self.timeout)
        response = None
        try:
            response = self.session.post(endpoint, headers=headers, json=payload, timeout=self.timeout)
            if int(getattr(response, "status_code", 0) or 0) != 202:
                return load_success_payload(response, "nvidia")
            request_id = _request_id(response)
            if not request_id:
                raise ChatAPIError("NVIDIA API pending 回應缺少 request ID", provider="nvidia")
            logger.info("nvidia.request_pending request_id_present=true")
        except requests.RequestException as exc:
            raise connection_error("nvidia") from exc
        finally:
            if response is not None:
                response.close()
        return self._poll(request_id, deadline)

    def _poll(self, request_id: str, deadline: float) -> dict:
        poll_url = f"{self.base_url}/status/{request_id}"
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "NVCF-POLL-SECONDS": POLL_SECONDS,
        }
        for attempt in range(1, self.max_poll_attempts + 1):
            remaining_seconds = deadline - time.monotonic()
            if remaining_seconds <= 0:
                break
            response = None
            try:
                response = self.session.get(
                    poll_url,
                    headers=headers,
                    timeout=_bounded_timeout(self.timeout, remaining_seconds),
                )
                status_code = int(getattr(response, "status_code", 0) or 0)
                if status_code != 202:
                    logger.info(
                        "nvidia.poll_complete attempts=%s status_code=%s",
                        attempt,
                        status_code,
                    )
                    return load_success_payload(response, "nvidia")
            except requests.RequestException as exc:
                raise connection_error("nvidia") from exc
            finally:
                if response is not None:
                    response.close()
        raise ChatAPIError("NVIDIA API pending 輪詢逾時", provider="nvidia", retryable=True)


class NvidiaChatClient(ChatClient):
    provider_name = "nvidia"

    def __init__(
        self,
        config: NvidiaChatConfig,
        *,
        message_adapter: NvidiaMessageAdapter | None = None,
        request_builder: NvidiaRequestBuilder | None = None,
        transport: NvidiaTransport | None = None,
        asset_manager: NvidiaAssetManager | None = None,
    ):
        self.config = config
        self.api_key = config.api_key
        self.model = config.model
        self.base_url = config.base_url
        self.message_adapter = message_adapter or NvidiaMessageAdapter(config.message_strategy)
        self.request_builder = request_builder or NvidiaRequestBuilder(config)
        self.transport = transport or NvidiaTransport(config.base_url, config.api_key)
        self.asset_manager = asset_manager or NvidiaAssetManager(config.asset_config, config.api_key)

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float = 0.7,
        persona_key: str | None = None,
    ) -> ChatResponse:
        del persona_key
        started_at = time.monotonic()
        prepared = None
        try:
            adapted = self.message_adapter.adapt(messages)
            prepared = self.asset_manager.prepare(adapted)
            payload, headers = self.request_builder.build(prepared.messages, temperature)
            asset_header = self.asset_manager.reference_header(prepared.asset_ids)
            if asset_header:
                headers["NVCF-INPUT-ASSET-REFERENCES"] = asset_header
            endpoint = f"{self.base_url}/chat/completions"
            self._log_request(messages, prepared, temperature, endpoint)
            response_payload = self.transport.send(endpoint, headers, payload)
            visible_content, thinking_content = extract_response_texts(response_payload, self.provider_name)
            log_chat_response(
                self.provider_name,
                self.model,
                started_at,
                response_payload,
                visible_content,
                thinking_content,
            )
            return ChatResponse(
                raw_content=visible_content,
                visible_content=visible_content,
                thinking_content=thinking_content,
            )
        except ChatAPIError as exc:
            log_chat_error(self.provider_name, self.model, started_at, exc)
            raise
        finally:
            if prepared is not None:
                try:
                    self.asset_manager.cleanup(prepared.asset_ids)
                except Exception as exc:
                    logger.warning("nvidia.asset_cleanup_failed error_type=%s", type(exc).__name__)

    def _log_request(self, original_messages, prepared, temperature: float, endpoint: str) -> None:
        options = self.config.request_options
        log_ai_api_event(
            "request",
            provider=self.provider_name,
            operation="chat_completions",
            model=self.model,
            request_body={
                "message_count": len(prepared.messages),
                "message_strategy": self.config.message_strategy.value,
                "temperature": temperature,
                "max_tokens": options.max_tokens,
                "top_p": options.top_p,
                "seed_configured": options.seed is not None,
                "thinking_configured": options.enable_thinking is not None,
                "estimated_chars": estimate_messages_chars(original_messages),
                "image_part_count": count_image_parts(prepared.messages),
                "asset_count": len(prepared.asset_ids),
                "asset_bytes": prepared.uploaded_bytes,
            },
            request_meta={"url": endpoint},
        )


def _prefix_content(content, protocol: str):
    if isinstance(content, str):
        return f"{protocol}\n\n{content}" if content else protocol
    if isinstance(content, list):
        return [{"type": "text", "text": protocol}, *content]
    return f"{protocol}\n\n{content}"


def _request_id(response) -> str:
    headers = getattr(response, "headers", {}) or {}
    request_id = str(headers.get("NVCF-REQID") or headers.get("nvcf-reqid") or "").strip()
    if request_id:
        return request_id
    try:
        payload = response.json()
    except (TypeError, ValueError):
        return ""
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("requestId") or payload.get("request_id") or "").strip()


def _read_timeout_seconds(timeout) -> float:
    if isinstance(timeout, tuple):
        return max(0.001, float(timeout[-1]))
    return max(0.001, float(timeout))


def _bounded_timeout(timeout, remaining_seconds: float):
    if not isinstance(timeout, tuple):
        return min(float(timeout), remaining_seconds)
    connect_timeout = min(float(timeout[0]), remaining_seconds)
    return connect_timeout, remaining_seconds
