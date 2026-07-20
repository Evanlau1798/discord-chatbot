from __future__ import annotations

import json
import unittest

from utils.browser_result_types import BrowserFetchResult
from utils.ai_chat_context import AiChatContextMixin
from utils.image_context_cache import CachedImageContext
from utils.image_reference_resolver import DeferredImageReference, ImageReferenceCandidate
from utils.json_response_protocol import ImageUnderstandingBlock
from utils.persona_store import Persona, PersonaPromptBuilder


class FakeImageContextCache:
    def __init__(self):
        self.requested_keys = []
        self.contexts = {
            "discord-message:100:200:300": CachedImageContext(
                message_key="discord-message:100:200:300",
                guild_id="100",
                channel_id="200",
                message_id="300",
                image_count=1,
                source_urls=("https://cdn.example.test/a.png",),
                understanding=ImageUnderstandingBlock(summary="一張反應梗圖。"),
                summary_text="一張反應梗圖。",
                created_at=1,
                expires_at=2,
            )
        }

    def get_many(self, keys):
        self.requested_keys.append(tuple(keys))
        return {key: self.contexts[key] for key in keys if key in self.contexts}


class FakeBrowserClient:
    def __init__(self, results=None):
        self.results = results or []
        self.received_urls = []

    async def fetch_urls_and_searches(self, urls, search_queries, find_requests=None, include_images=False, **kwargs):
        self.received_urls = list(urls)
        return self.results


class FakeCog(AiChatContextMixin):
    def __init__(self, browser_client=None):
        self.image_context_cache = FakeImageContextCache()
        self.user_history = {}
        self.bot = type("Bot", (), {"user": None})()
        self.prompt_builder = PersonaPromptBuilder()
        self.browser_client = browser_client or FakeBrowserClient()

    def save_user_history(self):
        return None


class AiChatContextImageCacheTests(unittest.TestCase):
    def test_finalize_server_history_batch_loads_cached_image_contexts(self):
        cog = FakeCog()
        history, participants = cog._finalize_server_history(
            [
                {
                    "role": "user",
                    "authorDisplayName": "Evan",
                    "content": "這張圖是什麼？",
                    "_authorID": 1,
                    "_messageContextKey": "discord-message:100:200:300",
                }
            ],
            current_author_id=1,
            current_display_name="Evan",
        )

        self.assertEqual(cog.image_context_cache.requested_keys, [("discord-message:100:200:300",)])
        self.assertEqual(participants, {1: "Evan"})
        self.assertNotIn("_messageContextKey", history[0])
        self.assertEqual(history[0]["imageUnderstanding"]["summary"], "一張反應梗圖。")

    def test_image_only_dm_history_keeps_placeholder_and_adds_cached_note_at_read_time(self):
        cog = FakeCog()

        cog._append_history(
            "user-1",
            "",
            "這是一張反應梗圖。",
            image_context_key="discord-message:100:200:300",
        )
        history = cog.get_user_history("user-1")

        self.assertEqual(cog.user_history["user-1"][0]["content"], "[使用者傳送了圖片]")
        self.assertNotIn("cached image understanding", cog.user_history["user-1"][0]["content"])
        self.assertIn("cached image understanding", history[0]["content"])

    def test_embed_only_server_history_entry_is_kept(self):
        cog = FakeCog()
        author = type("Author", (), {"id": 1, "bot": False, "display_name": "Evan", "name": "evan"})()
        channel = type("Channel", (), {"id": 200})()
        guild = type("Guild", (), {"id": 100, "get_member": lambda self, user_id: None})()
        embed = type("Embed", (), {"url": "https://media1.tenor.com/m/example/test.gif"})()
        message = type(
            "Message",
            (),
            {
                "id": 300,
                "content": "",
                "attachments": [],
                "embeds": [embed],
                "author": author,
                "channel": channel,
                "guild": guild,
            },
        )()

        entry = cog._build_server_history_entry(message, current_author_id=1, current_display_name="Evan")

        self.assertIsNotNone(entry)
        self.assertEqual(entry["embeds"][0]["url"], "https://media1.tenor.com/m/example/test.gif")


