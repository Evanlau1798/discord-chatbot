from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from extensions.AIChat import AiChat
from utils.imagine_rate_limit_store import ImagineQuotaStatus
from utils.json_response_protocol import ImageGenerationBlock, ParsedAIResponse


class AiChatImagineRateLimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_chat_does_not_generate_when_user_reaches_imagine_quota(self):
        cog = _fake_cog(
            ImagineQuotaStatus(
                allowed=False,
                unlimited=False,
                limit=3,
                used_count=3,
                remaining=0,
                reset_at=12345,
            )
        )
        cog._maybe_generate_image = AsyncMock(side_effect=AssertionError("image generation should not run"))

        result = await cog.chat(message=_fake_message(), dialogue="draw", is_dm=True)

        self.assertEqual(result["image_paths"], [])
        self.assertIn("-# 您已達到每日繪圖數量限制，請<t:12345:R>後再試一次", result["reply_text"])
        self.assertEqual(cog.imagine_rate_limiter.recorded_user_ids, [])

    async def test_chat_records_quota_only_after_successful_image_generation(self):
        cog = _fake_cog(
            ImagineQuotaStatus(
                allowed=True,
                unlimited=False,
                limit=3,
                used_count=2,
                remaining=1,
                reset_at=2000,
            )
        )
        cog._maybe_generate_image = AsyncMock(return_value=([Path("tmp/image.png")], False))

        result = await cog.chat(message=_fake_message(), dialogue="draw", is_dm=True)

        self.assertEqual(result["image_paths"], [Path("tmp/image.png")])
        self.assertEqual(cog.imagine_rate_limiter.recorded_user_ids, [42])

    async def test_chat_does_not_record_quota_when_image_service_fails(self):
        cog = _fake_cog(
            ImagineQuotaStatus(
                allowed=True,
                unlimited=False,
                limit=3,
                used_count=2,
                remaining=1,
                reset_at=2000,
            )
        )
        cog._maybe_generate_image = AsyncMock(return_value=([], True))

        result = await cog.chat(message=_fake_message(), dialogue="draw", is_dm=True)

        self.assertEqual(result["image_paths"], [])
        self.assertIn("圖片生成服務暫時不可用", result["reply_text"])
        self.assertEqual(cog.imagine_rate_limiter.recorded_user_ids, [])


class FakeLimiter:
    def __init__(self, status: ImagineQuotaStatus):
        self.status = status
        self.recorded_user_ids = []

    def check(self, user_id):
        return self.status

    def record_success(self, user_id):
        self.recorded_user_ids.append(user_id)
        return self.status


def _fake_cog(quota_status: ImagineQuotaStatus):
    cog = AiChat.__new__(AiChat)
    cog.image_generation_enabled = True
    cog.imagine_rate_limiter = FakeLimiter(quota_status)
    cog.user_history = {}
    cog.persona_cache_names = {}
    cog.user_settings = SimpleNamespace(get_persona=lambda user: "test")
    cog.persona_store = SimpleNamespace(
        resolve=lambda key: SimpleNamespace(key="test"),
        default_persona=lambda: SimpleNamespace(key="test"),
    )
    cog.memory_store = SimpleNamespace(get_memory=lambda user_id: "", set_memory=lambda user_id, value: None)
    cog.get_user_history = lambda user_id: []
    cog._build_request_messages = AsyncMock(return_value=[{"role": "user", "content": "draw"}])
    cog._complete_and_parse_with_raw = AsyncMock(return_value=(
        ParsedAIResponse(
            reply_text="ok",
            image_generation=ImageGenerationBlock(needed=True, prompt="draw a cat"),
        ),
        "{}",
    ))
    cog._upsert_image_status = AsyncMock(return_value=None)
    cog._store_image_understanding_context = lambda message, dialogue, parsed: None
    cog._append_history = lambda *args, **kwargs: None
    return cog


def _fake_message():
    author = SimpleNamespace(id=42, display_name="Evan", name="evan")
    return SimpleNamespace(author=author, guild=None, channel=SimpleNamespace(id=1), id=100, created_at=None)


if __name__ == "__main__":
    unittest.main()
