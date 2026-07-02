from __future__ import annotations

import binascii
import io
import struct
import unittest
import zlib

from utils.gif_frame_sampler import sample_apng_frames, sample_gif_frames, sample_webp_frames, select_gif_frame_indices


class GifFrameSamplerTests(unittest.TestCase):
    def test_selects_all_frames_for_few_frames(self):
        self.assertEqual(select_gif_frame_indices([100] * 5), (0, 1, 2, 3, 4))

    def test_selects_all_frames_for_short_gif_within_limit(self):
        self.assertEqual(select_gif_frame_indices([100] * 60), tuple(range(60)))

    def test_short_gif_with_many_frames_uses_max_frames(self):
        indices = select_gif_frame_indices([100] * 120)

        self.assertEqual(len(indices), 60)
        self.assertEqual(indices[0], 0)
        self.assertEqual(indices[-1], 119)

    def test_long_gif_uses_sixty_frame_hard_limit(self):
        self.assertEqual(len(select_gif_frame_indices([100] * 300)), 60)

    def test_samples_short_gif_to_jpeg_frames(self):
        Image = _load_pillow_image()
        frames = []
        for color in ("red", "green", "blue", "yellow"):
            frames.append(Image.new("RGBA", (24, 24), color))
        buffer = io.BytesIO()
        frames[0].save(
            buffer,
            format="GIF",
            save_all=True,
            append_images=frames[1:],
            duration=[80, 90, 100, 110],
            loop=0,
        )

        result = sample_gif_frames(buffer.getvalue())

        self.assertIsNotNone(result)
        self.assertEqual(result.frame_count, 4)
        self.assertEqual(result.duration_ms, 380)
        self.assertTrue(result.sampled_all)
        self.assertEqual(len(result.frames), 4)
        self.assertTrue(all(frame.mime_type == "image/jpeg" for frame in result.frames))
        self.assertTrue(all(frame.data.startswith(b"\xff\xd8") for frame in result.frames))

    def test_samples_static_webp_to_jpeg_frame(self):
        Image = _load_pillow_image()
        _require_webp_support()
        buffer = io.BytesIO()
        Image.new("RGBA", (24, 24), "blue").save(buffer, format="WEBP")

        result = sample_webp_frames(buffer.getvalue())

        self.assertIsNotNone(result)
        self.assertEqual(result.frame_count, 1)
        self.assertTrue(result.sampled_all)
        self.assertEqual(len(result.frames), 1)
        self.assertEqual(result.frames[0].mime_type, "image/jpeg")
        self.assertTrue(result.frames[0].data.startswith(b"\xff\xd8"))

    def test_samples_animated_webp_to_jpeg_frames(self):
        Image = _load_pillow_image()
        _require_webp_support(animated=True)
        frames = [Image.new("RGBA", (24, 24), color) for color in ("red", "green", "blue")]
        buffer = io.BytesIO()
        frames[0].save(
            buffer,
            format="WEBP",
            save_all=True,
            append_images=frames[1:],
            duration=[80, 90, 100],
            loop=0,
        )

        result = sample_webp_frames(buffer.getvalue())

        self.assertIsNotNone(result)
        self.assertEqual(result.frame_count, 3)
        self.assertGreaterEqual(result.duration_ms, 60)
        self.assertTrue(result.sampled_all)
        self.assertEqual(len(result.frames), 3)
        self.assertEqual([frame.frame_index for frame in result.frames], [0, 1, 2])
        self.assertTrue(all(frame.mime_type == "image/jpeg" for frame in result.frames))

    def test_samples_apng_to_jpeg_frames(self):
        Image = _load_pillow_image()
        frames = [Image.new("RGBA", (24, 24), color) for color in ("red", "green", "blue")]
        buffer = io.BytesIO()
        frames[0].save(
            buffer,
            format="PNG",
            save_all=True,
            append_images=frames[1:],
            duration=[80, 90, 100],
            loop=0,
        )

        result = sample_apng_frames(buffer.getvalue())

        self.assertIsNotNone(result)
        self.assertEqual(result.frame_count, 3)
        self.assertEqual(result.duration_ms, 270)
        self.assertTrue(result.sampled_all)
        self.assertEqual([frame.frame_index for frame in result.frames], [0, 1, 2])
        self.assertTrue(all(frame.mime_type == "image/jpeg" for frame in result.frames))

    def test_static_png_is_not_sampled_as_apng(self):
        Image = _load_pillow_image()
        buffer = io.BytesIO()
        Image.new("RGBA", (24, 24), "blue").save(buffer, format="PNG")

        result = sample_apng_frames(buffer.getvalue())

        self.assertIsNone(result)

    def test_samples_palette_apng_with_transparency_using_palette_colors(self):
        Image = _load_pillow_image()

        result = sample_apng_frames(_palette_apng_bytes())

        self.assertIsNotNone(result)
        self.assertEqual(result.frame_count, 2)
        first = Image.open(io.BytesIO(result.frames[0].data)).convert("RGB")
        second = Image.open(io.BytesIO(result.frames[1].data)).convert("RGB")
        self.assertGreater(first.getpixel((2, 2))[2], 180)
        self.assertGreater(second.getpixel((2, 2))[0], 180)


def _load_pillow_image():
    try:
        from PIL import Image
    except ImportError:
        raise unittest.SkipTest("Pillow is not installed")
    return Image


def _require_webp_support(*, animated: bool = False):
    try:
        from PIL import features
    except ImportError:
        raise unittest.SkipTest("Pillow is not installed")
    if not features.check("webp"):
        raise unittest.SkipTest("Pillow WebP support is not available")
    if animated and not features.check("webp_anim"):
        raise unittest.SkipTest("Pillow animated WebP support is not available")


def _palette_apng_bytes() -> bytes:
    width = height = 4
    return b"\x89PNG\r\n\x1a\n" + b"".join((
        _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 3, 0, 0, 0)),
        _png_chunk(b"PLTE", bytes([255, 255, 255, 80, 120, 220, 220, 80, 80])),
        _png_chunk(b"tRNS", bytes([0, 255, 255])),
        _png_chunk(b"acTL", struct.pack(">II", 2, 0)),
        _png_chunk(b"fcTL", _apng_frame_control(0, width, height)),
        _png_chunk(b"IDAT", _indexed_png_scanlines(width, height, 1)),
        _png_chunk(b"fcTL", _apng_frame_control(1, width, height)),
        _png_chunk(b"fdAT", struct.pack(">I", 2) + _indexed_png_scanlines(width, height, 2)),
        _png_chunk(b"IEND"),
    ))


def _apng_frame_control(sequence: int, width: int, height: int) -> bytes:
    return struct.pack(">IIIIIHHBB", sequence, width, height, 0, 0, 60, 1000, 0, 0)


def _indexed_png_scanlines(width: int, height: int, color_index: int) -> bytes:
    return zlib.compress(b"".join(b"\x00" + bytes([color_index]) * width for _ in range(height)))


def _png_chunk(kind: bytes, data: bytes = b"") -> bytes:
    crc = binascii.crc32(kind + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)


if __name__ == "__main__":
    unittest.main()
