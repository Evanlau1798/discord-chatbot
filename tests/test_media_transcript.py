from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from utils.local_asr_client import LocalASRError, LocalASRTranscript
from utils.media_transcript import enrich_media_transcripts
from utils.message_media import MessageMedia


class MediaTranscriptTests(unittest.IsolatedAsyncioTestCase):
    async def test_video_within_limit_combines_transcript_and_video_frames(self):
        client = _FakeClient()
        media = _video_media(duration=59.9)

        with patch("utils.media_transcript.extract_embedded_subtitles", return_value=()):
            enriched = await enrich_media_transcripts(media, client)

        self.assertEqual(client.calls, [(b"video", "clip.mp4", "video/mp4")])
        self.assertEqual(enriched.content_parts[0]["type"], "text")
        self.assertIn("untrusted_media_evidence", enriched.content_parts[0]["text"])
        self.assertIn("逐字稿", enriched.content_parts[0]["text"])
        self.assertEqual(enriched.content_parts[1]["type"], "video_bytes")
        self.assertEqual(enriched.diagnostics, [])

    async def test_video_over_limit_keeps_frames_and_skips_asr(self):
        client = _FakeClient()
        media = _video_media(duration=60.001)

        with patch("utils.media_transcript.extract_embedded_subtitles", return_value=()):
            enriched = await enrich_media_transcripts(media, client)

        self.assertEqual(client.calls, [])
        self.assertEqual([part["type"] for part in enriched.content_parts], ["video_bytes"])
        self.assertEqual(enriched.diagnostics[0]["code"], "duration_exceeded")
        self.assertEqual(enriched.diagnostics[0]["fallback"], "frames_only")

    async def test_embedded_subtitles_take_priority_over_asr(self):
        client = _FakeClient()
        captions = ({"startSeconds": 0.0, "endSeconds": 1.0, "text": "內嵌字幕"},)

        with patch("utils.media_transcript.extract_embedded_subtitles", return_value=captions):
            enriched = await enrich_media_transcripts(_video_media(duration=10), client)

        self.assertEqual(client.calls, [])
        self.assertIn("內嵌字幕", enriched.content_parts[0]["text"])
        self.assertEqual(enriched.content_parts[1]["type"], "video_bytes")

    async def test_voice_over_limit_has_explicit_diagnostic_and_no_truncation(self):
        client = _FakeClient()
        media = _audio_media(duration=61, is_voice=True)

        enriched = await enrich_media_transcripts(media, client)

        self.assertEqual(client.calls, [])
        self.assertEqual(enriched.content_parts, [])
        self.assertEqual(enriched.diagnostics[0]["code"], "duration_exceeded")
        self.assertIn("語音訊息", enriched.diagnostics[0]["userMessage"])

    async def test_asr_failure_keeps_video_and_exposes_diagnostic(self):
        client = _FakeClient(error=LocalASRError("busy", "busy", status=429))

        with patch("utils.media_transcript.extract_embedded_subtitles", return_value=()):
            enriched = await enrich_media_transcripts(_video_media(duration=10), client)

        self.assertEqual([part["type"] for part in enriched.content_parts], ["video_bytes"])
        self.assertEqual(enriched.diagnostics[0]["code"], "busy")


class _FakeClient:
    def __init__(self, error=None):
        self.settings = SimpleNamespace(enabled=True, max_duration_seconds=60.0, config_error="")
        self.error = error
        self.calls = []

    async def transcribe(self, data, *, filename, content_type):
        self.calls.append((data, filename, content_type))
        if self.error:
            raise self.error
        return LocalASRTranscript(
            text="逐字稿",
            language="zh",
            duration_seconds=1.0,
            segments=({"startSeconds": 0.0, "endSeconds": 1.0, "text": "逐字稿"},),
            backend="openvino-genai",
            device="GPU",
        )


def _video_media(*, duration):
    return MessageMedia(image_urls=[], content_parts=[{
        "type": "video_bytes",
        "video_bytes": {
            "data": b"video",
            "mime_type": "video/mp4",
            "filename": "clip.mp4",
            "duration_seconds": duration,
        },
    }])


def _audio_media(*, duration, is_voice):
    return MessageMedia(image_urls=[], content_parts=[{
        "type": "audio_bytes",
        "audio_bytes": {
            "data": b"audio",
            "mime_type": "audio/ogg",
            "filename": "voice.ogg",
            "duration_seconds": duration,
            "is_voice_message": is_voice,
        },
    }])


if __name__ == "__main__":
    unittest.main()
