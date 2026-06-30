from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from utils.ai_imagine_client import ImagineClient


class FakeImagineResponse:
    status_code = 200
    headers = {}

    def json(self):
        return {"data": [{"b64_json": "aGVsbG8=", "mime_type": "image/png"}]}

    def close(self):
        return None


class ImagineClientTests(unittest.TestCase):
    def test_generate_uses_extended_read_timeout(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            client = ImagineClient(
                api_key="test-key",
                base_url="https://imagine.example.test/v1",
                model="gpt-image-2",
                download_dir=Path(tmp_dir),
            )
            with patch("utils.ai_imagine_client.requests.post", return_value=FakeImagineResponse()) as post:
                client.generate("draw a cat")

        self.assertEqual(post.call_args.kwargs["timeout"], (10, 300))


if __name__ == "__main__":
    unittest.main()
