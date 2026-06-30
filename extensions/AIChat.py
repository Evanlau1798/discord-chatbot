from __future__ import annotations

import asyncio
import io
import logging
import os
from collections import defaultdict
from pathlib import Path

import discord
from discord.ext import commands
from google.genai import errors as genai_errors

from utils.EmbedMessage import SakuraEmbedMsg
from utils.ai_chat_browser import fetch_browser_results, format_browser_notice_targets
from utils.ai_chat_settings import AiChatUserSettingsStore
from utils.ai_chat_context import AiChatContextMixin, MAX_CHAT_TURNS
from utils.ai_imagine_client import ImagineAPIError, ImagineClient, LOCAL_SUB2API_BASE_URL
from utils.browser_result_payload import build_browser_followup_content
from utils.discord_notice_timing import MIN_BROWSER_NOTICE_DISPLAY_SECONDS, wait_for_min_notice_display
from utils.discord_files import send_content_with_files
from utils.discord_presence import keep_typing
from utils.gemini_api import DEFAULT_GEMINI_MODEL, GeminiChatClient, _is_retryable_api_error
from utils.image_context_cache import ImageContextCache
from utils.json_response_protocol import build_fallback_response, build_repair_instruction, parse_model_response
from utils.memory_store import MemoryStore
from utils.persona_image_prompt import PersonaImagePromptStore, merge_persona_image_prompt
from utils.persona_store import PersonaPromptBuilder, PersonaStore, format_persona_list
from utils.web_tool_client import HeadlessBrowserClient

logger = logging.getLogger("discord.extensions.AIChat")
USER_CHAT_LOCKS = defaultdict(asyncio.Lock)
GENAI_RETRY_DELAYS = (1, 5, 5, 10, 30)
LOADING_EMOJI = "<a:loading:1303077872805744650>"


