from __future__ import annotations

import unittest

from utils.discord_request_status import DiscordRequestStatus


class DiscordRequestStatusTests(unittest.IsolatedAsyncioTestCase):
    async def test_retry_is_appended_below_existing_processing_notice(self):
        message = _FakeMessage()
        status = DiscordRequestStatus(message, _FakeLogger())

        await status.set_base("-# loading 正在輸入回覆...")
        await status.set_retry("-# loading GenAI retry 3/5")

        self.assertEqual(len(message.replies), 1)
        self.assertEqual(
            message.replies[0].content,
            "-# loading 正在輸入回覆...\n\n-# loading GenAI retry 3/5",
        )

    async def test_base_update_preserves_visible_retry(self):
        message = _FakeMessage()
        status = DiscordRequestStatus(message, _FakeLogger())

        await status.set_base("-# loading waiting")
        await status.set_retry("-# loading retry")
        await status.set_base("-# loading running")

        self.assertEqual(message.replies[0].content, "-# loading running\n\n-# loading retry")

    async def test_clear_retry_restores_existing_processing_notice(self):
        message = _FakeMessage()
        status = DiscordRequestStatus(message, _FakeLogger())

        await status.set_base("-# loading running")
        await status.set_retry("-# loading retry")
        await status.clear_retry()

        self.assertEqual(message.replies[0].content, "-# loading running")
        self.assertFalse(message.replies[0].deleted)

    async def test_clear_retry_deletes_retry_only_notice(self):
        message = _FakeMessage()
        status = DiscordRequestStatus(message, _FakeLogger())

        await status.set_retry("-# loading retry")
        await status.clear_retry()

        self.assertTrue(message.replies[0].deleted)
        self.assertIsNone(status.notice)


class _FakeMessage:
    def __init__(self):
        self.replies = []

    async def reply(self, content=None, **kwargs):
        reply = _FakeReply(content)
        self.replies.append(reply)
        return reply


class _FakeReply:
    def __init__(self, content):
        self.content = content
        self.deleted = False

    async def edit(self, content=None, **kwargs):
        self.content = content
        return self

    async def delete(self):
        self.deleted = True


class _FakeLogger:
    def debug(self, *args, **kwargs):
        return None


if __name__ == "__main__":
    unittest.main()
