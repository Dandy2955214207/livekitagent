"""D1 isolation and lifecycle checks without cloud calls."""

from pathlib import Path
import tempfile
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from livekit_vlm_agent.config import Settings
from livekit_vlm_agent.media.fanout import VideoFanout
from livekit_vlm_agent.media.video import EncodedFrame
from livekit_vlm_agent.observation.contracts import MonitorAnswer
from livekit_vlm_agent.providers.qwen_live import QwenLiveModel, direct_capabilities
from livekit_vlm_agent.coordination.direct import DirectState
from test_realtime_adapter import FakeClient
from test_video import until


def frame(at=10, epoch=0, image="abc"):
    return EncodedFrame(image, at, at, epoch, 10, 10, 3, 0)


class ContractTests(unittest.TestCase):
    def test_strict_monitor_answers_do_not_accept_forged_provenance_or_coerce_negative(self):
        answer = '{"result":"not_matched","observation_summary":"蓝色",' \
                 '"evidence_summary":"当前蓝色画面","uncertainty":""}'
        self.assertEqual(MonitorAnswer.model_validate_json(answer).result, "not_matched")
        for invalid in (answer[:-1], answer[:-1] + ',"task_id":"forged"}',
                        answer.replace('"not_matched"', 'false')):
            with self.assertRaises(ValueError):
                MonitorAnswer.model_validate_json(invalid)

    def test_fanout_independent_cursors_byte_age_and_epoch_limits(self):
        fanout = VideoFanout(max_frames=2, max_bytes=6, max_age=10)
        for at in (10, 11, 12):
            fanout.publish(frame(at))
        first = fanout.read(after=0, camera_epoch=0, now=12)
        self.assertEqual([f.frame_id for f in first], [2, 3])
        self.assertEqual(first, fanout.read(after=0, camera_epoch=0, now=12))
        self.assertEqual([f.frame_id for f in fanout.read(after=2, camera_epoch=0, now=12)], [3])
        fanout.publish(frame(13, epoch=1))
        self.assertEqual(len(fanout.read(camera_epoch=1, now=13)), 1)
        self.assertFalse(fanout.read(camera_epoch=1, now=24))
        self.assertEqual(fanout.bytes, 0)
        fanout.publish(frame(25, image="oversized"))
        self.assertFalse(fanout.frames)


class DirectAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.patch = patch("livekit_vlm_agent.providers.qwen_realtime.QwenClient", FakeClient)
        self.patch.start()
        self.model = QwenLiveModel(Settings(_env_file=None, harness_mode="direct_background"))
        self.session = self.model.session()
        await self.session.wait_ready()

    async def asyncTearDown(self):
        await self.model.aclose()
        self.patch.stop()

    async def test_sample_is_encoded_once_and_background_cannot_consume_foreground(self):
        video = self.session.video
        video.encoder = lambda *args: ("abc", 10, 10, 3)
        video.offer("fixed raw")
        await until(lambda: bool(self.session.background_frames.frames))
        samples = self.session.background_frames.read(camera_epoch=video.epoch, now=time.monotonic())
        foreground = video.take()
        self.assertEqual(samples[0].image, foreground.image)
        self.assertEqual(video.counts["encoded"], 1)
        self.assertEqual(samples, self.session.background_frames.read(
            camera_epoch=video.epoch, now=time.monotonic()))
        self.session.clear_video()
        self.assertFalse(self.session.background_frames.frames)
        self.assertIsNone(video.take())

    async def test_direct_generation_does_not_require_decision_and_close_releases_resources(self):
        generated = []
        self.session.on("generation_created", generated.append)
        self.session._on_event({"type": "response.created", "response": {"id": "user-answer"}}, None)
        self.assertEqual(len(generated), 1)
        self.assertFalse(self.session._client.sent)
        self.session.background_frames.publish(frame(time.monotonic()))
        await self.session.aclose()
        self.assertFalse(self.session.background_frames.frames)
        self.assertFalse(self.model.sessions)
        self.assertTrue(self.session.video._task.done())
        self.assertTrue(self.session._main.done())
        self.assertTrue(self.session._generations["user-answer"].closed)
        self.assertEqual(direct_capabilities()["proactive_audio"], "unavailable")

    async def test_broken_background_callback_does_not_break_foreground_encoding(self):
        def broken(frame):
            raise RuntimeError("background unavailable")
        video = self.session.video
        video.encoder = lambda *args: ("abc", 10, 10, 3)
        video.on_encoded = broken
        video.offer("fixed raw")
        await until(lambda: video.counts["fanout_errors"] == 1)
        self.assertIsNotNone(video.take())
        self.assertFalse(video._task.done())

    async def test_paused_observation_and_local_memory_do_not_gate_direct_user_answers(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        state = DirectState('call', memory_root=folder.name)
        self.session.direct_state = state
        await state.control({'request_id': 'target', 'action': 'observation_create', 'content': '钥匙'})
        await state.control({'request_id': 'fact', 'action': 'memory_put', 'content': '用户事实'})
        self.session._on_event({'type': 'conversation.item.input_audio_transcription.completed',
                                'item_id': 'real-user', 'transcript': '正常问题'}, None)
        created = []
        self.session.on('generation_created', created.append)
        self.session._on_event({'type': 'response.created', 'response': {'id': 'direct-answer'}}, None)
        self.assertEqual(len(created), 1)
        self.assertEqual(state.recent[-1]['source_id'], 'real-user')
        self.assertEqual(len(state.memory.records()), 1)
        self.assertFalse(self.session._client.sent)  # no context injection or DECIDE
        self.session.clear_video()
        self.assertEqual(state.tasks.task.status, 'paused')
        await self.session.aclose()
        self.assertTrue(state._closed)


if __name__ == "__main__":
    unittest.main()
