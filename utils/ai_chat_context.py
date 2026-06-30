from __future__ import annotations

import json
import pickle
import re
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import discord

from utils.image_context_cache import (
    extract_discord_message_context_keys,
    image_context_to_history_note,
    message_context_key_from_message,
)
from utils.browser_prefetch import prefetch_explicit_web_urls
from utils.browser_result_payload import build_inline_browser_context, collect_browser_result_image_urls
from utils.message_media import build_multimodal_content, collect_message_media, collect_message_source_urls

logger = logging.getLogger("discord.extensions.AIChat")
MAX_CHAT_TURNS = 100
MENTION_PATTERN = re.compile(r"<@!?(?P<id>\d+)>")
TAIPEI_TIMEZONE = ZoneInfo("Asia/Taipei")
MAX_SERVER_HISTORY_MESSAGES = 12
MIN_CACHED_SERVER_HISTORY_MESSAGES = 3
MAX_SERVER_HISTORY_FETCH_DEPTH = 10
MAX_HISTORY_IMAGE_CONTEXTS = 20


class AiChatContextMixin:
    async def _build_server_history(self, message: discord.Message, current_display_name: str) -> tuple[list[dict], dict[int, str]]:
        reference = getattr(message, "reference", None)
        if reference is None:
            return [], {}
        referenced_message = self._resolve_referenced_message(reference)
        if referenced_message is None:
            referenced_message = await self._resolve_referenced_message_with_fetch(reference, message)
        if referenced_message is None:
            return [], {}
        current_author_id = getattr(getattr(message, "author", None), "id", None)
        cached_history = self._collect_cached_reply_chain(
            referenced_message,
            seen=set(),
            current_author_id=current_author_id,
            current_display_name=current_display_name,
        )
        if len(cached_history) >= MIN_CACHED_SERVER_HISTORY_MESSAGES:
            return self._finalize_server_history(cached_history[-MAX_SERVER_HISTORY_MESSAGES:], current_author_id, current_display_name)
        fetched_history = await self._collect_fetched_reply_chain(
            referenced_message,
            seen=set(),
            remaining=MAX_SERVER_HISTORY_FETCH_DEPTH,
            current_author_id=current_author_id,
            current_display_name=current_display_name,
        )
        return self._finalize_server_history(
            (fetched_history or cached_history)[-MAX_SERVER_HISTORY_MESSAGES:],
            current_author_id,
            current_display_name,
        )

    def _collect_cached_reply_chain(
        self,
        message: discord.Message,
        seen: set[int],
        current_author_id: int | None,
        current_display_name: str,
    ) -> list[dict]:
        message_id = int(getattr(message, "id", 0) or 0)
        if message_id in seen:
            return []
        if message_id:
            seen.add(message_id)
        history = []
        reference = getattr(message, "reference", None)
        if reference is not None:
            referenced_message = self._resolve_referenced_message(reference)
            if referenced_message is not None:
                history.extend(
                    self._collect_cached_reply_chain(
                        referenced_message,
                        seen,
                        current_author_id,
                        current_display_name,
                    )
                )
        entry = self._build_server_history_entry(message, current_author_id, current_display_name)
        if entry is not None:
            history.append(entry)
        return history

    async def _collect_fetched_reply_chain(
        self,
        message: discord.Message,
        seen: set[int],
        remaining: int,
        current_author_id: int | None,
        current_display_name: str,
    ) -> list[dict]:
        if remaining <= 0:
            return []
        message_id = int(getattr(message, "id", 0) or 0)
        if message_id in seen:
            return []
        if message_id:
            seen.add(message_id)
        history = []
        reference = getattr(message, "reference", None)
        if reference is not None:
            referenced_message = await self._resolve_referenced_message_with_fetch(reference, message)
            if referenced_message is not None:
                history.extend(
                    await self._collect_fetched_reply_chain(
                        referenced_message,
                        seen,
                        remaining - 1,
                        current_author_id,
                        current_display_name,
                    )
                )
        entry = self._build_server_history_entry(message, current_author_id, current_display_name)
        if entry is not None:
            history.append(entry)
        return history

    def _resolve_referenced_message(self, reference) -> discord.Message | None:
        resolved = getattr(reference, "resolved", None)
        if isinstance(resolved, discord.Message):
            return resolved
        message_id = getattr(reference, "message_id", None)
        if message_id is None or not hasattr(self.bot, "get_message"):
            return None
        cached = self.bot.get_message(message_id)
        return cached if isinstance(cached, discord.Message) else None

    async def _resolve_referenced_message_with_fetch(
        self,
        reference,
        source_message: discord.Message,
    ) -> discord.Message | None:
        cached = self._resolve_referenced_message(reference)
        if cached is not None:
            return cached
        message_id = getattr(reference, "message_id", None)
        if message_id is None:
            return None
        channel = self._resolve_reference_channel(reference, source_message)
        if channel is None or not hasattr(channel, "fetch_message"):
            return None
        try:
            return await channel.fetch_message(message_id)
        except discord.HTTPException:
            logger.debug("ai_chat.fetch_reference_message_failed message_id=%s", message_id, exc_info=True)
            return None

    def _resolve_reference_channel(self, reference, source_message: discord.Message):
        resolved = getattr(reference, "resolved", None)
        if isinstance(resolved, discord.Message):
            return getattr(resolved, "channel", None)
        channel_id = getattr(reference, "channel_id", None)
        if channel_id is not None and hasattr(self.bot, "get_channel"):
            channel = self.bot.get_channel(channel_id)
            if channel is not None:
                return channel
        return getattr(source_message, "channel", None)

    def _build_server_history_entry(
        self,
        message: discord.Message,
        current_author_id: int | None,
        current_display_name: str,
    ) -> dict | None:
        content = self._clean_history_content(getattr(message, "content", "") or "")
        attachments = self._build_attachment_payload(message)
        embeds = self._build_embed_payload(message)
        if not content and not attachments and not embeds:
            return None
        author = getattr(message, "author", None)
        is_bot_author = getattr(author, "bot", False)
        author_id = getattr(author, "id", None)
        author_display_name = current_display_name if author_id == current_author_id else self._message_author_display_name(message)
        entry = {
            "role": "assistant" if is_bot_author else "user",
            "authorDisplayName": "我" if is_bot_author else author_display_name,
            "content": content,
            "_messageContextKey": message_context_key_from_message(message),
        }
        if not is_bot_author and author_id is not None:
            entry["_authorID"] = int(author_id)
        if attachments:
            entry["attachments"] = attachments
        if embeds:
            entry["embeds"] = embeds
        return entry

    def _finalize_server_history(
        self,
        history: list[dict],
        current_author_id: int | None,
        current_display_name: str,
    ) -> tuple[list[dict], dict[int, str]]:
        participants = {}
        if current_author_id is not None:
            participants[int(current_author_id)] = current_display_name
        image_contexts = self._load_image_contexts_for_entries(history)
        sanitized_history = []
        for entry in history:
            author_id = entry.get("_authorID")
            if author_id is not None:
                participants[int(author_id)] = str(entry.get("authorDisplayName") or "").strip()
            sanitized_entry = {key: value for key, value in entry.items() if not key.startswith("_")}
            image_context = image_contexts.get(str(entry.get("_messageContextKey") or ""))
            if image_context is not None:
                sanitized_entry["imageUnderstanding"] = image_context.to_prompt_payload()
            sanitized_history.append(sanitized_entry)
        return sanitized_history, {user_id: name for user_id, name in participants.items() if name}

    def _build_memory_payload(
        self,
        current_user_id: int,
        current_display_name: str,
        current_memory: str,
        server_participants: dict[int, str],
    ):
        if len(server_participants) < 3:
            return current_memory
        participant_memories = {}
        participant_labels = self._build_unique_participant_labels(server_participants)
        for user_id, display_name in server_participants.items():
            if user_id == current_user_id:
                continue
            memory = self.memory_store.get_memory(user_id)
            if memory:
                participant_memories[participant_labels.get(user_id, display_name)] = memory
        total_memory_count = len(participant_memories) + (1 if current_memory else 0)
        if total_memory_count < 2:
            return current_memory
        return {
            "currentUser": {
                "displayName": participant_labels.get(current_user_id, current_display_name),
                "memory": current_memory,
            },
            "participants": participant_memories,
        }

    @staticmethod
    def _build_unique_participant_labels(server_participants: dict[int, str]) -> dict[int, str]:
        used_counts = {}
        labels = {}
        for user_id, display_name in server_participants.items():
            base_label = str(display_name or "未知使用者").strip() or "未知使用者"
            used_counts[base_label] = used_counts.get(base_label, 0) + 1
            labels[user_id] = base_label if used_counts[base_label] == 1 else f"{base_label} #{used_counts[base_label]}"
        return labels

    def _clean_history_content(self, content: str) -> str:
        if self.bot.user is not None:
            content = MENTION_PATTERN.sub(lambda match: "" if match.group("id") == str(self.bot.user.id) else match.group(0), content)
        return content.strip()

    def _append_history(self, user_id, dialogue: str, reply_text: str, image_context_key: str = "") -> None:
        history = _normalize_history(self.user_history.get(str(user_id), []))
        user_content = dialogue or ("[使用者傳送了圖片]" if image_context_key else dialogue)
        user_entry = {"role": "user", "content": user_content}
        if image_context_key:
            user_entry["imageContextKey"] = image_context_key
        history.extend([user_entry, {"role": "assistant", "content": reply_text}])
        self.user_history[str(user_id)] = history[-MAX_CHAT_TURNS * 2:]
        self.save_user_history()

    def get_user_history(self, user_id) -> list[dict]:
        return self._with_cached_image_notes(_normalize_history(self.user_history.get(str(user_id), [])))

    def load_user_history(self) -> dict:
        try:
            with open("./AIHistory/user_history.pickle", "rb") as file:
                raw_history = pickle.load(file)
        except (FileNotFoundError, EOFError):
            return {}
        if not isinstance(raw_history, dict):
            return {}
        return {str(user_id): _normalize_history(history) for user_id, history in raw_history.items()}

    def save_user_history(self) -> None:
        Path("AIHistory").mkdir(parents=True, exist_ok=True)
        with open("./AIHistory/user_history.pickle", "wb") as file:
            pickle.dump(self.user_history, file)

    async def _build_request_messages(
        self,
        message,
        dialogue: str,
        history: list[dict],
        persona,
        memory,
        server_history: list[dict] | None = None,
    ) -> list[dict]:
        system_prompt = self.prompt_builder.build_system_prompt(persona)
        display_name = self._message_author_display_name(message)
        current_attachments = self._build_attachment_payload(message)
        current_embeds = self._build_embed_payload(message)
        media = await collect_message_media(message, dialogue)
        source_urls = collect_message_source_urls(message, dialogue)
        prefetched_results = await prefetch_explicit_web_urls(
            getattr(self, "browser_client", None),
            dialogue,
            excluded_urls=source_urls,
        )
        prefetched_image_urls = collect_browser_result_image_urls(prefetched_results)
        payload_content = {
            "dialogue": dialogue,
            "persona": self.prompt_builder.build_request_persona_payload(persona),
            "memory": memory,
            "user": {"displayName": display_name},
            "runtimeContext": self._build_runtime_context(message),
            "conversationContext": {
                "currentConversationTarget": {"displayName": display_name},
                "instruction": f"你目前正在對話的對象是: {display_name}",
                "serverHistory": server_history or [],
            },
        }
        linked_contexts = self._load_image_contexts_by_keys(extract_discord_message_context_keys(dialogue))
        if linked_contexts:
            payload_content["linkedImageContexts"] = [context.to_prompt_payload() for context in linked_contexts.values()]
        if current_attachments:
            payload_content["attachments"] = current_attachments
        if current_embeds:
            payload_content["embeds"] = current_embeds
        if media.image_urls:
            payload_content["imageUrls"] = media.image_urls
        if prefetched_results:
            payload_content["prefetchedBrowserContext"] = build_inline_browser_context(prefetched_results)
        if media.image_urls or media.content_parts or prefetched_image_urls:
            payload_content["imageUnderstandingInstruction"] = (
                "本輪包含圖片。請在最終 JSON 加入 imageUnderstanding，摘要圖片可見內容、文字與語意。"
            )
        payload = {"inputType": "discord_chat", "payload": payload_content}
        content = build_multimodal_content(
            json.dumps(payload, ensure_ascii=False),
            image_urls=prefetched_image_urls,
            image_parts=media.content_parts,
        )
        return [{"role": "system", "content": system_prompt}, *history, {"role": "user", "content": content}]

    def _store_image_understanding_context(self, message, dialogue: str, parsed):
        understanding = getattr(parsed, "image_understanding", None)
        if understanding is None or not hasattr(self, "image_context_cache"):
            return None
        source_urls = collect_message_source_urls(message, dialogue)
        try:
            return self.image_context_cache.store_message_context(
                message,
                image_count=max(1, len(source_urls)),
                source_urls=source_urls,
                understanding=understanding,
            )
        except Exception:
            logger.debug("ai_chat.image_context_cache_store_failed", exc_info=True)
            return None

    def _mentions_bot(self, message: discord.Message) -> bool:
        bot_user = self.bot.user
        return bot_user is not None and any(user.id == bot_user.id for user in message.mentions)

    def _extract_dialogue_text(self, message: discord.Message, *, is_dm: bool) -> str:
        content = message.content or ""
        if not is_dm and self.bot.user is not None:
            content = MENTION_PATTERN.sub(lambda match: "" if match.group("id") == str(self.bot.user.id) else match.group(0), content)
        return content.strip()

    @staticmethod
    def _build_attachment_payload(message) -> list[dict]:
        attachments = []
        for attachment in getattr(message, "attachments", []) or []:
            attachments.append({
                "filename": getattr(attachment, "filename", ""),
                "url": getattr(attachment, "url", ""),
                "contentType": getattr(attachment, "content_type", ""),
            })
        return attachments

    @staticmethod
    def _build_embed_payload(message) -> list[dict]:
        embeds = []
        for embed in getattr(message, "embeds", []) or []:
            payload = {}
            for source_key, payload_key in (("url", "url"), ("title", "title"), ("description", "description")):
                value = str(_embed_value(embed, source_key) or "").strip()
                if value:
                    payload[payload_key] = value[:500]
            for source_key, payload_key in (("image", "imageUrl"), ("thumbnail", "thumbnailUrl"), ("video", "videoUrl")):
                url = _embed_proxy_url(_embed_value(embed, source_key))
                if url:
                    payload[payload_key] = url
            if payload:
                embeds.append(payload)
        return embeds[:5]

    @staticmethod
    def _display_name(user) -> str:
        return str(getattr(user, "display_name", "") or getattr(user, "name", "") or "未知使用者").strip()

    @staticmethod
    def _message_author_display_name(message: discord.Message) -> str:
        author = getattr(message, "author", None)
        guild = getattr(message, "guild", None)
        author_id = getattr(author, "id", None)
        if guild is not None and author_id is not None:
            member = guild.get_member(author_id)
            if member is not None:
                return AiChatContextMixin._display_name(member)
        return AiChatContextMixin._display_name(author)

    @staticmethod
    def _build_runtime_context(message) -> dict:
        created_at = getattr(message, "created_at", None)
        if isinstance(created_at, datetime):
            current_time = created_at.astimezone(TAIPEI_TIMEZONE)
        else:
            current_time = datetime.now(TAIPEI_TIMEZONE)
        return {
            "timezone": "Asia/Taipei",
            "currentTime": current_time.strftime("%Y/%m/%d %H:%M:%S"),
            "userDisplayName": AiChatContextMixin._message_author_display_name(message),
        }

    def _load_image_contexts_for_entries(self, entries: list[dict]):
        keys = [str(entry.get("_messageContextKey") or "") for entry in entries]
        return self._load_image_contexts_by_keys(keys)

    def _load_image_contexts_by_keys(self, keys: list[str]):
        cache = getattr(self, "image_context_cache", None)
        if cache is None:
            return {}
        try:
            return cache.get_many([key for key in keys if key])
        except Exception:
            logger.debug("ai_chat.image_context_cache_load_failed", exc_info=True)
            return {}

    def _with_cached_image_notes(self, history: list[dict]) -> list[dict]:
        keys = [entry.get("imageContextKey", "") for entry in history if entry.get("role") == "user"]
        contexts = self._load_image_contexts_by_keys(keys[-MAX_HISTORY_IMAGE_CONTEXTS:])
        enriched = []
        for entry in history:
            content = entry["content"]
            context = contexts.get(str(entry.get("imageContextKey") or ""))
            if context is not None:
                content = f"{content}\n\n{image_context_to_history_note(context)}"
            enriched.append({"role": entry["role"], "content": content})
        return enriched


def _normalize_history(history) -> list[dict]:
    if not isinstance(history, list):
        return []
    normalized = []
    for entry in history:
        if not isinstance(entry, dict):
            continue
        role = entry.get("role")
        content = entry.get("content")
        if role in {"user", "assistant"} and isinstance(content, str) and content.strip():
            normalized_entry = {"role": role, "content": content.strip()}
            image_context_key = entry.get("imageContextKey")
            if isinstance(image_context_key, str) and image_context_key.strip():
                normalized_entry["imageContextKey"] = image_context_key.strip()
            normalized.append(normalized_entry)
    return normalized


def _embed_value(value, key: str):
    if isinstance(value, dict):
        return value.get(key)
    return getattr(value, key, None)


def _embed_proxy_url(proxy) -> str:
    if proxy is None:
        return ""
    url = _embed_value(proxy, "url") or _embed_value(proxy, "proxy_url")
    return str(url or "").strip()
