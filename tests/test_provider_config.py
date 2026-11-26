"""Provider routing must never send the other provider's credentials."""

import unittest
from unittest.mock import patch

from pydantic import ValidationError

from livekit_vlm_agent.config import Settings


class ProviderConfigTests(unittest.TestCase):
    def settings(self, **values):
        with patch.dict("os.environ", {}, clear=True):
            return Settings(_env_file=None, **values)

    def test_official_harness_is_the_default_and_only_supported_mode(self):
        self.assertEqual(self.settings().harness_mode, 'qwen_live_official')
        for mode in ('legacy_auto', 'controlled_manual', 'direct_background'):
            with self.subTest(mode=mode), patch.dict("os.environ", {"HARNESS_MODE": mode}, clear=True):
                with self.assertRaisesRegex(ValidationError, 'qwen_live_official'):
                    Settings(_env_file=None)

    def test_yibuapi_uses_its_own_key_and_fixed_endpoint(self):
        settings = self.settings(
            model_provider="yibuapi", yibuapi_api_key=" yibu-test ",
            qwen_api_key="aliyun-test", qwen_realtime_url="wss://other.example/",
        )
        self.assertEqual(settings.qwen_endpoint(), "wss://yibuapi.com/v1/realtime")
        self.assertEqual(settings.realtime_headers(), {
            "Authorization": "Bearer yibu-test", "OpenAI-Beta": "realtime=v1",
        })
        self.assertEqual(settings.qwen_missing(), [])

    def test_yibuapi_never_falls_back_to_aliyun_key(self):
        settings = self.settings(model_provider="yibuapi", qwen_api_key="aliyun-test")
        self.assertEqual(settings.qwen_missing(), ["YIBUAPI_API_KEY"])
        self.assertEqual(settings.realtime_api_key(), "")

    def test_aliyun_keeps_workspace_validation_and_own_key(self):
        settings = self.settings(
            qwen_workspace_id="workspace-test", qwen_api_key="aliyun-test",
            yibuapi_api_key="yibu-test",
        )
        self.assertEqual(settings.realtime_headers(), {"Authorization": "Bearer aliyun-test"})
        self.assertEqual(settings.qwen_missing(), [])
        self.assertIn("workspace-test.cn-beijing.maas.aliyuncs.com", settings.qwen_endpoint())
        settings.qwen_realtime_url = "wss://yibuapi.com/v1/realtime"
        with self.assertRaises(ValueError):
            settings.qwen_endpoint()
