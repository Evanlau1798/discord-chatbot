from __future__ import annotations

import io
import unittest

from utils.media_frame_presentation import FramePresentationConfig, present_media_frames
from utils.media_frame_splitter import MediaFrame


class MediaFramePresentationTests(unittest.TestCase):
    def test_dedupes_similar_frames_and_keeps_temporal_order(self):
        frames = (
            _frame("red", 0, 0),
            _frame("red", 1, 40),
            _frame("blue", 2, 80),
            _frame("blue", 3, 120),
            _frame("green", 4, 160),
        )

        result = present_media_frames(
            frames,
            FramePresentationConfig(max_frames_per_sheet=8, columns=4, cell_long_edge_px=32),
        )

        self.assertIsNotNone(result)
        self.assertEqual(result.input_frame_count, 5)
        self.assertEqual(result.kept_frame_count, 3)
        self.assertEqual(result.dropped_similar_count, 2)
        self.assertEqual(result.sheets[0].frame_indices, (0, 2, 4))

    def test_splits_contact_sheets_by_frame_limit_without_drawing_text(self):
        frames = tuple(_frame(color, index, index * 50) for index, color in enumerate((
            "red",
            "green",
            "blue",
            "yellow",
            "purple",
        )))

        result = present_media_frames(
            frames,
            FramePresentationConfig(max_frames_per_sheet=4, columns=2, cell_long_edge_px=24),
        )

        self.assertIsNotNone(result)
        self.assertEqual(len(result.sheets), 2)
        self.assertEqual(result.sheets[0].frame_indices, (0, 1, 2, 3))
        self.assertEqual(result.sheets[1].frame_indices, (4,))
        with Image.open(io.BytesIO(result.sheets[0].data)) as sheet:
            self.assertEqual(sheet.size, (48, 48))


def _frame(color: str, frame_index: int, time_ms: int) -> MediaFrame:
    buffer = io.BytesIO()
    Image.new("RGB", (24, 24), color).save(buffer, format="JPEG", quality=90)
    return MediaFrame(
        data=buffer.getvalue(),
        mime_type="image/jpeg",
        frame_index=frame_index,
        time_ms=time_ms,
    )


def _load_pillow_image():
    try:
        from PIL import Image as PillowImage
    except ImportError:
        raise unittest.SkipTest("Pillow is not installed")
    return PillowImage


Image = _load_pillow_image()


if __name__ == "__main__":
    unittest.main()
