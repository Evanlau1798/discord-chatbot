from __future__ import annotations

import tempfile
import unittest
import base64
from pathlib import Path
from unittest.mock import patch

from utils.ai_imagine_client import ImagineAPIError, ImagineClient, ImagineSourceImage


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

    def test_edit_uses_multipart_images_edits_endpoint(self):
        source = ImagineSourceImage(filename="source.jpg", mime_type="image/jpeg", data=_tiny_png())
        with tempfile.TemporaryDirectory() as tmp_dir:
            client = ImagineClient(
                api_key="test-key",
                base_url="https://imagine.example.test/v1",
                model="gpt-image-2",
                download_dir=Path(tmp_dir),
            )
            with patch("utils.ai_imagine_client.requests.post", return_value=FakeImagineResponse()) as post:
                result = client.generate("change the shirt to blue", operation="edit", source_images=[source])

        self.assertEqual(result.operation, "edit")
        self.assertEqual(post.call_args.args[0], "https://imagine.example.test/v1/images/edits")
        self.assertEqual(post.call_args.kwargs["data"], {"model": "gpt-image-2", "prompt": "change the shirt to blue"})
        self.assertNotIn("Content-Type", post.call_args.kwargs["headers"])
        field_name, file_value = post.call_args.kwargs["files"][0]
        self.assertEqual(field_name, "image[]")
        self.assertEqual(file_value[2], "image/png")
        self.assertTrue(file_value[0].endswith(".png"))

    def test_variation_intent_uses_edits_endpoint(self):
        source = ImagineSourceImage(filename="source.png", mime_type="image/png", data=_tiny_png())
        with tempfile.TemporaryDirectory() as tmp_dir:
            client = ImagineClient("test-key", "https://imagine.example.test/v1", "gpt-image-2", Path(tmp_dir))
            with patch("utils.ai_imagine_client.requests.post", return_value=FakeImagineResponse()) as post:
                result = client.generate("create a similar pose", operation="variation", source_images=[source])

        self.assertEqual(result.operation, "variation")
        self.assertEqual(post.call_args.args[0], "https://imagine.example.test/v1/images/edits")

    def test_edit_requires_at_least_one_source_image(self):
        client = ImagineClient("test-key", "https://imagine.example.test/v1", "gpt-image-2")

        with self.assertRaisesRegex(ImagineAPIError, "來源圖片"):
            client.generate("change it", operation="edit")

    def test_create_rejects_source_images_instead_of_silently_editing(self):
        client = ImagineClient("test-key", "https://imagine.example.test/v1", "gpt-image-2")
        source = ImagineSourceImage(filename="source.png", mime_type="image/png", data=_tiny_png())

        with self.assertRaisesRegex(ImagineAPIError, "create"):
            client.generate("draw", operation="create", source_images=[source])

    def test_edit_rejects_invalid_image_bytes_before_http_request(self):
        client = ImagineClient("test-key", "https://imagine.example.test/v1", "gpt-image-2")
        source = ImagineSourceImage(filename="source.png", mime_type="image/png", data=b"not-an-image")

        with patch("utils.ai_imagine_client.requests.post") as post:
            with self.assertRaisesRegex(ImagineAPIError, "無效"):
                client.generate("change it", operation="edit", source_images=[source])

        post.assert_not_called()


def _tiny_png() -> bytes:
    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )


if __name__ == "__main__":
    unittest.main()
