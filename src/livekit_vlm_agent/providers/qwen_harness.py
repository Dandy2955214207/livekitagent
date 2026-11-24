"""Controlled session: one Qwen connection, internal decisions, SDK-only speech.

The event reducer remains responsive while a separate ordered executor waits for
configuration, commit, tool-result and cancellation acknowledgements.
"""

import asyncio
import base64
from collections import deque
from dataclasses import asdict, dataclass, field
import json
import logging
import time
import uuid

from livekit.agents import llm
from livekit.plugins import silero

from ..harness import Controller, Event
from ..harness.policy import TOOLS, compile_instructions
from ..harness.types import Command, Evidence
from ..media.presentation import PresentationFilter, distance
from ..media.speech_input import SpeechInput
from .qwen_client import QwenProtocolError
from .qwen_control import FunctionCallCollector
from .qwen_realtime import QwenRealtimeModel, QwenRealtimeSession

logger = logging.getLogger(__name__)


@dataclass
class Active:
    request_id: str
    purpose: str
    response_id: str | None = None
    collector: FunctionCallCollector | None = None
    cancelled: bool = False
    terminal: asyncio.Event | None = None
    requested_at: float = 0
    first_audio_at: float | None = None
    staged_text: list = field(default_factory=list)


@dataclass
class LiveInput:
    id: str
    pending: bytearray = field(default_factory=bytearray)
    total: int = 0
    valid: bool = True
    ending: bool = False
    queued: bool = False
    started: bool = False
    kind: str = "live_input"
    request_id: str | None = None


class QwenHarnessModel(QwenRealtimeModel):
    def session(self, *, turn_detection_disabled=False):
        session = QwenHarnessSession(self)
        self.sessions.add(session)
        return session


