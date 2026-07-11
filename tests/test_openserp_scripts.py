from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class OpenSerpScriptTests(unittest.TestCase):
    def test_start_script_uses_fixed_local_source_and_secure_runtime_settings(self):
        text = (ROOT / "start_openserp.sh").read_text(encoding="utf-8")

        self.assertIn('SOURCE_DIR="${ROOT_DIR}/reference/openserp"', text)
        self.assertIn('127.0.0.1:${OPENSERP_PORT:-17000}:7000', text)
        self.assertIn("OPENSERP_SERVER_INSECURE=false", text)
        self.assertIn("OPENSERP_CORS_ENABLED=false", text)
        self.assertIn("OPENSERP_PROXIES_ALLOW_REQUEST_PROXY_URL=false", text)
        self.assertIn("OPENSERP_CAPTCHA_SOLVER_ENABLED=false", text)
        self.assertIn("OPENSERP_RESILIENCE_MAX_RETRIES=0", text)
        self.assertIn("OPENSERP_GOOGLE_RATE_REQUESTS=60", text)
        self.assertIn("OPENSERP_GOOGLE_RATE_BURST=1", text)

    def test_stop_script_targets_only_project_container(self):
        text = (ROOT / "stop_openserp.sh").read_text(encoding="utf-8")

        self.assertIn('CONTAINER_NAME="discord-chatbot-openserp"', text)
        self.assertNotIn("podman stop --all", text)
        self.assertNotIn("docker stop $(", text)


if __name__ == "__main__":
    unittest.main()