class AiChatContextBrowserPrefetchTests(unittest.IsolatedAsyncioTestCase):
    async def test_request_messages_expose_trusted_image_generation_candidates(self):
        candidate = ImageReferenceCandidate(
            candidate_id="reply:90:0",
            source="discord_reply",
            message_id="90",
            attachment_id="900",
            filename="source.png",
            mime_type="image/png",
            data=b"reference-image",
        )
        cog = FakeCog()

        messages = await cog._build_request_messages(
            _fake_message(),
            "修改上一張圖片",
            [],
            _fake_persona(),
            "",
            [],
            image_candidates=[candidate],
            historical_image_references=[
                DeferredImageReference(
                    reference_id="discord-message:100:200:80",
                    guild_id="100",
                    channel_id="200",
                    message_id="80",
                    image_count=1,
                )
            ],
        )
        content = messages[-1]["content"]
        payload = json.loads(content[0]["text"])["payload"]

        self.assertEqual(payload["imageGenerationCandidates"][0]["id"], "reply:90:0")
        self.assertEqual(payload["imageGenerationCandidates"][0]["visualIndex"], 0)
        self.assertNotIn("data", payload["imageGenerationCandidates"][0])
        self.assertNotIn("imageOperationConstraint", payload)
        self.assertEqual(
            payload["historicalImageReferences"][0]["messageReferenceId"],
            "discord-message:100:200:80",
        )
        self.assertIn('id="reply:90:0"', content[1]["text"])
        self.assertEqual(content[2]["image_bytes"]["data"], b"reference-image")

    async def test_request_messages_prefetch_explicit_web_url_into_payload(self):
        browser_client = FakeBrowserClient([
            BrowserFetchResult(
                requested_url="https://example.test/page",
                source_type="url",
                final_url="https://example.test/page",
                title="Example",
                text="Readable page body.",
            )
        ])
        cog = FakeCog(browser_client=browser_client)

        messages = await cog._build_request_messages(
            _fake_message(),
            "請整理 https://example.test/page",
            [],
            _fake_persona(),
            "",
            [],
        )
        payload = json.loads(messages[-1]["content"])["payload"]

        self.assertEqual(browser_client.received_urls, ["https://example.test/page"])
        self.assertEqual(
            payload["prefetchedBrowserContext"]["browserResults"][0]["title"],
            "Example",
        )

    async def test_request_messages_attach_prefetched_browser_images_as_parts(self):
        browser_client = FakeBrowserClient([
            BrowserFetchResult(
                requested_url="https://example.test/article",
                source_type="url",
                final_url="https://example.test/article",
                title="Article",
                text="Readable page body.",
                image_urls=("https://example.test/cover.jpg",),
            )
        ])
        cog = FakeCog(browser_client=browser_client)

        messages = await cog._build_request_messages(
            _fake_message(),
            "請整理 https://example.test/article",
            [],
            _fake_persona(),
            "",
            [],
        )
        content = messages[-1]["content"]
        payload = json.loads(content[0]["text"])["payload"]

        self.assertEqual(content[1], {"type": "image_url", "image_url": {"url": "https://example.test/cover.jpg"}})
        self.assertIn("imageUnderstandingInstruction", payload)

    async def test_request_messages_do_not_prefetch_direct_image_url(self):
        browser_client = FakeBrowserClient()
        cog = FakeCog(browser_client=browser_client)

        messages = await cog._build_request_messages(
            _fake_message(),
            "這張圖 https://example.test/a.jpg",
            [],
            _fake_persona(),
            "",
            [],
        )
        content = messages[-1]["content"]
        text = content[0]["text"] if isinstance(content, list) else content
        payload = json.loads(text)["payload"]

        self.assertEqual(browser_client.received_urls, [])
        self.assertNotIn("prefetchedBrowserContext", payload)
        self.assertEqual(payload["imageUrls"], ["https://example.test/a.jpg"])


def _fake_persona():
    return Persona(key="test", name="Test", data={"characterName": "Test"})


def _fake_message():
    author = type("Author", (), {"id": 1, "bot": False, "display_name": "Evan", "name": "evan"})()
    channel = type("Channel", (), {"id": 200})()
    return type(
        "Message",
        (),
        {
            "id": 300,
            "content": "",
            "attachments": [],
            "embeds": [],
            "author": author,
            "channel": channel,
            "guild": None,
            "created_at": None,
        },
    )()


if __name__ == "__main__":
    unittest.main()
