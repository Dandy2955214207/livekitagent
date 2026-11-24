import asyncio
from pathlib import Path
from types import SimpleNamespace
import tempfile
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from livekit_vlm_agent.coordination.direct import DirectState
from livekit_vlm_agent.media.fanout import VideoFanout
from livekit_vlm_agent.observation.background import BackgroundObserver, visual_supported
from livekit_vlm_agent.config import Settings
from test_background_state import FakeEvaluator, until


class ObserverTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.state = DirectState('call', memory_root=self.folder.name, observation_available=True)
        self.session = SimpleNamespace(video=SimpleNamespace(enabled=True, epoch=0),
                                       background_frames=VideoFanout())
        self.evaluator = FakeEvaluator()
        self.observer = BackgroundObserver(self.session, self.state, evaluator=self.evaluator,
                                           now=lambda: 2, autostart=False)

    async def asyncTearDown(self):
        await self.observer.aclose()
        await self.state.aclose()
        self.folder.cleanup()

    def publish(self, at):
        self.session.background_frames.publish(SimpleNamespace(epoch=self.session.video.epoch,
                                               received_at=at, image='encoded'))

    async def create(self):
        await self.state.control({'action': 'observation_create', 'request_id': 'create', 'content': 'keys'})
        await self.observer.tick()

    async def test_opt_in_fresh_two_frames_once_text_result_and_close(self):
        self.publish(1)
        await self.observer.tick()
        self.assertFalse(self.evaluator.calls)  # no task, no model
        await self.create()
        self.assertEqual(self.state.tasks.task.status, 'active')
        await self.observer.tick()
        self.assertFalse(self.evaluator.calls)  # pre-task evidence ignored
        self.publish(1.5); self.publish(2)
        self.evaluator.release.set()
        pushed = []
        self.observer.on_changed = lambda: pushed.append(self.state.tasks.events[-1].event_id)
        await self.observer.tick()
        await until(lambda: self.state.tasks.task.status == 'completed')
        status = await self.state.status()
        self.assertEqual(len(status['events']), 1)
        self.assertFalse(status['events'][0]['voice_played'])
        self.assertEqual(status['events'][0]['delivery'], 'queued')
        self.assertEqual(pushed, [status['events'][0]['event_id']])
        await self.observer.tick()
        self.publish(2); self.publish(2)
        await self.observer.tick()
        self.assertEqual(len(self.evaluator.calls), 1)
        self.assertEqual(self.observer.runner.counts['generations'], 1)

    async def test_pause_cancels_inflight_without_clearing_budget_and_camera_close_preserves_goal(self):
        await self.create()
        self.publish(1.5); self.publish(2)
        await self.observer.tick()
        await until(lambda: bool(self.evaluator.calls))
        await self.state.control({'action':'observation_pause','request_id':'pause'})
        await self.observer.tick()
        self.assertFalse(self.state.tasks.events)
        self.assertEqual(self.observer.runner.counts['generations'], 1)
        await self.state.control({'action':'observation_resume','request_id':'resume'})
        self.session.video.enabled = False
        await self.observer.tick()
        self.assertEqual(self.state.tasks.task.status, 'active')
        self.assertEqual(self.observer.phase, 'waiting_camera')
        self.assertEqual(len(self.evaluator.calls), 1)

    def test_provider_and_feature_gate(self):
        self.assertTrue(visual_supported(Settings(_env_file=None, model_provider='yibuapi', background_observation_enabled=True)))
        self.assertFalse(visual_supported(Settings(_env_file=None, model_provider='aliyun')))
        self.assertFalse(visual_supported(Settings(_env_file=None, model_provider='yibuapi', background_observation_enabled=False)))
        self.assertFalse(visual_supported(Settings(_env_file=None, model_provider='yibuapi', qwen_model='other')))
