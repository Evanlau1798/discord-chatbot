from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping

AI_CHAT_MAX_PARALLEL_REQUESTS_ENV = "AI_CHAT_MAX_PARALLEL_REQUESTS"
DEFAULT_AI_CHAT_MAX_PARALLEL_REQUESTS = 3


def get_ai_chat_max_parallel_requests(env: Mapping[str, str] | None = None) -> int:
    values = os.environ if env is None else env
    raw_value = str(values.get(AI_CHAT_MAX_PARALLEL_REQUESTS_ENV, "")).strip()
    if not raw_value:
        return DEFAULT_AI_CHAT_MAX_PARALLEL_REQUESTS
    try:
        value = int(raw_value)
    except ValueError:
        return DEFAULT_AI_CHAT_MAX_PARALLEL_REQUESTS
    if value < 1:
        return DEFAULT_AI_CHAT_MAX_PARALLEL_REQUESTS
    return value


class AiChatRequestLimiter:
    def __init__(self, max_parallel_requests: int | None = None):
        self.max_parallel_requests = max_parallel_requests or get_ai_chat_max_parallel_requests()
        self._semaphore = asyncio.Semaphore(self.max_parallel_requests)

    async def __aenter__(self):
        await self._semaphore.acquire()
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        self._semaphore.release()
        return False
