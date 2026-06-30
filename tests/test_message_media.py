from __future__ import annotations

import unittest
from unittest.mock import patch

from utils.message_media import (
    build_multimodal_content,
    collect_message_image_urls,
    collect_message_media,
    collect_message_source_urls,
)


class FakeAttachment:
    def __init__(self, url: str, content_type: str = "", filename: str = "", data: bytes = b"image"):
        self.url = url
        self.content_type = content_type
        self.filename = filename
        self.size = len(data)
        self.data = data
        self.read_use_cached = None

    async def read(self, *, use_cached=False):
        self.read_use_cached = use_cached
        return self.data


class FakeMessage:
    def __init__(self, attachments=None, embeds=None):
        self.attachments = attachments or []
        self.embeds = embeds or []


class FakeEmbedProxy:
    def __init__(self, url: str = "", proxy_url: str = ""):
        self.url = url
        self.proxy_url = proxy_url


class FakeEmbed:
    def __init__(self, *, url: str = "", image=None, thumbnail=None, video=None):
        self.url = url
        self.image = image
        self.thumbnail = thumbnail
        self.video = video


class MessageMediaTests(unittest.IsolatedAsyncioTestCase):
    def test_collects_direct_image_url_from_dialogue(self):
        urls = collect_message_image_urls(
            FakeMessage(),
            "請看 https://i.ytimg.com/vi/dQw4w9WgXcQ/maxresdefault.jpg",
        )

        self.assertEqual(urls, ["https://i.ytimg.com/vi/dQw4w9WgXcQ/maxresdefault.jpg"])

    def test_collects_discord_image_attachment_url(self):
        message = FakeMessage([
            FakeAttachment("https://cdn.discordapp.com/file.png", "image/png", "file.png"),
        ])

        urls = collect_message_image_urls(message, "")

        self.assertEqual(urls, ["https://cdn.discordapp.com/file.png"])

    def test_collects_image_attachment_without_extension_when_content_type_is_image(self):
        message = FakeMessage([
            FakeAttachment("https://cdn.discordapp.com/attachments/123", "image/png", "upload"),
        ])

        urls = collect_message_image_urls(message, "")

        self.assertEqual(urls, ["https://cdn.discordapp.com/attachments/123"])

    def test_collects_embed_image_url_for_discord_gif_picker(self):
        gif_url = "https://media1.tenor.com/m/q15XQ3vgQnwAAAAd/test.gif"
        message = FakeMessage(embeds=[FakeEmbed(image=FakeEmbedProxy(gif_url))])

        urls = collect_message_image_urls(message, "")

        self.assertEqual(urls, [gif_url])

    def test_collects_supported_gif_page_url_as_source(self):
        urls = collect_message_source_urls(FakeMessage(), "https://tenor.com/view/test-gif-12345")

        self.assertEqual(urls, ["https://tenor.com/view/test-gif-12345"])

    def test_dedupes_same_image_url_from_attachment_and_dialogue(self):
        image_url = "https://example.test/a.jpg"
        message = FakeMessage([
            FakeAttachment(image_url, "image/jpeg", "a.jpg"),
        ])

        urls = collect_message_image_urls(message, f"請看 {image_url}")

        self.assertEqual(urls, [image_url])

    def test_rejects_private_image_url(self):
        urls = collect_message_image_urls(FakeMessage(), "http://127.0.0.1:8080/a.jpg")

        self.assertEqual(urls, [])

    def test_builds_openai_style_image_parts(self):
        content = build_multimodal_content("payload", ["https://example.test/a.jpg"])

        self.assertEqual(content[0], {"type": "text", "text": "payload"})
        self.assertEqual(content[1], {"type": "image_url", "image_url": {"url": "https://example.test/a.jpg"}})

    async def test_attachment_media_uses_cached_read_bytes(self):
        attachment = FakeAttachment(
            "https://cdn.discordapp.com/attachments/123",
            "image/png",
            "upload",
            data=b"png-bytes",
        )

        media = await collect_message_media(FakeMessage([attachment]), "")

        self.assertEqual(media.image_urls, ["https://cdn.discordapp.com/attachments/123"])
        self.assertEqual(media.content_parts[0]["type"], "image_bytes")
        self.assertEqual(media.content_parts[0]["image_bytes"]["data"], b"png-bytes")
        self.assertEqual(media.content_parts[0]["image_bytes"]["mime_type"], "image/png")
        self.assertTrue(attachment.read_use_cached)

    async def test_attachment_media_dedupes_dialogue_url_part(self):
        image_url = "https://example.test/a.jpg"
        attachment = FakeAttachment(image_url, "image/jpeg", "a.jpg", data=b"jpeg-bytes")

        media = await collect_message_media(FakeMessage([attachment]), f"請看 {image_url}")

        self.assertEqual(media.image_urls, [image_url])
        self.assertEqual(len(media.content_parts), 1)
        self.assertEqual(media.content_parts[0]["type"], "image_bytes")

    async def test_embed_media_adds_image_url_part_and_dedupes_dialogue_url(self):
        gif_url = "https://media1.tenor.com/m/q15XQ3vgQnwAAAAd/test.gif"
        message = FakeMessage(embeds=[FakeEmbed(image=FakeEmbedProxy(gif_url))])

        media = await collect_message_media(message, f"看這張 {gif_url}")

        self.assertEqual(media.image_urls, [gif_url])
        self.assertEqual(media.content_parts, [{"type": "image_url", "image_url": {"url": gif_url}}])

    async def test_resolves_supported_gif_page_url_from_dialogue(self):
        page_url = "https://tenor.com/view/test-gif-12345"
        gif_url = "https://media.tenor.com/example/tenor.gif"

        with patch("utils.message_media._resolve_media_page_image_url", return_value=gif_url):
            media = await collect_message_media(FakeMessage(), page_url)

        self.assertEqual(media.image_urls, [gif_url])
        self.assertEqual(media.content_parts, [{"type": "image_url", "image_url": {"url": gif_url}}])


if __name__ == "__main__":
    unittest.main()
