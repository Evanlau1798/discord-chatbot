from __future__ import annotations

import base64
import unittest
from unittest.mock import Mock

from utils.chat_client import ChatAPIError, ChatClientConfigError
from utils.nvidia_assets import NvidiaAssetConfig, NvidiaAssetManager, NvidiaAssetMode


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self.payload = payload
        self.closed = False

    def json(self):
        return self.payload

    def close(self):
        self.closed = True


class NvidiaAssetManagerTests(unittest.TestCase):
    def test_inline_mode_never_calls_asset_api(self):
        session = Mock()
        manager = NvidiaAssetManager(_config(NvidiaAssetMode.INLINE, threshold=1), "key", session=session)

        prepared = manager.prepare(_messages(b"large"))

        self.assertEqual(prepared.asset_ids, ())
        self.assertIn(";base64,", prepared.messages[0]["content"][0]["image_url"]["url"])
        session.post.assert_not_called()

    def test_nvcf_uploads_only_oversized_data_urls_and_deduplicates(self):
        session = Mock()
        session.post.return_value = FakeResponse(payload={"assetId": "asset-1", "uploadUrl": "https://upload.test"})
        session.put.return_value = FakeResponse()
        manager = NvidiaAssetManager(_config(NvidiaAssetMode.NVCF, threshold=3), "key", session=session)
        messages = _messages(b"large")
        messages[0]["content"].append(messages[0]["content"][0].copy())

        prepared = manager.prepare(messages)

        self.assertEqual(prepared.asset_ids, ("asset-1",))
        self.assertEqual(session.post.call_count, 1)
        self.assertEqual(session.put.call_count, 1)
        content = prepared.messages[0]["content"]
        self.assertIsInstance(content, str)
        self.assertEqual(content.count('src="data:image/png;asset_id,asset-1"'), 2)

    def test_small_data_url_stays_inline_in_nvcf_mode(self):
        session = Mock()
        manager = NvidiaAssetManager(_config(NvidiaAssetMode.NVCF, threshold=100), "key", session=session)

        prepared = manager.prepare(_messages(b"small"))

        self.assertEqual(prepared.asset_ids, ())
        session.post.assert_not_called()

    def test_partial_prepare_failure_cleans_up_created_assets(self):
        session = Mock()
        session.post.return_value = FakeResponse(payload={"assetId": "asset-1", "uploadUrl": "https://upload.test"})
        session.put.return_value = FakeResponse(status_code=500)
        session.delete.return_value = FakeResponse(status_code=204)
        manager = NvidiaAssetManager(_config(NvidiaAssetMode.NVCF, threshold=1), "key", session=session)

        with self.assertRaises(ChatAPIError):
            manager.prepare(_messages(b"large"))

        session.delete.assert_called_once()

    def test_cleanup_failure_is_nonfatal(self):
        session = Mock()
        session.delete.side_effect = RuntimeError("delete failed")
        manager = NvidiaAssetManager(_config(NvidiaAssetMode.NVCF, threshold=1), "key", session=session)

        manager.cleanup(("asset-1",))

    def test_asset_reference_header_limit_is_enforced(self):
        manager = NvidiaAssetManager(_config(NvidiaAssetMode.NVCF, threshold=1), "key")

        with self.assertRaisesRegex(ChatAPIError, "header"):
            manager.reference_header(tuple("x" * 100 for _ in range(4)))

    def test_invalid_asset_config_fails_fast(self):
        with self.assertRaises(ChatClientConfigError):
            NvidiaAssetConfig(inline_media_max_bytes=0)


def _config(mode, *, threshold):
    return NvidiaAssetConfig(
        mode=mode,
        inline_media_max_bytes=threshold,
        asset_base_url="https://assets.example.test/v2/nvcf/assets",
    )


def _messages(data: bytes):
    encoded = base64.b64encode(data).decode("ascii")
    return [{
        "role": "user",
        "content": [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}}],
    }]


if __name__ == "__main__":
    unittest.main()
