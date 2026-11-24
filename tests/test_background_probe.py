"""A successful primitive must not silently enable the full proactive gate."""

from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import probe_live_background as probe


class FakeProbeClient:
    instances = []
    reject_ack = False

    def __init__(self, settings, **kwargs):
        self.connection_id = "offline"
        self.trace, self.events, self.responses = [], [], {}
        self.cursor = 0
        self.ws = None
        self.sent = []
        self.closed = False
        self.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.aclose()

    async def aclose(self):
        self.closed = True

    async def configure(self, **kwargs):
        self.configuration = kwargs

    async def send(self, data):
        self.sent.append(data)
        self.cursor += 1

    async def wait_type(self, kind, **kwargs):
        if kind == "session.updated" and self.reject_ack:
            raise TimeoutError("no ack")
        if kind == "response.created":
            if self.sent[-1]["type"] != "response.create":
                raise TimeoutError("quiet")
            return SimpleNamespace(data={"response": {"id": "fixed"}}, seq=self.cursor)
        return SimpleNamespace(data={}, seq=self.cursor)

    async def wait_done(self, *args, **kwargs):
        return SimpleNamespace(id="fixed", status="completed", text="红色圆形已出现",
                               audio_bytes=960, usage={}, created_at=1, first_audio_at=2)


class ProbeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        FakeProbeClient.instances = []
        FakeProbeClient.reject_ack = False
        self.report = {"checks": {key: {"status": "unknown"} for key in probe.CHECKS},
                       "responses": []}

    def test_empty_transcription_completion_is_distinct_from_real_speech(self):
        events = [SimpleNamespace(seq=1, data={"type": "input_audio_buffer.speech_started"}),
                  SimpleNamespace(seq=2, data={"type": "conversation.item.input_audio_transcription.completed",
                                               "transcript": "  "})]
        counts = probe.monitor_input_activity(events, 1)
        self.assertEqual(counts, {"speech_events": 0, "transcription_completed_count": 1,
                                  "nonempty_transcriptions": 0, "unknown_transcriptions": 0})
        events.append(SimpleNamespace(seq=3, data={"type": "conversation.item.input_audio_transcription.completed",
                                                   "transcript": "real transcript"}))
        events.append(SimpleNamespace(seq=4, data={"type": "input_audio_buffer.speech_started"}))
        counts = probe.monitor_input_activity(events, 1)
        self.assertEqual(counts["nonempty_transcriptions"], 1)
        self.assertEqual(counts["speech_events"], 1)

    def test_missing_transcription_content_is_unknown_and_error_codes_are_safe(self):
        events = [SimpleNamespace(seq=1, data={"type": "conversation.item.input_audio_transcription.completed"})]
        self.assertEqual(probe.monitor_input_activity(events, 0)["unknown_transcriptions"], 1)
        error = ExceptionGroup("private payload", [probe.ProbeCheckError("fixed_code"), ValueError("private")])
        self.assertEqual(probe.error_codes(error), ["fixed_code"])

    async def test_event_ack_precedes_generation_and_primitive_keeps_gate_unknown(self):
        with patch.object(probe, "QwenClient", FakeProbeClient):
            await probe.run(self.report, "event")
        client = FakeProbeClient.instances[0]
        self.assertEqual([e["type"] for e in client.sent], ["session.update", "response.create"])
        self.assertEqual(set(client.sent[0]["session"]), {"instructions"})
        self.assertTrue(client.configuration["vad"])
        self.assertEqual(client.configuration["tools"], [])
        self.assertTrue(client.closed)
        self.assertEqual(self.report["checks"]["P0-D"]["status"], "unknown")

    async def test_missing_ack_closes_client_without_requesting_generation(self):
        FakeProbeClient.reject_ack = True
        with patch.object(probe, "QwenClient", FakeProbeClient), self.assertRaises(TimeoutError):
            await probe.run(self.report, "event")
        client = FakeProbeClient.instances[0]
        self.assertFalse(any(e["type"] == "response.create" for e in client.sent))
        self.assertTrue(client.closed)
        self.assertEqual(self.report["checks"]["P0-D"]["status"], "unknown")
