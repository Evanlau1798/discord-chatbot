from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from extensions import AIChat as ai_chat_module
from extensions.AIChat import AiChat
from utils.ai_chat_concurrency import (
    DEFAULT_AI_CHAT_MAX_PARALLEL_REQUESTS,
    AiChatRequestLimiter,
    get_ai_chat_max_parallel_requests,
)


class AiChatConcurrencyConfigTests(unittest.TestCase):
    def test_default_parallel_requests_is_three(self):
        self.assertEqual(get_ai_chat_max_parallel_requests({}), DEFAULT_AI_CHAT_MAX_PARALLEL_REQUESTS)
        self.assertEqual(DEFAULT_AI_CHAT_MAX_PARALLEL_REQUESTS, 3)

    def test_parallel_requests_can_be_set_from_env(self):
        value = get_ai_chat_max_parallel_requests({"AI_CHAT_MAX_PARALLEL_REQUESTS": "5"})

        self.assertEqual(value, 5)

    def test_invalid_parallel_requests_falls_back_to_default(self):
        self.assertEqual(
            get_ai_chat_max_parallel_requests({"AI_CHAT_MAX_PARALLEL_REQUESTS": "not-a-number"}),
            DEFAULT_AI_CHAT_MAX_PARALLEL_REQUESTS,
        )
        self.assertEqual(
            get_ai_chat_max_parallel_requests({"AI_CHAT_MAX_PARALLEL_REQUESTS": "0"}),
            DEFAULT_AI_CHAT_MAX_PARALLEL_REQUESTS,
        )


class AiChatRequestLimiterTests(unittest.IsolatedAsyncioTestCase):
    async def test_limiter_caps_active_requests(self):
        limiter = AiChatRequestLimiter(max_parallel_requests=2)
        active = 0
        max_seen = 0

        async def run_one():
            nonlocal active, max_seen
            async with limiter:
                active += 1
                max_seen = max(max_seen, active)
                await asyncio.sleep(0.01)
                active -= 1

        await asyncio.gather(*(run_one() for _ in range(6)))

        self.assertEqual(max_seen, 2)


class AiChatMessageHandlerConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_on_message_uses_request_limiter(self):
        cog = AiChat.__new__(AiChat)
        cog.bot = type("Bot", (), {"user": None})()
        cog.request_limiter = AiChatRequestLimiter(max_parallel_requests=2)
        active = 0
        max_seen = 0

        async def fake_chat(*, message, dialogue, is_dm):
            nonlocal active, max_seen
            active += 1
            max_seen = max(max_seen, active)
            await asyncio.sleep(0.01)
            active -= 1
            return {
                "reply_text": f"ok-{message.id}",
                "image_paths": [],
                "delivered_message": None,
                "browser_used": False,
            }

        cog.chat = fake_chat
        messages = [_FakeMessage(index) for index in range(6)]

        with patch.object(ai_chat_module.discord, "DMChannel", _FakeDMChannel):
            await asyncio.gather(*(cog.on_message(message) for message in messages))

        self.assertEqual(max_seen, 2)
        self.assertEqual([message.replies for message in messages], [[f"ok-{message.id}"] for message in messages])


class _FakeDMChannel:
    pass


class _FakeMessage:
    def __init__(self, message_id: int):
        self.id = message_id
        self.content = "hello"
        self.attachments = []
        self.embeds = []
        self.mentions = []
        self.channel = _FakeDMChannel()
        self.author = type("Author", (), {"id": message_id, "bot": False})()
        self.replies = []

    async def reply(self, content=None, **kwargs):
        self.replies.append(content)
        return type("Reply", (), {"content": content})()


if __name__ == "__main__":
    unittest.main()
