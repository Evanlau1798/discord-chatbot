from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from utils.discord_media_attachments import iter_image_attachments

DEFAULT_IMAGE_REFERENCE_DB_PATH = Path("databases/image_reference_cache.db")
DEFAULT_IMAGE_REFERENCE_TTL_SECONDS = 24 * 60 * 60


@dataclass(frozen=True)
class ImageReferenceRecord:
    guild_id: str
    channel_id: str
    message_id: str
    owner_id: str
    image_count: int
    created_at: int
    expires_at: int


class ImageReferenceStore:
    def __init__(
        self,
        db_path: str | Path = DEFAULT_IMAGE_REFERENCE_DB_PATH,
        ttl_seconds: int | None = None,
    ):
        self.db_path = Path(db_path)
        self.ttl_seconds = _resolve_ttl_seconds(ttl_seconds)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def record_message(self, message, *, owner_id=None, now: int | None = None) -> ImageReferenceRecord | None:
        image_count = len(iter_image_attachments(message))
        if image_count <= 0:
            return None
        guild = getattr(message, "guild", None)
        channel = getattr(message, "channel", None)
        author = getattr(message, "author", None)
        return self.record_ids(
            guild_id=_entity_id(guild) or "@me",
            channel_id=_entity_id(channel) or "unknown",
            message_id=_entity_id(message),
            owner_id=_entity_id(owner_id) or _entity_id(author),
            image_count=image_count,
            now=now,
        )

    def record_ids(
        self,
        *,
        guild_id,
        channel_id,
        message_id,
        owner_id,
        image_count: int,
        now: int | None = None,
    ) -> ImageReferenceRecord | None:
        identity = tuple(str(value or "").strip() for value in (guild_id, channel_id, message_id, owner_id))
        count = max(0, int(image_count or 0))
        if not all(identity) or count <= 0:
            return None
        now_value = int(time.time() if now is None else now)
        record = ImageReferenceRecord(
            guild_id=identity[0],
            channel_id=identity[1],
            message_id=identity[2],
            owner_id=identity[3],
            image_count=count,
            created_at=now_value,
            expires_at=now_value + self.ttl_seconds,
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO image_reference_cache (
                    guild_id, channel_id, message_id, owner_id,
                    image_count, created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.guild_id,
                    record.channel_id,
                    record.message_id,
                    record.owner_id,
                    record.image_count,
                    record.created_at,
                    record.expires_at,
                ),
            )
        return record

    def latest(
        self,
        *,
        guild_id,
        channel_id,
        owner_id,
        limit: int = 3,
        now: int | None = None,
    ) -> list[ImageReferenceRecord]:
        now_value = int(time.time() if now is None else now)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT guild_id, channel_id, message_id, owner_id,
                       image_count, created_at, expires_at
                FROM image_reference_cache
                WHERE guild_id = ? AND channel_id = ? AND owner_id = ? AND expires_at > ?
                ORDER BY created_at DESC, message_id DESC
                LIMIT ?
                """,
                (
                    str(guild_id or "").strip(),
                    str(channel_id or "").strip(),
                    str(owner_id or "").strip(),
                    now_value,
                    max(1, min(int(limit or 1), 10)),
                ),
            ).fetchall()
        return [ImageReferenceRecord(*row) for row in rows]

    def cleanup_expired(self, *, now: int | None = None) -> int:
        now_value = int(time.time() if now is None else now)
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM image_reference_cache WHERE expires_at <= ?", (now_value,))
            return int(cursor.rowcount or 0)

    def _connect(self):
        return sqlite3.connect(self.db_path)

    def _ensure_schema(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS image_reference_cache (
                    guild_id TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    image_count INTEGER NOT NULL,
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    PRIMARY KEY (guild_id, channel_id, message_id, owner_id)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_image_reference_lookup "
                "ON image_reference_cache (guild_id, channel_id, owner_id, expires_at, created_at)"
            )


def _resolve_ttl_seconds(value: int | None) -> int:
    if value is not None:
        return max(60, int(value))
    try:
        return max(60, int(os.getenv("AI_IMAGINE_REFERENCE_TTL_SECONDS", DEFAULT_IMAGE_REFERENCE_TTL_SECONDS)))
    except (TypeError, ValueError):
        return DEFAULT_IMAGE_REFERENCE_TTL_SECONDS


def _entity_id(value) -> str:
    raw_value = getattr(value, "id", value)
    return str(raw_value or "").strip()
