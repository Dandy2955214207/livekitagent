import asyncio
import json
import time
from types import SimpleNamespace
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock

import jwt
from pydantic import SecretStr

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from livekit_vlm_agent.config import Settings
from livekit_vlm_agent.session import SessionBusy, SessionRegistry


class SessionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.settings = Settings(_env_file=None, livekit_api_key=SecretStr("test-key"),
            livekit_api_secret=SecretStr("test-secret-for-unit-tests-only-123456"))
        self.client = SimpleNamespace(room=SimpleNamespace(create_room=AsyncMock(), delete_room=AsyncMock()),
            agent_dispatch=SimpleNamespace(create_dispatch=AsyncMock()))
        self.registry = SessionRegistry(self.settings, self.client)

    async def test_token_can_only_join_its_room_and_publish_microphone_and_camera(self):
        result = await self.registry.create()
        claims = jwt.decode(result["token"], self.settings.livekit_api_secret.get_secret_value(), algorithms=["HS256"])
        grant = claims["video"]
        self.assertEqual(grant["room"], result["room_name"])
        self.assertFalse(grant["roomAdmin"])
        self.assertFalse(grant["roomCreate"])
        self.assertEqual(grant["canPublishSources"], ["microphone", "camera"])
        self.assertLessEqual(claims["exp"] - claims["nbf"], 60)
        self.assertNotIn("api_key", json.dumps(result).lower())
        call = self.registry.calls[result["session_id"]]
        with self.assertRaises(KeyError):
            self.registry.authorize(call.id, result["access_token"], worker=True)
        with self.assertRaises(KeyError):
            self.registry.authorize(call.id, call.worker_access)

    async def test_concurrent_join_requests_create_only_one_dispatch(self):
        results = await asyncio.gather(self.registry.create(), self.registry.create(), return_exceptions=True)
        self.assertEqual(sum(isinstance(item, SessionBusy) for item in results), 1)
        self.client.agent_dispatch.create_dispatch.assert_awaited_once()

    async def test_official_core_capabilities_report_call_memory_without_credentials(self):
        result = await self.registry.create()
        self.assertEqual(result['harness_mode'], 'qwen_live_official')
        self.assertNotIn('direct_background_capabilities', result)
        self.assertNotIn('harness_auto_input', result)
        capabilities = result['official_harness_capabilities']
        self.assertEqual(capabilities['engine'], 'qwen-live-harness')
        self.assertEqual(capabilities['memory'], 'call_visual')
        self.assertFalse(capabilities['proactive_enabled'])
        self.assertNotIn('apiKey', json.dumps(capabilities))

    async def test_failed_dispatch_rolls_back_its_room_and_next_attempt_can_join(self):
        self.client.agent_dispatch.create_dispatch.side_effect = RuntimeError("Unavailable")
        with self.assertRaises(RuntimeError):
            await self.registry.create()
        self.client.room.delete_room.assert_awaited_once()
        self.assertTrue(next(iter(self.registry.calls.values())).cleaned)
        self.client.agent_dispatch.create_dispatch.side_effect = None
        result = await self.registry.create()
        self.assertEqual(result["state"], "connecting")

    async def test_join_timeout_and_repeated_hangup_clean_only_owned_room(self):
        result = await self.registry.create()
        call = self.registry.calls[result["session_id"]]
        call.created_at = time.monotonic() - self.settings.join_timeout_seconds - 1
        await self.registry.sweep()
        self.assertEqual(call.state, "error")
        self.assertTrue(call.cleaned)
        await self.registry.end(call)
        self.client.room.delete_room.assert_awaited_once()
        self.assertEqual(self.client.room.delete_room.call_args.args[0].room, result["room_name"])

    async def test_worker_loss_is_detected_and_releases_the_room(self):
        result = await self.registry.create()
        call = self.registry.calls[result["session_id"]]
        call.state, call.worker_seen = "ready", time.monotonic() - 30
        await self.registry.sweep()
        self.assertTrue(call.cleaned)
        self.assertIn("上下文", call.error)


if __name__ == "__main__":
    unittest.main()
