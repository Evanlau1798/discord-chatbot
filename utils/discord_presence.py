from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager


@asynccontextmanager
async def keep_typing(channel, interval: float = 8.0):
    trigger_typing = getattr(channel, "trigger_typing", None)
    if not callable(trigger_typing):
        yield
        return

    await trigger_typing()

    async def loop():
        try:
            while True:
                await asyncio.sleep(interval)
                await trigger_typing()
        except asyncio.CancelledError:
            return

    task = asyncio.create_task(loop())
    try:
        yield
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
