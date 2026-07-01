from __future__ import annotations

import unittest
from unittest.mock import patch

from extensions.ImagineQuota import (
    ImagineQuotaResetAllView,
    build_imagine_quota_embed,
    is_imagine_quota_admin,
)
from utils.imagine_rate_limit_store import ImagineQuotaStatus


class ImagineQuotaCommandTests(unittest.TestCase):
    def test_build_quota_embed_shows_remaining_and_reset_time(self):
        embed = build_imagine_quota_embed(
            ImagineQuotaStatus(
                allowed=True,
                unlimited=False,
                limit=3,
                used_count=1,
                remaining=2,
                reset_at=12345,
            )
        )

        self.assertIn("剩餘 2 / 3 次", embed.description)
        self.assertIn("<t:12345:R>", embed.description)

    def test_build_quota_embed_shows_unlimited_status(self):
        embed = build_imagine_quota_embed(
            ImagineQuotaStatus(
                allowed=True,
                unlimited=True,
                limit=3,
                used_count=0,
                remaining=3,
                reset_at=None,
            )
        )

        self.assertIn("不套用繪圖使用限制", embed.description)


class ImagineQuotaResetAllViewTests(unittest.IsolatedAsyncioTestCase):
    def test_admin_user_id_is_read_from_env(self):
        self.assertFalse(is_imagine_quota_admin(540134212217602050, {}))
        self.assertTrue(is_imagine_quota_admin(540134212217602050, {"IMAGINE_QUOTA_ADMIN_USER_ID": "540134212217602050"}))
        self.assertFalse(is_imagine_quota_admin(1, {"IMAGINE_QUOTA_ADMIN_USER_ID": "540134212217602050"}))

    async def test_unauthorized_user_cannot_reset_all_quota(self):
        store = FakeLimiter()
        interaction = FakeInteraction(user_id=540134212217602050)
        view = ImagineQuotaResetAllView(store)

        with patch.dict("os.environ", {}, clear=True):
            await view.handle_reset_all(interaction)

        self.assertEqual(store.reset_calls, 0)
        self.assertEqual(interaction.response.messages[0]["content"], "你沒有權限重置繪圖額度。")
        self.assertTrue(interaction.response.messages[0]["ephemeral"])

    async def test_authorized_user_can_reset_all_quota(self):
        store = FakeLimiter(reset_count=5)
        interaction = FakeInteraction(user_id=540134212217602050)
        view = ImagineQuotaResetAllView(store)

        with patch.dict("os.environ", {"IMAGINE_QUOTA_ADMIN_USER_ID": "540134212217602050"}, clear=True):
            await view.handle_reset_all(interaction)

        self.assertEqual(store.reset_calls, 1)
        self.assertIn("已重置 5 筆繪圖額度紀錄", interaction.response.edits[0]["embed"].description)


class FakeLimiter:
    def __init__(self, reset_count: int = 0):
        self.reset_count = reset_count
        self.reset_calls = 0

    def reset_all(self):
        self.reset_calls += 1
        return self.reset_count


class FakeInteraction:
    def __init__(self, user_id: int):
        self.user = type("User", (), {"id": user_id})()
        self.response = FakeInteractionResponse()


class FakeInteractionResponse:
    def __init__(self):
        self.messages = []
        self.edits = []

    async def send_message(self, content=None, *, ephemeral=False, **kwargs):
        self.messages.append({"content": content, "ephemeral": ephemeral, **kwargs})

    async def edit_message(self, **kwargs):
        self.edits.append(kwargs)


if __name__ == "__main__":
    unittest.main()
