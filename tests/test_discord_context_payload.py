from __future__ import annotations

import unittest

from utils.discord_context_payload import build_attachment_payload


class DiscordContextPayloadTests(unittest.TestCase):
    def test_voice_attachment_metadata_is_structured_for_model_context(self):
        attachment = type("Attachment", (), {
            "filename": "voice.ogg",
            "url": "https://cdn.discordapp.com/voice.ogg",
            "content_type": "audio/ogg",
            "duration_secs": 12.5,
        })()
        message = type("Message", (), {
            "attachments": [attachment],
            "flags": type("Flags", (), {"is_voice_message": True})(),
        })()

        payload = build_attachment_payload(message)

        self.assertEqual(payload[0]["durationSeconds"], 12.5)
        self.assertTrue(payload[0]["isVoiceMessage"])


if __name__ == "__main__":
    unittest.main()
