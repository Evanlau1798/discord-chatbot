from __future__ import annotations

import unittest

from utils.local_asr_client import LocalASRClient, LocalASRError, LocalASRSettings


class LocalASRClientTests(unittest.IsolatedAsyncioTestCase):
    def test_settings_use_loopback_and_safe_defaults(self):
        settings = LocalASRSettings.from_env({})

        self.assertTrue(settings.enabled)
        self.assertEqual(settings.base_url, "http://127.0.0.1:18765")
        self.assertEqual(settings.max_duration_seconds, 60.0)

    def test_invalid_settings_disable_client_without_external_host_override(self):
        settings = LocalASRSettings.from_env({"LOCAL_ASR_PORT": "80"})

        self.assertFalse(settings.enabled)
        self.assertEqual(settings.base_url, "http://127.0.0.1:18765")
        self.assertTrue(settings.config_error)

    async def test_transcription_uses_strict_local_multipart_request(self):
        session = _FakeSession(_FakeResponse(200, {
            "schemaVersion": 1,
            "text": "測試逐字稿",
            "language": "zh",
            "durationSeconds": 1.25,
            "segments": [{"startSeconds": 0, "endSeconds": 1.25, "text": "測試逐字稿"}],
            "backend": "openvino-genai",
            "device": "GPU",
        }))
        client = LocalASRClient(LocalASRSettings.from_env({}), session=session)

        result = await client.transcribe(b"media", filename="voice.ogg", content_type="audio/ogg")

        self.assertEqual(result.text, "測試逐字稿")
        self.assertEqual(session.calls[0][0], "http://127.0.0.1:18765/v1/transcriptions")
        self.assertEqual(session.calls[0][1]["files"]["file"], ("voice.ogg", b"media", "audio/ogg"))

    async def test_structured_service_error_is_preserved(self):
        response = _FakeResponse(422, {
            "schemaVersion": 1,
            "error": {"code": "duration_exceeded", "message": "too long"},
        })
        client = LocalASRClient(LocalASRSettings.from_env({}), session=_FakeSession(response))

        with self.assertRaises(LocalASRError) as raised:
            await client.transcribe(b"media", filename="voice.ogg", content_type="audio/ogg")

        self.assertEqual(raised.exception.code, "duration_exceeded")

    async def test_invalid_success_schema_is_rejected(self):
        client = LocalASRClient(
            LocalASRSettings.from_env({}),
            session=_FakeSession(_FakeResponse(200, {"schemaVersion": 1, "text": "missing fields"})),
        )

        with self.assertRaises(LocalASRError) as raised:
            await client.transcribe(b"media", filename="voice.ogg", content_type="audio/ogg")

        self.assertEqual(raised.exception.code, "invalid_response")

    async def test_non_monotonic_segments_are_rejected(self):
        client = LocalASRClient(LocalASRSettings.from_env({}), session=_FakeSession(_FakeResponse(200, {
            "schemaVersion": 1,
            "text": "bad",
            "language": "zh",
            "durationSeconds": 2,
            "segments": [
                {"startSeconds": 1, "endSeconds": 2, "text": "one"},
                {"startSeconds": 0, "endSeconds": 1, "text": "two"},
            ],
            "backend": "openvino-genai",
            "device": "GPU",
        })))

        with self.assertRaises(LocalASRError) as raised:
            await client.transcribe(b"media", filename="voice.ogg", content_type="audio/ogg")

        self.assertEqual(raised.exception.code, "invalid_response")


class _FakeSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


class _FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self.payload = payload

    def json(self):
        return self.payload


if __name__ == "__main__":
    unittest.main()
