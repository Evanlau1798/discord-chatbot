from __future__ import annotations

import json
import logging
import signal
import threading
import time
from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from services.openvino_asr.config import ASRConfig, ASRConfigError
from services.openvino_asr.media import decode_media
from services.openvino_asr.protocol import ASRServiceError, Transcription

logger = logging.getLogger("openvino_asr")
MAX_MULTIPART_OVERHEAD_BYTES = 1024 * 1024


@dataclass(frozen=True)
class Upload:
    data: bytes
    filename: str
    content_type: str


class ASRApplication:
    def __init__(self, config: ASRConfig, *, transcriber, decoder=decode_media):
        self.config = config
        self.transcriber = transcriber
        self.decoder = decoder
        self._slots = threading.BoundedSemaphore(config.max_concurrency)
        self._queue_lock = threading.Lock()
        self._waiting = 0

    @property
    def ready(self) -> bool:
        return bool(self.config.enabled and self.transcriber is not None and getattr(self.transcriber, "ready", False))

    def health_payload(self) -> dict:
        return {
            "schemaVersion": 1,
            "live": True,
            "ready": self.ready,
            "backend": "openvino-genai",
            "device": self.config.device,
        }

    def transcribe(self, upload: Upload) -> Transcription:
        if not self.config.enabled:
            raise ASRServiceError("service_disabled", "Local ASR service is disabled.", status=503)
        if not self.ready:
            raise ASRServiceError("model_unavailable", "Local ASR model is not ready.", status=503)
        self._acquire_slot()
        started = time.monotonic()
        try:
            decoded = self.decoder(upload.data, upload.filename, upload.content_type, self.config)
            result = self.transcriber.transcribe(decoded)
            logger.info(
                "asr.request_complete duration_seconds=%.3f elapsed_ms=%.1f segments=%s",
                decoded.duration_seconds,
                (time.monotonic() - started) * 1000,
                len(result.segments),
            )
            return result
        finally:
            self._slots.release()

    def _acquire_slot(self) -> None:
        if self._slots.acquire(blocking=False):
            return
        with self._queue_lock:
            if self._waiting >= self.config.max_queue_size:
                raise ASRServiceError("busy", "Local ASR service is busy.", status=429)
            self._waiting += 1
        try:
            acquired = self._slots.acquire(timeout=self.config.queue_timeout_seconds)
        finally:
            with self._queue_lock:
                self._waiting -= 1
        if not acquired:
            raise ASRServiceError("busy", "Local ASR queue wait timed out.", status=429)


class ASRHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address, application: ASRApplication):
        self.application = application
        super().__init__(server_address, ASRRequestHandler)


class ASRRequestHandler(BaseHTTPRequestHandler):
    server: ASRHTTPServer

    def do_GET(self) -> None:
        if self.path == "/health/live":
            self._json(200, self.server.application.health_payload())
            return
        if self.path == "/health/ready":
            status = 200 if self.server.application.ready else 503
            self._json(status, self.server.application.health_payload())
            return
        self._error(ASRServiceError("not_found", "Endpoint not found.", status=404))

    def do_POST(self) -> None:
        if self.path != "/v1/transcriptions":
            self._error(ASRServiceError("not_found", "Endpoint not found.", status=404))
            return
        try:
            upload = self._read_upload()
            result = self.server.application.transcribe(upload)
            self._json(200, result.to_payload())
        except ASRServiceError as exc:
            logger.warning("asr.request_rejected code=%s", exc.code)
            self._error(exc)
        except Exception as exc:
            logger.exception("asr.request_failed error_type=%s", type(exc).__name__)
            self._error(ASRServiceError("internal_error", "Local ASR request failed.", status=500))

    def _read_upload(self) -> Upload:
        content_type = str(self.headers.get("Content-Type") or "")
        if not content_type.lower().startswith("multipart/form-data"):
            raise ASRServiceError("invalid_content_type", "multipart/form-data is required.", status=415)
        try:
            content_length = int(self.headers.get("Content-Length") or "0")
        except ValueError as exc:
            raise ASRServiceError("invalid_length", "Content-Length is invalid.", status=400) from exc
        body_limit = self.server.application.config.max_upload_bytes + MAX_MULTIPART_OVERHEAD_BYTES
        if content_length <= 0 or content_length > body_limit:
            raise ASRServiceError("upload_too_large", "Upload body exceeds the configured limit.", status=413)
        body = self.rfile.read(content_length)
        envelope = BytesParser(policy=policy.default).parsebytes(
            f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("ascii") + body
        )
        for part in envelope.iter_parts():
            if part.get_param("name", header="content-disposition") != "file":
                continue
            data = part.get_payload(decode=True) or b""
            return Upload(
                data=data,
                filename=part.get_filename() or "upload.bin",
                content_type=part.get_content_type() or "application/octet-stream",
            )
        raise ASRServiceError("missing_file", "Multipart field 'file' is required.", status=400)

    def _error(self, error: ASRServiceError) -> None:
        self._json(error.status, error.to_payload())

    def _json(self, status: int, payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args) -> None:
        logger.debug("asr.http " + format, *args)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        config = ASRConfig.from_env()
    except ASRConfigError as exc:
        raise SystemExit(f"Invalid ASR configuration: {exc}") from exc
    transcriber = None
    try:
        from services.openvino_asr.transcriber import OpenVINOTranscriber

        transcriber = OpenVINOTranscriber(config)
    except Exception as exc:
        logger.exception("asr.model_initialization_failed error_type=%s", type(exc).__name__)
    application = ASRApplication(config, transcriber=transcriber)
    server = ASRHTTPServer(("0.0.0.0", config.port), application)

    def shutdown(*_args) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    logger.info("asr.server_started port=%s ready=%s device=%s", config.port, application.ready, config.device)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()
        logger.info("asr.server_stopped")


if __name__ == "__main__":
    main()
