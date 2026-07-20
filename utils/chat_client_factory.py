from __future__ import annotations

import os
from collections.abc import Mapping

from utils.chat_client import ChatClient, ChatClientConfigError
from utils.gemini_api import DEFAULT_GEMINI_MODEL, GeminiChatClient
from utils.nvidia_api import (
    DEFAULT_NVIDIA_BASE_URL,
    NvidiaChatClient,
    NvidiaChatConfig,
    NvidiaMessageStrategy,
    NvidiaRequestOptions,
)
from utils.nvidia_assets import (
    DEFAULT_INLINE_MEDIA_MAX_BYTES,
    DEFAULT_NVIDIA_ASSET_BASE_URL,
    NvidiaAssetConfig,
    NvidiaAssetMode,
)
from utils.openai_compatible_api import OpenAICompatibleChatClient

DEFAULT_CHAT_PROVIDER = "gemini"


def create_chat_client(env: Mapping[str, str] | None = None) -> ChatClient:
    values = os.environ if env is None else env
    provider = str(values.get("AI_CHAT_PROVIDER") or DEFAULT_CHAT_PROVIDER).strip().lower().replace("-", "_")
    if provider == "gemini":
        return GeminiChatClient(
            api_key=_first_value(values, "GEMINI_API_KEY", "GEMINIAPIKEY"),
            model=_value(values, "GEMINI_MODEL") or DEFAULT_GEMINI_MODEL,
        )
    if provider == "nvidia":
        config = NvidiaChatConfig(
            api_key=_required(values, "NVIDIA_API_KEY"),
            model=_required(values, "NVIDIA_MODEL"),
            base_url=_value(values, "NVIDIA_BASE_URL") or DEFAULT_NVIDIA_BASE_URL,
            message_strategy=_enum_value(
                values,
                "NVIDIA_MESSAGE_STRATEGY",
                NvidiaMessageStrategy,
                NvidiaMessageStrategy.PRESERVE,
            ),
            request_options=NvidiaRequestOptions(
                max_tokens=_optional_int(values, "NVIDIA_MAX_TOKENS"),
                top_p=_optional_float(values, "NVIDIA_TOP_P"),
                seed=_optional_int(values, "NVIDIA_SEED"),
                enable_thinking=_optional_bool(values, "NVIDIA_ENABLE_THINKING"),
            ),
            asset_config=NvidiaAssetConfig(
                mode=_enum_value(values, "NVIDIA_ASSET_MODE", NvidiaAssetMode, NvidiaAssetMode.INLINE),
                inline_media_max_bytes=_optional_int_or_default(
                    values, "NVIDIA_INLINE_MEDIA_MAX_BYTES", DEFAULT_INLINE_MEDIA_MAX_BYTES
                ),
                asset_base_url=_value(values, "NVIDIA_ASSET_BASE_URL") or DEFAULT_NVIDIA_ASSET_BASE_URL,
            ),
        )
        return NvidiaChatClient(config)
    if provider == "openai_compatible":
        return OpenAICompatibleChatClient(
            api_key=_value(values, "OPENAI_COMPAT_API_KEY"),
            model=_required(values, "OPENAI_COMPAT_MODEL"),
            base_url=_required(values, "OPENAI_COMPAT_BASE_URL"),
        )
    raise ChatClientConfigError(
        f"不支援的 AI_CHAT_PROVIDER: {provider}; 可用值為 gemini、nvidia、openai_compatible"
    )


def _required(values: Mapping[str, str], key: str) -> str:
    value = _value(values, key)
    if not value:
        raise ChatClientConfigError(f"缺少 {key}")
    return value


def _first_value(values: Mapping[str, str], *keys: str) -> str:
    for key in keys:
        value = _value(values, key)
        if value:
            return value
    raise ChatClientConfigError(f"缺少 {' 或 '.join(keys)}")


def _value(values: Mapping[str, str], key: str) -> str:
    return str(values.get(key) or "").strip()


def _optional_int(values: Mapping[str, str], key: str) -> int | None:
    raw = _value(values, key)
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise ChatClientConfigError(f"{key} 必須是整數") from exc


def _optional_int_or_default(values: Mapping[str, str], key: str, default: int) -> int:
    value = _optional_int(values, key)
    return default if value is None else value


def _optional_float(values: Mapping[str, str], key: str) -> float | None:
    raw = _value(values, key)
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError as exc:
        raise ChatClientConfigError(f"{key} 必須是數字") from exc


def _optional_bool(values: Mapping[str, str], key: str) -> bool | None:
    raw = _value(values, key).lower()
    if not raw:
        return None
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise ChatClientConfigError(f"{key} 必須是布林值")


def _enum_value(values, key: str, enum_type, default):
    raw = _value(values, key).lower()
    if not raw:
        return default
    try:
        return enum_type(raw)
    except ValueError as exc:
        allowed = "、".join(item.value for item in enum_type)
        raise ChatClientConfigError(f"{key} 只支援 {allowed}") from exc
