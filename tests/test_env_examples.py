from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class EnvironmentExampleTests(unittest.TestCase):
    def test_common_integrations_have_ready_to_use_defaults(self):
        values = _active_values(REPO_ROOT / ".env.example")

        self.assertEqual(values["AI_CHAT_PROVIDER"], "nvidia")
        self.assertEqual(values["OPENSERP_BASE_URL"], "http://127.0.0.1:17000")
        self.assertEqual(values["YT_DLP_BIN"], "yt-dlp")
        self.assertEqual(values["FFMPEG_BIN"], "ffmpeg")
        self.assertEqual(values["LOCAL_ASR_ENABLED"], "1")

    def test_image_generation_is_the_only_disabled_optional_service(self):
        values = _active_values(REPO_ROOT / ".env.example")

        self.assertEqual(values["AI_IMAGINE_ENABLED"], "0")
        self.assertEqual(values["AI_IMAGINE_RATE_LIMIT_ENABLED"], "1")

    def test_secrets_and_personal_identifiers_stay_blank(self):
        values = _active_values(REPO_ROOT / ".env.example")

        for key in (
            "DISCORD_BOT_TOKEN",
            "MEMORY_ENCRYPTION_KEY",
            "GEMINI_API_KEY",
            "NVIDIA_API_KEY",
            "OPENAI_COMPAT_API_KEY",
            "AI_IMAGINE_API_KEY",
            "IMAGINE_QUOTA_ADMIN_USER_ID",
        ):
            self.assertEqual(values[key], "")

    def test_nvidia_message_strategy_choices_are_documented(self):
        text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")

        self.assertIn("preserve: Keep system/user/assistant roles unchanged", text)
        self.assertIn("user_prefix: Move all system instructions", text)


def _active_values(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


if __name__ == "__main__":
    unittest.main()
