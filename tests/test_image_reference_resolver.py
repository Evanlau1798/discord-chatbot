from __future__ import annotations

import unittest

from utils.image_reference_resolver import ImageReferenceResolver


class ImageReferenceResolverTests(unittest.IsolatedAsyncioTestCase):
    async def test_current_attachments_are_first_and_use_trusted_ids(self):
        message = _message(100, owner_id=1, attachments=[
            _attachment(11, "first.png", b"first"),
            _attachment(12, "second.png", b"second"),
        ])
        resolver = ImageReferenceResolver(_bot(), _store())

        candidates = await resolver.resolve(message, "請參考這兩張畫一張")

        self.assertEqual([item.candidate_id for item in candidates], ["current:0", "current:1"])
        self.assertEqual(candidates[0].data, b"first")
        self.assertEqual(candidates[0].to_prompt_payload()["source"], "current_attachment")

    async def test_direct_reply_image_is_available_without_recent_image_words(self):
        replied = _message(90, owner_id=2, attachments=[_attachment(20, "old.png", b"old")])
        message = _message(100, owner_id=1, reference=_reference(replied))
        resolver = ImageReferenceResolver(_bot(), _store())

        candidates = await resolver.resolve(message, "把衣服改成藍色")

        self.assertEqual([item.candidate_id for item in candidates], ["reply:90:0"])

    async def test_previous_image_language_does_not_load_history_into_initial_request(self):
        recent = _message(80, owner_id=99, attachments=[_attachment(30, "generated.png", b"generated")])
        bot = _bot(messages={80: recent})
        store = _store(records=[_record("80", owner_id="1")])
        resolver = ImageReferenceResolver(bot, store)
        unrelated = _message(100, owner_id=1)

        candidates = await resolver.resolve(unrelated, "把剛才那張圖片換個姿勢")

        self.assertEqual(candidates, [])
        self.assertEqual(store.latest_calls, [])

    async def test_history_references_expose_ids_without_fetching_image_bytes(self):
        store = _store(records=[_record("80", owner_id="7", image_count=2, channel_id="55")])
        resolver = ImageReferenceResolver(_bot(), store)
        message = _message(100, owner_id=7, channel_id=55)

        references = resolver.list_history_references(message)

        self.assertEqual(store.latest_calls[0], ("10", "55", "7"))
        self.assertEqual(references[0].reference_id, "discord-message:10:55:80")
        self.assertEqual(references[0].to_prompt_payload()["imageCount"], 2)

    async def test_requested_history_reference_loads_only_allowed_message(self):
        recent = _message(80, owner_id=99, attachments=[_attachment(30, "generated.png", b"generated")])
        resolver = ImageReferenceResolver(_bot(messages={80: recent}), _store(records=[_record("80", owner_id="1")]))
        source_message = _message(100, owner_id=1)
        references = resolver.list_history_references(source_message)

        candidates = await resolver.resolve_requested(
            source_message,
            ("discord-message:10:20:80", "discord-message:10:20:999"),
            references,
        )

        self.assertEqual([item.candidate_id for item in candidates], ["history:80:0"])
        self.assertEqual(candidates[0].data, b"generated")

    async def test_discord_message_link_loads_images_from_the_current_guild(self):
        linked = _message(80, owner_id=2, attachments=[_attachment(40, "linked.png", b"linked")])
        resolver = ImageReferenceResolver(_bot(messages={80: linked}), _store())

        candidates = await resolver.resolve(
            _message(100, owner_id=1),
            "請修改 https://discord.com/channels/10/20/80 這張圖",
        )

        self.assertEqual([item.candidate_id for item in candidates], ["linked:80:0"])

    async def test_discord_message_link_cannot_cross_guild_boundaries(self):
        linked = _message(80, owner_id=2, attachments=[_attachment(40, "linked.png", b"linked")])
        resolver = ImageReferenceResolver(_bot(messages={80: linked}), _store())

        candidates = await resolver.resolve(
            _message(100, owner_id=1),
            "請修改 https://discord.com/channels/999/20/80 這張圖",
        )

        self.assertEqual(candidates, [])


class _Store:
    def __init__(self, records):
        self.records = records
        self.latest_calls = []

    def latest(self, *, guild_id, channel_id, owner_id, limit=3, now=None):
        self.latest_calls.append((guild_id, channel_id, owner_id))
        return self.records[:limit]


def _store(records=None):
    return _Store(records or [])


def _record(message_id: str, owner_id: str, image_count: int = 1, channel_id: str = "20"):
    return type("Record", (), {
        "guild_id": "10",
        "channel_id": channel_id,
        "message_id": message_id,
        "owner_id": owner_id,
        "image_count": image_count,
    })()


class _Bot:
    def __init__(self, messages=None):
        self.messages = messages or {}
        self.channels = {}

    def get_message(self, message_id):
        return self.messages.get(int(message_id))

    def get_channel(self, channel_id):
        return self.channels.get(int(channel_id))


def _bot(messages=None):
    return _Bot(messages)


def _reference(message):
    return type("Reference", (), {
        "resolved": message,
        "message_id": message.id,
        "channel_id": message.channel.id,
    })()


def _attachment(attachment_id: int, filename: str, data: bytes):
    class Attachment:
        id = attachment_id
        content_type = "image/png"
        size = len(data)

        async def read(self, use_cached=True):
            return data

    item = Attachment()
    item.filename = filename
    item.url = f"https://cdn.discordapp.com/attachments/{attachment_id}/{filename}"
    return item


def _message(message_id: int, *, owner_id: int, channel_id: int = 20, attachments=None, reference=None):
    return type("Message", (), {
        "id": message_id,
        "guild": type("Guild", (), {"id": 10})(),
        "channel": type("Channel", (), {"id": channel_id})(),
        "author": type("Author", (), {"id": owner_id})(),
        "attachments": attachments or [],
        "reference": reference,
    })()


if __name__ == "__main__":
    unittest.main()
