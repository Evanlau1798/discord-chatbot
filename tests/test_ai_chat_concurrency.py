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

    async def test_limiter_reports_waiting_next_and_running_status(self):
        limiter = AiChatRequestLimiter(max_parallel_requests=1)
        release_active = asyncio.Event()
        release_first_queued = asyncio.Event()
        active_started = asyncio.Event()
        first_queued_started = asyncio.Event()
        updates = []

        async def occupy_slot():
            async with limiter:
                active_started.set()
                await release_active.wait()

        async def queued_request(label: str, release_event: asyncio.Event | None = None):
            async def on_update(update):
                updates.append((label, update.status, update.queue_ahead))

            async with limiter.with_queue_updates(on_update):
                updates.append((label, "inside", -1))
                if label == "first":
                    first_queued_started.set()
                if release_event is not None:
                    await release_event.wait()

        active_task = asyncio.create_task(occupy_slot())
        await active_started.wait()
        first_task = asyncio.create_task(queued_request("first", release_first_queued))
        second_task = asyncio.create_task(queued_request("second"))
        await _wait_until(lambda: ("first", "next", 0) in updates and ("second", "waiting", 1) in updates)

        release_active.set()
        await first_queued_started.wait()
        await _wait_until(lambda: ("first", "running", 0) in updates and ("second", "next", 0) in updates)

        release_first_queued.set()
        await asyncio.gather(active_task, first_task, second_task)

        self.assertIn(("second", "running", 0), updates)


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
        self.assertEqual([[reply.content for reply in message.replies] for message in messages], [[f"ok-{message.id}"] for message in messages])

    async def test_on_message_updates_queue_notice_until_final_reply(self):
        cog = AiChat.__new__(AiChat)
        cog.bot = type("Bot", (), {"user": None})()
        cog.request_limiter = AiChatRequestLimiter(max_parallel_requests=1)
        release_events = {index: asyncio.Event() for index in range(3)}
        started_events = {index: asyncio.Event() for index in range(3)}

        async def fake_chat(*, message, dialogue, is_dm):
            started_events[message.id].set()
            await release_events[message.id].wait()
            return {
                "reply_text": f"ok-{message.id}",
                "image_paths": [],
                "delivered_message": None,
                "browser_used": False,
            }

        cog.chat = fake_chat
        messages = [_FakeMessage(index) for index in range(3)]

        with patch.object(ai_chat_module.discord, "DMChannel", _FakeDMChannel):
            tasks = [asyncio.create_task(cog.on_message(message)) for message in messages]
            await started_events[0].wait()
            await _wait_until(lambda: len(messages[1].replies) == 1 and len(messages[2].replies) == 1)

            self.assertEqual(messages[1].replies[0].content, "-#<a:loading:1303077872805744650> 正在等候訊息發送...您是下一位!")
            self.assertEqual(messages[2].replies[0].content, "-#<a:loading:1303077872805744650> 正在等候訊息發送...前面還有1則訊息")

            release_events[0].set()
            await started_events[1].wait()
            await _wait_until(lambda: "-#<a:loading:1303077872805744650> 正在輸入回覆..." in messages[1].replies[0].edits)
            await _wait_until(lambda: "-#<a:loading:1303077872805744650> 正在等候訊息發送...您是下一位!" in messages[2].replies[0].edits)

            release_events[1].set()
            await started_events[2].wait()
            await _wait_until(lambda: "-#<a:loading:1303077872805744650> 正在輸入回覆..." in messages[2].replies[0].edits)

            release_events[2].set()
            await asyncio.gather(*tasks)

        self.assertEqual(messages[0].replies[0].content, "ok-0")
        self.assertEqual(messages[1].replies[0].content, "ok-1")
        self.assertEqual(messages[2].replies[0].content, "ok-2")


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
        reply = _FakeReply(content)
        self.replies.append(reply)
        return reply


class _FakeReply:
    def __init__(self, content):
        self.content = content
        self.edits = []
        self.deleted = False

    async def edit(self, content=None, **kwargs):
        self.content = content
        self.edits.append(content)
        return self

    async def delete(self):
        self.deleted = True

    async def reply(self, content=None, **kwargs):
        return _FakeReply(content)


async def _wait_until(predicate, timeout: float = 1.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.001)
    raise AssertionError("condition was not reached before timeout")


if __name__ == "__main__":
    unittest.main()
