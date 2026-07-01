from __future__ import annotations

import asyncio
import unittest

from utils.async_cooldown_queue import AsyncCooldownQueue


class AsyncCooldownQueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_queue_runs_one_operation_at_a_time(self):
        queue = AsyncCooldownQueue()
        active = 0
        max_seen = 0

        async def operation():
            nonlocal active, max_seen
            active += 1
            max_seen = max(max_seen, active)
            await asyncio.sleep(0.01)
            active -= 1
            return "ok"

        results = await asyncio.gather(*(queue.run(operation, cooldown_seconds=0) for _ in range(5)))

        self.assertEqual(results, ["ok"] * 5)
        self.assertEqual(max_seen, 1)

    async def test_queue_applies_cooldown_between_operations(self):
        now = 100.0
        sleeps = []

        def clock():
            return now

        async def sleep(seconds: float):
            nonlocal now
            sleeps.append(seconds)
            now += seconds

        queue = AsyncCooldownQueue(clock=clock, sleep=sleep)

        await queue.run(lambda: "first", cooldown_seconds=1.0)
        await queue.run(lambda: "second", cooldown_seconds=1.0)

        self.assertEqual(sleeps, [1.0])

    async def test_queue_skips_cooldown_after_idle_time(self):
        now = 100.0
        sleeps = []

        def clock():
            return now

        async def sleep(seconds: float):
            sleeps.append(seconds)

        queue = AsyncCooldownQueue(clock=clock, sleep=sleep)

        await queue.run(lambda: "first", cooldown_seconds=1.0)
        now = 102.0
        await queue.run(lambda: "second", cooldown_seconds=1.0)

        self.assertEqual(sleeps, [])


if __name__ == "__main__":
    unittest.main()
