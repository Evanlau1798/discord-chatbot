from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from utils.media_frame_splitter import FrameSplitConfig
from utils.video_frame_splitter import (
    DEFAULT_FFMPEG_BIN,
    DEFAULT_FFPROBE_BIN,
    FfmpegVideoFrameSplitter,
    _resolve_binary,
)


class FfmpegVideoFrameSplitterTests(unittest.TestCase):
    def test_default_binaries_are_path_lookup_names(self):
        with patch.dict(os.environ, {}, clear=True):
            splitter = FfmpegVideoFrameSplitter(config=FrameSplitConfig())

        self.assertEqual(DEFAULT_FFMPEG_BIN, "ffmpeg")
        self.assertEqual(DEFAULT_FFPROBE_BIN, "ffprobe")
        self.assertEqual(splitter.ffmpeg_bin, "ffmpeg")
        self.assertEqual(splitter.ffprobe_bin, "ffprobe")

    def test_resolve_binary_expands_user_home(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            binary_path = Path(temp_dir) / ".local" / "bin" / "ffmpeg"
            binary_path.parent.mkdir(parents=True)
            binary_path.write_text("#!/bin/sh\n", encoding="utf-8")
            binary_path.chmod(0o755)

            with patch.dict(os.environ, {"HOME": temp_dir}, clear=True):
                resolved = _resolve_binary("~/.local/bin/ffmpeg")

        self.assertEqual(resolved, str(binary_path))

    def test_missing_binaries_return_none(self):
        splitter = FfmpegVideoFrameSplitter(config=FrameSplitConfig(), ffmpeg_bin="/missing/ffmpeg", ffprobe_bin="/missing/ffprobe")

        self.assertIsNone(splitter.split(b"video-bytes", "video/mp4"))

    def test_builds_expected_fps_for_ten_second_video(self):
        splitter = FfmpegVideoFrameSplitter(config=FrameSplitConfig(hard_frame_limit=60), ffmpeg_bin="/bin/ffmpeg", ffprobe_bin="/bin/ffprobe")
        calls = []

        def fake_run(args, **kwargs):
            calls.append(args)
            if args[0] == "/bin/ffprobe":
                return _completed(stdout=json.dumps({
                    "format": {"duration": "10.0"},
                    "streams": [{"codec_type": "video", "nb_frames": "300"}],
                }))
            output_dir = Path(args[-1]).parent
            output_dir.mkdir(parents=True, exist_ok=True)
            for index in range(3):
                (output_dir / f"frame_{index + 1:05d}.jpg").write_bytes(b"jpeg-frame")
            return _completed()

        with patch("utils.video_frame_splitter.shutil.which", side_effect=lambda value: value), patch(
            "utils.video_frame_splitter.subprocess.run",
            side_effect=fake_run,
        ):
            result = splitter.split(b"video-bytes", "video/mp4")

        self.assertIsNotNone(result)
        ffmpeg_args = calls[1]
        self.assertIn("fps=6.000", " ".join(ffmpeg_args))
        self.assertEqual(result.duration_ms, 10_000)
        self.assertEqual(result.frame_count, 300)
        self.assertEqual(len(result.frames), 3)

    def test_builds_expected_fps_for_thirty_second_video(self):
        splitter = FfmpegVideoFrameSplitter(config=FrameSplitConfig(hard_frame_limit=60), ffmpeg_bin="/bin/ffmpeg", ffprobe_bin="/bin/ffprobe")

        with patch("utils.video_frame_splitter.shutil.which", side_effect=lambda value: value), patch(
            "utils.video_frame_splitter.subprocess.run",
            return_value=_completed(stdout=json.dumps({
                "format": {"duration": "30.0"},
                "streams": [{"codec_type": "video", "nb_frames": "900"}],
            })),
        ):
            metadata = splitter.probe_metadata(Path("input.mp4"))

        self.assertAlmostEqual(splitter.target_fps(metadata), 2.0)


def _completed(stdout: str = ""):
    return type("Completed", (), {"stdout": stdout, "stderr": "", "returncode": 0})()


if __name__ == "__main__":
    unittest.main()
