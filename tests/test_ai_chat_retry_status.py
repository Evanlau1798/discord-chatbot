from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from extensions import AIChat as ai_chat_module
from extensions.AIChat import AiChat, LOADING_EMOJI, _user_error_message
from utils.chat_client import ChatAPIError
from utils.discord_request_status import DiscordRequestStatus


class AiChatRetryStatusTests(unittest.IsolatedAsyncioTestCase):
    async def test_first_two_retries_are_silent(self):
        cog = _fake_cog([RuntimeError("temporary"), RuntimeError("temporary"), _response("ok")])
        message = _FakeMessage()
        status = DiscordRequestStatus(message, _FakeLogger())

        with patch.object(ai_chat_module.asyncio, "sleep", new=AsyncMock()):
            response = await cog._complete_with_retry([], message, None, status)

        self.assertEqual(response.visible_content, "ok")
        self.assertEqual(message.replies, [])

    async def test_third_retry_is_appended_to_existing_processing_notice(self):
        cog = _fake_cog(
            [
                RuntimeError("temporary"),
                RuntimeError("temporary"),
                RuntimeError("temporary"),
                RuntimeError("temporary"),
                _response("ok"),
            ]
        )
        message = _FakeMessage()
        status = DiscordRequestStatus(message, _FakeLogger())
        base = f"-# {LOADING_EMOJI} 正在輸入回覆..."
        await status.set_base(base)

        with patch.object(ai_chat_module.asyncio, "sleep", new=AsyncMock()):
            await cog._complete_with_retry([], message, None, status)

        expected_retry = (
            f"-# {LOADING_EMOJI} GenAI 服務暫時不穩，正在重試 "
            "(3/5)，下一次嘗試約 5 秒後。"
        )
        expected_next_retry = (
            f"-# {LOADING_EMOJI} GenAI 服務暫時不穩，正在重試 "
            "(4/5)，下一次嘗試約 10 秒後。"
        )
        self.assertEqual(len(message.replies), 1)
        self.assertIn(f"{base}\n\n{expected_retry}", message.replies[0].edits)
        self.assertIn(f"{base}\n\n{expected_next_retry}", message.replies[0].edits)
        self.assertEqual(message.replies[0].content, base)
        self.assertFalse(message.replies[0].deleted)

    async def test_third_retry_creates_and_then_deletes_retry_only_notice(self):
        cog = _fake_cog(
            [RuntimeError("temporary"), RuntimeError("temporary"), RuntimeError("temporary"), _response("ok")]
        )
        message = _FakeMessage()
        status = DiscordRequestStatus(message, _FakeLogger())

        with patch.object(ai_chat_module.asyncio, "sleep", new=AsyncMock()):
            await cog._complete_with_retry([], message, None, status)

        self.assertEqual(len(message.replies), 1)
        self.assertTrue(message.replies[0].content.startswith(f"-# {LOADING_EMOJI} GenAI"))
        self.assertTrue(message.replies[0].deleted)
        self.assertIsNone(status.notice)

    async def test_multimodal_provider_rejection_has_an_actionable_message(self):
        error = ChatAPIError("provider rejected input", provider="test", status_code=422)

        message = _user_error_message(error)

        self.assertIn("模型無法處理", message)
        self.assertIn("多模態", message)


def _fake_cog(results):
    cog = AiChat.__new__(AiChat)
    cog.chat_client = SimpleNamespace(
        complete=_CompleteSequence(results),
        is_retryable_error=lambda exc: True,
        provider_name="test",
    )
    return cog


class _CompleteSequence:
    def __init__(self, results):
        self._results = list(results)

    def __call__(self, *args, **kwargs):
        result = self._results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _response(content):
    return SimpleNamespace(visible_content=content)


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
        self.edits = []
        self.deleted = False

    async def edit(self, content=None, **kwargs):
        self.content = content
        self.edits.append(content)
        return self

    async def delete(self):
        self.deleted = True


class _FakeLogger:
    def debug(self, *args, **kwargs):
        return None


if __name__ == "__main__":
    unittest.main()