class QwenHarnessSession(QwenRealtimeSession):
    def __init__(self, model):
        super().__init__(model, manual=True)
        self.controller = Controller(uuid.uuid4().hex, checks_enabled=False,
            allowed_modes=("guidance", "showcase"), completion_bases=("user_reported",),
            showcase_enabled=True, stable_seconds=self.settings.showcase_stable_seconds,
            recheck_seconds=self.settings.showcase_recheck_seconds,
            early_observe_seconds=self.settings.showcase_early_observe_seconds,
            fast_enabled=self.settings.showcase_fast_path,
            fast_dwell_seconds=self.settings.showcase_fast_dwell_seconds)
        self._presentation_filter = PresentationFilter(self.settings.showcase_motion_threshold)
        self.video.sample_fps = self.settings.showcase_sample_fps
        self.video.on_sample = self._sample_view
        self._observation_media = {}
        self._observe_signature = None
        self._observe_signature_context = None
        self._latest_sample = None
        self._commit_origin = None
        self._user_audio_items = deque(maxlen=64)
        self._command_queue = asyncio.Queue(maxsize=64)
        self._active = None
        self._speech_input = None
        self._live_input = None
        self._live_ready = deque()
        self._remote_live_dirty = False
        self._last_audio_at = 0
        self._unacknowledged_calls = {}
        self._recording_id = None
        self._recording_at = None
        self._pcm = bytearray()
        self._media = {}
        self._submitted = deque(maxlen=64)
        self._controls = deque(maxlen=128)
        self._executor = None
        self._clock_task = None
        self._submitting = False
        self._submit_task = None
        self._notice = None
        self._speak_request = None
        self.stop_playback = None
        self.on_status = None

    @property
    def speak_request(self):
        return self._speak_request

    def snapshot(self):
        state = self.controller.state
        task = state.task
        activity = ("speaking" if state.segment else "confirming" if state.candidate else "observing")
        if not state.pending and not state.segment:
            activity = ("waiting_video" if not state.latest_frame or
                        time.monotonic() - state.latest_frame.received_mono > 1.5 else
                        "moving" if state.stable_seconds < self.controller.stable_seconds else
                        "confirming" if state.candidate else "waiting_subject")
        return {"mode": "controlled_manual", "auto_input": self.settings.harness_auto_input,
                "recording": self._recording_id is not None,
                "recording_id": self._recording_id, "submitting": self._submitting,
                "control_id": self._controls[-1] if self._controls else None,
                "busy": bool(state.pending or state.segment), "notice": self._notice,
                "camera_enabled": state.camera_enabled,
                "activity": activity,
                "task": {"status": task.status, "goal": task.goal,
                         "step": task.index + 1, "total": len(task.plan),
                         "instruction": task.step.instruction, "mode": task.mode,
                         "introduced": len(state.introduced)} if task else None}

    def _sample_view(self, frame, signature):
        self._latest_sample = frame
        scene, stable = self._presentation_filter.update(signature, frame.received_at)
        self.video.motion = self._presentation_filter.metrics
        if self.video.motion.get("change_reason"):
            logger.info("harness_view_change %s", json.dumps(self.video.motion))
        frame_id = uuid.uuid4().hex
        state = self.controller.state
        match_id = None
        checked_id = (state.candidate_id if state.candidate_fast and
                      state.candidate_context_id == self._observe_signature_context else None)
        visual_difference = (distance(signature, self._observe_signature)
                             if self._observe_signature is not None else None)
        if (state.candidate_fast and state.candidate_context_id == self._observe_signature_context
                and visual_difference is not None
                and visual_difference <= self.settings.showcase_fast_match_threshold):
            match_id = state.candidate_id
        self._post(Event("view", evidence=Evidence(frame_id, self.controller.state.camera_epoch,
            frame.received_at, "memory://" + frame_id), scene_id=scene, stable_seconds=stable,
            candidate_match_id=match_id,
            candidate_checked_id=checked_id,
            content_changed=visual_difference is not None and visual_difference > .10))
        self._post(Event("tick"))

    def _publish_status(self):
        if self.on_status:
            self.on_status(self.snapshot())

    def _post(self, event):
        if not self._closed and not self._failed:
            self.controller.post(event)
            self._wake.set()

    def _current(self, request_id):
        pending = self.controller.state.pending
        return pending is not None and pending.id == request_id and not self._closed

    async def _run(self):
        try:
            detector = None
            if self.settings.harness_auto_input:
                def load_detector():
                    return silero.VAD.load(min_speech_duration=.15,
                        min_silence_duration=self.settings.harness_end_silence_seconds,
                        prefix_padding_duration=.3, max_buffered_speech=30)
                detector = await asyncio.to_thread(load_detector)
            async with self._client:
                # Never switch a text-initialized connection into audio mid-session.
                await self._client.configure(vad=False, modalities=["text", "audio"], tools=TOOLS)
                if detector:
                    self._speech_input = SpeechInput(detector, start=self._start_live,
                        audio=self._append_live, end=self._end_live, failed=self._fail)
                self._ready.set()
                self._executor = asyncio.create_task(self._execute_loop())
                self._clock_task = asyncio.create_task(self._ticks())
                while True:
                    await self._wake.wait()
                    self._wake.clear()
                    commands = self.controller.drain()
                    self._check_ready_candidate()
                    for command in commands:
                        if command.kind == "generate" and command.context.purpose in {"observe", "confirm"}:
                            self._observation_media[command.request_id] = self.video.pending
                        if command.kind in {"cancel", "stop_playback"}:
                            self._close_output(command.request_id)
                        self._command_queue.put_nowait(command)
                    while self._live_ready:
                        self._command_queue.put_nowait(self._live_ready.popleft())
                    self._publish_status()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("harness_session_failed type=%s", type(exc).__name__)
            self._fail(type(exc).__name__)
        finally:
            self._ready.set()
            self.controller.handle(Event("close"))
            for task in (self._executor, self._clock_task, self._submit_task):
                if task:
                    task.cancel()
            await asyncio.gather(*(task for task in (self._executor, self._clock_task, self._submit_task)
                                   if task), return_exceptions=True)
            self._pcm.clear()
            self._media.clear()
            self._observation_media.clear()
            self._user_audio_items.clear()
            self._unacknowledged_calls.clear()
            if self._speech_input:
                await self._speech_input.aclose()
            if self._live_input:
                self._live_input.pending.clear()
            await self.video.aclose()
            for generation in self._generations.values():
                generation.close()

    def _check_ready_candidate(self):
        """Reuse a fresh frame received during DECIDE; don't wait another sample."""
        state, sample = self.controller.state, self._latest_sample
        if (not state.candidate_fast or state.candidate_checked_id == state.candidate_id or
                not sample or not self.video.fresh(sample) or sample.signature is None or
                not state.latest_frame or sample.received_at != state.latest_frame.received_mono or
                sample.received_at <= state.candidate_received_mono or
                state.candidate_context_id != self._observe_signature_context or self._observe_signature is None):
            return
        matched = distance(sample.signature, self._observe_signature) <= self.settings.showcase_fast_match_threshold
        self._post(Event("view", evidence=state.latest_frame, scene_id=state.scene_id,
            stable_seconds=state.stable_seconds, candidate_checked_id=state.candidate_id,
            candidate_match_id=state.candidate_id if matched else None))
        self._post(Event("tick"))

    async def _ticks(self):
        while True:
            await asyncio.sleep(.1)
            # Muted/lost RTC audio may stop delivering frames; close a speech
            # boundary with local silence instead of leaving the task listening.
            if (self._speech_input and self._speech_input.speaking and
                    time.monotonic() - self._last_audio_at > .3):
                from livekit import rtc
                for _ in range(5):
                    self._speech_input.push(rtc.AudioFrame(bytes(640), 16000, 1, 320))
            if self._recording_at and time.monotonic() - self._recording_at >= 30:
                self._discard_recording()
                self._notice = "录音已超过30秒，请重新分段说话。"
                self._post(Event("pause"))
            self._post(Event("tick"))

    async def _execute_loop(self):
        carried = None
        try:
            while True:
                command = carried or await self._command_queue.get()
                carried = None
                if command.kind == "generate":
                    await self._generate_controlled(command)
                elif command.kind == "live_input":
                    await self._flush_live(command)
                elif command.kind == "tool_result":
                    # Only coalesce adjacent commands already authorized by the reducer.
                    try:
                        carried = self._command_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                    if (carried and carried.kind == "generate" and self._pipelined(carried.context)
                            and carried.context.purpose == "speak" and self._current(carried.request_id)):
                        tool_cursor = await self._tool_result(command, wait_for_ack=False)
                        await self._generate_controlled(carried, tool_ack=(command, tool_cursor))
                        carried = None
                    else:
                        await self._tool_result(command)
                elif command.kind == "cancel":
                    await self._cancel_remote(command.request_id)
                elif command.kind == "stop_playback":
                    if self.stop_playback:
                        await asyncio.wait_for(self.stop_playback(), 5)
                elif command.kind == "notice" and command.reason != "user_interruption":
                    self._notice = ("模型没有返回有效的任务决定，请重新说一遍；当前步骤不会跳过。"
                        if command.reason.startswith("invalid_decision:") else {
                        "user_paused": "任务已暂停，可以继续或结束任务。",
                        "generation_budget": "操作较频繁，请稍等一分钟后继续。",
                        "playback_not_confirmed": "播放未完整完成，请点击继续或重新提问。",
                        "no_progress": "等待时间较长，任务已暂停。",
                    }.get(command.reason, "本轮未能完成，请重新说话或继续任务。"))
                self._publish_status()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("harness_executor_failed type=%s", type(exc).__name__)
            self._fail(type(exc).__name__)

    def _pipelined(self, context):
        return (self.settings.showcase_pipeline and context.task and context.task.mode == "showcase"
                and (context.purpose in {"observe", "confirm"} or
                     context.purpose == "speak" and context.trigger in {"presentation", "presentation_fast"}))

    async def _generate_controlled(self, command, tool_ack=None):
        context, request_id = command.context, command.request_id
        pipeline = self._pipelined(context)
        acknowledgements = []
        if tool_ack:
            acknowledgements.append(("tool", tool_ack))
        configured_at = time.monotonic()
        observation = self._observation_media.pop(request_id, None)
        if not self._current(request_id):
            if tool_ack:
                await self._ack_tool_result(*tool_ack)
            return
        if self._active:
            raise QwenProtocolError("Overlapping generation rejected")
        config_cursor = await self._client.configure(vad=False, modalities=["text", "audio"],
            tools=TOOLS,
            max_tokens=512 if context.purpose == "speak" else 2048,
            instructions=compile_instructions(context), wait_for_ack=not pipeline,
            instructions_only=bool(pipeline))
        if pipeline:
            acknowledgements.append(("session.updated", config_cursor))
        logger.info("harness_configuration request=%s purpose=%s phase=%s elapsed_ms=%.1f",
                    request_id, context.purpose, "sent" if pipeline else "acknowledged",
                    (time.monotonic() - configured_at) * 1000)
        if not self._current(request_id):
            await self._await_protocol(acknowledgements)
            return
        if context.purpose in {"observe", "confirm"}:
            if self._remote_live_dirty:
                await self._clear_remote_audio()
                self._remote_live_dirty = False
            if (not observation or not self.video.fresh(observation)
                    or not context.evidence
                    or observation.received_at != context.evidence[0].received_mono):
                self._post(Event("observation_skip", request_id=request_id))
                await self._await_protocol(acknowledgements)
                return
            if context.purpose == "observe":
                self._observe_signature = observation.signature
                self._observe_signature_context = context.id
            # A synthetic transport envelope for a fresh image, NOT user speech.
            cursor = self._client.cursor
            await self._client.stream_pcm(bytes(16000), image=base64.b64decode(observation.image),
                realtime=False, on_image_sent=lambda started: self.video.sent(observation, started))
            if not self._current(request_id):
                await self._await_protocol(acknowledgements)
                await self._clear_remote_audio()
                return
            self._commit_origin = "transport_silence"
            await self._client.send({"type": "input_audio_buffer.commit"})
            if pipeline:
                acknowledgements.append(("input_audio_buffer.committed", cursor))
            else:
                await self._client.wait_type("input_audio_buffer.committed", after=cursor, timeout=10)
            logger.info("harness_observation request=%s scene=%s stable_seconds=%s audio_origin=transport_silence",
                request_id, context.presentation["scene_id"], context.presentation["stable_seconds"])
        if context.audio_ref and context.audio_ref not in self._submitted:
            pcm, frame = self._media.pop(context.audio_ref)
            if frame and not self.video.fresh(frame):
                self._notice = "提交前画面已过期，请重新说话并提交。"
                self._post(Event("pause"))
                return
            cursor = self._client.cursor
            await self._client.stream_pcm(pcm, image=None if frame is None else base64.b64decode(frame.image),
                realtime=False, on_image_sent=(lambda started: self.video.sent(frame, started)) if frame else None)
            if not self._current(request_id):
                await self._clear_remote_audio()
                return
            self._commit_origin = "live_mic"
            await self._client.send({"type": "input_audio_buffer.commit"})
            await self._client.wait_type("input_audio_buffer.committed", after=cursor, timeout=10)
            self._submitted.append(context.audio_ref)
        if not self._current(request_id):
            await self._await_protocol(acknowledgements)
            return
        self._active = Active(request_id, context.purpose, terminal=asyncio.Event(), requested_at=time.monotonic())
        cursor = self._client.cursor
        # Keep the connection audio-capable from its first config, but request
        # text-only internal responses; no need to generate discarded speech.
        modalities = ["text"] if context.purpose in {"observe", "confirm"} else ["text", "audio"]
        try:
            await self._await_protocol(acknowledgements)
            if not self._current(request_id):
                self._active = None
                return
            self._active.requested_at = time.monotonic()
            logger.info("harness_generation request=%s context=%s purpose=%s trigger=%s task=%s epoch=%s session=%s step=%s connection=%s",
                        request_id, context.id, context.purpose, context.trigger, context.task.id if context.task else None,
                        context.generation_epoch, context.session_id,
                        context.task.current_step_id if context.task else None, context.connection_epoch)
            await self._client.send({"type": "response.create", "response": {"modalities": modalities}})
            await self._client.wait_type("response.created", after=cursor, timeout=10)
            if pipeline:
                logger.info("harness_pipeline_ready request=%s purpose=%s elapsed_ms=%.1f",
                    request_id, context.purpose, (time.monotonic() - configured_at) * 1000)
        except BaseException:
            self._close_output(request_id)
            raise

    async def _await_protocol(self, acknowledgements):
        for kind, value in acknowledgements:
            if kind == "tool":
                await self._ack_tool_result(*value)
            else:
                await self._client.wait_type(kind, after=value, timeout=12)

    async def _clear_remote_audio(self):
        cursor = self._client.cursor
        await self._client.send({"type": "input_audio_buffer.clear"})
        await self._client.wait_type("input_audio_buffer.cleared", after=cursor, timeout=5)

    async def _tool_result(self, command, *, wait_for_ack=True):
        cursor = self._client.cursor
        result = {"accepted": command.accepted, "reason": command.reason,
                  "next_response_mode": "speak" if (command.accepted and command.speech_intent)
                      or command.reason == "speak_only_no_tools" else "decide_or_wait",
                  "speech_intent": command.speech_intent,
                  "authoritative_state": asdict(command.authoritative_state) if command.authoritative_state else None}
        # Pydantic plan values need their JSON representation, never repr/string fallbacks.
        if result["authoritative_state"]:
            result["authoritative_state"]["plan"] = [step.model_dump() for step in command.authoritative_state.plan]
        await self._client.send({"type": "conversation.item.create", "item": {
            "type": "function_call_output", "call_id": command.call_id,
            "output": json.dumps(result, ensure_ascii=False)}})
        if wait_for_ack:
            await self._ack_tool_result(command, cursor)
        return cursor

    async def _ack_tool_result(self, command, cursor):
        await self._client.wait(lambda event: event.data.get("type") == "conversation.item.created"
            and event.data.get("item", {}).get("call_id") == command.call_id, after=cursor, timeout=10)
        for request_id, calls in list(self._unacknowledged_calls.items()):
            remaining = tuple(call for call in calls if call.id != command.call_id)
            if remaining:
                self._unacknowledged_calls[request_id] = remaining
            else:
                self._unacknowledged_calls.pop(request_id, None)
        logger.info("harness_tool_result call=%s accepted=%s reason=%s", command.call_id, command.accepted, command.reason)

    def _close_output(self, request_id):
        active = self._active
        if active and active.request_id == request_id:
            active.cancelled = True
            if active.response_id:
                self._client.gate.cancel(active.response_id)
                generation = self._generations.get(active.response_id)
                if generation:
                    generation.close()

    async def _cancel_remote(self, request_id):
        active = self._active
        if active and active.request_id == request_id:
            if active.response_id is None:
                raise QwenProtocolError("Cannot associate cancellation")
            state = self._client.responses.get(active.response_id)
            if state and state.done_at is None:
                self._client.gate.cancel(active.response_id)
                await self._client.send({"type": "response.cancel"})
                await asyncio.wait_for(active.terminal.wait(), 8)
        for call in self._unacknowledged_calls.pop(request_id, ()):
            await self._tool_result(Command("tool_result", call_id=call.id, reason="cancelled",
                                           authoritative_state=self.controller._view()))

    def _on_event(self, event, pcm):
        if self._closed:
            return
        kind = event.get("type")
        if kind == "input_audio_buffer.committed":
            if self._commit_origin == "live_mic" and event.get("item_id"):
                self._user_audio_items.append(event["item_id"])
            self._commit_origin = None
            return
        if kind == "conversation.item.input_audio_transcription.completed":
            if event.get("item_id") not in self._user_audio_items or not event.get("transcript", "").strip():
                return  # Synthetic silence must never appear as a user turn.
        if kind in {"input_audio_buffer.speech_started", "input_audio_buffer.speech_stopped"}:
            return  # Manual control owns user turn boundaries, including during observations.
        active = self._active
        if kind == "response.created":
            if not active or active.response_id is not None:
                self._fail("unexpected_generation")
                return
            active.response_id = event["response"]["id"]
            active.collector = FunctionCallCollector(active.response_id)
            if active.purpose == "speak" and self._current(active.request_id):
                self._speak_request = active.request_id
                super()._on_event(event, None)
            return
        if kind.startswith("response."):
            rid = event.get("response_id") or event.get("response", {}).get("id")
            if not active or rid != active.response_id:
                return  # Old output cannot enter any new SDK generation.
            calls = active.collector.feed(event) if active.collector else None
            if active.purpose == "speak" and not active.cancelled and self._current(active.request_id):
                segment = self.controller.state.segment
                stage_text = (segment and segment.visual_scene is not None and active.first_audio_at is None
                              and kind in {"response.audio_transcript.delta", "response.text.delta"})
                if stage_text:
                    if len(active.staged_text) >= 512:
                        self._fail("unstarted_speech_text_limit")
                        return
                    active.staged_text.append(event)
                if pcm and active.first_audio_at is None:
                    if segment and segment.visual_scene is not None:
                        motion = self._presentation_filter.metrics
                        sample = self._latest_sample
                        if (self._presentation_filter.scene != segment.visual_scene or
                                not sample or not self.video.fresh(sample) or sample.signature is None or
                                segment.visual_context_id != self._observe_signature_context or
                                self._observe_signature is None or
                                distance(sample.signature, self._observe_signature) > self.settings.showcase_fast_match_threshold or
                                motion.get("stable_seconds", 0) < self.controller.fast_dwell_seconds):
                            active.cancelled = True
                            active.staged_text.clear()
                            self._client.gate.cancel(rid)
                            self._post(Event("presentation_stale", request_id=active.request_id))
                            return
                    active.first_audio_at = time.monotonic()
                    dwell_ms = ((active.first_audio_at - segment.dwell_started_at) * 1000
                                if segment and segment.dwell_started_at is not None else -1)
                    logger.info("harness_first_audio request=%s response=%s generate_ms=%.1f dwell_ms=%.1f",
                        active.request_id, rid, (active.first_audio_at - active.requested_at) * 1000, dwell_ms)
                    self._post(Event("speech_started", request_id=active.request_id))
                    for staged in active.staged_text:
                        super()._on_event(staged, None)
                    active.staged_text.clear()
                if not stage_text:
                    super()._on_event(event, pcm)
            # No DECIDE event, audio, text, or control JSON reaches AgentSession.
            if kind == "response.done":
                status = event["response"].get("status", "unknown")
                response = self._client.responses.get(rid)
                logger.info("harness_response request=%s response=%s purpose=%s status=%s calls=%s audio_bytes=%s dropped_audio_bytes=%s text_chars=%s",
                    active.request_id, rid, active.purpose, status, len(calls or ()),
                    response.audio_bytes if response else 0, response.dropped_audio_bytes if response else 0,
                    len(response.text) if response else 0)
                if active.purpose == "speak" and status == "completed":
                    response = self._client.responses.get(rid)
                    if not response or not response.audio_bytes:
                        status = "failed"
                active.terminal.set()
                for call in calls or ():
                    logger.info("harness_function request=%s response=%s call=%s", active.request_id, rid, call.id)
                if calls:
                    if len(self._unacknowledged_calls) >= 32:
                        raise QwenProtocolError("Unclosed stale tool limit")
                    self._unacknowledged_calls[active.request_id] = calls
                self._active = None
                self._post(Event("speech_done" if active.purpose == "speak" else "decision_done",
                    request_id=active.request_id, response_id=rid, status=status, calls=calls or (),
                    text=response.text[:600] if active.purpose == "speak" and response else None))
            return
        super()._on_event(event, pcm)

    def push_audio(self, frame):
        if self.settings.harness_auto_input:
            if self._closed or self._failed or not self._speech_input:
                return
            if frame.sample_rate != 16000 or frame.num_channels != 1:
                self._fail("unsupported_input_format")
                return
            self._last_audio_at = time.monotonic()
            self._speech_input.push(frame)
            return
        if not self._recording_id or self._closed or self._failed:
            return
        if frame.sample_rate != 16000 or frame.num_channels != 1:
            self._fail("unsupported_input_format")
            return
        data = bytes(frame.data)
        if len(self._pcm) + len(data) > 30 * 32000:
            self._discard_recording()
            self._notice = "录音超过30秒，请重新分段说话。"
            self._post(Event("pause"))
            return
        self._pcm.extend(data)

    def _queue_live(self, turn):
        if turn.valid and not turn.queued:
            turn.queued = True
            self._live_ready.append(turn)
            self._wake.set()

    def _start_live(self, prefix):
        if self._closed or self._failed:
            return
        self._discard_recording()
        self._submitting = False
        self._notice = None
        self._recording_id = uuid.uuid4().hex
        self._recording_at = time.monotonic()
        self._live_input = LiveInput(self._recording_id)
        logger.info("harness_live_started input=%s", self._recording_id)
        self._post(Event("user_started"))
        self._append_live(prefix)
        self._publish_status()

    def _append_live(self, data):
        turn = self._live_input
        if not turn or not turn.valid or turn.ending:
            return
        turn.total += len(data)
        if turn.total > 30 * 32000 or len(turn.pending) + len(data) > 5 * 32000:
            self._discard_recording()
            self._notice = "语音过长或上传积压，请稍后重新说话。"
            self._post(Event("pause"))
            return
        turn.pending.extend(data)
        self._queue_live(turn)

    def _end_live(self):
        turn = self._live_input
        if not turn or not turn.valid:
            return
        turn.ending = True
        logger.info("harness_live_ended input=%s audio_bytes=%s", turn.id, turn.total)
        self._recording_id = self._recording_at = None
        self._submitting = True
        self._queue_live(turn)
        self._publish_status()

    async def _flush_live(self, turn):
        turn.queued = False
        if not turn.valid:
            return
        if not turn.started:
            if self._remote_live_dirty:
                await self._clear_remote_audio()
                self._remote_live_dirty = False
            turn.started = True
        if not turn.valid:
            return
        pcm = bytes(turn.pending)
        turn.pending.clear()
        if pcm:
            self._remote_live_dirty = True
            await self._client.stream_pcm(pcm, realtime=False)
        if not turn.valid or not turn.ending:
            return
        frame = self.video.pending
        camera_epoch = self.controller.state.camera_epoch
        if frame and not self.video.fresh(frame):
            frame = None
        frame_id = None
        if frame:
            await self._client.send({"type": "input_image_buffer.append", "image": frame.image})
            self.video.sent(frame, time.monotonic())
            frame_id = uuid.uuid4().hex
        if not turn.valid:
            return
        cursor = self._client.cursor
        self._commit_origin = "live_mic"
        logger.info("harness_live_commit input=%s", turn.id)
        await self._client.send({"type": "input_audio_buffer.commit"})
        await self._client.wait_type("input_audio_buffer.committed", after=cursor, timeout=10)
        self._remote_live_dirty = False
        if not turn.valid:
            return
        # Audio is already on this connection. The existing DECIDE/SPEAK
        # executor skips re-upload through its usual submitted-reference gate.
        self._submitted.append(turn.id)
        if frame and camera_epoch == self.controller.state.camera_epoch and self.video.fresh(frame):
            self._post(Event("frame", evidence=Evidence(frame_id, camera_epoch,
                frame.received_at, "memory://" + frame_id)))
        logger.info("harness_live_input input=%s audio_bytes=%s frame=%s", turn.id, turn.total, frame_id)
        self._post(Event("user_turn", input_id=turn.id, audio_ref=turn.id))
        turn.valid = False
        self._live_input = None
        self._submitting = False
        self._publish_status()

    def push_video(self, frame):
        if not self.video.enabled:
            self._post(Event("camera_changed", camera_enabled=True))
        super().push_video(frame)

    def clear_video(self):
        self._presentation_filter.reset()
        super().clear_video()
        self._post(Event("camera_changed", camera_enabled=False))

    def _discard_recording(self):
        if self.settings.harness_auto_input:
            self._submitting = False
        if self._live_input:
            self._live_input.valid = False
            self._live_input.pending.clear()
            self._live_input = None
        self._recording_id = self._recording_at = None
        self._pcm.clear()

    def control(self, payload):
        """Called only for reliable data from the session's selected participant."""
        if self._closed or self._failed or not isinstance(payload, dict):
            return
        key, action = payload.get("id"), payload.get("action")
        if not isinstance(key, str) or not 1 <= len(key) <= 80 or key in self._controls:
            return
        self._controls.append(key)
        if self.settings.harness_auto_input and action in {"record_start", "record_submit"}:
            return
        if action == "record_start" and not self._recording_id and not self._submitting:
            self._notice = None
            self._media.clear()
            self._recording_id, self._recording_at = key, time.monotonic()
            self._pcm.clear()
            self._post(Event("user_started"))
        elif action == "record_submit" and payload.get("recording_id") == self._recording_id and self._recording_id and not self._submitting:
            self._submitting = True
            self._submit_task = asyncio.create_task(self._submit_recording())
        elif action in {"pause", "resume", "cancel"}:
            self._notice = None
            self._discard_recording()
            self._post(Event(action))
        self._publish_status()

    async def _submit_recording(self):
        recording_id = self._recording_id
        try:
            await asyncio.sleep(0.2)  # Allow final RTP after the browser mutes its microphone.
            if recording_id != self._recording_id or self._closed:
                return
            pcm = bytes(self._pcm)
            self._discard_recording()
            if len(pcm) < 6400:
                self._notice = "录音太短或未收到声音，请重新说话后提交。"
                self._post(Event("pause"))
                return
            frame = self.video.pending
            if frame is not None and not self.video.fresh(frame):
                frame = None
            if frame:
                frame_id = uuid.uuid4().hex
                self._post(Event("frame", evidence=Evidence(frame_id,
                    self.controller.state.camera_epoch, frame.received_at, "memory://" + frame_id)))
            self._media[recording_id] = (pcm, frame)
            logger.info("harness_input input=%s audio_bytes=%s frame=%s camera_epoch=%s",
                recording_id, len(pcm), frame_id if frame else None, self.controller.state.camera_epoch)
            self._post(Event("user_turn", input_id=recording_id, audio_ref=recording_id))
        finally:
            self._submitting = False
            self._publish_status()

    def playback_done(self, segment_id, *, interrupted=False):
        logger.info("harness_playback segment=%s interrupted=%s source=server_playout", segment_id, interrupted)
        self._post(Event("playback_done", segment_id=segment_id,
                         status="interrupted" if interrupted else "completed",
                         playback_source="server_playout"))

    def interrupt(self):
        # SDK may interrupt playout; remote generation remains owned by the reducer.
        if self._active:
            logger.info("harness_sdk_interrupt request=%s purpose=%s first_audio=%s",
                        self._active.request_id, self._active.purpose, self._active.first_audio_at is not None)
            self._close_output(self._active.request_id)

    def generate_reply(self, **kwargs):
        future = asyncio.get_running_loop().create_future()
        future.set_exception(llm.RealtimeError("语音回答由 harness 轮次控制触发。"))
        return future

    def commit_audio(self):
        pass  # Only the controlled submit path may commit or generate.

    def clear_audio(self):
        pass  # Local capture is explicitly discarded by control actions.

    async def update_instructions(self, instructions):
        await self.wait_ready()  # Per-request compiler owns instructions.
