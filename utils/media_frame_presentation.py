from __future__ import annotations

import io
import math
from dataclasses import dataclass

from utils.media_frame_splitter import MediaFrame

try:
    from PIL import Image, ImageChops, ImageStat, UnidentifiedImageError
except ImportError:
    Image = None
    ImageChops = None
    ImageStat = None
    UnidentifiedImageError = OSError


@dataclass(frozen=True)
class FramePresentationConfig:
    max_frames_per_sheet: int = 20
    columns: int = 5
    cell_long_edge_px: int = 192
    jpeg_quality: int = 85
    dedupe_thumbnail_px: int = 32
    similar_frame_rms_threshold: float = 3.0


@dataclass(frozen=True)
class FrameContactSheet:
    data: bytes
    mime_type: str
    frame_indices: tuple[int, ...]
    time_ms: tuple[int, ...]


@dataclass(frozen=True)
class FramePresentationResult:
    sheets: tuple[FrameContactSheet, ...]
    input_frame_count: int
    kept_frame_count: int
    dropped_similar_count: int


def present_media_frames(
    frames: list[MediaFrame] | tuple[MediaFrame, ...],
    config: FramePresentationConfig | None = None,
) -> FramePresentationResult | None:
    if Image is None or not frames:
        return None
    presentation_config = config or FramePresentationConfig()
    decoded = _decode_frames(frames)
    if not decoded:
        return None
    kept = _dedupe_decoded_frames(decoded, presentation_config)
    sheets = _build_contact_sheets(kept, presentation_config)
    if not sheets:
        return None
    return FramePresentationResult(
        sheets=tuple(sheets),
        input_frame_count=len(decoded),
        kept_frame_count=len(kept),
        dropped_similar_count=max(0, len(decoded) - len(kept)),
    )


def _decode_frames(frames: list[MediaFrame] | tuple[MediaFrame, ...]) -> list[tuple[MediaFrame, object]]:
    decoded = []
    for frame in frames:
        image = _open_rgb_image(frame.data)
        if image is not None:
            decoded.append((frame, image))
    return decoded


def _open_rgb_image(data: bytes):
    if Image is None or not data:
        return None
    try:
        with Image.open(io.BytesIO(data)) as image:
            return image.convert("RGB")
    except (OSError, UnidentifiedImageError):
        return None


def _dedupe_decoded_frames(decoded: list[tuple[MediaFrame, object]], config: FramePresentationConfig):
    if len(decoded) <= 2:
        return decoded
    kept = [decoded[0]]
    previous_thumbnail = _thumbnail(decoded[0][1], config.dedupe_thumbnail_px)
    for frame, image in decoded[1:-1]:
        thumbnail = _thumbnail(image, config.dedupe_thumbnail_px)
        if _rms_difference(previous_thumbnail, thumbnail) < config.similar_frame_rms_threshold:
            continue
        kept.append((frame, image))
        previous_thumbnail = thumbnail
    if kept[-1][0].frame_index != decoded[-1][0].frame_index:
        kept.append(decoded[-1])
    return kept


def _thumbnail(image, size_px: int):
    thumbnail = image.copy()
    thumbnail.thumbnail((max(1, size_px), max(1, size_px)), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (max(1, size_px), max(1, size_px)), "white")
    x = (canvas.width - thumbnail.width) // 2
    y = (canvas.height - thumbnail.height) // 2
    canvas.paste(thumbnail, (x, y))
    return canvas


def _rms_difference(left, right) -> float:
    if ImageChops is None or ImageStat is None:
        return 255.0
    diff = ImageChops.difference(left, right)
    stat = ImageStat.Stat(diff)
    return math.sqrt(sum(value * value for value in stat.rms) / max(1, len(stat.rms)))


def _build_contact_sheets(
    decoded: list[tuple[MediaFrame, object]],
    config: FramePresentationConfig,
) -> list[FrameContactSheet]:
    sheets = []
    limit = max(1, int(config.max_frames_per_sheet or 1))
    for start in range(0, len(decoded), limit):
        chunk = decoded[start:start + limit]
        sheet = _build_one_sheet(chunk, config)
        if sheet is not None:
            sheets.append(sheet)
    return sheets


def _build_one_sheet(
    decoded: list[tuple[MediaFrame, object]],
    config: FramePresentationConfig,
) -> FrameContactSheet | None:
    if not decoded:
        return None
    columns = max(1, min(int(config.columns or 1), len(decoded)))
    rows = int(math.ceil(len(decoded) / columns))
    cell_size = max(1, int(config.cell_long_edge_px or 1))
    canvas = Image.new("RGB", (columns * cell_size, rows * cell_size), "white")
    frame_indices = []
    time_ms = []
    for offset, (frame, image) in enumerate(decoded):
        thumbnail = _fit_into_cell(image, cell_size)
        x = (offset % columns) * cell_size + (cell_size - thumbnail.width) // 2
        y = (offset // columns) * cell_size + (cell_size - thumbnail.height) // 2
        canvas.paste(thumbnail, (x, y))
        frame_indices.append(frame.frame_index)
        time_ms.append(frame.time_ms)
    data = _save_jpeg(canvas, config.jpeg_quality)
    if not data:
        return None
    return FrameContactSheet(
        data=data,
        mime_type="image/jpeg",
        frame_indices=tuple(frame_indices),
        time_ms=tuple(time_ms),
    )


def _fit_into_cell(image, cell_size: int):
    fitted = image.copy()
    fitted.thumbnail((cell_size, cell_size), Image.Resampling.LANCZOS)
    return fitted


def _save_jpeg(image, quality: int) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=max(1, min(95, int(quality or 85))), optimize=True)
    return buffer.getvalue()
