from __future__ import annotations


def format_queue_notice_content(update, loading_emoji: str) -> str:
    prefix = f"-# {loading_emoji} 正在等候訊息發送..."
    if getattr(update, "status", "") == "waiting":
        return f"{prefix}前面還有{int(getattr(update, 'queue_ahead', 0))}則訊息"
    if getattr(update, "status", "") == "next":
        return f"{prefix}您是下一位!"
    return f"-# {loading_emoji} 正在輸入回覆..."


async def upsert_reply_notice(message, notice, content: str, logger):
    try:
        if notice is None:
            return await message.reply(content=content[:1900], mention_author=False)
        await notice.edit(content=content[:1900])
        return notice
    except Exception:
        logger.debug("discord.status_notice_upsert_failed", exc_info=True)
        return notice


async def edit_notice(notice, content: str, logger) -> bool:
    if notice is None:
        return False
    try:
        await notice.edit(content=str(content or "")[:2000])
        return True
    except Exception:
        logger.debug("discord.status_notice_edit_failed", exc_info=True)
        return False


async def delete_notice(notice, logger) -> None:
    if notice is None:
        return
    try:
        await notice.delete()
    except Exception:
        logger.debug("discord.status_notice_delete_failed", exc_info=True)
