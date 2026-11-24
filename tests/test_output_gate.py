"""Meaningful late-event regressions, independent of network or paid calls."""

from pathlib import Path
import json
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from livekit_vlm_agent.providers.qwen_client import OutputGate


class OutputGateTests(unittest.TestCase):
    def test_real_cancel_trace_replays_expected_audio_boundaries(self):
        path = Path(__file__).parent / "fixtures/qwen-cancel-trace.json"
        fixture = json.loads(path.read_text(encoding="utf-8"))
        gate = OutputGate("replay")
        totals = {}
        for event in fixture["events"]:
            kind = event["type"]
            if event["direction"] == "recv" and kind == "response.created":
                gate.begin(event["response_id"])
            elif event["direction"] == "send" and kind == "response.cancel":
                gate.cancel(gate.active_response)
            elif kind == "response.audio.delta" and gate.accepts("replay", event["response_id"]):
                rid = event["response_id"]
                totals[rid] = totals.get(rid, 0) + event["byte_count"]
        self.assertEqual(totals, fixture["expected_audio_bytes"])

    def test_delayed_cancelled_audio_cannot_enter_following_response(self):
        gate = OutputGate("connection-1")
        gate.begin("old")
        self.assertTrue(gate.accepts("connection-1", "old"))
        gate.cancel("old")
        self.assertFalse(gate.accepts("connection-1", "old"))
        gate.begin("new")
        self.assertFalse(gate.accepts("connection-1", "old"))
        self.assertTrue(gate.accepts("connection-1", "new"))

    def test_reconnected_session_rejects_old_connection_even_with_same_response_id(self):
        gate = OutputGate("new-connection")
        gate.begin("response")
        self.assertFalse(gate.accepts("old-connection", "response"))
        self.assertTrue(gate.accepts("new-connection", "response"))

    def test_cancelled_response_cannot_be_revived_by_a_late_created_event(self):
        gate = OutputGate("connection")
        gate.begin("old")
        gate.cancel("old")
        gate.begin("new")
        gate.begin("old")
        self.assertFalse(gate.accepts("connection", "old"))
        self.assertTrue(gate.accepts("connection", "new"))


if __name__ == "__main__":
    unittest.main()
