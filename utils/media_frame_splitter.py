from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FrameSplitConfig:
    hard_frame_limit: int = 60
    max_long_edge_px: int = 512
    fallback_long_edge_px: int = 384
    max_frame_bytes: int = 300 * 1024
    max_total_bytes: int = 12 * 1024 * 1024
    jpeg_quality: int = 85
    min_jpeg_quality: int = 55


@dataclass(frozen=True)
class MediaFrame:
    data: bytes
    mime_type: str
    frame_index: int
    time_ms: int


@dataclass(frozen=True)
class MediaSplitResult:
    frames: tuple[MediaFrame, ...]
    frame_count: int
    duration_ms: int
    sampled_all: bool
    source_kind: str


class FrameSelector:
    def __init__(self, config: FrameSplitConfig | None = None):
        self.config = config or FrameSplitConfig()

    def select_indices(self, frame_count: int, *, max_frames: int | None = None) -> tuple[int, ...]:
        count = max(0, int(frame_count or 0))
        limit = self._limit(max_frames)
        if count <= 0 or limit <= 0:
            return ()
        if count <= limit:
            return tuple(range(count))
        return _evenly_spaced_indices(count, limit)

    def select_indices_by_durations(
        self,
        durations_ms: list[int] | tuple[int, ...],
        *,
        max_frames: int | None = None,
    ) -> tuple[int, ...]:
        durations = _normalize_durations(durations_ms)
        if not durations:
            return ()
        limit = self._limit(max_frames)
        if len(durations) <= limit:
            return tuple(range(len(durations)))
        targets = _time_targets(sum(durations), limit)
        cumulative_end = []
        elapsed = 0
        for duration in durations:
            elapsed += duration
            cumulative_end.append(elapsed)
        indices = []
        for target in targets:
            selected = _index_for_time(cumulative_end, target)
            if selected not in indices:
                indices.append(selected)
        if indices[-1] != len(durations) - 1:
            indices[-1] = len(durations) - 1
        return tuple(_fill_unique_indices(indices, len(durations), limit))

    def target_fps(self, *, duration_ms: int, frame_count: int, max_frames: int | None = None) -> float:
        duration_seconds = max(0.001, float(duration_ms or 0) / 1000)
        target_count = min(max(0, int(frame_count or 0)), self._limit(max_frames))
        if target_count <= 0:
            target_count = self._limit(max_frames)
        return target_count / duration_seconds

    def _limit(self, max_frames: int | None) -> int:
        configured = max(1, int(self.config.hard_frame_limit or 1))
        if max_frames is None:
            return configured
        return max(1, min(configured, int(max_frames or configured)))


def _evenly_spaced_indices(frame_count: int, target_count: int) -> tuple[int, ...]:
    if target_count <= 1:
        return (0,)
    return tuple(round(index * (frame_count - 1) / (target_count - 1)) for index in range(target_count))


def _normalize_durations(durations_ms: list[int] | tuple[int, ...]) -> list[int]:
    return [max(20, int(duration or 100)) for duration in durations_ms or []]


def _time_targets(total_duration_ms: int, target_count: int) -> list[int]:
    if target_count <= 1:
        return [0]
    return [round(index * max(0, total_duration_ms - 1) / (target_count - 1)) for index in range(target_count)]


def _index_for_time(cumulative_end: list[int], target_ms: int) -> int:
    for index, end_time in enumerate(cumulative_end):
        if target_ms < end_time:
            return index
    return max(0, len(cumulative_end) - 1)


def _fill_unique_indices(indices: list[int], frame_count: int, target_count: int) -> list[int]:
    selected = list(dict.fromkeys(indices))
    for index in _evenly_spaced_indices(frame_count, target_count):
        if index not in selected:
            selected.append(index)
        if len(selected) >= target_count:
            break
    selected = sorted(selected[:target_count])
    if selected and selected[-1] != frame_count - 1:
        selected[-1] = frame_count - 1
    return selected
