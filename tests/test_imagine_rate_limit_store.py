from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from utils.imagine_rate_limit_store import (
    ImagineRateLimitConfig,
    ImagineRateLimiter,
    format_imagine_rate_limit_notice,
    get_imagine_rate_limit_config,
)


class ImagineRateLimitConfigTests(unittest.TestCase):
    def test_config_defaults_to_disabled_three_uses(self):
        config = get_imagine_rate_limit_config({})

        self.assertFalse(config.enabled)
        self.assertEqual(config.daily_limit, 3)
        self.assertEqual(config.whitelist_ids, frozenset())

    def test_config_reads_env_and_whitelist(self):
        config = get_imagine_rate_limit_config({
            "AI_IMAGINE_RATE_LIMIT_ENABLED": "0",
            "AI_IMAGINE_DAILY_LIMIT": "5",
            "AI_IMAGINE_RATE_LIMIT_WHITELIST": "123, 456, abc",
        })

        self.assertFalse(config.enabled)
        self.assertEqual(config.daily_limit, 5)
        self.assertEqual(config.whitelist_ids, frozenset({"123", "456"}))

    def test_invalid_daily_limit_falls_back_to_three(self):
        self.assertEqual(
            get_imagine_rate_limit_config({"AI_IMAGINE_DAILY_LIMIT": "0"}).daily_limit,
            3,
        )
        self.assertEqual(
            get_imagine_rate_limit_config({"AI_IMAGINE_DAILY_LIMIT": "abc"}).daily_limit,
            3,
        )


class ImagineRateLimiterTests(unittest.TestCase):
    def test_rolling_window_starts_on_first_success_and_resets_after_full_24_hours(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            clock = FakeClock(1000)
            limiter = _limiter(Path(temp_dir) / "quota.db", clock)

            initial = limiter.check("42")
            self.assertTrue(initial.allowed)
            self.assertEqual(initial.remaining, 3)
            self.assertIsNone(initial.reset_at)

            self.assertEqual(limiter.record_success("42").remaining, 2)
            self.assertEqual(limiter.record_success("42").remaining, 1)
            self.assertEqual(limiter.record_success("42").remaining, 0)

            limited = limiter.check("42")
            self.assertFalse(limited.allowed)
            self.assertEqual(limited.reset_at, 1000 + 24 * 60 * 60)

            clock.now = limited.reset_at - 1
            self.assertFalse(limiter.check("42").allowed)

            clock.now = limited.reset_at
            reset = limiter.check("42")
            self.assertTrue(reset.allowed)
            self.assertEqual(reset.remaining, 3)
            self.assertIsNone(reset.reset_at)

            consumed = limiter.record_success("42")
            self.assertEqual(consumed.reset_at, clock.now + 24 * 60 * 60)

    def test_disabled_and_whitelisted_users_are_unlimited_and_not_recorded(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            disabled = _limiter(
                Path(temp_dir) / "disabled.db",
                FakeClock(1000),
                ImagineRateLimitConfig(enabled=False, daily_limit=3, whitelist_ids=frozenset()),
            )
            whitelisted = _limiter(
                Path(temp_dir) / "whitelist.db",
                FakeClock(1000),
                ImagineRateLimitConfig(enabled=True, daily_limit=3, whitelist_ids=frozenset({"42"})),
            )

            self.assertTrue(disabled.record_success("42").unlimited)
            self.assertEqual(disabled.raw_row_count(), 0)
            self.assertTrue(whitelisted.record_success("42").unlimited)
            self.assertEqual(whitelisted.raw_row_count(), 0)

    def test_reset_all_clears_existing_rows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            limiter = _limiter(Path(temp_dir) / "quota.db", FakeClock(1000))
            limiter.record_success("1")
            limiter.record_success("2")

            self.assertEqual(limiter.reset_all(), 2)
            self.assertEqual(limiter.raw_row_count(), 0)

    def test_rate_limit_notice_uses_discord_relative_timestamp(self):
        self.assertEqual(
            format_imagine_rate_limit_notice(12345),
            "-# 您已達到每日繪圖數量限制，請<t:12345:R>後再試一次",
        )


class FakeClock:
    def __init__(self, now: int):
        self.now = now

    def __call__(self) -> float:
        return float(self.now)


def _limiter(path: Path, clock: FakeClock, config: ImagineRateLimitConfig | None = None) -> ImagineRateLimiter:
    return ImagineRateLimiter(
        path=path,
        config=config or ImagineRateLimitConfig(enabled=True, daily_limit=3, whitelist_ids=frozenset()),
        clock=clock,
    )


if __name__ == "__main__":
    unittest.main()
