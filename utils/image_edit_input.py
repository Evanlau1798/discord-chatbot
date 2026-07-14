from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_IMAGE_EDIT_INPUT_BYTES = 20 * 1024 * 1024
MAX_IMAGE_EDIT_PIXELS = 40_000_000


@dataclass(frozen=True)
class NormalizedImageInput:
    filename: str
    mime_type: str
    data: bytes


def normalize_image_input(data: bytes, filename: str = "image.png") -> NormalizedImageInput:
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise ValueError("來源圖片是無效的空白資料")
    if len(data) > MAX_IMAGE_EDIT_INPUT_BYTES:
        raise ValueError("來源圖片超過 20 MB 上限")
    try:
        with Image.open(io.BytesIO(bytes(data))) as opened:
            width, height = opened.size
            if width <= 0 or height <= 0 or width * height > MAX_IMAGE_EDIT_PIXELS:
                raise ValueError("來源圖片尺寸無效或像素數過高")
            opened.seek(0)
            opened.load()
            normalized = ImageOps.exif_transpose(opened)
            if normalized.mode not in {"RGB", "RGBA"}:
                normalized = normalized.convert("RGBA" if "A" in normalized.getbands() else "RGB")
            output = io.BytesIO()
            normalized.save(output, format="PNG")
    except (OSError, UnidentifiedImageError) as exc:
        raise ValueError("來源圖片包含無效或不支援的圖片資料") from exc
    normalized_data = output.getvalue()
    if len(normalized_data) > MAX_IMAGE_EDIT_INPUT_BYTES:
        raise ValueError("正規化後的來源圖片超過 20 MB 上限")
    safe_stem = Path(str(filename or "image")).stem.strip() or "image"
    return NormalizedImageInput(filename=f"{safe_stem}.png", mime_type="image/png", data=normalized_data)
