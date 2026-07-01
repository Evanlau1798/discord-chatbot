from __future__ import annotations

import asyncio
import inspect
import weakref
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")


class AsyncCooldownQueue:
    def __init__(
        self,
        *,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ):
        self._clock = clock
        self._sleep = sleep or asyncio.sleep
        self._locks: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock] = weakref.WeakKeyDictionary()
        self._last_finished_at: float | None = None

    async def run(self, operation: Callable[[], T | Awaitable[T]], *, cooldown_seconds: float) -> T:
        async with self._lock_for_current_loop():
            await self._wait_for_cooldown(cooldown_seconds)
            try:
                value = operation()
                if inspect.isawaitable(value):
                    return await value
                return value
            finally:
                self._last_finished_at = self._now()

    def reset(self) -> None:
        self._last_finished_at = None

    async def _wait_for_cooldown(self, cooldown_seconds: float) -> None:
        cooldown = max(0.0, float(cooldown_seconds or 0))
        if cooldown <= 0 or self._last_finished_at is None:
            return
        remaining = cooldown - max(0.0, self._now() - self._last_finished_at)
        if remaining > 0:
            await self._sleep(remaining)

    def _now(self) -> float:
        if self._clock is not None:
            return float(self._clock())
        return asyncio.get_running_loop().time()

    def _lock_for_current_loop(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        lock = self._locks.get(loop)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[loop] = lock
        return lock
