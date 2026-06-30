from __future__ import annotations

import unittest
from unittest.mock import patch

from utils import gemini_api
from utils.gif_frame_sampler import GifFrameSample, GifFrameSamplingResult
from utils.media_frame_splitter import MediaFrame, MediaSplitResult
from utils.gemini_api import GeminiChatClient, _estimate_content_chars, _explicit_cache_enabled


class FakePart:
    @staticmethod
    def from_text(text: str):
        return ("text", text)

    @staticmethod
    def from_bytes(data: bytes, mime_type: str):
        return ("bytes", data, mime_type)


class FakeGenaiTypes:
    Part = FakePart


class GeminiApiTests(unittest.TestCase):
    def test_convert_parts_accepts_inline_image_bytes(self):
        client = GeminiChatClient.__new__(GeminiChatClient)
        content = [
            {"type": "text", "text": "describe this"},
            {"type": "image_bytes", "image_bytes": {"data": b"png-bytes", "mime_type": "image/png"}},
        ]

        with patch.object(gemini_api, "genai_types", FakeGenaiTypes):
            parts = client._convert_parts(content)

        self.assertEqual(parts[0], ("text", "describe this"))
        self.assertEqual(parts[1], ("bytes", b"png-bytes", "image/png"))

    def test_convert_parts_expands_inline_gif_bytes_to_png_frames(self):
        client = GeminiChatClient.__new__(GeminiChatClient)
        content = [
            {"type": "text", "text": "describe this"},
            {"type": "image_bytes", "image_bytes": {"data": b"gif-bytes", "mime_type": "image/gif"}},
        ]
        sampling = GifFrameSamplingResult(
            frames=(
                GifFrameSample(data=b"frame-1", mime_type="image/png", frame_index=0, time_ms=0),
                GifFrameSample(data=b"frame-2", mime_type="image/png", frame_index=4, time_ms=320),
            ),
            frame_count=5,
            duration_ms=400,
            sampled_all=False,
        )

        with (
            patch.object(gemini_api, "genai_types", FakeGenaiTypes),
            patch.object(gemini_api, "sample_gif_frames", return_value=sampling),
        ):
            parts = client._convert_parts(content)

        self.assertEqual(parts[0], ("text", "describe this"))
        self.assertIn("sampled image frames", parts[1][1])
        self.assertEqual(parts[2], ("bytes", b"frame-1", "image/png"))
        self.assertEqual(parts[3], ("bytes", b"frame-2", "image/png"))

    def test_estimate_content_chars_ignores_inline_image_bytes(self):
        content = [
            {"type": "text", "text": "hello"},
            {"type": "image_bytes", "image_bytes": {"data": b"x" * 1024, "mime_type": "image/png"}},
        ]

        self.assertEqual(_estimate_content_chars(content), 5)

    def test_convert_parts_ignores_video_url_payloads(self):
        client = GeminiChatClient.__new__(GeminiChatClient)
        content = [
            {"type": "text", "text": "describe this"},
            {"type": "video_url", "video_url": {"url": "https://video.example.test/video.mp4"}},
        ]

        with patch.object(gemini_api, "genai_types", FakeGenaiTypes):
            parts = client._convert_parts(content)

        self.assertEqual(parts, [("text", "describe this")])

    def test_convert_parts_expands_inline_video_bytes_to_sampled_frames(self):
        client = GeminiChatClient.__new__(GeminiChatClient)
        content = [
            {"type": "text", "text": "describe this"},
            {"type": "video_bytes", "video_bytes": {"data": b"mp4-bytes", "mime_type": "video/mp4", "filename": "clip.mp4"}},
        ]
        split_result = MediaSplitResult(
            frames=(
                MediaFrame(data=b"frame-1", mime_type="image/jpeg", frame_index=0, time_ms=0),
                MediaFrame(data=b"frame-2", mime_type="image/jpeg", frame_index=30, time_ms=1000),
            ),
            frame_count=60,
            duration_ms=2000,
            sampled_all=False,
            source_kind="video",
        )

        with (
            patch.object(gemini_api, "genai_types", FakeGenaiTypes),
            patch.object(gemini_api, "split_video_bytes", return_value=split_result),
        ):
            parts = client._convert_parts(content)

        self.assertEqual(parts[0], ("text", "describe this"))
        self.assertIn("sampled JPEG frames", parts[1][1])
        self.assertIn("filename=clip.mp4", parts[1][1])
        self.assertEqual(parts[2], ("bytes", b"frame-1", "image/jpeg"))
        self.assertEqual(parts[3], ("bytes", b"frame-2", "image/jpeg"))

    def test_explicit_cache_auto_skips_gemma_models(self):
        with patch.dict("os.environ", {}, clear=True):
            enabled, reason = _explicit_cache_enabled("gemma-4-31b-it")

        self.assertFalse(enabled)
        self.assertEqual(reason, "gemma_model_does_not_support_explicit_cache")

    def test_explicit_cache_can_be_forced_by_env(self):
        with patch.dict("os.environ", {"GEMINI_CACHE_ENABLED": "true"}, clear=True):
            enabled, reason = _explicit_cache_enabled("gemma-4-31b-it")

        self.assertTrue(enabled)
        self.assertEqual(reason, "forced_by_env")

    def test_refresh_persona_caches_skips_delete_and_create_for_gemma(self):
        client = GeminiChatClient.__new__(GeminiChatClient)
        client.model = "gemma-4-31b-it"
        client.delete_project_caches = lambda: self.fail("delete should not run")
        client.create_system_prompt_cache = lambda key, prompt: self.fail("create should not run")

        with patch.dict("os.environ", {}, clear=True):
            cache_names = client.refresh_persona_caches({"akira": "prompt"})

        self.assertEqual(cache_names, {})


if __name__ == "__main__":
    unittest.main()
