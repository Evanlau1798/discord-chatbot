from __future__ import annotations

import logging

from utils.ai_imagine_client import ImagineAPIError
from utils.chat_client import ChatAPIError

logger = logging.getLogger("discord.extensions.AIChat")


def log_image_operation(parsed, policy) -> None:
    block = parsed.image_generation
    logger.info(
        "ai_chat.image_operation_selected operation=%s candidate_count=%s selected_source_count=%s",
        block.operation if block else "none",
        len(policy.candidate_ids) if policy else 0,
        len(block.source_image_ids) if block else 0,
    )


def user_error_message(exc: Exception) -> str:
    if isinstance(exc, ImagineAPIError):
        return "圖片生成服務暫時不可用，請稍後再試一次。"
    if isinstance(exc, ChatAPIError):
        if exc.status_code in {400, 413, 415, 422}:
            return "所選模型無法處理這個請求或附件，請確認模型支援目前的文字與多模態輸入。"
        if exc.status_code in {401, 403, 404}:
            return "模型服務設定或驗證失敗，請聯絡管理員檢查 provider、model 與 API key。"
        return "模型服務目前忙碌或暫時不可用，請稍後再試一次。"
    if isinstance(exc, (ValueError, RuntimeError)):
        return str(exc)
    return "系統處理訊息時發生未預期錯誤，請稍後再試一次。"
