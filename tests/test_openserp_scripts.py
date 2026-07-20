from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class OpenSerpScriptTests(unittest.TestCase):
    def test_start_script_uses_pinned_stable_release_and_secure_runtime_settings(self):
        text = (ROOT / "start_openserp.sh").read_text(encoding="utf-8")

        self.assertIn('OPENSERP_RELEASE="', text)
        self.assertIn('OPENSERP_IMAGE_DIGEST="sha256:', text)
        self.assertIn('IMAGE_REFERENCE="docker.io/karust/openserp@${OPENSERP_IMAGE_DIGEST}"', text)
        self.assertIn('"${RUNTIME}" pull "${IMAGE_REFERENCE}"', text)
        self.assertIn("--pull never", text)
        self.assertNotIn("karust/openserp:latest", text)
        self.assertNotIn("reference/openserp", text)
        self.assertNotIn('"${RUNTIME}" build', text)
        self.assertIn('127.0.0.1:${OPENSERP_PORT:-17000}:7000', text)
        self.assertIn("OPENSERP_SERVER_INSECURE=false", text)
        self.assertIn("OPENSERP_CORS_ENABLED=false", text)
        self.assertIn("OPENSERP_PROXIES_ALLOW_REQUEST_PROXY_URL=false", text)
        self.assertIn("OPENSERP_CAPTCHA_SOLVER_ENABLED=false", text)
        self.assertIn("OPENSERP_RESILIENCE_MAX_RETRIES=0", text)
        self.assertIn("OPENSERP_GOOGLE_RATE_REQUESTS=60", text)
        self.assertIn("OPENSERP_GOOGLE_RATE_BURST=1", text)
        self.assertIn('"${IMAGE_REFERENCE}" serve', text)

    def test_run_bot_does_not_update_openserp_source(self):
        text = (ROOT / "run_bot.sh").read_text(encoding="utf-8")

        self.assertIn('START_OPENSERP_SCRIPT="$PROJECT_DIR/start_openserp.sh"', text)
        self.assertNotIn("git pull", text)
        self.assertNotIn("git fetch", text)
        self.assertNotIn("git clone", text)

    def test_run_bot_checks_memory_key_before_stopping_services(self):
        text = (ROOT / "run_bot.sh").read_text(encoding="utf-8")

        self.assertIn('MEMORY_KEY_SETUP_SCRIPT="$PROJECT_DIR/utils/memory_key_setup.py"', text)
        self.assertIn("ensure_memory_encryption_key", text)
        self.assertIn("[Y/n]", text)
        self.assertLess(text.index("ensure_memory_encryption_key\nstop_bot"), text.index('echo "Stopping existing OpenVINO'))
        self.assertNotIn("MEMORY_ENCRYPTION_KEY=", text)

    def test_stop_script_targets_only_project_container(self):
        text = (ROOT / "stop_openserp.sh").read_text(encoding="utf-8")

        self.assertIn('CONTAINER_NAME="discord-chatbot-openserp"', text)
        self.assertNotIn("podman stop --all", text)
        self.assertNotIn("docker stop $(", text)


if __name__ == "__main__":
    unittest.main()
