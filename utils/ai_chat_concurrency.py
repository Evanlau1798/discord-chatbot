from __future__ import annotations

import asyncio
import inspect
import os
import weakref
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Optional, Union

AI_CHAT_MAX_PARALLEL_REQUESTS_ENV = "AI_CHAT_MAX_PARALLEL_REQUESTS"
DEFAULT_AI_CHAT_MAX_PARALLEL_REQUESTS = 3

QueueUpdateCallback = Callable[["QueueStatusUpdate"], Union[Awaitable[None], None]]


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


@dataclass(frozen=True)
class QueueStatusUpdate:
    status: str
    queue_ahead: int


@dataclass
class _LimiterState:
    condition: asyncio.Condition
    active_count: int
    waiters: deque[object]


class AiChatRequestLimiter:
    def __init__(self, max_parallel_requests: int | None = None):
        self.max_parallel_requests = max_parallel_requests or get_ai_chat_max_parallel_requests()
        self._states: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, _LimiterState] = weakref.WeakKeyDictionary()

    async def __aenter__(self):
        await self._acquire(None)
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        await self._release()
        return False

    def with_queue_updates(self, on_update: QueueUpdateCallback):
        return _LimiterLease(self, on_update)

    async def _acquire(self, on_update: Optional[QueueUpdateCallback]) -> None:
        state = self._state_for_current_loop()
        async with state.condition:
            if state.active_count < self.max_parallel_requests and not state.waiters:
                state.active_count += 1
                return
            waiter = object()
            state.waiters.append(waiter)
            state.condition.notify_all()
        await self._wait_for_turn(state, waiter, on_update)

    async def _wait_for_turn(
        self,
        state: _LimiterState,
        waiter: object,
        on_update: Optional[QueueUpdateCallback],
    ) -> None:
        last_update: tuple[str, int] | None = None
        queued = True
        try:
            while True:
                update = None
                async with state.condition:
                    queue_ahead = _queue_ahead(state.waiters, waiter)
                    if queue_ahead is None:
                        raise RuntimeError("AI chat request queue state was lost")
                    if queue_ahead == 0 and state.active_count < self.max_parallel_requests:
                        state.waiters.popleft()
                        state.active_count += 1
                        state.condition.notify_all()
                        break
                    status = "next" if queue_ahead == 0 else "waiting"
                    candidate = (status, queue_ahead)
                    if candidate != last_update:
                        update = QueueStatusUpdate(status=status, queue_ahead=queue_ahead)
                        last_update = candidate
                    else:
                        await state.condition.wait()
                        continue
                if update is not None:
                    await _notify(on_update, update)
            queued = False
            await _notify(on_update, QueueStatusUpdate(status="running", queue_ahead=0))
        finally:
            if queued:
                async with state.condition:
                    try:
                        state.waiters.remove(waiter)
                    except ValueError:
                        pass
                    state.condition.notify_all()

    async def _release(self) -> None:
        state = self._state_for_current_loop()
        async with state.condition:
            state.active_count = max(0, state.active_count - 1)
            state.condition.notify_all()

    def _state_for_current_loop(self) -> _LimiterState:
        loop = asyncio.get_running_loop()
        state = self._states.get(loop)
        if state is None:
            state = _LimiterState(condition=asyncio.Condition(), active_count=0, waiters=deque())
            self._states[loop] = state
        return state


class _LimiterLease:
    def __init__(self, limiter: AiChatRequestLimiter, on_update: QueueUpdateCallback):
        self._limiter = limiter
        self._on_update = on_update

    async def __aenter__(self):
        await self._limiter._acquire(self._on_update)
        return self._limiter

    async def __aexit__(self, exc_type, exc, traceback):
        await self._limiter._release()
        return False


def _queue_ahead(waiters: deque[object], waiter: object) -> int | None:
    for index, current in enumerate(waiters):
        if current is waiter:
            return index
    return None


async def _notify(callback: Optional[QueueUpdateCallback], update: QueueStatusUpdate) -> None:
    if callback is None:
        return
    try:
        result = callback(update)
        if inspect.isawaitable(result):
            await result
    except Exception:
        return
