from __future__ import annotations

import logging
import os
from pathlib import Path

import discord
from dotenv import load_dotenv

load_dotenv()


def setup_logging() -> None:
    Path("tmp").mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


class DiscordChatBot(discord.Bot):
    async def on_ready(self):
        await self.change_presence(activity=discord.Activity(type=discord.ActivityType.listening, name="@mention / DM"))
        print(f"Bot ready: {self.user}")


def build_bot() -> DiscordChatBot:
    intents = discord.Intents.default()
    intents.messages = True
    intents.dm_messages = True
    intents.message_content = os.getenv("DISCORD_MESSAGE_CONTENT_INTENT", "").strip() == "1"
    bot = DiscordChatBot(intents=intents)
    bot.load_extension("extensions.AIChat")
    return bot


if __name__ == "__main__":
    setup_logging()
    token = (
        os.getenv("DISCORD_BOT_TOKEN")
        or os.getenv("DISCORD_BOT_KEY")
        or os.getenv("DISCORD_TOKEN")
        or os.getenv("BOT_TOKEN")
    )
    if not token:
        raise RuntimeError("缺少 DISCORD_BOT_TOKEN / DISCORD_TOKEN / BOT_TOKEN")
    build_bot().run(token)
