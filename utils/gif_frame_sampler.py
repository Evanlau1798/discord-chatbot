from __future__ import annotations

import io
from dataclasses import dataclass

from utils.media_frame_encoder import FrameEncoder
from utils.media_frame_splitter import FrameSelector, FrameSplitConfig, MediaFrame

try:
    from PIL import Image, ImageSequence, UnidentifiedImageError
except ImportError:
    Image = None
    ImageSequence = None
    UnidentifiedImageError = OSError

DEFAULT_FRAME_DURATION_MS = 100
MAX_GIF_SAMPLE_FRAMES = 60


@dataclass(frozen=True)
class GifFrameSample:
    data: bytes
    mime_type: str
    frame_index: int
    time_ms: int


@dataclass(frozen=True)
class GifFrameSamplingResult:
    frames: tuple[GifFrameSample, ...]
    frame_count: int
    duration_ms: int
    sampled_all: bool


class AnimatedImageFrameSplitter:
    def __init__(self, allowed_formats: set[str], config: FrameSplitConfig | None = None):
        self.allowed_formats = {str(item or "").upper() for item in allowed_formats}
        self.config = config or FrameSplitConfig(hard_frame_limit=MAX_GIF_SAMPLE_FRAMES)
        self.selector = FrameSelector(self.config)
        self.encoder = FrameEncoder(self.config)

    def split(self, image_bytes: bytes, *, max_frames: int | None = None) -> GifFrameSamplingResult | None:
        if Image is None or ImageSequence is None:
            return None
        if not isinstance(image_bytes, (bytes, bytearray)) or not image_bytes:
            return None
        try:
            image = Image.open(io.BytesIO(image_bytes))
        except (OSError, UnidentifiedImageError):
            return None
        try:
            if str(getattr(image, "format", "") or "").upper() not in self.allowed_formats:
                return None
            return self._split_open_image(image, max_frames=max_frames)
        finally:
            image.close()

    def _split_open_image(self, image, *, max_frames: int | None) -> GifFrameSamplingResult | None:
        durations = _gif_frame_durations(image)
        selected_indices = self.selector.select_indices_by_durations(durations, max_frames=max_frames)
        selected_set = set(selected_indices)
        frame_start_times = _frame_start_times(durations)
        frames = []
        seen = set()
        for index, frame in enumerate(ImageSequence.Iterator(image)):
            if index not in selected_set:
                continue
            encoded = self.encoder.encode_pil_frame(
                frame,
                frame_index=index,
                time_ms=frame_start_times[index] if index < len(frame_start_times) else 0,
            )
            if encoded is None or encoded.data in seen:
                continue
            seen.add(encoded.data)
            frames.append(GifFrameSample(
                data=encoded.data,
                mime_type=encoded.mime_type,
                frame_index=encoded.frame_index,
                time_ms=encoded.time_ms,
            ))
        frames = _fit_total_budget(frames, self.encoder)
        if not frames:
            return None
        return GifFrameSamplingResult(
            frames=tuple(frames),
            frame_count=len(durations),
            duration_ms=sum(durations),
            sampled_all=len(frames) == len(durations),
        )


class GifFrameSplitter(AnimatedImageFrameSplitter):
    def __init__(self, config: FrameSplitConfig | None = None):
        super().__init__({"GIF"}, config=config)


class WebpFrameSplitter(AnimatedImageFrameSplitter):
    def __init__(self, config: FrameSplitConfig | None = None):
        super().__init__({"WEBP"}, config=config)


class ApngFrameSplitter(AnimatedImageFrameSplitter):
    def __init__(self, config: FrameSplitConfig | None = None):
        super().__init__({"PNG"}, config=config)

    def _split_open_image(self, image, *, max_frames: int | None) -> GifFrameSamplingResult | None:
        if not getattr(image, "is_animated", False) or int(getattr(image, "n_frames", 1) or 1) <= 1:
            return None
        return super()._split_open_image(image, max_frames=max_frames)


def is_gif_mime_type(mime_type: str) -> bool:
    return str(mime_type or "").split(";", 1)[0].strip().lower() == "image/gif"


def is_webp_mime_type(mime_type: str) -> bool:
    return str(mime_type or "").split(";", 1)[0].strip().lower() == "image/webp"


def is_apng_mime_type(mime_type: str) -> bool:
    normalized = str(mime_type or "").split(";", 1)[0].strip().lower()
    return normalized in {"image/png", "image/apng"}


def sample_gif_frames(image_bytes: bytes, *, max_frames: int = MAX_GIF_SAMPLE_FRAMES) -> GifFrameSamplingResult | None:
    return GifFrameSplitter().split(image_bytes, max_frames=max_frames)


def sample_webp_frames(image_bytes: bytes, *, max_frames: int = MAX_GIF_SAMPLE_FRAMES) -> GifFrameSamplingResult | None:
    return WebpFrameSplitter().split(image_bytes, max_frames=max_frames)


def sample_apng_frames(image_bytes: bytes, *, max_frames: int = MAX_GIF_SAMPLE_FRAMES) -> GifFrameSamplingResult | None:
    return ApngFrameSplitter().split(image_bytes, max_frames=max_frames)


def select_gif_frame_indices(durations_ms: list[int] | tuple[int, ...], *, max_frames: int = MAX_GIF_SAMPLE_FRAMES) -> tuple[int, ...]:
    return FrameSelector(FrameSplitConfig(hard_frame_limit=max_frames)).select_indices_by_durations(durations_ms)


def _gif_frame_durations(image) -> list[int]:
    durations = []
    for frame in ImageSequence.Iterator(image):
        duration = int(frame.info.get("duration") or DEFAULT_FRAME_DURATION_MS)
        durations.append(max(20, duration))
    return durations or [DEFAULT_FRAME_DURATION_MS]


def _frame_start_times(durations: list[int]) -> list[int]:
    starts = []
    elapsed = 0
    for duration in durations:
        starts.append(elapsed)
        elapsed += duration
    return starts


def _fit_total_budget(frames: list[GifFrameSample], encoder: FrameEncoder) -> tuple[GifFrameSample, ...]:
    media_frames = [
        MediaFrame(
            data=frame.data,
            mime_type=frame.mime_type,
            frame_index=frame.frame_index,
            time_ms=frame.time_ms,
        )
        for frame in frames
    ]
    fitted = encoder.fit_total_budget(media_frames)
    return tuple(GifFrameSample(
        data=frame.data,
        mime_type=frame.mime_type,
        frame_index=frame.frame_index,
        time_ms=frame.time_ms,
    ) for frame in fitted)
