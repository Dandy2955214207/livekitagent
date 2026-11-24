"""Ensure configuration-only success cannot conceal the observed P3 failure."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("harness_probe", ROOT / "scripts/probe_harness.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class ProbeTests(unittest.IsolatedAsyncioTestCase):
    async def test_text_only_continuation_fails_speak_gate_and_retains_delay_evidence(self):
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.trace = []
        client.responses = {}
        report = {"checks": {}, "responses": [], "status": "failed"}
        with (patch.object(probe, "Settings", return_value=SimpleNamespace(qwen_missing=lambda: [])),
              patch.object(probe, "QwenClient", return_value=client),
              patch.object(probe, "configure", AsyncMock()),
              patch.object(probe, "pcm_fixture", return_value=bytes(640)),
              patch.object(probe, "commit", AsyncMock()),
              patch.object(probe, "decide", AsyncMock(return_value=(SimpleNamespace(audio_bytes=0), None))),
              patch.object(probe, "expect_quiet", AsyncMock()),
              patch.object(probe, "generate", AsyncMock(return_value=(
                  SimpleNamespace(id="r2", status="completed", audio_bytes=0, text="成功"), ())))):
            with self.assertRaises(probe.QwenProtocolError):
                await probe.run_probe("control", report)
        self.assertEqual(report["checks"]["P3"], "failed_speak_missing_audio_or_unexpected_tool")
        self.assertEqual(report["checks"]["P5"], "passed_one_second_delay")
        self.assertEqual(report["status"], "failed")

    async def test_default_checklist_never_opens_a_connection(self):
        with (patch("sys.argv", ["probe_harness.py"]), patch("builtins.print"),
              patch.object(probe, "QwenClient") as client):
            self.assertEqual(probe.main(), 0)
        client.assert_not_called()
