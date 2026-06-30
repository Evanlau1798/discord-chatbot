from __future__ import annotations

import unittest

from utils.imagine_config import get_imagine_base_url, is_image_generation_enabled


class ImagineConfigTests(unittest.TestCase):
    def test_image_generation_is_disabled_by_default(self):
        self.assertFalse(is_image_generation_enabled({}))

    def test_image_generation_can_be_enabled(self):
        self.assertTrue(is_image_generation_enabled({"AI_IMAGINE_ENABLED": "1"}))
        self.assertTrue(is_image_generation_enabled({"AI_IMAGINE_ENABLED": "true"}))

    def test_image_generation_can_be_disabled(self):
        self.assertFalse(is_image_generation_enabled({"AI_IMAGINE_ENABLED": "0"}))
        self.assertFalse(is_image_generation_enabled({"AI_IMAGINE_ENABLED": "false"}))

    def test_get_imagine_base_url_uses_env_value(self):
        self.assertEqual(
            get_imagine_base_url({"AI_IMAGINE_BASE_URL": "https://imagine.example.test/v1"}),
            "https://imagine.example.test/v1",
        )


if __name__ == "__main__":
    unittest.main()
