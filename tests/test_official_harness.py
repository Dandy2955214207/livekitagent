"""Exercise the real official Node core with a loopback provider, never an account."""
import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, Mock, call, patch

from livekit import rtc

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from livekit_vlm_agent.config import PROJECT_ROOT, Settings
from livekit_vlm_agent.official.bridge import OfficialBridge, capabilities
from livekit_vlm_agent.official.worker import run_official_call


class OfficialTests(unittest.IsolatedAsyncioTestCase):
    async def test_worker_forwards_mute_and_unmute_only_for_the_callers_microphone(self):
        room = rtc.EventEmitter()
        room.remote_participants = {}
        room.isconnected = lambda: False
        ctx = SimpleNamespace(room=room, connect=AsyncMock(), wait_for_participant=AsyncMock(),
                              add_shutdown_callback=Mock(), shutdown=Mock())
        user = SimpleNamespace(identity='caller')
        other = SimpleNamespace(identity='other')
        mic = SimpleNamespace(source=rtc.TrackSource.SOURCE_MICROPHONE)
        camera = SimpleNamespace(source=rtc.TrackSource.SOURCE_CAMERA)

        async def emit_track_events():
            for event in ('track_muted', 'track_unmuted'):
                # LiveKit emits (participant, publication) for both events.
                room.emit(event, user, mic)
                room.emit(event, other, mic)
                room.emit(event, user, camera)
            room.emit('participant_disconnected', user)

        bridge = Mock(start=AsyncMock(side_effect=emit_track_events), aclose=AsyncMock())
        session = MagicMock()
        client = MagicMock()
        session.__aenter__.return_value = client
        response = Mock(json=AsyncMock(return_value={'state': 'ready'}))
        client.post.return_value.__aenter__.return_value = response
        with (
            patch('livekit_vlm_agent.official.worker.OfficialBridge', return_value=bridge),
            patch('livekit_vlm_agent.official.worker.aiohttp.ClientSession', return_value=session),
        ):
            await run_official_call(ctx, Settings(_env_file=None),
                                    {'session_id': 'fixture', 'identity': user.identity, 'worker_access': 'fixture'})

        self.assertEqual(bridge.control.call_args_list, [
            call({'type': 'input_mute', 'muted': True}),
            call({'type': 'input_mute', 'muted': False}),
        ])
        reported_states = [request.kwargs['json']['state'] for request in client.post.call_args_list]
        self.assertEqual(reported_states, ['ready', 'closed'])
        bridge.aclose.assert_awaited_once()

    async def test_real_child_core_protocol_and_owned_child_cleanup(self):
        events = []

        async def event_received(event):
            events.append(event)

        settings = Settings(_env_file=None, model_provider='aliyun', qwen_workspace_id='fixture',
                            qwen_api_key='fixture-key', yibuapi_api_key='other-provider-key')
        fixture = PROJECT_ROOT / 'harness/qwen-live/test/fixture-bridge.mjs'
        with patch('livekit_vlm_agent.official.bridge.BRIDGE', fixture):
            bridge = OfficialBridge(settings, 'fixture-call', event_received)
            try:
                await bridge.start()
                self.assertTrue(any(event['type'] == 'ready' for event in events))
                await bridge.push_audio(rtc.AudioFrame(bytes(640), 16000, 1, 320))
                async with asyncio.timeout(6):
                    while not any(event['type'] == 'audio_end' for event in events):
                        await asyncio.sleep(.02)
                self.assertTrue(any(event['type'] == 'transcript' for event in events))
                self.assertFalse(any(event['type'].startswith('playback_') for event in events))
                pid = bridge.process.pid
            finally:
                await bridge.aclose()
            self.assertIsNotNone(pid)
            self.assertIsNotNone(bridge.process.returncode)
            self.assertTrue(bridge.video._task.done())
            self.assertTrue(all(task.done() for task in bridge.tasks))
            await bridge.aclose()

    async def test_configuration_bounds_and_provider_isolation(self):
        settings = Settings(_env_file=None, harness_mode='qwen_live_official', qwen_workspace_id='invalid/workspace')
        with self.assertRaises(ValueError):
            OfficialBridge(settings, 'call', None)
        self.assertFalse(capabilities(settings)['proactive_enabled'])
        self.assertEqual(capabilities(settings)['memory'], 'call_visual')
        for kwargs in ({'qwen_live_max_generations': 81}, {'qwen_live_max_monitor_generations': 61}, {'qwen_live_max_monitor_generations': 0},
                       {'qwen_live_max_call_seconds': 1801}):
            with self.assertRaises(ValueError):
                Settings(_env_file=None, **kwargs)

    async def test_control_and_audio_queues_are_bounded(self):
        events = []

        async def event_received(event):
            events.append(event)

        bridge = OfficialBridge(Settings(_env_file=None, model_provider='yibuapi'), 'call', event_received)
        try:
            for _ in range(20):
                bridge.control({'type': 'camera_clear'})
            self.assertEqual(len(bridge._controls), 16)
            for _ in range(101):
                await bridge.push_audio(rtc.AudioFrame(bytes(640), 16000, 1, 320))
            self.assertTrue(bridge.failed)
            self.assertEqual(bridge._audio.qsize(), 100)
            self.assertEqual(events[-1]['code'], 'official_input_backlog')
        finally:
            await bridge.aclose()


if __name__ == '__main__':
    unittest.main()
