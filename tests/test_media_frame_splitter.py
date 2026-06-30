from __future__ import annotations

import unittest

from utils.media_frame_splitter import FrameSelector, FrameSplitConfig


class FrameSelectorTests(unittest.TestCase):
    def test_selects_all_frames_when_under_limit(self):
        selector = FrameSelector(FrameSplitConfig(hard_frame_limit=60))

        self.assertEqual(selector.select_indices(5), tuple(range(5)))

    def test_selects_all_frames_at_limit(self):
        selector = FrameSelector(FrameSplitConfig(hard_frame_limit=60))

        self.assertEqual(selector.select_indices(60), tuple(range(60)))

    def test_selects_sixty_evenly_spaced_frames_and_preserves_edges(self):
        selector = FrameSelector(FrameSplitConfig(hard_frame_limit=60))

        indices = selector.select_indices(600)

        self.assertEqual(len(indices), 60)
        self.assertEqual(indices[0], 0)
        self.assertEqual(indices[-1], 599)
        self.assertEqual(len(set(indices)), 60)

    def test_target_fps_uses_target_frame_count_over_duration(self):
        selector = FrameSelector(FrameSplitConfig(hard_frame_limit=60))

        self.assertAlmostEqual(selector.target_fps(duration_ms=30_000, frame_count=900), 2.0)
        self.assertAlmostEqual(selector.target_fps(duration_ms=10_000, frame_count=600), 6.0)


if __name__ == "__main__":
    unittest.main()
