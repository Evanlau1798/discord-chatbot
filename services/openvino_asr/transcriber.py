from __future__ import annotations

from pathlib import Path

from services.openvino_asr.config import ASRConfig
from services.openvino_asr.media import DecodedAudio
from services.openvino_asr.protocol import ASRServiceError, Transcription, normalize_segments

REQUIRED_MODEL_FILE = "openvino_encoder_model.xml"


class OpenVINOTranscriber:
    backend = "openvino-genai"

    def __init__(self, config: ASRConfig):
        self.config = config
        self.ready = False
        self.error = ""
        try:
            self._initialize()
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            raise

    def _initialize(self) -> None:
        import openvino as ov
        import openvino_genai as ov_genai

        _ensure_model(self.config)
        self.config.cache_dir.mkdir(parents=True, exist_ok=True)
        available_devices = tuple(ov.Core().available_devices)
        if not _device_available(self.config.device, available_devices):
            raise ASRServiceError("gpu_unavailable", "Configured OpenVINO GPU is unavailable.", status=503)
        self._pipeline = ov_genai.WhisperPipeline(
            self.config.model_dir,
            self.config.device,
            CACHE_DIR=str(self.config.cache_dir),
        )
        self.ready = True

    def transcribe(self, decoded: DecodedAudio) -> Transcription:
        if not self.ready:
            raise ASRServiceError("model_unavailable", "ASR model is not ready.", status=503)
        generation_config = self._pipeline.get_generation_config()
        generation_config.task = "transcribe"
        generation_config.return_timestamps = True
        result = self._pipeline.generate(list(decoded.samples), generation_config)
        text = _result_text(result).strip()
        return Transcription(
            text=text,
            language="",
            duration_seconds=decoded.duration_seconds,
            segments=normalize_segments(getattr(result, "chunks", None) or (), duration_seconds=decoded.duration_seconds),
            backend=self.backend,
            device=self.config.device,
        )


def _ensure_model(config: ASRConfig) -> None:
    model_dir = Path(config.model_dir)
    if (model_dir / REQUIRED_MODEL_FILE).is_file():
        return
    model_dir.mkdir(parents=True, exist_ok=True)
    from huggingface_hub import snapshot_download

    snapshot_download(
        repo_id=config.model_id,
        revision=config.model_revision,
        local_dir=str(model_dir),
    )
    if not (model_dir / REQUIRED_MODEL_FILE).is_file():
        raise RuntimeError("downloaded model is incomplete")


def _device_available(device: str, available_devices: tuple[str, ...]) -> bool:
    normalized = device.upper()
    return any(candidate.upper() == normalized or candidate.upper().startswith(f"{normalized}.") for candidate in available_devices)


def _result_text(result) -> str:
    texts = getattr(result, "texts", None)
    if texts:
        return str(texts[0])
    return str(result)
