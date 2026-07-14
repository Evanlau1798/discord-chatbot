from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

import discord

from utils.ai_imagine_client import ImagineAPIError, ImagineClient, ImagineSourceImage
from utils.image_reference_resolver import ImageReferenceCandidate
from utils.imagine_config import get_imagine_base_url
from utils.persona_image_prompt import merge_persona_image_prompt

logger = logging.getLogger("discord.extensions.AIChat")
IMAGE_LOADING_EMOJI = "<a:loading:1303077872805744650>"
MISSING_IMAGE_REFERENCE_MESSAGE = "找不到你指定的原圖，請重新附圖或直接回覆原圖後再試一次。"


class AiChatImageFlowMixin:
    async def _upsert_image_status(self, message, browser_notice, reply_text: str):
        content = self._append_status_block(reply_text, "正在繪製圖片")
        if browser_notice is not None:
            try:
                await browser_notice.edit(content=content)
                return browser_notice
            except discord.HTTPException:
                logger.debug("ai_chat.image_status_edit_failed", exc_info=True)
        try:
            return await message.reply(content=content, mention_author=False)
        except discord.HTTPException:
            logger.debug("ai_chat.image_status_send_failed", exc_info=True)
            return browser_notice

    @staticmethod
    def _append_status_block(reply_text: str, status_text: str) -> str:
        status_line = f"-# {IMAGE_LOADING_EMOJI} {status_text}"
        normalized_reply = str(reply_text or "").strip()
        max_reply_len = 2000 - len(status_line) - 2
        if len(normalized_reply) > max_reply_len:
            normalized_reply = f"{normalized_reply[:max(0, max_reply_len - 3)]}..."
        return f"{normalized_reply}\n\n{status_line}".strip()

    async def _maybe_generate_image(
        self,
        parsed,
        persona,
        image_candidates: list[ImageReferenceCandidate] | tuple[ImageReferenceCandidate, ...] = (),
    ) -> tuple[list[Path], str | bool]:
        block = parsed.image_generation
        if block is None or not self.image_generation_enabled:
            return [], False
        selected_sources = _select_source_images(block, image_candidates)
        if block.operation != "create" and selected_sources is None:
            logger.info(
                "ai_chat.image_reference_unavailable operation=%s requested_sources=%s available_sources=%s",
                block.operation,
                len(block.source_image_ids),
                len(image_candidates),
            )
            return [], MISSING_IMAGE_REFERENCE_MESSAGE
        image_prompt = merge_persona_image_prompt(
            self.persona_image_prompt_store.get_prompt(persona),
            block.prompt,
            operation=block.operation,
        )
        try:
            result = await asyncio.to_thread(
                self._get_imagine_client().generate,
                image_prompt,
                operation=block.operation,
                source_images=selected_sources or (),
            )
        except ImagineAPIError as exc:
            logger.warning(
                "ai_chat.image_generation_failed operation=%s error_type=%s error=%s",
                block.operation,
                type(exc).__name__,
                exc,
            )
            return [], "圖片生成服務暫時不可用，文字回覆先送出。"
        return result.image_paths, ""

    def _record_image_reference(self, message, *, owner_id) -> None:
        store = getattr(self, "image_reference_store", None)
        if store is None or message is None:
            return
        try:
            store.record_message(message, owner_id=owner_id)
        except Exception:
            logger.debug("ai_chat.image_reference_store_failed", exc_info=True)

    def _get_imagine_client(self):
        if self.imagine_client is None:
            self.imagine_client = ImagineClient(
                api_key=os.getenv("AI_IMAGINE_API_KEY"),
                base_url=get_imagine_base_url(),
                model=os.getenv("AI_IMAGINE_MODEL", ""),
                api_mode=os.getenv("AI_IMAGINE_API_MODE", "local"),
            )
        return self.imagine_client


def _select_source_images(block, candidates) -> tuple[ImagineSourceImage, ...] | None:
    if block.operation == "create":
        return ()
    candidates_by_id = {candidate.candidate_id: candidate for candidate in candidates}
    if any(source_id not in candidates_by_id for source_id in block.source_image_ids):
        return None
    return tuple(
        ImagineSourceImage(
            filename=candidates_by_id[source_id].filename,
            mime_type=candidates_by_id[source_id].mime_type,
            data=candidates_by_id[source_id].data,
        )
        for source_id in block.source_image_ids
    )
