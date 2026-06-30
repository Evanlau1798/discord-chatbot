from __future__ import annotations

import json
import logging
import mimetypes
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from utils.media_frame_splitter import FrameSelector, FrameSplitConfig, MediaFrame, MediaSplitResult

DEFAULT_FFMPEG_BIN = "/home/evanlau/.local/bin/ffmpeg"
DEFAULT_FFPROBE_BIN = "/home/evanlau/.local/bin/ffprobe"
FFMPEG_BIN_ENV = "FFMPEG_BIN"
FFPROBE_BIN_ENV = "FFPROBE_BIN"
logger = logging.getLogger("discord.utils.video_frame_splitter")


@dataclass(frozen=True)
class VideoMetadata:
    duration_ms: int
    frame_count: int


class FfmpegVideoFrameSplitter:
    def __init__(
        self,
        config: FrameSplitConfig | None = None,
        *,
        ffmpeg_bin: str | None = None,
        ffprobe_bin: str | None = None,
    ):
        self.config = config or FrameSplitConfig()
        self.selector = FrameSelector(self.config)
        self.ffmpeg_bin = ffmpeg_bin or os.getenv(FFMPEG_BIN_ENV, DEFAULT_FFMPEG_BIN)
        self.ffprobe_bin = ffprobe_bin or os.getenv(FFPROBE_BIN_ENV, DEFAULT_FFPROBE_BIN)

    def split(self, video_bytes: bytes, mime_type: str, *, filename: str = "") -> MediaSplitResult | None:
        if not isinstance(video_bytes, (bytes, bytearray)) or not video_bytes:
            return None
        ffmpeg = _resolve_binary(self.ffmpeg_bin)
        ffprobe = _resolve_binary(self.ffprobe_bin)
        if not ffmpeg or not ffprobe:
            return None
        try:
            return self._split_with_binaries(bytes(video_bytes), mime_type, filename, ffmpeg, ffprobe)
        except Exception as exc:
            logger.warning("video.frame_split_failed error_type=%s error=%s", type(exc).__name__, exc)
            return None

    def probe_metadata(self, path: Path) -> VideoMetadata:
        ffprobe = _resolve_binary(self.ffprobe_bin)
        if not ffprobe:
            return VideoMetadata(duration_ms=0, frame_count=0)
        completed = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "format=duration:stream=nb_frames,avg_frame_rate",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
        if completed.returncode != 0:
            return VideoMetadata(duration_ms=0, frame_count=0)
        return _parse_probe_output(completed.stdout)

    def target_fps(self, metadata: VideoMetadata) -> float:
        return self.selector.target_fps(duration_ms=metadata.duration_ms, frame_count=metadata.frame_count)

    def _split_with_binaries(
        self,
        video_bytes: bytes,
        mime_type: str,
        filename: str,
        ffmpeg: str,
        ffprobe: str,
    ) -> MediaSplitResult | None:
        temp_root = Path("tmp") if Path("tmp").exists() else None
        with tempfile.TemporaryDirectory(prefix="video-frames-", dir=temp_root) as temp_dir:
            work_dir = Path(temp_dir)
            input_path = work_dir / f"input{_video_suffix(mime_type, filename)}"
            output_pattern = work_dir / "frame_%05d.jpg"
            input_path.write_bytes(video_bytes)
            self.ffprobe_bin = ffprobe
            metadata = self.probe_metadata(input_path)
            if metadata.duration_ms <= 0:
                return None
            fps = self.target_fps(metadata)
            command = [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(input_path),
                "-vf",
                f"fps={fps:.3f},scale='if(gt(iw,ih),min({self.config.max_long_edge_px},iw),-2)':'if(gt(iw,ih),-2,min({self.config.max_long_edge_px},ih))'",
                "-frames:v",
                str(self.config.hard_frame_limit),
                "-q:v",
                "3",
                str(output_pattern),
            ]
            completed = subprocess.run(command, capture_output=True, text=True, check=False, timeout=60)
            if completed.returncode != 0:
                return None
            frames = _read_output_frames(work_dir, fps)
            if not frames:
                return None
            frames = _fit_total_budget(frames, self.config.max_total_bytes)
            return MediaSplitResult(
                frames=tuple(frames),
                frame_count=metadata.frame_count,
                duration_ms=metadata.duration_ms,
                sampled_all=len(frames) >= metadata.frame_count,
                source_kind="video",
            )


def split_video_bytes(video_bytes: bytes, mime_type: str, *, filename: str = "") -> MediaSplitResult | None:
    return FfmpegVideoFrameSplitter().split(video_bytes, mime_type, filename=filename)


def _resolve_binary(value: str) -> str:
    return shutil.which(str(value or "").strip()) or ""


def _parse_probe_output(output: str) -> VideoMetadata:
    try:
        payload = json.loads(output or "{}")
    except ValueError:
        return VideoMetadata(duration_ms=0, frame_count=0)
    duration_ms = int(max(0.0, _float_value(payload.get("format", {}).get("duration"))) * 1000)
    stream = _first_video_stream(payload.get("streams"))
    frame_count = int(_float_value(stream.get("nb_frames")))
    if frame_count <= 0 and duration_ms > 0:
        frame_count = int((duration_ms / 1000) * _frame_rate(stream.get("avg_frame_rate")))
    return VideoMetadata(duration_ms=duration_ms, frame_count=max(1, frame_count))


def _first_video_stream(streams) -> dict:
    if not isinstance(streams, list):
        return {}
    for stream in streams:
        if isinstance(stream, dict):
            return stream
    return {}


def _frame_rate(value) -> float:
    text = str(value or "")
    if "/" in text:
        numerator, denominator = text.split("/", 1)
        denominator_value = _float_value(denominator)
        return _float_value(numerator) / denominator_value if denominator_value else 0.0
    return _float_value(text)


def _float_value(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _video_suffix(mime_type: str, filename: str) -> str:
    guessed = mimetypes.guess_extension(str(mime_type or "").split(";", 1)[0].strip())
    if guessed:
        return guessed
    suffix = Path(str(filename or "")).suffix
    return suffix if suffix else ".mp4"


def _read_output_frames(work_dir: Path, fps: float) -> list[MediaFrame]:
    frames = []
    for index, path in enumerate(sorted(work_dir.glob("frame_*.jpg"))):
        data = path.read_bytes()
        if not data:
            continue
        frames.append(MediaFrame(
            data=data,
            mime_type="image/jpeg",
            frame_index=index,
            time_ms=int(index * 1000 / max(0.001, fps)),
        ))
    return frames


def _fit_total_budget(frames: list[MediaFrame], max_total_bytes: int) -> list[MediaFrame]:
    selected = list(frames)
    while selected and sum(len(frame.data) for frame in selected) > max_total_bytes:
        if len(selected) <= 2:
            selected = selected[:1]
        else:
            selected = [selected[0], *selected[1:-1:2], selected[-1]]
    return selected
