"""H2 executor, output isolation, media bounds and cancellation without network."""

import asyncio
import json
from pathlib import Path
import sys
import time
from unittest.mock import patch
import unittest

from livekit import rtc

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from livekit_vlm_agent.config import Settings
from livekit_vlm_agent.harness import Event
from livekit_vlm_agent.providers.qwen_client import OutputGate, Response
from livekit_vlm_agent.providers.qwen_harness import QwenHarnessModel


class FakeClient:
    def __init__(self, settings, *, on_event=None, **kwargs):
        self.on_event = on_event
        self.gate = OutputGate("test")
        self.responses = {}
        self.sent = []
        self.configs = []
        self.inputs = []
        self.cursor = 0
        self.configure_gate = None
        self.ack_gates = {}
        self.tool_ack_gate = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def configure(self, **kwargs):
        self.configs.append(kwargs)
        if self.configure_gate:
            await self.configure_gate.wait()

    async def send(self, event):
        self.sent.append(event)
        if event["type"] == "response.create":
            rid = f"r{len(self.responses) + 1}"
            self.responses[rid] = Response(rid, time.monotonic())
            self.gate.begin(rid)
            self.on_event({"type": "response.created", "response": {"id": rid}}, None)
        elif event["type"] == "response.cancel":
            rid = next(reversed(self.responses))
            self.finish(rid, "cancelled")

    async def wait_type(self, *args, **kwargs):
        if args and args[0] in self.ack_gates:
            await self.ack_gates[args[0]].wait()

    async def wait(self, *args, **kwargs):
        if self.tool_ack_gate:
            await self.tool_ack_gate.wait()

    async def stream_pcm(self, pcm, **kwargs):
        self.inputs.append((pcm, kwargs))

    async def aclose(self):
        pass

    def finish(self, rid, status="completed"):
        self.responses[rid].status = status
        self.responses[rid].done_at = time.monotonic()
        self.on_event({"type": "response.done", "response": {"id": rid, "status": status}}, None)


class AdapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.patch = patch("livekit_vlm_agent.providers.qwen_realtime.QwenClient", FakeClient)
        self.patch.start()
        self.model = QwenHarnessModel(Settings(_env_file=None, harness_auto_input=False))
        self.session = self.model.session()
        self.generations = []
        self.session.on("generation_created", self.generations.append)
        await self.session.wait_ready()
        self.client = self.session._client

    async def asyncTearDown(self):
        await self.model.aclose()
        self.patch.stop()

    async def settle(self):
        for _ in range(10):
            await asyncio.sleep(0)

    async def submit(self, key="u1"):
        self.session.control({"id": key, "action": "record_start"})
        await self.settle()
        self.session.push_audio(rtc.AudioFrame(bytes(9600), 16000, 1, 4800))
        self.session.control({"id": key + "-submit", "action": "record_submit", "recording_id": key})
        await asyncio.sleep(0.22)
        await self.settle()

    def decision(self, data, *, call_id="c1"):
        rid = self.session._active.response_id
        tool = ("confirm_presentation" if self.session._active.purpose == "confirm" else
                "assess_presentation" if self.session._active.purpose == "observe" else "submit_task_decision")
        self.client.on_event({"type": "response.output_item.done", "response_id": rid,
            "item": {"type": "function_call", "call_id": call_id, "name": tool,
                     "arguments": json.dumps(data)}}, None)
        self.client.finish(rid)

    async def test_raw_audio_decide_is_silent_then_speech_has_no_executable_actions(self):
        await self.submit()
        pending = self.session.controller.state.pending
        self.assertEqual(pending.context.audio_ref, "u1")
        self.assertIsNone(pending.context.input_text)
        rid = self.session._active.response_id
        self.client.on_event({"type": "response.text.delta", "response_id": rid, "delta": "internal JSON"}, None)
        self.client.on_event({"type": "response.audio.delta", "response_id": rid}, bytes(960))
        self.assertFalse(self.generations)
        self.decision({"action": "answer", "speech_intent": "这是红色"})
        await self.settle()
        self.assertEqual(len(self.generations), 1)
        self.assertEqual(self.session.controller.state.pending.context.allowed_actions, ())
        types = [event["type"] for event in self.client.sent]
        self.assertLess(types.index("conversation.item.create"), len(types) - 1)
        self.assertEqual(len(self.client.inputs), 1)
        active = self.session._active
        self.client.on_event({"type": "response.output_item.added", "response_id": active.response_id,
                             "item": {"type": "message", "id": "m1"}}, None)
        self.client.on_event({"type": "response.audio.delta", "response_id": active.response_id}, bytes(960))
        self.client.responses[active.response_id].audio_bytes = 960
        self.client.finish(active.response_id)
        await self.settle()
        self.assertIsNotNone(self.session.controller.state.segment)
        self.session.playback_done(active.request_id)
        await self.settle()
        self.assertIsNone(self.session.controller.state.segment)

    async def test_capture_is_gated_and_duplicate_controls_are_idempotent(self):
        frame = rtc.AudioFrame(bytes(640), 16000, 1, 320)
        self.session.push_audio(frame)
        self.assertFalse(self.session._pcm)
        payload = {"id": "u1", "action": "record_start"}
        self.session.control(payload)
        self.session.push_audio(frame)
        self.session.control(payload)
        self.assertEqual(len(self.session._pcm), 640)
        self.assertFalse(self.client.inputs)
        self.session.control({"id": "stop", "action": "cancel"})
        self.assertFalse(self.session._pcm)
        self.assertIsNone(self.session._recording_id)

    async def test_automatic_input_streams_before_end_and_never_uploads_twice(self):
        self.session.settings.harness_auto_input = True
        self.session._start_live(bytes(6400))
        await self.settle()
        self.assertEqual(sum(len(pcm) for pcm, _ in self.client.inputs), 6400)
        self.assertFalse(self.client.responses)
        self.session._append_live(bytes(3200))
        await self.settle()
        self.session._end_live()
        await self.settle()
        self.assertEqual(sum(len(pcm) for pcm, _ in self.client.inputs), 9600)
        self.assertEqual(sum(e["type"] == "input_audio_buffer.commit" for e in self.client.sent), 1)
        self.assertEqual(self.session._active.purpose, "user_turn")
        self.assertFalse(self.session._submitting)
        self.decision({"action": "answer", "speech_intent": "测试回答"})
        await self.settle()
        self.assertEqual(self.session._active.purpose, "speak")

    async def test_automatic_speech_interrupts_old_generation_before_new_input(self):
        await self.submit()
        self.decision({"action": "answer", "speech_intent": "旧回答"})
        await self.settle()
        old = self.session._active
        before = len(self.generations)
        self.session.settings.harness_auto_input = True
        self.session._start_live(bytes(9600))
        await self.settle()
        self.assertIn({"type": "response.cancel"}, self.client.sent)
        self.client.on_event({"type": "response.audio.delta", "response_id": old.response_id}, bytes(960))
        self.assertEqual(len(self.generations), before)
        self.session._end_live()
        await self.settle()
        self.assertEqual(self.session._active.purpose, "user_turn")

    async def test_camera_changed_during_live_commit_does_not_bind_old_frame(self):
        await self.observe()
        self.session.settings.harness_auto_input = True
        gate = self.client.ack_gates["input_audio_buffer.committed"] = asyncio.Event()
        self.session._start_live(bytes(9600))
        self.session._end_live()
        await self.settle()
        self.session.clear_video()
        await self.settle()
        gate.set()
        await self.settle()
        self.assertFalse(self.session.controller.state.pending.context.evidence)

    async def test_automatic_input_pause_discards_and_next_turn_clears_old_buffer(self):
        self.session.settings.harness_auto_input = True
        self.session._start_live(bytes(6400))
        await self.settle()
        self.session.control({"id": "pause-live", "action": "pause"})
        self.session._end_live()
        await self.settle()
        self.assertFalse(self.client.responses)
        self.session._start_live(bytes(9600))
        self.session._end_live()
        await self.settle()
        self.assertIn({"type": "input_audio_buffer.clear"}, self.client.sent)
        self.assertEqual(len(self.client.responses), 1)

    async def test_new_speech_supersedes_turn_waiting_for_commit_ack(self):
        self.session.settings.harness_auto_input = True
        gate = self.client.ack_gates["input_audio_buffer.committed"] = asyncio.Event()
        self.session._start_live(bytes(6400))
        self.session._end_live()
        await self.settle()
        first = self.session._live_input.id
        self.session._start_live(bytes(9600))
        second = self.session._live_input.id
        self.session._end_live()
        gate.set()
        await self.settle()
        self.assertNotEqual(first, second)
        self.assertEqual(len(self.client.responses), 1)
        self.assertEqual(self.session.controller.state.pending.context.audio_ref, second)

    async def test_automatic_input_ignores_legacy_record_controls_and_bounds_backlog(self):
        self.session.settings.harness_auto_input = True
        self.session.control({"id": "obsolete", "action": "record_start"})
        self.assertIsNone(self.session._recording_id)
        self.session._start_live(bytes(6400))
        self.session._append_live(bytes(160000))
        await self.settle()
        self.assertIsNone(self.session._live_input)
        self.assertFalse(self.client.responses)

    async def test_cancel_ack_precedes_next_generation_and_old_audio_is_dropped(self):
        await self.submit()
        old = self.session._active
        self.session.control({"id": "u2", "action": "record_start"})
        await self.settle()
        self.assertIn({"type": "response.cancel"}, self.client.sent)
        self.assertIsNone(self.session._active)
        self.client.on_event({"type": "response.audio.delta", "response_id": old.response_id}, bytes(960))
        self.assertFalse(self.generations)
        self.session.push_audio(rtc.AudioFrame(bytes(9600), 16000, 1, 4800))
        self.session.control({"id": "u2-submit", "action": "record_submit", "recording_id": "u2"})
        await asyncio.sleep(0.22)
        await self.settle()
        self.assertEqual(len(self.client.responses), 2)

    async def test_pause_during_configuration_prevents_any_response_create(self):
        self.client.configure_gate = asyncio.Event()
        await self.submit()
        self.session.control({"id": "p1", "action": "pause"})
        await self.settle()
        self.client.configure_gate.set()
        await self.settle()
        self.assertFalse(self.client.responses)
        self.assertFalse(self.client.inputs)

    async def test_missing_tools_get_one_correction_without_speech(self):
        await self.submit()
        for _ in range(2):
            rid = self.session._active.response_id
            self.client.finish(rid)
            await self.settle()
        self.assertFalse(self.generations)
        self.assertIsNone(self.session.controller.state.pending)
        self.assertEqual(len(self.client.responses), 2)

    async def test_frame_is_bound_before_user_context_and_sent_only_with_submit(self):
        from livekit_vlm_agent.media.video import EncodedFrame
        import base64
        self.session.video.enabled = True
        self.session._post(Event("camera_changed", camera_enabled=True))
        frame = EncodedFrame(base64.b64encode(b"jpeg").decode(), time.monotonic(), time.monotonic(),
                             self.session.video.epoch, 32, 32, 4, 0)
        self.session.video.pending = frame
        self.session._latest_sample = frame
        await self.submit()
        self.assertEqual(len(self.session.controller.state.pending.context.evidence), 1)
        self.assertEqual(self.client.inputs[0][1]["image"], b"jpeg")

    async def test_task_plan_compiles_and_raw_audio_confirmation_advances(self):
        from livekit_vlm_agent.harness.policy import compile_instructions
        await self.submit()
        self.decision({"action": "start_task", "mode": "guidance", "goal": "搭积木",
            "plan": [{"id": "s1", "instruction": "放一个", "completion_condition": "一个放好"},
                     {"id": "s2", "instruction": "再放一个", "completion_condition": "两个放好"}],
            "current_step_id": "s1", "speech_intent": "请放一个积木"})
        await self.settle()
        spoken = compile_instructions(self.session.controller.state.pending.context)
        self.assertIn("请放一个积木", spoken)
        self.assertIn('"plan"', spoken)
        active = self.session._active
        self.client.responses[active.response_id].audio_bytes = 960
        self.client.finish(active.response_id)
        self.session.playback_done(active.request_id)
        await self.settle()
        await self.submit("u2")
        self.decision({"action": "advance", "current_step_id": "s1", "speech_intent": "再放一个",
            "assessment": {"result": "complete", "basis": "user_reported", "evidence_ids": ["u2"], "reason": "用户确认"}}, call_id="c2")
        await self.settle()
        self.assertEqual(self.session.controller.state.task.step.id, "s2")

    async def test_recording_limit_discards_the_entire_input(self):
        self.session.control({"id": "long", "action": "record_start"})
        self.session.push_audio(rtc.AudioFrame(bytes(30 * 32000 + 2), 16000, 1, 30 * 16000 + 1))
        self.assertIsNone(self.session._recording_id)
        self.assertFalse(self.session._pcm)
        self.assertFalse(self.client.inputs)

    async def test_speak_tool_call_cannot_mutate_a_task(self):
        await self.submit()
        self.decision({"action": "answer", "speech_intent": "你好"})
        await self.settle()
        self.decision({"action": "start_task", "mode": "guidance"}, call_id="unexpected")
        await self.settle()
        self.assertIsNone(self.session.controller.state.task)
        self.assertIsNone(self.session.controller.state.pending)

    async def test_provider_schema_is_inline_and_has_an_array_plan(self):
        from livekit_vlm_agent.harness.policy import TOOL, TOOLS, PRESENTATION_TOOL
        schema = TOOL["function"]["parameters"]
        self.assertEqual(schema["properties"]["plan"]["type"], "array")
        self.assertEqual(schema["properties"]["plan"]["items"]["type"], "object")
        self.assertNotIn('$ref', json.dumps(schema))
        self.assertNotIn('anyOf', json.dumps(schema))
        self.assertEqual({item["function"]["name"] for item in TOOLS},
                         {"submit_task_decision", "assess_presentation", "confirm_presentation"})
        self.assertNotIn("plan", PRESENTATION_TOOL["function"]["parameters"]["properties"])
        self.assertTrue(all(config.get("tools") == TOOLS for config in self.client.configs))

    async def test_showcase_does_not_enable_visual_guidance_or_finite_narration(self):
        self.assertFalse(self.session.controller.checks_enabled)
        self.assertEqual(self.session.controller.completion_bases, ("user_reported",))
        self.assertEqual(self.session.controller.allowed_modes, ("guidance", "showcase"))
        self.assertTrue(self.session.controller.showcase_enabled)

    async def test_schema_feedback_never_contains_user_input_values(self):
        await self.submit()
        self.decision({"action": "start_task", "plan": "private-user-content"})
        await self.settle()
        result = next(event for event in self.client.sent if event["type"] == "conversation.item.create")
        self.assertNotIn("private-user-content", result["item"]["output"])
        self.assertIn("expected_json_array", result["item"]["output"])

    async def test_user_start_racing_a_complete_tool_closes_it_as_rejected(self):
        await self.submit()
        self.session.control({"id": "interrupt", "action": "record_start"})
        self.decision({"action": "answer", "speech_intent": "不应播放"})
        await self.settle()
        results = [event for event in self.client.sent if event["type"] == "conversation.item.create"]
        self.assertEqual(len(results), 1)
        self.assertFalse(json.loads(results[0]["item"]["output"])["accepted"])
        self.assertFalse(self.generations)
        self.assertFalse(self.session._unacknowledged_calls)

    async def start_showcase(self):
        await self.submit()
        self.decision({"action": "start_task", "mode": "showcase", "goal": "逐个介绍展示物品",
            "plan": [{"id": "showcase", "instruction": "等待展示", "completion_condition": "用户结束"}],
            "current_step_id": "showcase", "speech_intent": "好的，请展示物品。"})
        await self.settle()
        active = self.session._active
        self.client.responses[active.response_id].audio_bytes = 960
        self.client.finish(active.response_id)
        self.session.playback_done(active.request_id)
        await self.settle()

    async def observe(self, *, scene=1, match=False):
        from livekit_vlm_agent.media.video import EncodedFrame
        import base64
        import uuid
        from livekit_vlm_agent.harness.types import Evidence
        import numpy as np
        if match:
            await asyncio.sleep(.02)  # Two sampled frames have distinct receive times.
        self.session.video.enabled = True
        self.session.controller.state.camera_enabled = True
        frame = EncodedFrame(base64.b64encode(b"jpeg").decode(), time.monotonic(), time.monotonic(),
                             self.session.video.epoch, 32, 32, 4, 0)
        frame.signature = (np.zeros((24, 32, 3)), np.zeros(3))
        self.session.video.pending = frame
        self.session._latest_sample = frame
        self.session._presentation_filter.scene = scene
        self.session._presentation_filter.metrics = {"stable_seconds": 2}
        self.session._post(Event("view", evidence=Evidence(uuid.uuid4().hex,
            self.session.controller.state.camera_epoch, frame.received_at, "memory://frame"),
            scene_id=scene, stable_seconds=2,
            candidate_match_id=self.session.controller.state.candidate_id if match else None,
            candidate_checked_id=self.session.controller.state.candidate_id))
        self.session.controller.state.due_at = 0
        self.session._post(Event("tick"))
        await self.settle()

    async def test_observation_uploads_new_frame_with_labeled_silence_and_no_sdk_output(self):
        await self.start_showcase()
        count = len(self.generations)
        await self.observe()
        self.assertEqual(self.session._active.purpose, "observe")
        self.assertEqual(self.client.inputs[-1][0], bytes(16000))
        self.assertEqual(self.client.inputs[-1][1]["image"], b"jpeg")
        self.assertIn('"audio_origin": "transport_silence"', self.client.configs[-1]["instructions"])
        self.decision({"action": "observe", "presentation": "presented", "object_key": "cup",
            "object_summary": "白杯", "covered": "用途", "speech_intent": "这是杯子。"}, call_id="first")
        await self.settle()
        self.assertEqual(len(self.generations), count)
        await self.observe()
        self.assertEqual(self.session._active.purpose, "confirm")
        instructions = self.client.configs[-1]["instructions"]
        self.assertNotIn("这是杯子", instructions)
        self.assertNotIn('"covered"', instructions)
        self.assertEqual(self.client.sent[-1]["response"]["modalities"], ["text"])
        self.decision({"candidate_id": self.session.controller.state.candidate_id,
                       "same_subject": True, "still_presented": True}, call_id="second")
        await self.settle()
        self.assertEqual(len(self.generations), count + 1,
                         (self.session.controller.state, self.client.sent[-5:]))
        self.assertEqual(self.session._active.purpose, "speak")

    async def test_pipeline_overlaps_config_and_input_but_requires_commit_before_response(self):
        await self.start_showcase()
        gate = self.client.ack_gates["input_audio_buffer.committed"] = asyncio.Event()
        await self.observe()
        active = self.session._active
        self.assertIsNone(active.response_id)
        self.assertEqual(self.client.sent[-1]["type"], "input_audio_buffer.commit")
        self.assertEqual(self.client.configs[-1]["wait_for_ack"], False)
        gate.set()
        await self.settle()
        self.assertIsNotNone(active.response_id)

    async def test_pipeline_upload_does_not_wait_for_configuration_round_trip(self):
        await self.start_showcase()
        gate = self.client.ack_gates["session.updated"] = asyncio.Event()
        before = len(self.client.responses)
        await self.observe()
        self.assertEqual(self.client.sent[-1]["type"], "input_audio_buffer.commit")
        self.assertEqual(len(self.client.responses), before)
        self.assertEqual(self.client.inputs[-1][0], bytes(16000))
        gate.set()
        await self.settle()
        self.assertEqual(len(self.client.responses), before + 1)

    async def test_pipeline_overlaps_tool_and_config_but_pause_prevents_generation(self):
        await self.start_showcase()
        await self.observe()
        self.decision({"action": "observe", "presentation": "presented", "object_key": "cup",
            "object_summary": "白杯", "needs_confirmation": False}, call_id="candidate")
        await self.settle()
        gate = self.client.tool_ack_gate = asyncio.Event()
        before = len(self.generations)
        await self.observe(match=True)
        self.assertEqual(self.session._active.purpose, "speak")
        self.assertEqual(self.client.sent[-1]["type"], "conversation.item.create")
        self.assertFalse(self.client.configs[-1]["wait_for_ack"])
        self.assertEqual(len(self.generations), before)
        self.session.control({"id": "pause-barrier", "action": "pause"})
        await self.settle()
        gate.set()
        await self.settle()
        self.assertEqual(len(self.generations), before)
        self.assertIsNone(self.session._active)
        self.assertFalse(self.session._unacknowledged_calls)

    async def test_pipeline_ack_failure_never_starts_response(self):
        await self.start_showcase()
        from unittest.mock import AsyncMock
        self.client.wait_type = AsyncMock(side_effect=TimeoutError("missing acknowledgement"))
        before = len(self.generations)
        await self.observe()
        self.assertTrue(self.session._failed)
        self.assertEqual(len(self.generations), before)

    async def test_pipeline_can_be_disabled_without_changing_manual_flow(self):
        self.session.settings.showcase_pipeline = False
        await self.start_showcase()
        gate = self.client.ack_gates["input_audio_buffer.committed"] = asyncio.Event()
        before = len(self.client.responses)
        await self.observe()
        self.assertEqual(len(self.client.responses), before)
        gate.set()
        await self.settle()
        self.assertEqual(len(self.client.responses), before + 1)

    async def test_motion_during_observation_configuration_cancels_before_upload(self):
        await self.start_showcase()
        self.client.configure_gate = asyncio.Event()
        before = len(self.client.inputs)
        await self.observe()
        self.session._post(Event("view", evidence=self.session.controller.state.latest_frame,
                                 scene_id=2, stable_seconds=0))
        await self.settle()
        self.client.configure_gate.set()
        await self.settle()
        self.assertEqual(len(self.client.inputs), before)
        self.assertIsNone(self.session._active)

    async def test_transport_silence_never_emits_user_transcription_or_speech_events(self):
        transcripts, starts = [], []
        self.session.on("input_audio_transcription_completed", transcripts.append)
        self.session.on("input_speech_started", starts.append)
        for origin, key in (("transport_silence", "synthetic"), ("live_mic", "user")):
            self.session._commit_origin = origin
            self.client.on_event({"type": "input_audio_buffer.committed", "item_id": key}, None)
            self.client.on_event({"type": "conversation.item.input_audio_transcription.completed",
                                  "item_id": key, "transcript": "固定测试"}, None)
        self.client.on_event({"type": "input_audio_buffer.speech_started"}, None)
        self.assertEqual(len(transcripts), 1)
        self.assertEqual(transcripts[0].item_id, "user")
        self.assertFalse(starts)

    async def test_direct_speech_in_confirmation_is_isolated_and_corrected_once(self):
        await self.start_showcase()
        await self.observe()
        self.decision({"action": "observe", "presentation": "presented", "object_key": "cup",
            "object_summary": "白杯", "covered": "用途", "speech_intent": "这是杯子。"}, call_id="candidate")
        await self.settle()
        await self.observe()
        count = len(self.generations)
        active = self.session._active
        self.client.responses[active.response_id].audio_bytes = 960
        self.client.on_event({"type": "response.audio.delta", "response_id": active.response_id}, bytes(960))
        self.client.finish(active.response_id)
        await self.settle()
        self.assertEqual(len(self.generations), count)
        self.assertEqual(self.session._active.purpose, "confirm")
        self.decision({"candidate_id": self.session.controller.state.candidate_id,
            "same_subject": True, "still_presented": True}, call_id="confirmed")
        await self.settle()
        self.assertEqual(self.session._active.purpose, "speak")
        self.assertEqual(len(self.generations), count + 1)
        self.assertEqual(self.session.controller.state.pending.context.speech_intent, "这是杯子。")

    async def test_fast_path_uses_one_decision_and_guard_blocks_stale_first_audio(self):
        from livekit_vlm_agent.providers.qwen_realtime import QwenRealtimeSession
        await self.start_showcase()
        await self.observe()
        self.decision({"action": "observe", "presentation": "presented", "object_key": "cup",
            "object_summary": "白杯", "covered": "用途", "speech_intent": "这是杯子。",
            "needs_confirmation": False}, call_id="fast")
        await self.settle()
        await self.observe(match=True)
        active = self.session._active
        self.assertEqual(active.purpose, "speak")
        self.assertIsNotNone(self.session.controller.state.segment.visual_scene)
        self.session._presentation_filter.scene += 1
        with patch.object(QwenRealtimeSession, "_on_event") as delivery:
            self.client.on_event({"type": "response.audio.delta", "response_id": active.response_id}, bytes(960))
            delivery.assert_not_called()
        await self.settle()
        self.assertFalse(self.session.controller.state.introduced)
        self.assertIsNone(self.session.controller.state.segment)

    async def test_sample_callback_supplies_bound_match_for_fast_path(self):
        from dataclasses import replace
        await self.start_showcase()
        await self.observe()
        source = self.session.video.pending
        self.decision({"action": "observe", "presentation": "presented", "object_key": "cup",
            "object_summary": "白杯", "covered": "用途", "speech_intent": "这是杯子。",
            "needs_confirmation": False}, call_id="fast-sample")
        await self.settle()
        await asyncio.sleep(.02)
        now = time.monotonic()
        frame = replace(source, received_at=now)
        detector = self.session._presentation_filter
        detector.previous = frame.signature
        detector.since, detector.last_at = now - 2, now - .3
        self.session.video.pending = frame
        self.session._sample_view(frame, frame.signature)
        await self.settle()
        self.assertEqual(self.session._active.purpose, "speak")
        self.assertEqual(self.session.controller.state.segment.purpose, "presentation_fast")

    async def test_fast_speech_stages_text_until_first_audio_guard_passes(self):
        from livekit_vlm_agent.providers.qwen_realtime import QwenRealtimeSession
        await self.start_showcase()
        await self.observe()
        self.decision({"action": "observe", "presentation": "presented", "object_key": "cup",
            "object_summary": "白杯", "covered": "用途", "speech_intent": "这是杯子。",
            "needs_confirmation": False}, call_id="fast-text")
        await self.settle()
        await self.observe(match=True)
        active = self.session._active
        text_event = {"type": "response.text.delta", "response_id": active.response_id, "delta": "杯子"}
        audio_event = {"type": "response.audio.delta", "response_id": active.response_id}
        with patch.object(QwenRealtimeSession, "_on_event") as delivery:
            self.client.on_event(text_event, None)
            delivery.assert_not_called()
            self.client.on_event(audio_event, bytes(960))
            self.assertEqual([call.args[0] for call in delivery.call_args_list], [text_event, audio_event])
        await self.settle()
        self.assertTrue(self.session.controller.state.segment.audio_started)

    async def test_showcase_speech_corrects_unstarted_tool_once_without_executing_it(self):
        await self.start_showcase()
        await self.observe()
        self.decision({"action": "observe", "presentation": "presented", "object_key": "cup",
            "object_summary": "白杯", "covered": "用途", "speech_intent": "这是杯子。",
            "needs_confirmation": False}, call_id="retry-candidate")
        await self.settle()
        await self.observe(match=True)
        goal = self.session.controller.state.task.goal
        self.decision({"action": "cancel"}, call_id="wrong-speak-tool")
        await self.settle()
        self.assertEqual(self.session._active.purpose, "speak")
        self.assertEqual(self.session.controller.state.pending.corrections, 1)
        self.assertEqual(self.session.controller.state.task.goal, goal)
        self.assertNotEqual(self.session.controller.state.task.status, "cancelled")
        self.decision({"action": "cancel"}, call_id="wrong-speak-again")
        await self.settle()
        self.assertIsNone(self.session.controller.state.pending)
        self.assertEqual(self.session.controller.state.task.status, "paused")

    async def test_new_frame_received_during_decision_is_reused_without_another_sample(self):
        await self.start_showcase()
        await self.observe()
        await asyncio.sleep(.02)
        await self.observe()
        self.decision({"action": "observe", "presentation": "presented", "object_key": "cup",
            "object_summary": "白杯", "covered": "用途", "speech_intent": "这是杯子。",
            "needs_confirmation": False}, call_id="ready-frame")
        await self.settle()
        self.assertEqual(self.session._active.purpose, "speak")
        self.assertEqual(self.session.controller.state.segment.purpose, "presentation_fast")

    async def test_pause_closes_deferred_fast_tool_before_any_speech(self):
        await self.start_showcase()
        await self.observe()
        self.decision({"action": "observe", "presentation": "presented", "object_key": "cup",
            "object_summary": "白杯", "covered": "用途", "speech_intent": "这是杯子。",
            "needs_confirmation": False}, call_id="pause-candidate")
        await self.settle()
        self.assertEqual(self.session.controller.state.candidate_call_id, "pause-candidate")
        self.session.control({"id": "pause-deferred", "action": "pause"})
        await self.settle()
        self.assertIsNone(self.session.controller.state.candidate_call_id)
        self.assertFalse(self.session._unacknowledged_calls)
        self.assertEqual(self.session.controller.state.task.status, "paused")
