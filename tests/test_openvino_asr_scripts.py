from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class OpenVINOASRScriptTests(unittest.TestCase):
    def test_start_script_builds_current_source_and_binds_loopback_gpu_service(self):
        text = (ROOT / "start_openvino_asr.sh").read_text(encoding="utf-8")

        self.assertIn('CONTAINER_NAME="discord-chatbot-openvino-asr"', text)
        self.assertIn('services/openvino_asr/Containerfile', text)
        self.assertIn('127.0.0.1:${LOCAL_ASR_PORT}:${LOCAL_ASR_PORT}', text)
        self.assertIn('--device /dev/dri/renderD128', text)
        self.assertIn('--userns keep-id', text)
        self.assertIn('--group-add keep-groups', text)
        self.assertIn('LOCAL_ASR_MAX_CONCURRENCY', text)
        self.assertIn('LOCAL_ASR_MAX_DURATION_SECONDS', text)
        self.assertIn('/health/ready', text)
        self.assertNotIn('0.0.0.0:${LOCAL_ASR_PORT}', text)

        containerfile = (ROOT / "services/openvino_asr/Containerfile").read_text(encoding="utf-8")
        self.assertIn("chmod -R a+rX /app/services", containerfile)

    def test_start_script_honors_disabled_service(self):
        text = (ROOT / "start_openvino_asr.sh").read_text(encoding="utf-8")

        self.assertIn('LOCAL_ASR_ENABLED', text)
        self.assertIn('"${ROOT_DIR}/stop_openvino_asr.sh"', text)

    def test_stop_script_targets_only_project_asr_container(self):
        text = (ROOT / "stop_openvino_asr.sh").read_text(encoding="utf-8")

        self.assertIn('CONTAINER_NAME="discord-chatbot-openvino-asr"', text)
        self.assertNotIn("stop --all", text)
        self.assertNotIn("rm --all", text)

    def test_scripts_have_valid_bash_syntax(self):
        for filename in ("start_openvino_asr.sh", "stop_openvino_asr.sh"):
            with self.subTest(filename=filename):
                completed = subprocess.run(
                    ["bash", "-n", str(ROOT / filename)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
