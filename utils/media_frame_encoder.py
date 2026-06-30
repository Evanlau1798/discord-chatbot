from __future__ import annotations

import io

from utils.media_frame_splitter import FrameSplitConfig, MediaFrame

try:
    from PIL import Image
except ImportError:
    Image = None


class FrameEncoder:
    def __init__(self, config: FrameSplitConfig | None = None):
        self.config = config or FrameSplitConfig()

    def encode_pil_frame(self, frame, *, frame_index: int, time_ms: int) -> MediaFrame | None:
        if Image is None or frame is None:
            return None
        image = _flatten_to_rgb(frame)
        data = self._encode_with_budget(image)
        if not data:
            return None
        return MediaFrame(data=data, mime_type="image/jpeg", frame_index=frame_index, time_ms=time_ms)

    def fit_total_budget(self, frames: list[MediaFrame] | tuple[MediaFrame, ...]) -> tuple[MediaFrame, ...]:
        selected = [frame for frame in frames if frame.data]
        while selected and _total_bytes(selected) > self.config.max_total_bytes:
            selected = _drop_every_other_middle_frame(selected)
        return tuple(selected)

    def _encode_with_budget(self, image) -> bytes:
        edge = self.config.max_long_edge_px
        while edge >= self.config.fallback_long_edge_px:
            resized = _resize_to_edge(image, edge)
            for quality in range(self.config.jpeg_quality, self.config.min_jpeg_quality - 1, -10):
                data = _save_jpeg(resized, quality)
                if len(data) <= self.config.max_frame_bytes:
                    return data
            if edge == self.config.fallback_long_edge_px:
                return _save_jpeg(resized, self.config.min_jpeg_quality)
            edge = max(self.config.fallback_long_edge_px, int(edge * 0.8))
        return b""


def _flatten_to_rgb(frame):
    image = frame.convert("RGBA")
    background = Image.new("RGBA", image.size, (255, 255, 255, 255))
    background.alpha_composite(image)
    return background.convert("RGB")


def _resize_to_edge(image, max_edge: int):
    width, height = image.size
    longest = max(width, height)
    if longest <= max_edge:
        return image
    scale = max_edge / float(longest)
    size = (max(1, int(width * scale)), max(1, int(height * scale)))
    return image.resize(size, Image.Resampling.LANCZOS)


def _save_jpeg(image, quality: int) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality, optimize=True)
    return buffer.getvalue()


def _total_bytes(frames: list[MediaFrame]) -> int:
    return sum(len(frame.data) for frame in frames)


def _drop_every_other_middle_frame(frames: list[MediaFrame]) -> list[MediaFrame]:
    if len(frames) <= 2:
        return frames[:1]
    middle = frames[1:-1:2]
    return [frames[0], *middle, frames[-1]]
