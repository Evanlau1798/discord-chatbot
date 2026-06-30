from __future__ import annotations

import io
import unittest

from utils.gif_frame_sampler import sample_gif_frames, sample_webp_frames, select_gif_frame_indices


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


if __name__ == "__main__":
    unittest.main()