class PersonaSelect(discord.ui.Select):
    def __init__(self, cog: "AiChat", user_id: int):
        self.cog = cog
        self.user_id = user_id
        options = []
        for persona in cog.persona_store.list_personas()[:24]:
            options.append(
                discord.SelectOption(
                    label=persona.name[:100],
                    value=persona.key[:100],
                    description=f"檔名: {persona.key}"[:100],
                )
            )
        super().__init__(
            placeholder="選擇要切換的人設",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("這個選單不是給你使用的。", ephemeral=True)
            return
        selected = self.values[0]
        self.cog.user_settings.modify(user=interaction.user, persona=selected)
        await interaction.response.edit_message(
            embed=SakuraEmbedMsg(
                title="人設已切換",
                description=self.cog._format_current_settings(interaction.user),
            ),
            view=self.view,
        )


class PersonaSelectView(discord.ui.View):
    def __init__(self, cog: "AiChat", user_id: int):
        super().__init__(timeout=180)
        self.add_item(PersonaSelect(cog, user_id))


class AiChat(AiChatContextMixin, commands.Cog):
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
        self.browser_client = HeadlessBrowserClient()
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
        if not text and not message.attachments and not getattr(message, "embeds", []):
            return
        async with keep_typing(message.channel):
            try:
                result = await self.chat(message=message, dialogue=text, is_dm=is_dm)
                if result.get("delivered_message") is not None:
                    if result["image_paths"]:
                        image_sender = (
                            (lambda content, files: message.channel.send(content=content or None, files=files))
                            if is_dm and result.get("browser_used")
                            else (lambda content, files: result["delivered_message"].reply(content=content or None, files=files, mention_author=False))
                        )
                        await send_content_with_files(
                            image_sender,
                            "",
                            result["image_paths"],
                        )
                    return
                if result["image_paths"]:
                    await send_content_with_files(
                        lambda content, files: message.reply(content=content, files=files, mention_author=False),
                        result["reply_text"],
                        result["image_paths"],
                    )
                    return
                await message.reply(content=result["reply_text"], mention_author=False)
            except Exception as exc:
                logger.exception("ai_chat.message_failed")
                await message.reply(
                    embed=SakuraEmbedMsg(
                        title="訊息無法傳送",
                        description=_user_error_message(exc),
                    ),
                    mention_author=False,
                )

    async def chat(self, *, message: discord.Message, dialogue: str, is_dm: bool) -> dict:
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
            request_messages = await self._build_request_messages(message, dialogue, history, persona, memory, server_history)
            cached_content = self.persona_cache_names.get(persona.key) if persona is not None else None
            parsed, raw_response = await self._complete_and_parse_with_raw(request_messages, message, cached_content)
            browser_notice = None
            browser_used = False
            if parsed.browser is not None:
                browser_used = True
                parsed, browser_notice = await self._complete_after_browser(
                    request_messages,
                    raw_response,
                    parsed.browser.urls,
                    parsed.browser.search_queries,
                    parsed.browser.find_requests,
                    parsed.browser.include_images,
                    message,
                    cached_content,
                )
            image_status_message = None
            if parsed.image_generation is not None:
                image_status_message = await self._upsert_image_status(message, browser_notice, parsed.reply_text)
                if image_status_message is not None:
                    browser_notice = image_status_message
            image_paths, image_error = await self._maybe_generate_image(parsed, persona)
            if parsed.memory is not None:
                self.memory_store.set_memory(message.author.id, parsed.memory.content)
            reply_text = parsed.reply_text
            if image_error:
                reply_text = f"{reply_text}\n\n圖片生成服務暫時不可用，文字回覆先送出。"
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

    async def _complete_and_parse(self, messages: list[dict], message: discord.Message, cached_content: str | None):
        parsed, _ = await self._complete_and_parse_with_raw(messages, message, cached_content)
        return parsed

    async def _complete_and_parse_with_raw(self, messages: list[dict], message: discord.Message, cached_content: str | None):
        response = await self._complete_with_retry(messages, message, cached_content)
        try:
            return parse_model_response(response.visible_content), response.visible_content
        except ValueError:
            repair_messages = messages + [
                {"role": "assistant", "content": response.visible_content},
                {"role": "user", "content": build_repair_instruction()},
            ]
            repair_response = await self._complete_with_retry(repair_messages, message, cached_content)
            try:
                return parse_model_response(repair_response.visible_content), repair_response.visible_content
            except ValueError:
                logger.warning("ai_chat.invalid_json_response preview=%r", repair_response.visible_content[:300])
                return build_fallback_response(), repair_response.visible_content

    async def _complete_after_browser(
        self,
        request_messages: list[dict],
        raw_response: str,
        urls: list[str],
        search_queries: list[str],
        find_requests: list,
        include_images: bool,
        message: discord.Message,
        cached_content: str | None,
    ):
        browser_notice = await self._send_browser_notice(message, urls, search_queries, find_requests)
        browser_notice_sent_at = asyncio.get_running_loop().time() if browser_notice is not None else None
        browser_results = await fetch_browser_results(self.browser_client, urls, search_queries, find_requests, logger, include_images)
        await self._set_browser_reading_notice(browser_notice, browser_notice_sent_at)
        followup_messages = request_messages + [
            {"role": "assistant", "content": raw_response},
            {"role": "user", "content": build_browser_followup_content(browser_results)},
        ]
        parsed, _ = await self._complete_and_parse_with_raw(followup_messages, message, cached_content)
        return parsed, browser_notice

    async def _complete_with_retry(self, messages: list[dict], message: discord.Message, cached_content: str | None):
        retry_notice = None
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
                    retry_notice = await self._upsert_retry_notice(message, retry_notice, retry_number, delay_seconds)
                    await asyncio.sleep(delay_seconds)
        finally:
            if retry_notice is not None:
                try:
                    await retry_notice.delete()
                except discord.HTTPException:
                    logger.debug("ai_chat.retry_notice_delete_failed", exc_info=True)
        raise RuntimeError("模型服務目前忙碌或暫時不可用，請稍後再試一次。")

    async def _upsert_retry_notice(
        self,
        message: discord.Message,
        retry_notice: discord.Message | None,
        retry_number: int,
        delay_seconds: int,
    ) -> discord.Message | None:
        content = (
            f"{LOADING_EMOJI} GenAI 服務暫時不穩，正在重試 "
            f"({retry_number}/{len(GENAI_RETRY_DELAYS)})，下一次嘗試約 {delay_seconds} 秒後。"
        )
        try:
            if retry_notice is None:
                return await message.reply(content=content, mention_author=False)
            await retry_notice.edit(content=content)
            return retry_notice
        except discord.HTTPException:
            logger.debug("ai_chat.retry_notice_send_failed", exc_info=True)
            return retry_notice

    async def _send_browser_notice(self, message: discord.Message, urls: list[str], search_queries: list[str], find_requests: list) -> discord.Message | None:
        content = f"-# {LOADING_EMOJI} 正在上網查詢資料: {format_browser_notice_targets(urls, search_queries, find_requests)}"
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

    async def _upsert_image_status(self, message: discord.Message, browser_notice: discord.Message | None, reply_text: str):
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
        status_line = f"-# {LOADING_EMOJI} {status_text}"
        normalized_reply = str(reply_text or "").strip()
        max_reply_len = 2000 - len(status_line) - 2
        if len(normalized_reply) > max_reply_len:
            normalized_reply = f"{normalized_reply[:max(0, max_reply_len - 3)]}..."
        return f"{normalized_reply}\n\n{status_line}".strip()

    async def _maybe_generate_image(self, parsed, persona) -> tuple[list[Path], bool]:
        if parsed.image_generation is None:
            return [], False
        image_prompt = merge_persona_image_prompt(
            self.persona_image_prompt_store.get_prompt(persona),
            parsed.image_generation.prompt,
        )
        try:
            result = await asyncio.to_thread(self._get_imagine_client().generate, image_prompt)
        except ImagineAPIError as exc:
            logger.warning("ai_chat.image_generation_failed error=%s", exc)
            return [], True
        return result.image_paths, False

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

    def _get_imagine_client(self):
        if self.imagine_client is None:
            self.imagine_client = ImagineClient(
                api_key=os.getenv("AI_IMAGINE_API_KEY"),
                base_url=os.getenv("AI_IMAGINE_BASE_URL") or LOCAL_SUB2API_BASE_URL,
                model=os.getenv("AI_IMAGINE_MODEL", ""),
                api_mode=os.getenv("AI_IMAGINE_API_MODE", "local"),
            )
        return self.imagine_client

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
