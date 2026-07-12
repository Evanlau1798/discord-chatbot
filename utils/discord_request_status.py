from __future__ import annotations


class DiscordRequestStatus:
    def __init__(self, message, logger):
        self._message = message
        self._logger = logger
        self._base_content: str | None = None
        self._retry_content: str | None = None
        self._retry_only_notice = False
        self.notice = None

    async def set_base(self, content: str) -> None:
        self._base_content = str(content or "")
        self._retry_only_notice = False
        await self._upsert()

    async def set_retry(self, content: str) -> None:
        self._retry_content = str(content or "")
        self._retry_only_notice = self.notice is None and self._base_content is None
        await self._upsert()

    async def clear_retry(self) -> None:
        self._retry_content = None
        if self.notice is None:
            self._retry_only_notice = False
            return
        if self._retry_only_notice and self._base_content is None:
            notice = self.notice
            self.notice = None
            self._retry_only_notice = False
            try:
                await notice.delete()
            except Exception:
                self._logger.debug("discord.request_status_delete_failed", exc_info=True)
            return
        self._retry_only_notice = False
        await self._edit_current()

    def _render(self) -> str:
        parts = [part for part in (self._base_content, self._retry_content) if part]
        return "\n\n".join(parts)[:1900]

    async def _upsert(self) -> None:
        content = self._render()
        try:
            if self.notice is None:
                self.notice = await self._message.reply(content=content, mention_author=False)
                return
            await self.notice.edit(content=content)
        except Exception:
            self._logger.debug("discord.request_status_upsert_failed", exc_info=True)

    async def _edit_current(self) -> None:
        try:
            await self.notice.edit(content=self._render())
        except Exception:
            self._logger.debug("discord.request_status_edit_failed", exc_info=True)
