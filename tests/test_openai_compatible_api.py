from __future__ import annotations

import base64
import unittest
from unittest.mock import patch

import requests

from utils.chat_client import ChatAPIError, ChatClientConfigError
from utils.openai_compatible_api import OpenAICompatibleChatClient


class FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None):
        self.status_code = status_code
        self.payload = payload
        self.headers = headers or {}
        self.closed = False

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload

    def close(self):
        self.closed = True


class OpenAICompatibleChatClientTests(unittest.TestCase):
    def test_complete_sends_chat_completions_without_auth_when_key_is_empty(self):
        client = OpenAICompatibleChatClient(base_url="http://localhost:8000/v1/", model="local", api_key="")
        response = FakeResponse(payload={
            "choices": [{"message": {"content": '{"replyText":"ok"}'}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 4},
        })

        with patch("utils.openai_compatible_api.requests.post", return_value=response) as post:
            result = client.complete([{"role": "system", "content": "rules"}, {"role": "user", "content": "hello"}])

        self.assertEqual(result.visible_content, '{"replyText":"ok"}')
        args, kwargs = post.call_args
        self.assertEqual(args[0], "http://localhost:8000/v1/chat/completions")
        self.assertNotIn("Authorization", kwargs["headers"])
        self.assertEqual(kwargs["json"]["messages"][0], {"role": "system", "content": "rules"})
        self.assertFalse(kwargs["json"]["stream"])
        self.assertTrue(response.closed)

    def test_complete_adds_bearer_auth_and_extracts_reasoning(self):
        client = OpenAICompatibleChatClient(base_url="https://api.example.test/v1", model="remote", api_key="secret")
        response = FakeResponse(payload={
            "choices": [{"message": {"content": "answer", "reasoning_content": "private reasoning"}}],
        })

        with patch("utils.openai_compatible_api.requests.post", return_value=response) as post:
            result = client.complete([{"role": "user", "content": "hello"}])

        self.assertEqual(result.visible_content, "answer")
        self.assertEqual(result.thinking_content, "private reasoning")
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer secret")

    def test_image_bytes_are_encoded_as_a_data_url(self):
        client = OpenAICompatibleChatClient(base_url="http://localhost:8000/v1", model="vision")
        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": "describe"},
                {"type": "image_bytes", "image_bytes": {"data": b"png", "mime_type": "image/png"}},
            ],
        }]

        converted = client._convert_messages(messages)

        expected = base64.b64encode(b"png").decode("ascii")
        self.assertEqual(converted[0]["content"][1]["image_url"]["url"], f"data:image/png;base64,{expected}")

    def test_image_urls_are_preserved(self):
        client = OpenAICompatibleChatClient(base_url="http://localhost:8000/v1", model="vision")
        converted = client._convert_messages([{
            "role": "user",
            "content": [{"type": "image_url", "image_url": {"url": "https://example.test/image.png"}}],
        }])

        self.assertEqual(
            converted[0]["content"],
            [{"type": "image_url", "image_url": {"url": "https://example.test/image.png"}}],
        )

    def test_video_bytes_are_converted_from_sampled_media_parts(self):
        client = OpenAICompatibleChatClient(base_url="http://localhost:8000/v1", model="vision")
        prepared = [
            {"type": "text", "text": "sampled video frames"},
            {"type": "image_bytes", "image_bytes": {"data": b"frame", "mime_type": "image/jpeg"}},
        ]
        messages = [{
            "role": "user",
            "content": [{"type": "video_bytes", "video_bytes": {"data": b"video", "mime_type": "video/mp4"}}],
        }]

        with patch("utils.openai_compatible_api.prepare_video_bytes", return_value=prepared):
            converted = client._convert_messages(messages)

        self.assertEqual(converted[0]["content"][0], {"type": "text", "text": "sampled video frames"})
        self.assertTrue(converted[0]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,"))

    def test_retryable_and_non_retryable_http_errors_are_classified(self):
        client = OpenAICompatibleChatClient(base_url="http://localhost:8000/v1", model="model")
        for status_code, retryable in ((429, True), (503, True), (401, False), (422, False)):
            with self.subTest(status_code=status_code):
                with patch("utils.openai_compatible_api.requests.post", return_value=FakeResponse(status_code=status_code)):
                    with self.assertRaises(ChatAPIError) as raised:
                        client.complete([{"role": "user", "content": "hello"}])
                self.assertEqual(raised.exception.status_code, status_code)
                self.assertEqual(raised.exception.retryable, retryable)

    def test_connection_errors_are_retryable_and_do_not_expose_request_content(self):
        client = OpenAICompatibleChatClient(base_url="http://localhost:8000/v1", model="model")
        with patch(
            "utils.openai_compatible_api.requests.post",
            side_effect=requests.Timeout("secret prompt should not be copied"),
        ):
            with self.assertRaises(ChatAPIError) as raised:
                client.complete([{"role": "user", "content": "private input"}])

        self.assertTrue(raised.exception.retryable)
        self.assertNotIn("private input", str(raised.exception))
        self.assertNotIn("secret prompt", str(raised.exception))

    def test_malformed_success_response_is_rejected(self):
        client = OpenAICompatibleChatClient(base_url="http://localhost:8000/v1", model="model")
        with patch("utils.openai_compatible_api.requests.post", return_value=FakeResponse(payload={"choices": []})):
            with self.assertRaisesRegex(ChatAPIError, "choices"):
                client.complete([{"role": "user", "content": "hello"}])

    def test_invalid_base_url_is_rejected(self):
        with self.assertRaises(ChatClientConfigError):
            OpenAICompatibleChatClient(base_url="file:///tmp/socket", model="model")


if __name__ == "__main__":
    unittest.main()
