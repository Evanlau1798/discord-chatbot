from __future__ import annotations

import asyncio
import io
import logging
import os
from collections import defaultdict

import discord
from discord.ext import commands
from google.genai import errors as genai_errors

from utils.EmbedMessage import SakuraEmbedMsg
from utils.ai_chat_browser import format_browser_notice_targets
from utils.ai_chat_browser_flow import AiChatBrowserFlowMixin
from utils.ai_chat_concurrency import AiChatRequestLimiter
from utils.ai_chat_settings import AiChatUserSettingsStore
from utils.ai_chat_context import AiChatContextMixin, MAX_CHAT_TURNS
from utils.ai_chat_image_flow import AiChatImageFlowMixin
from utils.ai_imagine_client import ImagineAPIError
from utils.discord_notice_timing import MIN_BROWSER_NOTICE_DISPLAY_SECONDS, wait_for_min_notice_display
from utils.discord_files import send_content_with_files
from utils.discord_presence import keep_typing
from utils.discord_request_status import DiscordRequestStatus
from utils.discord_status_notice import delete_notice, edit_notice, format_queue_notice_content
from utils.gemini_api import DEFAULT_GEMINI_MODEL, GeminiChatClient, _is_retryable_api_error
from utils.image_context_cache import ImageContextCache
from utils.image_reference_resolver import ImageReferenceResolver
from utils.image_reference_store import ImageReferenceStore
from utils.imagine_config import is_image_generation_enabled
from utils.imagine_rate_limit_store import ImagineRateLimiter, format_imagine_rate_limit_notice
from utils.json_response_protocol import build_fallback_response, build_repair_instruction, parse_model_response
from utils.local_asr_client import LocalASRClient
from utils.memory_store import MemoryStore
from utils.message_media import message_has_video_attachment
from utils.persona_image_prompt import PersonaImagePromptStore
from utils.persona_select_view import PersonaSelectView
from utils.persona_store import PersonaPromptBuilder, PersonaStore, format_persona_list
from utils.web_tool_client import HeadlessBrowserClient

logger = logging.getLogger("discord.extensions.AIChat")
USER_CHAT_LOCKS = defaultdict(asyncio.Lock)
GENAI_RETRY_DELAYS = (1, 5, 5, 10, 30)
LOADING_EMOJI = "<a:loading:1303077872805744650>"
VIDEO_PROCESSING_STATUS = f"-# {LOADING_EMOJI} 幀在處理影片文字"


