from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ChatResponse:
    raw_content: str
    visible_content: str
    thinking_content: str = ""
    delivery_mode: str = "complete"


class ChatClientConfigError(ValueError):
    """Raised when the selected chat provider is not configured correctly."""


class ChatAPIError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        provider: str = "",
        status_code: int | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.status_code = status_code
        self.retryable = retryable


class ChatClient(ABC):
    provider_name = "unknown"

    @abstractmethod
    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float = 0.7,
        persona_key: str | None = None,
    ) -> ChatResponse:
        raise NotImplementedError

    def refresh_persona_caches(self, prompts_by_key: dict[str, str]) -> dict[str, str]:
        return {}

    @staticmethod
    def is_retryable_error(exc: Exception) -> bool:
        return is_retryable_api_error(exc)


def is_retryable_api_error(exc: Exception) -> bool:
    if isinstance(exc, ChatAPIError):
        return exc.retryable
    status_code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    try:
        if int(status_code) in {429, 500, 502, 503, 504}:
            return True
    except (TypeError, ValueError):
        pass
    message = str(exc).lower()
    return any(
        marker in message
        for marker in (
            "500",
            "internal",
            "502",
            "bad gateway",
            "503",
            "unavailable",
            "504",
            "gateway timeout",
            "429",
            "rate limit",
            "high demand",
            "temporarily",
            "timeout",
            "timed out",
            "connection error",
        )
    )
