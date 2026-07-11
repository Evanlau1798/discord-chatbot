import unittest
from pathlib import Path

from utils.openserp_client import OpenSerpResponse, OpenSerpSource
from utils.openserp_search import SearchPlanner


class _LoggingClient:
    def search(self, request):
        return OpenSerpResponse(
            sources=(
                OpenSerpSource("Private user query source one", "https://one.test", text="private-content-one " * 900),
                OpenSerpSource("Private user query source two", "https://two.test", text="private-content-two " * 900),
            ),
            failed_engines=("google",),
            diagnostics=("google:captcha_detected",),
        )


class SearchObservabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_logs_metrics_without_query_or_page_content(self):
        planner = SearchPlanner(timeout_ms=1000, client=_LoggingClient())

        with self.assertLogs("utils.openserp_search", level="INFO") as captured:
            await planner.search_many(["private user query"])

        text = "\n".join(captured.output)
        self.assertIn("queries=1", text)
        self.assertIn("selected=2", text)
        self.assertIn("captcha=True", text)
        self.assertIn("failed_engines=google", text)
        self.assertNotIn("private user query", text)
        self.assertNotIn("private-content", text)

    def test_invalid_model_response_log_does_not_include_preview(self):
        source = (Path(__file__).resolve().parents[1] / "extensions" / "AIChat.py").read_text(encoding="utf-8")

        self.assertNotIn("invalid_json_response preview=", source)
        self.assertIn("invalid_json_response content_chars=", source)


if __name__ == "__main__":
    unittest.main()
