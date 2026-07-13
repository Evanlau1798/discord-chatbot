from __future__ import annotations

import threading
import unittest

import requests

from services.openvino_asr.app import ASRApplication, ASRHTTPServer, Upload
from services.openvino_asr.config import ASRConfig
from services.openvino_asr.media import DecodedAudio
from services.openvino_asr.protocol import ASRServiceError, Transcription


class ASRApplicationTests(unittest.TestCase):
    def test_http_contract_accepts_multipart_and_reports_readiness(self):
        config = ASRConfig.from_env({})
        app = ASRApplication(config, transcriber=_FakeTranscriber(), decoder=_FakeDecoder())
        server = ASRHTTPServer(("127.0.0.1", 0), app)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        base_url = f"http://127.0.0.1:{server.server_port}"
        try:
            health = requests.get(f"{base_url}/health/ready", timeout=2)
            response = requests.post(
                f"{base_url}/v1/transcriptions",
                files={"file": ("voice.ogg", b"audio", "audio/ogg")},
                timeout=2,
            )
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=1)

        self.assertEqual(health.status_code, 200)
        self.assertTrue(health.json()["ready"])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["schemaVersion"], 1)
        self.assertEqual(response.json()["text"], "transcribed")

    def test_transcribes_uploaded_media(self):
        config = ASRConfig.from_env({})
        decoder = _FakeDecoder(DecodedAudio(samples=(0.0, 0.1), duration_seconds=0.001))
        transcriber = _FakeTranscriber()
        app = ASRApplication(config, transcriber=transcriber, decoder=decoder)

        result = app.transcribe(Upload(data=b"audio", filename="voice.ogg", content_type="audio/ogg"))

        self.assertEqual(result.text, "transcribed")
        self.assertEqual(decoder.calls[0][1:3], ("voice.ogg", "audio/ogg"))
        self.assertIs(decoder.calls[0][3], config)
        self.assertEqual(transcriber.calls, [decoder.result])

    def test_disabled_service_rejects_requests(self):
        config = ASRConfig.from_env({"LOCAL_ASR_ENABLED": "0"})
        app = ASRApplication(config, transcriber=_FakeTranscriber(), decoder=_FakeDecoder())

        with self.assertRaises(ASRServiceError) as raised:
            app.transcribe(Upload(data=b"audio", filename="voice.ogg", content_type="audio/ogg"))

        self.assertEqual(raised.exception.code, "service_disabled")

    def test_queue_rejects_request_when_single_slot_is_busy(self):
        config = ASRConfig.from_env({
            "LOCAL_ASR_MAX_CONCURRENCY": "1",
            "LOCAL_ASR_MAX_QUEUE_SIZE": "0",
            "LOCAL_ASR_QUEUE_TIMEOUT_SECONDS": "0.01",
        })
        started = threading.Event()
        release = threading.Event()
        transcriber = _BlockingTranscriber(started, release)
        app = ASRApplication(config, transcriber=transcriber, decoder=_FakeDecoder())
        first_error = []

        def run_first():
            try:
                app.transcribe(Upload(data=b"one", filename="one.wav", content_type="audio/wav"))
            except Exception as exc:  # pragma: no cover - assertion captures unexpected worker errors
                first_error.append(exc)

        worker = threading.Thread(target=run_first)
        worker.start()
        self.assertTrue(started.wait(timeout=1))

        with self.assertRaises(ASRServiceError) as raised:
            app.transcribe(Upload(data=b"two", filename="two.wav", content_type="audio/wav"))

        self.assertEqual(raised.exception.code, "busy")
        release.set()
        worker.join(timeout=1)
        self.assertFalse(first_error)


class _FakeDecoder:
    def __init__(self, result=None):
        self.result = result or DecodedAudio(samples=(0.0,), duration_seconds=0.001)
        self.calls = []

    def __call__(self, data, filename, content_type, config):
        self.calls.append((data, filename, content_type, config))
        return self.result


class _FakeTranscriber:
    ready = True
    error = ""

    def __init__(self):
        self.calls = []

    def transcribe(self, decoded):
        self.calls.append(decoded)
        return Transcription(
            text="transcribed",
            language="",
            duration_seconds=decoded.duration_seconds,
            segments=(),
            backend="openvino-genai",
            device="GPU",
        )


class _BlockingTranscriber(_FakeTranscriber):
    def __init__(self, started, release):
        super().__init__()
        self.started = started
        self.release = release

    def transcribe(self, decoded):
        self.started.set()
        self.release.wait(timeout=1)
        return super().transcribe(decoded)


if __name__ == "__main__":
    unittest.main()
