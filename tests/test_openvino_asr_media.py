from __future__ import annotations

import io
import shutil
import unittest
import wave

from services.openvino_asr.config import ASRConfig
from services.openvino_asr.media import decode_media
from services.openvino_asr.protocol import ASRServiceError


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg tools are required")
class ASRMediaTests(unittest.TestCase):
    def test_decodes_wav_and_validates_decoded_duration(self):
        config = ASRConfig.from_env({"LOCAL_ASR_MAX_DURATION_SECONDS": "1"})

        decoded = decode_media(_wav_bytes(0.1), "voice.wav", "audio/wav", config)

        self.assertAlmostEqual(decoded.duration_seconds, 0.1, places=3)
        self.assertEqual(len(decoded.samples), 1600)

    def test_rejects_media_over_duration_limit_before_inference(self):
        config = ASRConfig.from_env({"LOCAL_ASR_MAX_DURATION_SECONDS": "0.05"})

        with self.assertRaises(ASRServiceError) as raised:
            decode_media(_wav_bytes(0.1), "voice.wav", "audio/wav", config)

        self.assertEqual(raised.exception.code, "duration_exceeded")


def _wav_bytes(duration_seconds: float) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16_000)
        wav_file.writeframes(b"\x00\x00" * int(duration_seconds * 16_000))
    return buffer.getvalue()


if __name__ == "__main__":
    unittest.main()
