from __future__ import annotations

import unittest

from services.openvino_asr.config import ASRConfig, ASRConfigError


class ASRConfigTests(unittest.TestCase):
    def test_defaults_are_safe_for_local_gpu_service(self):
        config = ASRConfig.from_env({})

        self.assertTrue(config.enabled)
        self.assertEqual(config.port, 18765)
        self.assertEqual(config.max_concurrency, 1)
        self.assertEqual(config.max_duration_seconds, 60.0)
        self.assertEqual(config.max_queue_size, 2)
        self.assertEqual(config.device, "GPU")

    def test_all_runtime_limits_can_be_overridden(self):
        config = ASRConfig.from_env({
            "LOCAL_ASR_ENABLED": "false",
            "LOCAL_ASR_PORT": "28765",
            "LOCAL_ASR_MAX_CONCURRENCY": "2",
            "LOCAL_ASR_MAX_DURATION_SECONDS": "45.5",
            "LOCAL_ASR_MAX_QUEUE_SIZE": "4",
            "LOCAL_ASR_QUEUE_TIMEOUT_SECONDS": "7",
            "LOCAL_ASR_REQUEST_TIMEOUT_SECONDS": "30",
            "LOCAL_ASR_MAX_UPLOAD_BYTES": "1048576",
            "LOCAL_ASR_DEVICE": "GPU.0",
        })

        self.assertFalse(config.enabled)
        self.assertEqual(config.port, 28765)
        self.assertEqual(config.max_concurrency, 2)
        self.assertEqual(config.max_duration_seconds, 45.5)
        self.assertEqual(config.max_queue_size, 4)
        self.assertEqual(config.queue_timeout_seconds, 7.0)
        self.assertEqual(config.request_timeout_seconds, 30.0)
        self.assertEqual(config.max_upload_bytes, 1048576)
        self.assertEqual(config.device, "GPU.0")

    def test_invalid_boolean_is_rejected(self):
        with self.assertRaisesRegex(ASRConfigError, "LOCAL_ASR_ENABLED"):
            ASRConfig.from_env({"LOCAL_ASR_ENABLED": "sometimes"})

    def test_invalid_limits_are_rejected(self):
        invalid_values = (
            ("LOCAL_ASR_PORT", "80"),
            ("LOCAL_ASR_MAX_CONCURRENCY", "0"),
            ("LOCAL_ASR_MAX_DURATION_SECONDS", "0"),
            ("LOCAL_ASR_MAX_DURATION_SECONDS", "nan"),
            ("LOCAL_ASR_MAX_QUEUE_SIZE", "-1"),
            ("LOCAL_ASR_REQUEST_TIMEOUT_SECONDS", "0"),
        )
        for key, value in invalid_values:
            with self.subTest(key=key), self.assertRaisesRegex(ASRConfigError, key):
                ASRConfig.from_env({key: value})

    def test_auto_and_cpu_devices_are_rejected(self):
        for device in ("AUTO", "CPU"):
            with self.subTest(device=device), self.assertRaisesRegex(ASRConfigError, "LOCAL_ASR_DEVICE"):
                ASRConfig.from_env({"LOCAL_ASR_DEVICE": device})


if __name__ == "__main__":
    unittest.main()
