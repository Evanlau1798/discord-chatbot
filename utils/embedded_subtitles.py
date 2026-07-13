from __future__ import annotations

import json
import logging
import mimetypes
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger("discord.utils.embedded_subtitles")
MAX_SUBTITLE_CHARS = 50_000
MAX_SUBTITLE_SEGMENTS = 500
SRT_CUE_PATTERN = re.compile(
    r"(?:^|\n)(?:\d+\s*\n)?(?P<start>\d{2}:\d{2}:\d{2}[,.]\d{3})\s+-->\s+"
    r"(?P<end>\d{2}:\d{2}:\d{2}[,.]\d{3})[^\n]*\n(?P<text>.*?)(?=\n\s*\n|\Z)",
    re.DOTALL,
)


def extract_embedded_subtitles(video_bytes: bytes, mime_type: str, filename: str) -> tuple[dict, ...]:
    ffmpeg = shutil.which(os.getenv("FFMPEG_BIN", "ffmpeg"))
    ffprobe = shutil.which(os.getenv("FFPROBE_BIN", "ffprobe"))
    if not ffmpeg or not ffprobe or not video_bytes:
        return ()
    temp_root = Path("tmp") if Path("tmp").is_dir() else None
    try:
        with tempfile.TemporaryDirectory(prefix="embedded-captions-", dir=temp_root) as temp_dir:
            source = Path(temp_dir) / f"input{_suffix(mime_type, filename)}"
            source.write_bytes(video_bytes)
            stream_index = _select_subtitle_stream(source, ffprobe)
            if stream_index is None:
                return ()
            completed = subprocess.run(
                [ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(source), "-map", f"0:{stream_index}", "-f", "srt", "pipe:1"],
                capture_output=True,
                check=False,
                timeout=30,
            )
            if completed.returncode != 0:
                return ()
            text = completed.stdout.decode("utf-8", errors="replace")[:MAX_SUBTITLE_CHARS]
            return parse_srt_segments(text)
    except Exception as exc:
        logger.warning("video.embedded_subtitle_failed error_type=%s", type(exc).__name__)
        return ()


def parse_srt_segments(text: str) -> tuple[dict, ...]:
    segments = []
    for match in SRT_CUE_PATTERN.finditer(str(text or "").replace("\r\n", "\n")):
        cue_text = " ".join(line.strip() for line in match.group("text").splitlines() if line.strip())
        cue_text = re.sub(r"<[^>]+>", "", cue_text).strip()
        if not cue_text:
            continue
        segments.append({
            "startSeconds": _timestamp_seconds(match.group("start")),
            "endSeconds": _timestamp_seconds(match.group("end")),
            "text": cue_text,
        })
        if len(segments) >= MAX_SUBTITLE_SEGMENTS:
            break
    return tuple(segments)


def _select_subtitle_stream(path: Path, ffprobe: str) -> int | None:
    completed = subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", "s", "-show_entries", "stream=index:stream_tags=language", "-of", "json", str(path)],
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    if completed.returncode != 0:
        return None
    try:
        streams = json.loads(completed.stdout or "{}").get("streams", [])
    except json.JSONDecodeError:
        return None
    candidates = []
    for stream in streams if isinstance(streams, list) else []:
        try:
            index = int(stream.get("index"))
        except (AttributeError, TypeError, ValueError):
            continue
        language = str((stream.get("tags") or {}).get("language") or "").lower()
        candidates.append((_language_priority(language), index))
    return min(candidates)[1] if candidates else None


def _language_priority(language: str) -> int:
    priorities = {"zh-tw": 0, "zht": 0, "chi": 1, "zho": 1, "zh": 1, "eng": 2, "en": 2}
    return priorities.get(language, 10)


def _timestamp_seconds(value: str) -> float:
    hours, minutes, seconds = str(value).replace(",", ".").split(":")
    return round(int(hours) * 3600 + int(minutes) * 60 + float(seconds), 3)


def _suffix(mime_type: str, filename: str) -> str:
    suffix = Path(Path(str(filename or "")).name).suffix.lower()
    if suffix and len(suffix) <= 10:
        return suffix
    return mimetypes.guess_extension(str(mime_type or "").split(";", 1)[0].strip()) or ".mp4"
