from __future__ import annotations

import unittest

from utils.browser_challenge_detector import (
    ANTI_BOT_ERROR,
    build_reliable_content,
    detect_captcha_challenge,
)


class BrowserChallengeDetectorContentTests(unittest.TestCase):
    def test_rejects_mtcaptcha_text_from_reference_project(self):
        content = build_reliable_content("Security check", "Complete the MTCaptcha challenge to continue.")

        self.assertEqual(content.text, "")
        self.assertEqual(content.error, ANTI_BOT_ERROR)

    def test_rejects_geetest_slider_text_from_reference_project(self):
        content = build_reliable_content("Verification", "Drag the Geetest slider puzzle to verify you are human.")

        self.assertEqual(content.text, "")
        self.assertEqual(content.error, ANTI_BOT_ERROR)

    def test_rejects_short_page_with_detected_captcha_dom(self):
        content = build_reliable_content("Human verification", "Continue", has_captcha_challenge=True)

        self.assertEqual(content.text, "")
        self.assertEqual(content.error, ANTI_BOT_ERROR)

    def test_allows_regular_content_when_captcha_dom_is_not_blocking(self):
        text = "OpenAI Codex for Open Source\n" + "\n".join(f"Section {index}: readable content" for index in range(80))

        content = build_reliable_content("OpenAI", text, has_captcha_challenge=True)

        self.assertEqual(content.error, "")
        self.assertIn("OpenAI Codex for Open Source", content.text)


class FakePage:
    def __init__(self, value):
        self.value = value
        self.argument = None

    async def evaluate(self, _script, argument):
        self.argument = argument
        return self.value


class BrowserChallengeDetectorDomTests(unittest.IsolatedAsyncioTestCase):
    async def test_detect_captcha_challenge_passes_reference_selectors_to_browser(self):
        page = FakePage(True)

        detected = await detect_captcha_challenge(page)

        self.assertTrue(detected)
        self.assertIn(".geetest_window", page.argument["selectors"])
        self.assertIn("#mtcaptcha-iframe-1", page.argument["selectors"])
        self.assertIn("recaptcha", page.argument["frameMarkers"])

    async def test_detect_captcha_challenge_fails_closed_on_browser_error(self):
        class BrokenPage:
            async def evaluate(self, _script, _argument):
                raise RuntimeError("detached")

        detected = await detect_captcha_challenge(BrokenPage())

        self.assertFalse(detected)


if __name__ == "__main__":
    unittest.main()