class AiChat(AiChatImageFlowMixin, AiChatBrowserFlowMixin, AiChatContextMixin, commands.Cog):
    def __init__(self, bot):
        self.bot: discord.Bot = bot
        self.user_history = self.load_user_history()
        self.user_settings = AiChatUserSettingsStore()
        self.persona_store = PersonaStore()
        self.persona_image_prompt_store = PersonaImagePromptStore()
        self.prompt_builder = PersonaPromptBuilder()
        self.memory_store = MemoryStore()
        self.image_context_cache = ImageContextCache()
        self.image_context_cache.cleanup_expired()
        self.image_reference_store = ImageReferenceStore()
        self.image_reference_store.cleanup_expired()
        self.image_reference_resolver = ImageReferenceResolver(bot, self.image_reference_store)
        self.imagine_rate_limiter = ImagineRateLimiter()
        self.browser_client = HeadlessBrowserClient()
        self.local_asr_client = LocalASRClient()
        self.request_limiter = AiChatRequestLimiter()
        logger.info("ai_chat.request_limiter_configured max_parallel_requests=%s", self.request_limiter.max_parallel_requests)
        self.image_generation_enabled = is_image_generation_enabled()
        self.gemini_client = GeminiChatClient(
            api_key=os.getenv("GEMINIAPIKEY") or os.getenv("GEMINI_API_KEY"),
            model=os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL),
        )
        self.persona_cache_names = self._refresh_persona_caches()
        self.imagine_client = None

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        is_dm = isinstance(message.channel, discord.DMChannel)
        is_mention = self._mentions_bot(message)
        if not is_dm and not is_mention:
            return
        text = self._extract_dialogue_text(message, is_dm=is_dm)
        if not text and not message.attachments and not getattr(message, "embeds", []) and not getattr(message, "stickers", []):
            return
        request_status = DiscordRequestStatus(message, logger)
        self._record_image_reference(message, owner_id=message.author.id)

        async def on_queue_update(update):
            await request_status.set_base(format_queue_notice_content(update, LOADING_EMOJI))

        async with keep_typing(message.channel):
            async with self.request_limiter.with_queue_updates(on_queue_update):
                try:
                    if message_has_video_attachment(message):
                        await request_status.set_base(VIDEO_PROCESSING_STATUS)
                    result = await self.chat(
                        message=message,
                        dialogue=text,
                        is_dm=is_dm,
                        request_status=request_status,
                    )
                    if result.get("delivered_message") is not None:
                        await delete_notice(request_status.notice, logger)
                        if result["image_paths"]:
                            image_sender = (
                                (lambda content, files: message.channel.send(content=content or None, files=files))
                                if is_dm and result.get("browser_used")
                                else (lambda content, files: result["delivered_message"].reply(content=content or None, files=files, mention_author=False))
                            )
                            sent_image_message = await send_content_with_files(
                                image_sender,
                                "",
                                result["image_paths"],
                            )
                            self._record_image_reference(sent_image_message, owner_id=message.author.id)
                        return
                    if result["image_paths"]:
                        sent_image_message = await send_content_with_files(
                            lambda content, files: message.reply(content=content, files=files, mention_author=False),
                            result["reply_text"],
                            result["image_paths"],
                        )
                        self._record_image_reference(sent_image_message, owner_id=message.author.id)
                        await delete_notice(request_status.notice, logger)
                        return
                    if await edit_notice(request_status.notice, result["reply_text"], logger):
                        return
                    await message.reply(content=result["reply_text"], mention_author=False)
                except Exception as exc:
                    logger.exception("ai_chat.message_failed")
                    await delete_notice(request_status.notice, logger)
                    await message.reply(
                        embed=SakuraEmbedMsg(
                            title="訊息無法傳送",
                            description=_user_error_message(exc),
                        ),
                        mention_author=False,
                    )

    async def chat(
        self,
        *,
        message: discord.Message,
        dialogue: str,
        is_dm: bool,
        request_status: DiscordRequestStatus | None = None,
    ) -> dict:
        async with USER_CHAT_LOCKS[message.author.id]:
            history = self.get_user_history(message.author.id) if is_dm else []
            if sum(1 for item in history if item.get("role") == "assistant") >= MAX_CHAT_TURNS:
                raise RuntimeError("您目前已達到對話上限，請使用 /forgotjuice 重置對話。")
            persona_key = self.user_settings.get_persona(message.author)
            persona = self.persona_store.resolve(persona_key) or self.persona_store.default_persona()
            current_display_name = self._message_author_display_name(message)
            current_memory = self.memory_store.get_memory(message.author.id)
            server_history = []
            server_participants = {}
            if not is_dm:
                server_history, server_participants = await self._build_server_history(message, current_display_name)
            memory = self._build_memory_payload(
                current_user_id=message.author.id,
                current_display_name=current_display_name,
                current_memory=current_memory,
                server_participants=server_participants,
            )
            image_candidates = []
            resolver = getattr(self, "image_reference_resolver", None)
            if resolver is not None:
                image_candidates = await resolver.resolve(message, dialogue)
            request_messages = await self._build_request_messages(
                message,
                dialogue,
                history,
                persona,
                memory,
                server_history,
                image_candidates=image_candidates,
            )
            cached_content = self.persona_cache_names.get(persona.key) if persona is not None else None
            parsed, raw_response = await self._complete_and_parse_with_raw(
                request_messages, message, cached_content, request_status
            )
            browser_notice = None
            browser_used = False
            if parsed.browser is not None:
                browser_used = True
                parsed, browser_notice = await self._complete_after_browser(
                    request_messages,
                    raw_response,
                    parsed.browser.urls,
                    parsed.browser.search_queries,
                    parsed.browser.youtube_search_queries,
                    parsed.browser.find_requests,
                    parsed.browser.include_images,
                    parsed.browser.search_options,
                    message,
                    cached_content,
                    request_status,
                )
            image_status_message = None
            image_rate_limited_until = None
            image_quota_status = None
            if parsed.image_generation is not None and self.image_generation_enabled:
                image_quota_status = self.imagine_rate_limiter.check(message.author.id)
                if not image_quota_status.allowed:
                    image_rate_limited_until = image_quota_status.reset_at
            if parsed.image_generation is not None and self.image_generation_enabled and image_rate_limited_until is None:
                image_status_message = await self._upsert_image_status(message, browser_notice, parsed.reply_text)
                if image_status_message is not None:
                    browser_notice = image_status_message
                image_paths, image_error = await self._maybe_generate_image(parsed, persona, image_candidates)
                if image_paths and not image_error and image_quota_status is not None:
                    self.imagine_rate_limiter.record_success(message.author.id)
            else:
                image_paths, image_error = [], False
            if parsed.memory is not None:
                self.memory_store.set_memory(message.author.id, parsed.memory.content)
            reply_text = parsed.reply_text
            if image_error:
                error_message = (
                    image_error
                    if isinstance(image_error, str)
                    else "圖片生成服務暫時不可用，文字回覆先送出。"
                )
                if error_message not in reply_text:
                    reply_text = f"{reply_text}\n\n{error_message}"
            if image_rate_limited_until is not None:
                reply_text = f"{reply_text}\n\n{format_imagine_rate_limit_notice(image_rate_limited_until)}"
            image_context = self._store_image_understanding_context(message, dialogue, parsed)
            if is_dm:
                self._append_history(
                    message.author.id,
                    dialogue,
                    reply_text,
                    image_context_key=getattr(image_context, "message_key", ""),
                )
            delivered_message = None
            if browser_notice is not None:
                delivered_message = await self._edit_browser_notice(browser_notice, reply_text)
            return {
                "reply_text": reply_text,
                "image_paths": image_paths,
                "delivered_message": delivered_message,
                "browser_used": browser_used,
            }

    async def _complete_and_parse(
        self,
        messages: list[dict],
        message: discord.Message,
        cached_content: str | None,
        request_status: DiscordRequestStatus | None = None,
    ):
        parsed, _ = await self._complete_and_parse_with_raw(messages, message, cached_content, request_status)
        return parsed

    async def _complete_and_parse_with_raw(
        self,
        messages: list[dict],
        message: discord.Message,
        cached_content: str | None,
        request_status: DiscordRequestStatus | None = None,
    ):
        response = await self._complete_with_retry(messages, message, cached_content, request_status)
        try:
            return parse_model_response(response.visible_content), response.visible_content
        except ValueError:
            repair_messages = messages + [
                {"role": "assistant", "content": response.visible_content},
                {"role": "user", "content": build_repair_instruction()},
            ]
            repair_response = await self._complete_with_retry(repair_messages, message, cached_content, request_status)
            try:
                return parse_model_response(repair_response.visible_content), repair_response.visible_content
            except ValueError:
                logger.warning(
                    "ai_chat.invalid_json_response content_chars=%s",
                    len(repair_response.visible_content or ""),
                )
                return build_fallback_response(), repair_response.visible_content

    async def _complete_with_retry(
        self,
        messages: list[dict],
        message: discord.Message,
        cached_content: str | None,
        request_status: DiscordRequestStatus | None = None,
    ):
        status = request_status or DiscordRequestStatus(message, logger)
        try:
            for attempt_index in range(len(GENAI_RETRY_DELAYS) + 1):
                try:
                    return await asyncio.to_thread(self.gemini_client.complete, messages, cached_content=cached_content)
                except Exception as exc:
                    if cached_content and not _is_retryable_api_error(exc):
                        logger.warning(
                            "ai_chat.cached_content_failed_fallback error_type=%s error=%s",
                            type(exc).__name__,
                            exc,
                        )
                        return await asyncio.to_thread(self.gemini_client.complete, messages)
                    if attempt_index >= len(GENAI_RETRY_DELAYS) or not _is_retryable_api_error(exc):
                        raise
                    retry_number = attempt_index + 1
                    delay_seconds = GENAI_RETRY_DELAYS[attempt_index]
                    logger.warning(
                        "ai_chat.genai_retry retry=%s delay_seconds=%s error_type=%s error=%s",
                        retry_number,
                        delay_seconds,
                        type(exc).__name__,
                        exc,
                    )
                    if retry_number >= 3:
                        retry_content = (
                            f"-# {LOADING_EMOJI} GenAI 服務暫時不穩，正在重試 "
                            f"({retry_number}/{len(GENAI_RETRY_DELAYS)})，下一次嘗試約 {delay_seconds} 秒後。"
                        )
                        await status.set_retry(retry_content)
                    await asyncio.sleep(delay_seconds)
        finally:
            await status.clear_retry()
        raise RuntimeError("模型服務目前忙碌或暫時不可用，請稍後再試一次。")

    async def _send_browser_notice(
        self,
        message: discord.Message,
        urls: list[str],
        search_queries: list[str],
        youtube_search_queries: list[str],
        find_requests: list,
    ) -> discord.Message | None:
        targets = format_browser_notice_targets(
            urls,
            search_queries,
            find_requests,
            youtube_search_queries=youtube_search_queries,
        )
        content = f"-# {LOADING_EMOJI} 正在上網查詢資料: {targets}"
        try:
            return await message.reply(content=content[:1900], mention_author=False)
        except discord.HTTPException:
            logger.debug("ai_chat.browser_notice_send_failed", exc_info=True)
            return None

    async def _set_browser_reading_notice(self, browser_notice: discord.Message | None, sent_at: float | None) -> None:
        if browser_notice is None:
            return
        await wait_for_min_notice_display(sent_at, MIN_BROWSER_NOTICE_DISPLAY_SECONDS)
        try:
            await browser_notice.edit(content=f"-# {LOADING_EMOJI} 正在閱讀網頁內容並編寫回覆")
        except discord.HTTPException:
            logger.debug("ai_chat.browser_notice_reading_edit_failed", exc_info=True)

    async def _edit_browser_notice(self, browser_notice: discord.Message | None, reply_text: str):
        if browser_notice is None:
            return None
        try:
            await browser_notice.edit(content=reply_text[:2000])
            return browser_notice
        except discord.HTTPException:
            logger.debug("ai_chat.browser_notice_edit_failed", exc_info=True)
            return None

    @commands.slash_command(description="查看並切換 AI 人設")
    async def persona(self, ctx: discord.ApplicationContext):
        await ctx.respond(
            embed=SakuraEmbedMsg(
                title="人設設定",
                description=self._format_current_settings(ctx.author),
            ),
            view=PersonaSelectView(self, ctx.author.id),
            ephemeral=True,
        )
        return

    @commands.slash_command(name="forgotjuice", description="重置 DM AI 對話歷史")
    async def forgotjuice(self, ctx: discord.ApplicationContext):
        self.user_history[str(ctx.author.id)] = []
        self.save_user_history()
        await ctx.respond(content=f"{ctx.author.mention} 已重置 DM 對話歷史", ephemeral=True)

    @commands.slash_command(name="chat_history", description="將目前的 DM AI 對話紀錄私訊給您")
    async def chat_history(self, ctx: discord.ApplicationContext):
        history = self.get_user_history(ctx.author.id)
        if not history:
            await ctx.respond(embed=SakuraEmbedMsg("錯誤", "該使用者的對話紀錄不存在"), ephemeral=True)
            return
        text = "\n".join(f"{entry['role']}: {entry['content']}" for entry in history)
        await ctx.author.send(file=discord.File(io.BytesIO(text.encode("utf-8")), f"{ctx.author.id}_chat_history.txt"))
        await ctx.respond(embed=SakuraEmbedMsg("成功", "對話紀錄已私訊給您"), ephemeral=True)

    @commands.slash_command(name="memory_view", description="查看目前儲存的使用者記憶")
    async def memory_view(self, ctx: discord.ApplicationContext):
        memory = self.memory_store.get_memory(ctx.author.id) or "目前沒有儲存記憶。"
        await ctx.respond(embed=SakuraEmbedMsg(title="目前記憶", description=memory[:3900]), ephemeral=True)

    @commands.slash_command(name="memory_reset", description="清除目前儲存的使用者記憶")
    async def memory_reset(self, ctx: discord.ApplicationContext):
        self.memory_store.reset_memory(ctx.author.id)
        await ctx.respond(content="已清除你的使用者記憶。", ephemeral=True)

    def _format_current_settings(self, user) -> str:
        setting = self.user_settings.get(user)
        persona_key = setting.get("persona")
        persona = self.persona_store.resolve(persona_key) or self.persona_store.default_persona()
        current = persona.name if persona else "找不到預設人設"
        return f"目前人設: {current}\n\n可切換清單:\n{format_persona_list(self.persona_store.list_personas())}"

    def _refresh_persona_caches(self) -> dict[str, str]:
        prompts_by_key = {}
        for persona in self.persona_store.list_personas():
            try:
                prompts_by_key[persona.key] = self.prompt_builder.build_system_prompt(persona)
            except ValueError as exc:
                logger.warning("ai_chat.persona_prompt_build_failed persona=%s error=%s", persona.key, exc)
        if not prompts_by_key:
            logger.warning("ai_chat.no_persona_cache_candidates")
            return {}
        return self.gemini_client.refresh_persona_caches(prompts_by_key)

    def _resolve_persona_setting(self, value: str) -> str | None:
        normalized = str(value or "").strip()
        persona = self.persona_store.resolve(normalized)
        if persona is None:
            raise ValueError(f"找不到人設: {normalized}")
        return persona.key

def _user_error_message(exc: Exception) -> str:
    if isinstance(exc, ImagineAPIError):
        return "圖片生成服務暫時不可用，請稍後再試一次。"
    if isinstance(exc, genai_errors.APIError):
        return "模型服務目前忙碌或暫時不可用，請稍後再試一次。"
    if isinstance(exc, (ValueError, RuntimeError)):
        return str(exc)
    return "系統處理訊息時發生未預期錯誤，請稍後再試一次。"


def setup(bot: discord.Bot):
    bot.add_cog(AiChat(bot))
