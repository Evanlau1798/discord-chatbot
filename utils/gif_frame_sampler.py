from __future__ import annotations

import io
from dataclasses import dataclass

try:
    from PIL import Image, ImageSequence, UnidentifiedImageError
except ImportError:
    Image = None
    ImageSequence = None
    UnidentifiedImageError = OSError

DEFAULT_FRAME_DURATION_MS = 100
MAX_GIF_SAMPLE_FRAMES = 12
MAX_FRAME_EDGE_PX = 1280
MIN_FRAME_EDGE_PX = 384
MAX_FRAME_BYTES = 1_200_000
PNG_COMPRESS_LEVEL = 6


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


def is_gif_mime_type(mime_type: str) -> bool:
    return str(mime_type or "").split(";", 1)[0].strip().lower() == "image/gif"


def sample_gif_frames(image_bytes: bytes, *, max_frames: int = MAX_GIF_SAMPLE_FRAMES) -> GifFrameSamplingResult | None:
    if Image is None or ImageSequence is None:
        return None
    if not isinstance(image_bytes, (bytes, bytearray)) or not image_bytes:
        return None
    try:
        image = Image.open(io.BytesIO(image_bytes))
    except (OSError, UnidentifiedImageError):
        return None
    if str(getattr(image, "format", "") or "").upper() != "GIF":
        return None
    durations = _gif_frame_durations(image)
    selected_indices = select_gif_frame_indices(durations, max_frames=max_frames)
    selected_set = set(selected_indices)
    frame_start_times = _frame_start_times(durations)
    samples = []
    seen = set()
    try:
        for index, frame in enumerate(ImageSequence.Iterator(image)):
            if index not in selected_set:
                continue
            png_bytes = _frame_to_png_bytes(frame)
            if not png_bytes or png_bytes in seen:
                continue
            seen.add(png_bytes)
            samples.append(
                GifFrameSample(
                    data=png_bytes,
                    mime_type="image/png",
                    frame_index=index,
                    time_ms=frame_start_times[index] if index < len(frame_start_times) else 0,
                )
            )
    finally:
        image.close()
    if not samples:
        return None
    return GifFrameSamplingResult(
        frames=tuple(samples),
        frame_count=len(durations),
        duration_ms=sum(durations),
        sampled_all=len(samples) == len(durations),
    )


def select_gif_frame_indices(durations_ms: list[int] | tuple[int, ...], *, max_frames: int = MAX_GIF_SAMPLE_FRAMES) -> tuple[int, ...]:
    durations = _normalize_durations(durations_ms)
    frame_count = len(durations)
    if frame_count <= 0 or max_frames <= 0:
        return ()
    if frame_count <= min(6, max_frames):
        return tuple(range(frame_count))
    total_duration = sum(durations)
    target_count = _target_frame_count(frame_count, total_duration, max_frames)
    if target_count >= frame_count:
        return tuple(range(frame_count))
    indices = _time_spaced_indices(durations, target_count)
    if len(indices) < target_count:
        indices = _fill_missing_indices(indices, frame_count, target_count)
    return tuple(indices[:target_count])


def _target_frame_count(frame_count: int, total_duration_ms: int, max_frames: int) -> int:
    capped_max = min(frame_count, max(1, max_frames))
    if total_duration_ms <= 2_000:
        return capped_max
    if total_duration_ms <= 5_000:
        return min(capped_max, 8)
    if total_duration_ms <= 10_000:
        return min(capped_max, 10)
    return capped_max


def _time_spaced_indices(durations: list[int], target_count: int) -> list[int]:
    if target_count <= 1:
        return [0]
    total_duration = sum(durations)
    targets = [round(index * max(0, total_duration - 1) / (target_count - 1)) for index in range(target_count)]
    cumulative_end = []
    elapsed = 0
    for duration in durations:
        elapsed += duration
        cumulative_end.append(elapsed)
    indices = []
    for target in targets:
        selected = 0
        for index, end_time in enumerate(cumulative_end):
            if target < end_time:
                selected = index
                break
        if selected not in indices:
            indices.append(selected)
    if indices[-1] != len(durations) - 1:
        indices.append(len(durations) - 1)
    return indices


def _fill_missing_indices(indices: list[int], frame_count: int, target_count: int) -> list[int]:
    selected = list(dict.fromkeys(indices))
    for index in _index_spaced_indices(frame_count, target_count):
        if index not in selected:
            selected.append(index)
        if len(selected) >= target_count:
            break
    return sorted(selected)


def _index_spaced_indices(frame_count: int, target_count: int) -> list[int]:
    if target_count <= 1:
        return [0]
    return [round(index * (frame_count - 1) / (target_count - 1)) for index in range(target_count)]


def _gif_frame_durations(image) -> list[int]:
    durations = []
    for frame in ImageSequence.Iterator(image):
        duration = int(frame.info.get("duration") or DEFAULT_FRAME_DURATION_MS)
        durations.append(max(20, duration))
    return durations or [DEFAULT_FRAME_DURATION_MS]


def _normalize_durations(durations_ms: list[int] | tuple[int, ...]) -> list[int]:
    return [max(20, int(duration or DEFAULT_FRAME_DURATION_MS)) for duration in durations_ms or []]


def _frame_start_times(durations: list[int]) -> list[int]:
    starts = []
    elapsed = 0
    for duration in durations:
        starts.append(elapsed)
        elapsed += duration
    return starts


def _frame_to_png_bytes(frame) -> bytes:
    image = frame.convert("RGBA")
    image = _resize_to_edge(image, MAX_FRAME_EDGE_PX)
    png_bytes = _save_png(image)
    edge = MAX_FRAME_EDGE_PX
    while len(png_bytes) > MAX_FRAME_BYTES and edge > MIN_FRAME_EDGE_PX:
        edge = max(MIN_FRAME_EDGE_PX, int(edge * 0.8))
        png_bytes = _save_png(_resize_to_edge(image, edge))
    return png_bytes


def _resize_to_edge(image, max_edge: int):
    width, height = image.size
    longest = max(width, height)
    if longest <= max_edge:
        return image
    scale = max_edge / float(longest)
    size = (max(1, int(width * scale)), max(1, int(height * scale)))
    return image.resize(size, Image.Resampling.LANCZOS)


def _save_png(image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True, compress_level=PNG_COMPRESS_LEVEL)
    return buffer.getvalue()
