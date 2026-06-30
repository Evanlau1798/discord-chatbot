from __future__ import annotations

import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

from utils.image_context_cache import (
    ImageContextCache,
    extract_discord_message_context_keys,
    message_context_key_from_ids,
)
from utils.json_response_protocol import ImageUnderstandingBlock


class FakeEntity:
    def __init__(self, entity_id):
        self.id = entity_id


class FakeMessage:
    def __init__(self, *, message_id=300, guild_id=100, channel_id=200, author_id=400):
        self.id = message_id
        self.guild = FakeEntity(guild_id) if guild_id is not None else None
        self.channel = FakeEntity(channel_id)
        self.author = FakeEntity(author_id)
        self.created_at = datetime.fromtimestamp(1_700_000_000, timezone.utc)


class ImageContextCacheTests(unittest.TestCase):
    def test_message_context_key_uses_guild_channel_and_message_id(self):
        key = message_context_key_from_ids(guild_id=100, channel_id=200, message_id=300)

        self.assertEqual(key, "discord-message:100:200:300")

    def test_extracts_discord_message_link_keys(self):
        keys = extract_discord_message_context_keys(
            "請看 https://discord.com/channels/100/200/300 和 https://ptb.discord.com/channels/@me/201/301"
        )

        self.assertEqual(keys, ["discord-message:100:200:300", "discord-message:@me:201:301"])

    def test_stores_and_batch_loads_unexpired_context(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            cache = ImageContextCache(Path(tmp_dir) / "image_context_cache.db", ttl_seconds=3600)
            message = FakeMessage()
            understanding = ImageUnderstandingBlock(
                summary="一張反應梗圖。",
                visible_text=("不要瞎掰",),
                details=("角色在吐槽",),
            )

            stored = cache.store_message_context(
                message,
                image_count=1,
                source_urls=["https://cdn.example.test/a.gif"],
                understanding=understanding,
            )
            loaded = cache.get_many([stored.message_key])

        self.assertEqual(loaded[stored.message_key].summary_text, "一張反應梗圖。")
        self.assertEqual(loaded[stored.message_key].understanding.summary, "一張反應梗圖。")
        self.assertEqual(loaded[stored.message_key].source_urls, ("https://cdn.example.test/a.gif",))

    def test_expired_context_is_not_returned(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            cache = ImageContextCache(Path(tmp_dir) / "image_context_cache.db", ttl_seconds=-1)
            message = FakeMessage()
            stored = cache.store_message_context(
                message,
                image_count=1,
                source_urls=[],
                understanding=ImageUnderstandingBlock(summary="過期摘要。"),
            )

            loaded = cache.get_many([stored.message_key], now=int(time.time()))

        self.assertEqual(loaded, {})


if __name__ == "__main__":
    unittest.main()
