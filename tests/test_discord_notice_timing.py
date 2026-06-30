from __future__ import annotations

import unittest

from utils.discord_notice_timing import remaining_notice_delay, wait_for_min_notice_display


class DiscordNoticeTimingTests(unittest.IsolatedAsyncioTestCase):
    def test_remaining_notice_delay_waits_until_minimum(self):
        delay = remaining_notice_delay(sent_at=10.0, now=12.0, minimum_seconds=5.0)

        self.assertEqual(delay, 3.0)

    def test_remaining_notice_delay_does_not_wait_after_minimum(self):
        delay = remaining_notice_delay(sent_at=10.0, now=16.0, minimum_seconds=5.0)

        self.assertEqual(delay, 0.0)

    async def test_wait_for_min_notice_display_uses_remaining_delay(self):
        slept = []

        async def fake_sleep(delay: float) -> None:
            slept.append(delay)

        await wait_for_min_notice_display(10.0, 5.0, clock=lambda: 12.5, sleep=fake_sleep)

        self.assertEqual(slept, [2.5])

    async def test_wait_for_min_notice_display_skips_missing_timestamp(self):
        slept = []

        async def fake_sleep(delay: float) -> None:
            slept.append(delay)

        await wait_for_min_notice_display(None, 5.0, clock=lambda: 12.5, sleep=fake_sleep)

        self.assertEqual(slept, [])


if __name__ == "__main__":
    unittest.main()
