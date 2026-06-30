from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

MIN_BROWSER_NOTICE_DISPLAY_SECONDS = 5.0


def remaining_notice_delay(sent_at: float | None, now: float, minimum_seconds: float) -> float:
    if sent_at is None:
        return 0.0
    return max(0.0, minimum_seconds - (now - sent_at))


async def wait_for_min_notice_display(
    sent_at: float | None,
    minimum_seconds: float = MIN_BROWSER_NOTICE_DISPLAY_SECONDS,
    *,
    clock: Callable[[], float] | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    if sent_at is None:
        return
    now = clock() if clock is not None else asyncio.get_running_loop().time()
    delay = remaining_notice_delay(sent_at, now, minimum_seconds)
    if delay > 0:
        await sleep(delay)
