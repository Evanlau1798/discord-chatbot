from __future__ import annotations

import unittest
from unittest.mock import Mock

from utils.chat_client import ChatAPIError, ChatClientConfigError
from utils.nvidia_api import (
    NvidiaChatClient,
    NvidiaChatConfig,
    NvidiaMessageAdapter,
    NvidiaMessageStrategy,
    NvidiaRequestBuilder,
    NvidiaRequestOptions,
    NvidiaTransport,
)
from utils.nvidia_assets import NvidiaAssetConfig, NvidiaAssetMode


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


class NvidiaMessageAdapterTests(unittest.TestCase):
    def test_preserve_strategy_keeps_roles(self):
        adapter = NvidiaMessageAdapter(NvidiaMessageStrategy.PRESERVE)

        messages = adapter.adapt([
            {"role": "system", "content": "rules"},
            {"role": "user", "content": "hello"},
        ])

        self.assertEqual(messages[0], {"role": "system", "content": "rules"})

    def test_user_prefix_combines_all_system_messages(self):
        adapter = NvidiaMessageAdapter(NvidiaMessageStrategy.USER_PREFIX)

        messages = adapter.adapt([
            {"role": "system", "content": "rule one"},
            {"role": "system", "content": "rule two"},
            {"role": "user", "content": "hello"},
        ])

        self.assertEqual([message["role"] for message in messages], ["user"])
        self.assertIn("<application_protocol>", messages[0]["content"])
        self.assertIn("rule one\n\nrule two", messages[0]["content"])
        self.assertTrue(messages[0]["content"].endswith("hello"))

    def test_user_prefix_prepends_text_to_multimodal_user_content(self):
        adapter = NvidiaMessageAdapter(NvidiaMessageStrategy.USER_PREFIX)

        messages = adapter.adapt([
            {"role": "system", "content": "rules"},
            {"role": "user", "content": [
                {"type": "text", "text": "describe"},
                {"type": "image_url", "image_url": {"url": "https://example.test/a.png"}},
            ]},
        ])

        self.assertEqual(messages[0]["content"][0]["type"], "text")
        self.assertIn("<application_protocol>", messages[0]["content"][0]["text"])
        self.assertEqual(messages[0]["content"][1]["text"], "describe")

    def test_user_prefix_inserts_user_message_before_leading_assistant(self):
        adapter = NvidiaMessageAdapter(NvidiaMessageStrategy.USER_PREFIX)

        messages = adapter.adapt([
            {"role": "system", "content": "rules"},
            {"role": "assistant", "content": "old answer"},
            {"role": "user", "content": "new question"},
        ])

        self.assertEqual([message["role"] for message in messages], ["user", "assistant", "user"])
        self.assertIn("<application_protocol>", messages[0]["content"])

class NvidiaConfigurationTests(unittest.TestCase):
    def test_request_builder_omits_unconfigured_options(self):
        config = _config(options=NvidiaRequestOptions())

        payload, _ = NvidiaRequestBuilder(config).build([{"role": "user", "content": "hi"}], 0.7)

        self.assertEqual(set(payload), {"model", "messages", "temperature", "stream"})

    def test_request_builder_includes_typed_options(self):
        options = NvidiaRequestOptions(max_tokens=99999, top_p=0.9, seed=12, enable_thinking=False)
        config = _config(options=options)

        payload, _ = NvidiaRequestBuilder(config).build([{"role": "user", "content": "hi"}], 0.4)

        self.assertEqual(payload["max_tokens"], 99999)
        self.assertEqual(payload["top_p"], 0.9)
        self.assertEqual(payload["seed"], 12)
        self.assertEqual(payload["chat_template_kwargs"], {"enable_thinking": False})

    def test_invalid_options_fail_fast(self):
        for kwargs in ({"max_tokens": 0}, {"top_p": 0}, {"top_p": 1.1}, {"seed": True}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ChatClientConfigError):
                    NvidiaRequestOptions(**kwargs)


