import asyncio
from types import SimpleNamespace
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from livekit_vlm_agent.providers.qwen_client import OutputGate
from livekit_vlm_agent.config import Settings
from livekit_vlm_agent.providers.qwen_realtime import QwenRealtimeModel


class FakeClient:
    def __init__(self, settings, **kwargs):
        self.gate = OutputGate("test-connection")
        self.responses = {}
        self.sent = []
        self.cursor = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def configure(self, **kwargs):
        pass

    async def send(self, data):
        self.sent.append(data)

    async def aclose(self):
        pass


class AdapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.patch = patch("livekit_vlm_agent.providers.qwen_realtime.QwenClient", FakeClient)
        self.patch.start()
        self.model = QwenRealtimeModel(Settings(_env_file=None, output_buffer_max_ms=1000))
        self.session = self.model.session()
        self.generations = []
        self.errors = []
        self.session.on("generation_created", self.generations.append)
        self.session.on("error", self.errors.append)
        await self.session.wait_ready()

    async def asyncTearDown(self):
        await self.model.aclose()
        self.patch.stop()

    def begin(self):
        self.session._client.gate.begin("response")
        self.session._client.responses["response"] = SimpleNamespace(done_at=None, text="测试回答")
        self.session._on_event({"type": "response.created", "response": {"id": "response"}}, None)
        self.session._on_event({"type": "response.output_item.added", "response_id": "response",
            "item": {"type": "message", "id": "message"}}, None)

    async def test_streams_deliver_24khz_frames_text_and_terminal_close(self):
        self.begin()
        message = await anext(self.generations[0].message_stream)
        self.session._on_event({"type": "response.audio_transcript.delta", "response_id": "response", "delta": "测试"}, None)
        self.session._on_event({"type": "response.audio.delta", "response_id": "response"}, bytes(1920))
        text = await anext(message.text_stream)
        frame = await anext(message.audio_stream)
        self.assertEqual(text, "测试")
        self.assertEqual((frame.sample_rate, frame.num_channels, frame.samples_per_channel), (24000, 1, 480))
        self.session._on_event({"type": "response.done", "response": {"id": "response"}}, None)
        await anext(message.audio_stream)
        with self.assertRaises(StopAsyncIteration):
            await anext(message.audio_stream)
        self.assertEqual(self.session.chat_ctx.items[-1].id, "message")

    async def test_provider_speech_start_invalidates_output_without_duplicate_remote_cancel(self):
        self.begin()
        self.session.on("input_speech_started", lambda event: self.session.interrupt())
        self.session._on_event({"type": "input_audio_buffer.speech_started"}, None)
        await asyncio.sleep(0)
        self.assertFalse(self.session._client.gate.accepts("test-connection", "response"))
        self.assertFalse(any(event["type"] == "response.cancel" for event in self.session._client.sent))

    async def test_output_backlog_fails_before_unbounded_audio_allocation(self):
        self.begin()
        self.session._on_event({"type": "response.audio.delta", "response_id": "response"}, bytes(48002))
        self.assertEqual(len(self.errors), 1)
        self.assertEqual(self.session._generations["response"].queued_bytes, 0)

    async def test_network_input_queue_is_bounded_and_closure_removes_model_session(self):
        for _ in range(105):
            self.session._enqueue("audio", bytes(640))
        self.assertLessEqual(self.session._input.qsize(), 100)
        self.assertEqual(len(self.errors), 1)
        await self.session.aclose()
        self.assertFalse(self.model.sessions)


if __name__ == "__main__":
    unittest.main()
