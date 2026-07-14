from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from utils.image_reference_store import ImageReferenceStore


class ImageReferenceStoreTests(unittest.TestCase):
    def test_latest_records_are_isolated_by_owner_and_channel(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = ImageReferenceStore(Path(tmp_dir) / "references.db", ttl_seconds=60)
            store.record_ids(guild_id="10", channel_id="20", message_id="100", owner_id="1", image_count=1, now=10)
            store.record_ids(guild_id="10", channel_id="20", message_id="101", owner_id="2", image_count=1, now=11)
            store.record_ids(guild_id="10", channel_id="21", message_id="102", owner_id="1", image_count=1, now=12)

            records = store.latest(guild_id="10", channel_id="20", owner_id="1", now=20)

        self.assertEqual([record.message_id for record in records], ["100"])

    def test_latest_returns_newest_first_and_excludes_expired_records(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = ImageReferenceStore(Path(tmp_dir) / "references.db", ttl_seconds=60)
            store.record_ids(guild_id="@me", channel_id="20", message_id="old", owner_id="1", image_count=1, now=1)
            store.record_ids(guild_id="@me", channel_id="20", message_id="new", owner_id="1", image_count=2, now=80)

            records = store.latest(guild_id="@me", channel_id="20", owner_id="1", now=100)

        self.assertEqual([record.message_id for record in records], ["new"])
        self.assertEqual(records[0].image_count, 2)

    def test_record_message_ignores_messages_without_images(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = ImageReferenceStore(Path(tmp_dir) / "references.db")
            message = _message(message_id=100, owner_id=1, image_count=0)

            self.assertIsNone(store.record_message(message, owner_id=1))
            self.assertEqual(store.latest(guild_id="10", channel_id="20", owner_id="1"), [])


def _message(*, message_id: int, owner_id: int, image_count: int):
    attachments = [
        type("Attachment", (), {"content_type": "image/png", "filename": f"{index}.png"})()
        for index in range(image_count)
    ]
    return type("Message", (), {
        "id": message_id,
        "guild": type("Guild", (), {"id": 10})(),
        "channel": type("Channel", (), {"id": 20})(),
        "author": type("Author", (), {"id": owner_id})(),
        "attachments": attachments,
    })()


if __name__ == "__main__":
    unittest.main()
