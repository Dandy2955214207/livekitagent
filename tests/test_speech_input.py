import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

from livekit import rtc
from livekit.agents.vad import VADEventType

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from livekit_vlm_agent.media.speech_input import SpeechInput


class Stream:
    def __init__(self):
        self.events = asyncio.Queue()
        self.closed = False

    def push_frame(self, frame):
        pass

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self.events.get()

    async def aclose(self):
        self.closed = True


class SpeechInputTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_detector_initializes_and_silence_stays_quiet(self):
        from unittest.mock import patch
        from livekit_vlm_agent.config import Settings
        from livekit_vlm_agent.providers.qwen_harness import QwenHarnessModel
        from test_harness_adapter import FakeClient
        with patch("livekit_vlm_agent.providers.qwen_realtime.QwenClient", FakeClient):
            model = QwenHarnessModel(Settings(_env_file=None, harness_auto_input=True))
            session = model.session()
            try:
                await session.wait_ready()
                self.assertIsNotNone(session._speech_input)
                for _ in range(50):
                    session.push_audio(rtc.AudioFrame(bytes(640), 16000, 1, 320))
                    await asyncio.sleep(.005)
                await asyncio.sleep(.1)
                self.assertFalse(session._client.responses)
                self.assertFalse(session._client.inputs)
                self.assertIsNone(session._recording_id)
                self.assertFalse(session._failed)
            finally:
                await model.aclose()

    async def test_preroll_streaming_silence_and_cleanup(self):
        stream = Stream()
        starts, audio, ends, failures = [], [], [], []
        speech = SpeechInput(SimpleNamespace(stream=lambda: stream),
            start=starts.append, audio=audio.append, end=lambda: ends.append(True), failed=failures.append)
        try:
            for _ in range(20):
                speech.push(rtc.AudioFrame(bytes(640), 16000, 1, 320))
            self.assertFalse(starts)
            await stream.events.put(SimpleNamespace(type=VADEventType.START_OF_SPEECH))
            await asyncio.sleep(0)
            self.assertEqual(len(starts[0]), 15 * 640)
            speech.push(rtc.AudioFrame(bytes([1, 0]) * 320, 16000, 1, 320))
            self.assertEqual(audio, [bytes([1, 0]) * 320])
            await stream.events.put(SimpleNamespace(type=VADEventType.END_OF_SPEECH))
            await asyncio.sleep(0)
            speech.push(rtc.AudioFrame(bytes(640), 16000, 1, 320))
            self.assertEqual(len(audio), 1)
            self.assertEqual(ends, [True])
            self.assertFalse(failures)
        finally:
            await speech.aclose()
        self.assertTrue(stream.closed)
        self.assertTrue(speech.task.done())

    async def test_vad_backlog_is_bounded(self):
        failures = []
        stream = Stream()
        speech = SpeechInput(SimpleNamespace(stream=lambda: stream), start=lambda _: None,
            audio=lambda _: None, end=lambda: None, failed=failures.append)
        try:
            for _ in range(151):
                speech.push(rtc.AudioFrame(bytes(640), 16000, 1, 320))
            self.assertEqual(failures, ["vad_backlog"])
        finally:
            await speech.aclose()
