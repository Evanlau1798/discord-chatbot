from __future__ import annotations

import unittest

from services.openvino_asr.protocol import ASRServiceError, Transcription, normalize_segments


class ASRProtocolTests(unittest.TestCase):
    def test_normalizes_and_clamps_timestamp_segments(self):
        segments = normalize_segments(
            [
                {"start": -1, "end": 1.23456, "text": " first "},
                {"start": 1.2344, "end": 1.1, "text": "second"},
                {"start": 99, "end": 100, "text": "outside"},
            ],
            duration_seconds=2.0,
        )

        self.assertEqual(segments, (
            {"startSeconds": 0.0, "endSeconds": 1.235, "text": "first"},
            {"startSeconds": 1.235, "endSeconds": 1.235, "text": "second"},
            {"startSeconds": 2.0, "endSeconds": 2.0, "text": "outside"},
        ))

    def test_transcription_serializes_strict_schema(self):
        transcription = Transcription(
            text="測試",
            language="zh",
            duration_seconds=1.5,
            segments=({"startSeconds": 0.0, "endSeconds": 1.5, "text": "測試"},),
            backend="openvino-genai",
            device="GPU",
        )

        payload = transcription.to_payload()

        self.assertEqual(payload["schemaVersion"], 1)
        self.assertEqual(payload["text"], "測試")
        self.assertEqual(payload["durationSeconds"], 1.5)
        self.assertEqual(payload["segments"][0]["text"], "測試")
        self.assertNotIn("prompt", payload)

    def test_service_error_serializes_without_internal_details(self):
        error = ASRServiceError("gpu_unavailable", "ASR GPU is unavailable.", status=503)

        self.assertEqual(error.to_payload(), {
            "schemaVersion": 1,
            "error": {"code": "gpu_unavailable", "message": "ASR GPU is unavailable."},
        })


if __name__ == "__main__":
    unittest.main()
