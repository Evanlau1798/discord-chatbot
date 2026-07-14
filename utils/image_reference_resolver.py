from __future__ import annotations

import os
import re
from dataclasses import dataclass

from utils.discord_media_attachments import attachment_mime_type, iter_image_attachments, read_attachment_bytes
from utils.image_context_cache import extract_discord_message_context_keys

MAX_REFERENCE_IMAGE_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_REFERENCE_IMAGES = 4
MAX_REFERENCE_IMAGES_HARD_LIMIT = 16
MAX_REPLY_REFERENCE_DEPTH = 10
PREVIOUS_IMAGE_PATTERN = re.compile(
    r"(?:之前|剛才|剛剛|上一張|前一張|那張|先前).{0,8}(?:圖|圖片|影像)|"
    r"(?:圖|圖片|影像).{0,8}(?:之前|剛才|剛剛|上一張|前一張|那張|先前)|"
    r"\b(?:previous|last|earlier|that)\s+(?:image|picture|photo)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ImageReferenceCandidate:
    candidate_id: str
    source: str
    message_id: str
    attachment_id: str
    filename: str
    mime_type: str
    data: bytes

    def to_prompt_payload(self, visual_index: int | None = None) -> dict:
        payload = {
            "id": self.candidate_id,
            "source": self.source,
            "filename": self.filename,
            "mimeType": self.mime_type,
        }
        if visual_index is not None:
            payload["visualIndex"] = visual_index
        return payload

    def to_content_part(self) -> dict:
        return {"type": "image_bytes", "image_bytes": {"data": self.data, "mime_type": self.mime_type}}


class ImageReferenceResolver:
    def __init__(self, bot, store, *, max_candidates: int | None = None):
        self.bot = bot
        self.store = store
        self.max_candidates = _resolve_max_candidates(max_candidates)

    async def resolve(self, message, dialogue: str) -> list[ImageReferenceCandidate]:
        candidates = []
        seen = set()
        await self._append_message_images(candidates, seen, message, source="current_attachment", prefix="current")
        await self._append_reply_chain(candidates, seen, message)
        await self._append_linked_messages(candidates, seen, message, dialogue)
        if PREVIOUS_IMAGE_PATTERN.search(str(dialogue or "")):
            await self._append_recent_messages(candidates, seen, message)
        return candidates[:self.max_candidates]

    async def _append_reply_chain(self, candidates, seen, source_message) -> None:
        current = source_message
        for _ in range(MAX_REPLY_REFERENCE_DEPTH):
            reference = getattr(current, "reference", None)
            if reference is None or len(candidates) >= self.max_candidates:
                return
            current = await self._resolve_reference(reference, current)
            if current is None:
                return
            message_id = _entity_id(current)
            await self._append_message_images(
                candidates,
                seen,
                current,
                source="discord_reply",
                prefix=f"reply:{message_id}",
            )

    async def _append_linked_messages(self, candidates, seen, source_message, dialogue: str) -> None:
        for key in extract_discord_message_context_keys(dialogue):
            if len(candidates) >= self.max_candidates:
                return
            _, guild_id, channel_id, message_id = key.split(":", 3)
            linked = await self._fetch_message(channel_id, message_id, source_message)
            if linked is None:
                continue
            await self._append_message_images(
                candidates,
                seen,
                linked,
                source="discord_message_link",
                prefix=f"linked:{message_id}",
            )

    async def _append_recent_messages(self, candidates, seen, source_message) -> None:
        records = self.store.latest(
            guild_id=_entity_id(getattr(source_message, "guild", None)) or "@me",
            channel_id=_entity_id(getattr(source_message, "channel", None)) or "unknown",
            owner_id=_entity_id(getattr(source_message, "author", None)),
            limit=3,
        )
        for record in records:
            if len(candidates) >= self.max_candidates:
                return
            recent = await self._fetch_message(
                _entity_id(getattr(source_message, "channel", None)),
                record.message_id,
                source_message,
            )
            if recent is None:
                continue
            await self._append_message_images(
                candidates,
                seen,
                recent,
                source="recent_image",
                prefix=f"recent:{record.message_id}",
            )

    async def _append_message_images(self, candidates, seen, message, *, source: str, prefix: str) -> None:
        message_id = _entity_id(message)
        for index, attachment in enumerate(iter_image_attachments(message)):
            if len(candidates) >= self.max_candidates:
                return
            identity = (_entity_id(attachment) or str(getattr(attachment, "url", "")), message_id)
            if identity in seen:
                continue
            data = await read_attachment_bytes(attachment, max_bytes=MAX_REFERENCE_IMAGE_BYTES)
            if not data:
                continue
            seen.add(identity)
            candidates.append(ImageReferenceCandidate(
                candidate_id=f"{prefix}:{index}",
                source=source,
                message_id=message_id,
                attachment_id=_entity_id(attachment),
                filename=str(getattr(attachment, "filename", "") or f"image-{index}.png"),
                mime_type=attachment_mime_type(attachment),
                data=data,
            ))

    async def _resolve_reference(self, reference, source_message):
        resolved = getattr(reference, "resolved", None)
        if resolved is not None:
            return resolved
        return await self._fetch_message(
            _entity_id(getattr(reference, "channel_id", None)) or _entity_id(getattr(source_message, "channel", None)),
            _entity_id(getattr(reference, "message_id", None)),
            source_message,
        )

    async def _fetch_message(self, channel_id: str, message_id: str, source_message):
        if not message_id:
            return None
        get_message = getattr(self.bot, "get_message", None)
        if callable(get_message):
            cached = get_message(int(message_id))
            if cached is not None:
                return cached
        channel = None
        get_channel = getattr(self.bot, "get_channel", None)
        if callable(get_channel) and channel_id:
            channel = get_channel(int(channel_id))
        if channel is None and _entity_id(getattr(source_message, "channel", None)) == str(channel_id):
            channel = getattr(source_message, "channel", None)
        fetch_message = getattr(channel, "fetch_message", None)
        if not callable(fetch_message):
            return None
        try:
            return await fetch_message(int(message_id))
        except Exception:
            return None


def _resolve_max_candidates(value: int | None) -> int:
    raw_value = value if value is not None else os.getenv("AI_IMAGINE_MAX_REFERENCE_IMAGES", DEFAULT_MAX_REFERENCE_IMAGES)
    try:
        return max(1, min(int(raw_value), MAX_REFERENCE_IMAGES_HARD_LIMIT))
    except (TypeError, ValueError):
        return DEFAULT_MAX_REFERENCE_IMAGES


def _entity_id(value) -> str:
    return str(getattr(value, "id", value) or "").strip()
