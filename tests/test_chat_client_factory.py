from __future__ import annotations

import unittest
from unittest.mock import patch

from utils.chat_client import ChatClientConfigError
from utils.chat_client_factory import create_chat_client
from utils.gemini_api import GeminiChatClient
from utils.nvidia_api import (
    DEFAULT_NVIDIA_BASE_URL,
    NvidiaChatClient,
)
from utils.nvidia_assets import NvidiaAssetMode
from utils.openai_compatible_api import OpenAICompatibleChatClient


class ChatClientFactoryTests(unittest.TestCase):
    def test_gemini_is_the_backwards_compatible_default(self):
        with patch.object(GeminiChatClient, "__init__", return_value=None) as constructor:
            client = create_chat_client({"GEMINI_API_KEY": "gemini-key"})

        self.assertIsInstance(client, GeminiChatClient)
        constructor.assert_called_once_with(api_key="gemini-key", model="gemma-4-31b-it")

    def test_legacy_gemini_api_key_alias_is_supported(self):
        with patch.object(GeminiChatClient, "__init__", return_value=None) as constructor:
            create_chat_client({"GEMINIAPIKEY": "legacy-key", "GEMINI_MODEL": "test-model"})

        constructor.assert_called_once_with(api_key="legacy-key", model="test-model")

    def test_nvidia_uses_cloud_default_and_requires_model(self):
        client = create_chat_client({
            "AI_CHAT_PROVIDER": "nvidia",
            "NVIDIA_API_KEY": "nv-key",
            "NVIDIA_MODEL": "nvidia/test-model",
        })

        self.assertIsInstance(client, NvidiaChatClient)
        self.assertEqual(client.base_url, DEFAULT_NVIDIA_BASE_URL)

    def test_nvidia_typed_options_are_loaded_without_model_specific_caps(self):
        client = create_chat_client({
            "AI_CHAT_PROVIDER": "nvidia",
            "NVIDIA_API_KEY": "nv-key",
            "NVIDIA_MODEL": "vendor/test-model",
            "NVIDIA_MESSAGE_STRATEGY": "user_prefix",
            "NVIDIA_ENABLE_THINKING": "0",
            "NVIDIA_MAX_TOKENS": "99999",
            "NVIDIA_TOP_P": "0.95",
            "NVIDIA_SEED": "7",
            "NVIDIA_ASSET_MODE": "nvcf",
            "NVIDIA_INLINE_MEDIA_MAX_BYTES": "200000",
            "NVIDIA_ASSET_BASE_URL": "https://assets.example.test/v2/nvcf/assets",
        })

        self.assertEqual(client.config.message_strategy.value, "user_prefix")
        self.assertEqual(client.config.request_options.max_tokens, 99999)
        self.assertFalse(client.config.request_options.enable_thinking)
        self.assertIs(client.config.asset_config.mode, NvidiaAssetMode.NVCF)

    def test_nvidia_invalid_typed_option_fails_fast(self):
        with self.assertRaisesRegex(ChatClientConfigError, "NVIDIA_ENABLE_THINKING"):
            create_chat_client({
                "AI_CHAT_PROVIDER": "nvidia",
                "NVIDIA_API_KEY": "nv-key",
                "NVIDIA_MODEL": "vendor/test-model",
                "NVIDIA_ENABLE_THINKING": "sometimes",
            })

    def test_openai_compatible_allows_an_empty_key(self):
        client = create_chat_client({
            "AI_CHAT_PROVIDER": "openai-compatible",
            "OPENAI_COMPAT_BASE_URL": "http://127.0.0.1:8000/v1/",
            "OPENAI_COMPAT_MODEL": "local-model",
        })

        self.assertIsInstance(client, OpenAICompatibleChatClient)
        self.assertEqual(client.api_key, "")
        self.assertEqual(client.base_url, "http://127.0.0.1:8000/v1")

    def test_unknown_provider_fails_fast(self):
        with self.assertRaisesRegex(ChatClientConfigError, "AI_CHAT_PROVIDER"):
            create_chat_client({"AI_CHAT_PROVIDER": "mystery"})

    def test_selected_provider_reports_missing_required_setting(self):
        with self.assertRaisesRegex(ChatClientConfigError, "NVIDIA_MODEL"):
            create_chat_client({"AI_CHAT_PROVIDER": "nvidia", "NVIDIA_API_KEY": "nv-key"})


if __name__ == "__main__":
    unittest.main()