class NvidiaTransportTests(unittest.TestCase):
    def test_pending_response_is_polled_for_any_configured_base_url(self):
        session = Mock()
        pending = FakeResponse(status_code=202, headers={"NVCF-REQID": "request-123"})
        completed = FakeResponse(payload={"choices": [{"message": {"content": "done"}}]})
        session.post.return_value = pending
        session.get.return_value = completed
        transport = NvidiaTransport("https://nim.example.test/v1", "key", session=session)

        payload = transport.send("https://nim.example.test/v1/chat/completions", {}, {})

        self.assertEqual(payload["choices"][0]["message"]["content"], "done")
        self.assertEqual(session.get.call_args.args[0], "https://nim.example.test/v1/status/request-123")
        self.assertTrue(pending.closed)
        self.assertTrue(completed.closed)

    def test_pending_response_accepts_body_request_id(self):
        session = Mock()
        session.post.return_value = FakeResponse(status_code=202, payload={"requestId": "body-id"})
        session.get.return_value = FakeResponse(payload={"choices": [{"message": {"content": "done"}}]})
        transport = NvidiaTransport("https://nim.example.test/v1", "key", session=session)

        transport.send("https://nim.example.test/v1/chat/completions", {}, {})

        self.assertTrue(session.get.call_args.args[0].endswith("/status/body-id"))

    def test_pending_response_without_request_id_is_rejected(self):
        session = Mock()
        session.post.return_value = FakeResponse(status_code=202, payload={})
        transport = NvidiaTransport("https://nim.example.test/v1", "key", session=session)

        with self.assertRaisesRegex(ChatAPIError, "request ID"):
            transport.send("https://nim.example.test/v1/chat/completions", {}, {})

    def test_pending_poll_timeout_is_retryable(self):
        session = Mock()
        session.post.return_value = FakeResponse(status_code=202, headers={"NVCF-REQID": "request-123"})
        session.get.return_value = FakeResponse(status_code=202)
        transport = NvidiaTransport(
            "https://nim.example.test/v1",
            "key",
            session=session,
            max_poll_attempts=2,
        )

        with self.assertRaises(ChatAPIError) as raised:
            transport.send("https://nim.example.test/v1/chat/completions", {}, {})

        self.assertTrue(raised.exception.retryable)


class NvidiaChatClientTests(unittest.TestCase):
    def test_client_is_not_an_openai_compatible_client_subclass(self):
        from utils.openai_compatible_api import OpenAICompatibleChatClient

        self.assertFalse(issubclass(NvidiaChatClient, OpenAICompatibleChatClient))

    def test_complete_composes_adapter_builder_transport_and_cleanup(self):
        transport = Mock()
        transport.send.return_value = {
            "choices": [{"message": {"content": '{"replyText":"ok"}'}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2},
        }
        assets = Mock()
        assets.prepare.return_value = Mock(
            messages=[{"role": "user", "content": "hello"}],
            asset_ids=("asset-1",),
            uploaded_bytes=200000,
        )
        assets.reference_header.return_value = "asset-1"
        client = NvidiaChatClient(_config(), transport=transport, asset_manager=assets)

        result = client.complete([{"role": "user", "content": "hello"}])

        self.assertEqual(result.visible_content, '{"replyText":"ok"}')
        self.assertEqual(
            transport.send.call_args.args[1]["NVCF-INPUT-ASSET-REFERENCES"],
            "asset-1",
        )
        assets.cleanup.assert_called_once_with(("asset-1",))

    def test_refresh_persona_caches_remains_a_network_free_noop(self):
        transport = Mock()
        client = NvidiaChatClient(_config(), transport=transport)

        self.assertEqual(client.refresh_persona_caches({"persona": "prompt"}), {})
        transport.send.assert_not_called()

    def test_cleanup_exception_does_not_override_successful_response(self):
        transport = Mock()
        transport.send.return_value = {"choices": [{"message": {"content": "done"}}]}
        assets = Mock()
        assets.prepare.return_value = Mock(messages=[{"role": "user", "content": "hello"}], asset_ids=(), uploaded_bytes=0)
        assets.reference_header.return_value = ""
        assets.cleanup.side_effect = RuntimeError("cleanup failed")
        client = NvidiaChatClient(_config(), transport=transport, asset_manager=assets)

        result = client.complete([{"role": "user", "content": "hello"}])

        self.assertEqual(result.visible_content, "done")


def _config(*, options=None, asset_config=None):
    return NvidiaChatConfig(
        api_key="nv-key",
        model="vendor/test-model",
        base_url="https://nim.example.test/v1",
        request_options=options or NvidiaRequestOptions(),
        asset_config=asset_config or NvidiaAssetConfig(mode=NvidiaAssetMode.INLINE),
    )


if __name__ == "__main__":
    unittest.main()
