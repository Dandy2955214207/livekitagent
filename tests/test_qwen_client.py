"""Replay protocol races and errors without network or model charges."""

import asyncio
import base64
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock
from pydantic import SecretStr

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from livekit_vlm_agent.config import Settings
from livekit_vlm_agent.providers.qwen_client import QwenClient, QwenProtocolError


class QueueSocket:
    def __init__(self):
        self.events = asyncio.Queue()

    async def __aiter__(self):
        while True:
            item = await self.events.get()
            if item is None:
                return
            yield json.dumps(item)


class ClientReplayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = QwenClient(Settings(_env_file=None, qwen_api_key=SecretStr("fixed-test-secret")), capture_audio=True)
        self.socket = QueueSocket()
        self.client.ws = self.socket
        self.task = asyncio.create_task(self.client._receive())

    async def asyncTearDown(self):
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)

    async def feed(self, event):
        cursor = self.client.cursor
        await self.socket.events.put(event)
        return await self.client.wait_type(event["type"], after=cursor, timeout=1)

    async def test_control_config_is_opt_in_and_legacy_defaults_are_preserved(self):
        self.client.send = AsyncMock()
        self.client.wait_type = AsyncMock()
        await self.client.configure()
        legacy = self.client.send.call_args.args[0]["session"]
        self.assertEqual(legacy["modalities"], ["text", "audio"])
        self.assertEqual(legacy["max_tokens"], 256)
        self.assertNotIn("tools", legacy)
        await self.client.configure(modalities=["text"], tools=[], max_tokens=2048)
        control = self.client.send.call_args.args[0]["session"]
        self.assertEqual(control["modalities"], ["text"])
        self.assertEqual(control["tools"], [])
        self.assertEqual(control["max_tokens"], 2048)
        with self.assertRaises(ValueError):
            await self.client.configure(max_tokens=0)

    async def test_control_trace_keeps_correlation_but_omits_arguments_and_tool_output(self):
        summary = self.client._summary("recv", {"type": "response.output_item.done",
            "response_id": "r1", "item": {"id": "i1", "type": "function_call",
            "call_id": "c1", "arguments": "private data"}})
        self.assertEqual(summary["call_id"], "c1")
        self.assertNotIn("private data", json.dumps(summary))
        self.client._summary("recv", {"type": "session.updated", "session": {"tools": None}})

    async def test_thin_scheduler_options_are_opt_in_and_do_not_change_legacy_defaults(self):
        self.client.send = AsyncMock()
        self.client.wait_type = AsyncMock()
        await self.client.configure(vad=True)
        default = self.client.send.call_args.args[0]['session']['turn_detection']
        self.assertNotIn('create_response', default)
        self.assertNotIn('interrupt_response', default)
        await self.client.configure(vad='semantic_vad', create_response=False, interrupt_response=True)
        thin = self.client.send.call_args.args[0]['session']['turn_detection']
        self.assertEqual(thin, {'type': 'semantic_vad', 'create_response': False, 'interrupt_response': True})
        with self.assertRaises(ValueError):
            await self.client.configure(vad=False, create_response=False)

    async def test_instruction_update_has_explicit_ack_cursor_and_no_fixed_fields(self):
        self.client.send = AsyncMock()
        self.client.wait_type = AsyncMock()
        cursor = self.client.cursor
        result = await self.client.configure(instructions="next phase", max_tokens=512,
            tools=[], instructions_only=True, wait_for_ack=False)
        self.assertEqual(result, cursor)
        self.assertEqual(self.client.send.call_args.args[0]["session"],
                         {"instructions": "next phase", "max_tokens": 512})
        self.client.wait_type.assert_not_awaited()

    async def test_cancelled_audio_is_counted_but_never_appended_to_next_output(self):
        await self.feed({"type": "response.created", "response": {"id": "old"}})
        audio = base64.b64encode(bytes([1, 0]) * 320).decode("ascii")
        event = {"type": "response.audio.delta", "response_id": "old", "delta": audio}
        await self.feed(event)
        self.client.gate.cancel("old")
        await self.feed(event)
        await self.feed({"type": "response.done", "response": {"id": "old", "status": "cancelled"}})
        await self.feed({"type": "response.created", "response": {"id": "new"}})
        await self.feed(event)  # old connection's in-flight audio after a new response starts
        await self.feed({**event, "response_id": "new"})
        await self.feed({"type": "response.done", "response": {"id": "new", "status": "completed"}})
        old, new = self.client.responses["old"], self.client.responses["new"]
        self.assertEqual((old.audio_bytes, old.dropped_audio_bytes), (640, 1280))
        self.assertEqual(bytes(new.audio), bytes([1, 0]) * 320)
        self.assertEqual((new.status, new.audio_bytes), ("completed", 640))
        self.assertNotIn(audio, json.dumps(self.client.trace))
        self.assertTrue(all("delta" not in e.data for e in self.client.events if e.data["type"] == "response.audio.delta"))

    async def test_remote_error_fails_wait_and_redacts_key(self):
        secret = self.client.settings.qwen_api_key.get_secret_value()
        await self.socket.events.put({"type": "error", "error": {"code": "invalid_value", "message": "Rejected " + secret}})
        with self.assertRaisesRegex(QwenProtocolError, "invalid_value") as caught:
            await self.client.wait_type("response.created", timeout=1)
        self.assertNotIn(secret, str(caught.exception))
        self.assertIn("[REDACTED]", str(caught.exception))

    async def test_yibuapi_remote_error_redacts_selected_provider_key(self):
        self.client.settings.model_provider = "yibuapi"
        self.client.settings.yibuapi_api_key = SecretStr("yibu-test-secret")
        await self.socket.events.put({"type": "error", "error": {
            "code": "invalid_value", "message": "Rejected yibu-test-secret",
        }})
        with self.assertRaises(QwenProtocolError) as caught:
            await self.client.wait_type("response.created", timeout=1)
        self.assertNotIn("yibu-test-secret", str(caught.exception))
        self.assertIn("[REDACTED]", str(caught.exception))

    async def test_closed_connection_wakes_a_waiter_without_hanging(self):
        await self.socket.events.put(None)
        with self.assertRaisesRegex(QwenProtocolError, "Connection closed"):
            await self.client.wait_type("response.created", timeout=1)

    async def test_streaming_history_stays_bounded_while_cursor_keeps_advancing(self):
        self.client.streaming = True
        for _ in range(300):
            await self.feed({"type": "session.updated", "session": {}})
        self.assertEqual(self.client.cursor, 300)
        self.assertEqual(len(self.client.events), 256)
        self.assertFalse(self.client.trace)
        event = await self.feed({"type": "session.updated", "session": {}})
        self.assertEqual(event.seq, 301)


if __name__ == "__main__":
    unittest.main()
