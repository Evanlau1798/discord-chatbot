from __future__ import annotations

import logging
from pathlib import Path

import discord

logger = logging.getLogger("discord.utils.discord_files")


def build_discord_files(paths) -> list[discord.File]:
    return [discord.File(str(Path(path)), filename=Path(path).name) for path in paths or ()]


def cleanup_local_files(paths):
    for raw_path in paths or ():
        path = Path(raw_path)
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("failed_to_cleanup_local_file path=%s error=%s", path, exc)


async def send_content_with_files(send_callable, content: str, image_paths):
    files = build_discord_files(image_paths)
    try:
        return await send_callable(content, files)
    finally:
        for file in files:
            close = getattr(file, "close", None)
            if callable(close):
                close()
        cleanup_local_files(image_paths)
