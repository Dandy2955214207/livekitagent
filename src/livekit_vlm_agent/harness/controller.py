"""Serial, non-blocking reducer. I/O executes returned commands outside this owner.

Backends must bind request IDs locally, gate DECIDE audio/text, acknowledge tool
results before executing a subsequent generate command, and confirm cancellation
before allowing another generation. No network/RTC capability is implied by H1.
"""

from collections import deque
import heapq
import time
import uuid

from pydantic import ValidationError

from .context import build_context
from .state import Pending, Segment, State, Task, TERMINAL
from .types import Command, Decision, Event, PresentationConfirmation

PRIORITY = {"close": 0, "failed": 0, "cancel": 0, "pause": 0, "user_started": 1,
            "camera_changed": 1, "speech_started": 1, "presentation_stale": 2, "user_turn": 2, "playback_done": 3,
            "decision_done": 3, "speech_done": 3, "observation_skip": 3,
            "frame": 2, "view": 2, "resume": 4, "tick": 5}


class Controller:
    def __init__(self, session_id: str, *, clock=time.monotonic, max_frame_age=1.5,
                 checks_enabled=False, max_generations_per_minute=20,
                 stall_seconds=120, playback_timeout=60, allowed_modes=("guidance", "narration"),
                 completion_bases=("visual", "user_reported", "mixed"),
                 showcase_enabled=False, stable_seconds=1.5, recheck_seconds=12,
                 early_observe_seconds=None, fast_enabled=False, fast_dwell_seconds=1.2):
        self.state = State(session_id)
        self.clock = clock
        self.max_frame_age = max_frame_age
        # Explicit capability gate. Production must enable only after P4 passes.
        self.checks_enabled = checks_enabled
        self.limit = max_generations_per_minute
        self.stall_seconds = stall_seconds
        self.playback_timeout = playback_timeout
        self.allowed_modes = allowed_modes
        self.completion_bases = completion_bases
        self.showcase_enabled = showcase_enabled
        self.stable_seconds = stable_seconds
        self.recheck_seconds = recheck_seconds
        self.early_observe_seconds = min(stable_seconds, early_observe_seconds if early_observe_seconds is not None else stable_seconds)
        self.fast_enabled = fast_enabled
        self.fast_dwell_seconds = min(stable_seconds, max(self.early_observe_seconds, fast_dwell_seconds))
        self._queue = []
        self._seq = 0
        self._calls = deque(maxlen=256)
        self._generation_times = deque()
        self._playback_deadline = None
        self._user_interrupted = False
        self._commands = []

    def post(self, event: Event):
        """RTC/provider callbacks enqueue; only drain mutates authoritative state."""
        if event.kind == "tick" and any(e.kind == "tick" for _, _, e in self._queue):
            return
        if event.kind in {"frame", "view"}:
            self._queue = [item for item in self._queue if item[2].kind != event.kind]
            heapq.heapify(self._queue)
        if len(self._queue) >= 64:
            # Fail closed on event overload, preserving urgent stop events.
            self._queue.clear()
            event = Event("failed")
        self._seq += 1
        heapq.heappush(self._queue, (PRIORITY[event.kind], self._seq, event))

    def drain(self) -> list[Command]:
        self._commands = []
        while self._queue:
            _, _, event = heapq.heappop(self._queue)
            if not self.state.closed:
                self._handle(event)
        return list(self._commands)

    def handle(self, event: Event) -> list[Command]:
        self.post(event)
        return self.drain()

    def _changed(self):
        self.state.revision += 1
        if self.state.task:
            self.state.task.revision += 1

    def _clear_candidate(self):
        self._settle_candidate_call(accepted=False, reason="candidate_discarded")
        self.state.candidate = self.state.candidate_frame = None
        self.state.candidate_id = self.state.candidate_speech = None
        self.state.candidate_fast = False
        self.state.candidate_match_id = None
        self.state.candidate_checked_id = None
        self.state.candidate_received_mono = 0
        self.state.candidate_context_id = None

    def _settle_candidate_call(self, *, accepted=True, reason="candidate_requires_confirmation", speech=None):
        state = self.state
        if state.candidate_call_id:
            self._commands.append(Command("tool_result", call_id=state.candidate_call_id,
                accepted=accepted, reason=reason, authoritative_state=self._view(), speech_intent=speech))
            state.candidate_call_id = None

    def _invalidate(self):
        state = self.state
        state.generation_epoch += 1
        if state.pending:
            self._commands.append(Command("cancel", request_id=state.pending.id))
        if state.segment:
            state.interrupted.append(state.segment.id)
            self._commands.append(Command("stop_playback", request_id=state.segment.id))
        state.pending = state.segment = None
        self._clear_candidate()
        state.due_at = self._playback_deadline = None
        self._changed()

    def _pause(self, reason):
        task = self.state.task
        if task and task.status not in TERMINAL and task.status != "paused":
            task.resume_status = "waiting_action" if task.status == "checking" else task.status
            task.status = "paused"
        self._invalidate()
        self._commands.append(Command("notice", reason=reason))

    def _generate(self, purpose, trigger, *, text=None, input_id=None,
                  speech_intent=None, correction=None, corrections=0, audio_ref=None):
        state, now = self.state, self.clock()
        if state.pending or state.segment or state.closed or state.input_state != "quiet":
            return
        while self._generation_times and now - self._generation_times[0] >= 60:
            self._generation_times.popleft()
        # Background work leaves two slots for user interactions/corrections.
        ceiling = max(0, self.limit - 2) if trigger == "observation_timer" else self.limit
        if len(self._generation_times) >= ceiling:
            if trigger == "observation_timer":
                state.due_at = now + self.recheck_seconds
                return
            self._pause("generation_budget")
            return
        self._generation_times.append(now)
        if purpose == "observe":
            state.last_observation_at = now
        state.generation_epoch += 1
        request_id = uuid.uuid4().hex
        context = build_context(state, context_id=uuid.uuid4().hex, purpose=purpose,
                                trigger=trigger, now=now, max_frame_age=self.max_frame_age,
                                text=text, input_id=input_id, speech_intent=speech_intent,
                                correction=correction, audio_ref=audio_ref, fast_enabled=self.fast_enabled)
        state.pending = Pending(request_id, context, corrections)
        state.due_at = None
        if purpose == "speak":
            task = state.task
            state.segment = Segment(request_id, task.id if task else None,
                                    task.step.id if task else None, trigger)
            self._playback_deadline = now + self.playback_timeout
        self._commands.append(Command("generate", request_id=request_id, context=context))

    def _valid_pending(self, event):
        state, pending = self.state, self.state.pending
        if not pending or event.request_id != pending.id:
            return False
        ctx = pending.context
        return (ctx.connection_epoch == state.connection_epoch
                and ctx.generation_epoch == state.generation_epoch
                and ctx.state_revision == state.revision
                and ctx.camera_epoch == state.camera_epoch)

    def _reject(self, pending, event, reason):
        for call in event.calls:
            self._commands.append(Command("tool_result", call_id=call.id, reason=reason,
                                          authoritative_state=self._view()))
        if pending.context.purpose in {"observe", "confirm"} and reason == "view_changed":
            self.state.due_at = self.clock() + 1
            return  # A brief wobble waits for a fresh stable view, not a protocol retry.
        if pending.corrections == 0 and event.status == "completed":
            self._generate(pending.context.purpose, pending.context.trigger,
                           text=pending.context.input_text, input_id=pending.context.input_id,
                           audio_ref=pending.context.audio_ref,
                           correction=reason, corrections=1)
        else:
            self._pause("invalid_decision:" + reason)

    def _view(self):
        return self.state.task.view() if self.state.task else None

    def _validate(self, decision, context):
        state, task = self.state, self.state.task
        if decision.action not in context.allowed_actions:
            return "action_not_allowed"
        if decision.action == "start_task" and decision.mode not in self.allowed_modes:
            return "mode_not_enabled"
        if decision.action == "start_task" and decision.mode == "showcase" and not self.showcase_enabled:
            return "mode_not_enabled"
        if context.purpose == "observe" and task and task.mode == "showcase":
            if decision.action != "observe":
                return "observation_must_use_observe"
        if decision.action == "observe":
            if not task or task.mode != "showcase" or context.purpose != "observe":
                return "observation_not_requested"
            if (not context.evidence or context.presentation["scene_id"] != state.scene_id
                    or state.stable_seconds < self.early_observe_seconds or not state.latest_frame
                    or not 0 <= self.clock() - state.latest_frame.received_mono <= self.max_frame_age):
                return "view_changed"
            if decision.presentation == "detail" and decision.object_key not in {
                    key for key, _, _ in state.introduced}:
                return "detail_requires_introduced_object"
            if decision.presentation == "known" and decision.object_key not in {
                    key for key, _, _ in state.introduced}:
                return "known_requires_played_object_key"
            return None
        if decision.assessment:
            supplied_ids = {frame.id for frame in context.evidence}
            if context.input_id and (context.input_text or context.audio_ref):
                supplied_ids.add(context.input_id)
            if not set(decision.assessment.evidence_ids) <= supplied_ids:
                return "unknown_evidence"
        if decision.action in {"start_task", "answer", "clarify", "pause", "cancel"}:
            return None
        if not task or decision.current_step_id != task.step.id:
            return "wrong_current_step"
        if decision.action in {"advance", "complete"}:
            if (decision.action == "advance") == (task.index == len(task.plan) - 1):
                return "cannot_skip_or_finish_early"
            if task.mode == "guidance":
                assessment = decision.assessment
                if not assessment or assessment.result != "complete":
                    return "completion_assessment_required"
                if assessment.basis not in self.completion_bases:
                    return "completion_basis_not_enabled"
                ids = set(assessment.evidence_ids)
                if not ids:
                    return "completion_evidence_required"
                valid_frames = {frame.id for frame in context.evidence
                                if 0 <= self.clock() - frame.received_mono <= self.max_frame_age
                                and frame.camera_epoch == state.camera_epoch}
                user_ids = {context.input_id} if context.input_id and (context.input_text or context.audio_ref) else set()
                if not ids <= (valid_frames | user_ids):
                    return "unknown_or_expired_evidence"
                if assessment.basis in {"visual", "mixed"} and not ids & valid_frames:
                    return "current_visual_evidence_required"
                if assessment.basis in {"user_reported", "mixed"} and not ids & user_ids:
                    return "user_confirmation_required"
                if assessment.basis == "visual" and ids & user_ids:
                    return "user_report_is_not_visual"
                if assessment.basis == "user_reported" and ids & valid_frames:
                    return "visual_is_not_user_report"
            elif task.step.id not in {step for step, _ in task.completed}:
                return "playback_required"
        if decision.action == "wait":
            if not decision.assessment or decision.assessment.result not in {"incomplete", "unknown"}:
                return "wait_requires_incomplete_or_unknown"
        return None

    def _decision(self, event):
        state = self.state
        if not self._valid_pending(event) or state.pending.context.purpose == "speak":
            return
        pending, state.pending = state.pending, None
        if event.status != "completed" or len(event.calls) != 1:
            self._reject(pending, event, "one_successful_complete_tool_required")
            return
        call = event.calls[0]
        key = (state.connection_epoch, call.id)
        if key in self._calls:
            self._reject(pending, event, "duplicate_call")
            return
        self._calls.append(key)
        try:
            expected_tool = ("confirm_presentation" if pending.context.purpose == "confirm" else
                             "assess_presentation" if pending.context.purpose == "observe"
                             and state.task and state.task.mode == "showcase" else "submit_task_decision")
            if call.name != expected_tool or len(call.arguments) > 16000:
                raise ValueError("invalid_tool")
            if pending.context.purpose == "confirm":
                decision = PresentationConfirmation.model_validate_json(call.arguments)
                reason = self._validate_confirmation(decision, pending.context)
            else:
                decision = Decision.model_validate_json(call.arguments)
                reason = self._validate(decision, pending.context)
        except ValidationError as exc:
            fields = [".".join(map(str, error["loc"])) + ":" +
                      {"tuple_type": "expected_json_array"}.get(error["type"], error["type"])
                      for error in exc.errors(include_input=False, include_context=False)[:4]]
            decision, reason = None, "invalid_schema:" + ",".join(fields)
        except ValueError:
            decision, reason = None, "invalid_tool"
        if reason:
            self._reject(pending, event, reason)
            return
        if isinstance(decision, PresentationConfirmation):
            self._confirm_presentation(decision, call.id)
            return
        if decision.action == "observe":
            self._presentation_decision(decision, pending.context, call.id, fast_allowed=pending.corrections == 0)
            return
        self._apply(decision)
        self._commands.append(Command("tool_result", call_id=call.id, accepted=True,
                                      authoritative_state=self._view(), speech_intent=decision.speech_intent))
        if decision.speech_intent:
            self._generate("speak", decision.action, speech_intent=decision.speech_intent)
        elif state.task and state.task.status == "waiting_action":
            state.due_at = self.clock() + decision.next_check_after_ms / 1000

    def _apply(self, decision):
        state, action = self.state, decision.action
        if action == "start_task":
            state.task = Task(uuid.uuid4().hex, decision.mode, decision.goal, decision.plan)
            state.progress_at = self.clock()
            state.introduced.clear()
            state.waiting_observations = 0
            self._clear_candidate()
        task = state.task
        if task and task.status not in TERMINAL:
            if action in {"advance", "complete"}:
                basis = decision.assessment.basis if decision.assessment else "playback"
                task.completed.append((task.step.id, basis))
                state.progress_at = self.clock()
                if action == "advance":
                    task.index += 1
                    task.status = "active"
                else:
                    task.status = "completed"
            elif action == "wait":
                task.status = "waiting_action"
                task.check_delay = decision.next_check_after_ms / 1000
            elif action == "pause":
                task.resume_status = "waiting_action" if task.status == "checking" else task.status
                task.status = "paused"
            elif action == "cancel":
                task.status = "cancelled"
            elif action == "explain":
                task.status = "active"
        self._changed()

    def _presentation_decision(self, decision, context, call_id, *, fast_allowed=True):
        state = self.state
        item = (decision.object_key, decision.object_summary, decision.covered or decision.object_summary)
        speech = decision.speech_intent or f"介绍：{decision.object_summary}；简短说明外观与一项常见用途，不推断未知品牌或材质。"
        if decision.presentation in {"presented", "detail"}:
            known = next((entry for entry in state.introduced if entry[0] == item[0]), None)
            repeat = known and (decision.presentation == "presented" or item[2] in known[2])
            if repeat:
                self._clear_candidate()
            else:
                state.candidate, state.candidate_frame = item, context.evidence[0].id
                state.candidate_id, state.candidate_speech = uuid.uuid4().hex, speech
                state.candidate_received_mono = context.evidence[0].received_mono
                state.candidate_context_id = context.id
                state.candidate_fast = (self.fast_enabled and fast_allowed and
                    decision.presentation == "presented" and not decision.needs_confirmation)
                state.candidate_match_id = None
                state.candidate_checked_id = None
                state.candidate_decided_at = self.clock()
                if state.candidate_fast:
                    state.candidate_call_id = call_id
        else:
            self._clear_candidate()
        if not state.candidate_fast:
            self._commands.append(Command("tool_result", call_id=call_id, accepted=True,
                reason=("candidate_presentation" if state.candidate else "quiet_observation:" + decision.presentation),
                authoritative_state=self._view()))
        if decision.presentation == "wait":
            state.waiting_observations += 1
        else:
            state.waiting_observations = 0
        delay = (min(2, self.recheck_seconds) if self.fast_enabled and decision.presentation == "wait"
                 and state.waiting_observations <= 2 else self.recheck_seconds)
        state.due_at = self.clock() if state.candidate else self.clock() + delay

    def _validate_confirmation(self, decision, context):
        state = self.state
        if not state.task or state.task.mode != "showcase" or state.task.status != "waiting_action":
            return "confirmation_not_requested"
        if (not state.candidate_id or decision.candidate_id != state.candidate_id
                or decision.candidate_id != context.presentation["candidate_id"]):
            return "wrong_candidate"
        if (not context.evidence or context.evidence[0].id == state.candidate_frame
                or context.presentation["scene_id"] != state.scene_id
                or state.stable_seconds < self.stable_seconds or not state.latest_frame
                or not 0 <= self.clock() - state.latest_frame.received_mono <= self.max_frame_age):
            return "view_changed"
        return None

    def _confirm_presentation(self, decision, call_id):
        state = self.state
        ready = decision.same_subject and decision.still_presented
        speech = state.candidate_speech if ready else None
        self._commands.append(Command("tool_result", call_id=call_id, accepted=True,
            reason="confirmed_presentation" if ready else "presentation_not_confirmed",
            authoritative_state=self._view(), speech_intent=speech))
        if ready:
            self._start_presentation()
        else:
            self._clear_candidate()
            state.due_at = self.clock() + 3

    def _start_presentation(self, *, fast=False):
        state = self.state
        item, speech = state.candidate, state.candidate_speech
        visual_context_id = state.candidate_context_id
        source_frame, source_received = state.candidate_frame, state.candidate_received_mono
        if fast:
            self._settle_candidate_call(reason="fast_presentation_confirmed", speech=speech)
        self._clear_candidate()
        state.progress_at = self.clock()
        self._generate("speak", "presentation_fast" if fast else "presentation", speech_intent=speech)
        if state.segment:
            state.segment.presented = item
            state.segment.dwell_started_at = state.latest_frame.received_mono - state.stable_seconds
            state.segment.source_frame_id = source_frame
            state.segment.source_received_mono = source_received
            state.segment.presentation_scene = state.scene_id
            if fast:
                state.segment.visual_scene = state.scene_id
                state.segment.visual_context_id = visual_context_id

    def _recover_unstarted_presentation(self):
        state = self.state
        previous, pending = state.segment, state.pending
        draft = previous.presented if previous else None
        speech = pending.context.speech_intent if pending else None
        self._invalidate()
        if draft and speech:
            state.candidate, state.candidate_speech = draft, speech
            state.candidate_id = uuid.uuid4().hex
            state.candidate_frame = previous.source_frame_id
            state.candidate_received_mono = previous.source_received_mono
            # The retry must use a new model confirmation, never another fast attempt.
            state.candidate_fast = False
        state.due_at = self.clock()

    def _finish_segment(self):
        state, segment = self.state, self.state.segment
        if not segment or not segment.generation_done or not segment.playback_done:
            return
        state.played.append(segment.id)
        state.segment = None
        self._playback_deadline = None
        task = state.task
        if not task or task.id != segment.task_id or task.status in TERMINAL | {"paused"}:
            return
        if task.mode == "showcase":
            if segment.presented:
                key, summary, covered = segment.presented
                previous = next((entry for entry in state.introduced if entry[0] == key), None)
                if previous:
                    state.introduced.remove(previous)
                    covered = (previous[2] + "；" + covered)[-600:]
                state.introduced.append((key, summary, covered))
            task.status = "waiting_action"
            # A finished introduction is already known; checking the same image
            # immediately can race the user's next object and waste a model turn.
            # Changed content/scene still wakes observation without this delay.
            unchanged = segment.presented and segment.presentation_scene == state.scene_id
            state.due_at = self.clock() + (self.recheck_seconds if unchanged else 0)
            self._changed()
            return
        if segment.purpose in {"start_task", "explain", "advance"}:
            if task.mode == "narration":
                task.completed.append((task.step.id, "playback"))
                state.progress_at = self.clock()
                if task.index == len(task.plan) - 1:
                    task.status = "completed"
                else:
                    task.index += 1
                    task.status = "active"
                    self._changed()
                    self._generate("narrate", "playback_complete")
                    return
            else:
                task.status = "waiting_action"
                if segment.purpose in {"start_task", "advance"}:
                    state.progress_at = self.clock()
        elif task.status == "checking":
            task.status = "waiting_action"
        self._changed()
        if task.status == "waiting_action":
            state.due_at = self.clock() + task.check_delay
        elif task.status == "active":
            self._generate("narrate", "answer_playback_complete")

    def _handle(self, event):
        state, task, now = self.state, self.state.task, self.clock()
        if event.kind in {"close", "failed", "cancel"}:
            self._invalidate()
            state.input_state = "quiet"
            if task and task.status not in TERMINAL:
                task.status = "failed" if event.kind == "failed" else "cancelled"
            if event.kind in {"close", "failed"}:
                state.closed = True
                state.latest_frame = None
                state.turns.clear()
                state.played.clear()
                state.interrupted.clear()
                self._queue.clear()
            return
        if event.kind == "pause":
            state.input_state = "quiet"
            self._user_interrupted = False
            self._pause("user_paused")
        elif event.kind == "resume" and task and task.status == "paused":
            self._invalidate()
            task.status = task.resume_status
            state.progress_at = now
            self._changed()
            if task.mode == "showcase":
                task.status = "waiting_action"
                state.due_at = now + 1
            elif task.status == "waiting_action":
                state.due_at = now + task.check_delay
            else:
                self._generate("narrate", "resume")
        elif event.kind == "user_started":
            if state.input_state == "user_speaking":
                return
            self._user_interrupted = not task or task.status != "paused"
            self._pause("user_interruption")
            state.input_state = "user_speaking"
        elif event.kind == "user_turn":
            if (not event.text and not event.audio_ref) or (event.text and len(event.text) > 2000):
                self._pause("input_length_or_missing_text")
                return
            self._invalidate()
            state.input_state = "quiet"
            if event.text:
                state.turns.append(event.text)
            if task and task.status == "paused" and self._user_interrupted:
                task.status = task.resume_status
            self._user_interrupted = False
            if task and task.status == "waiting_action":
                task.status = "checking"
            self._changed()
            self._generate("user_turn", "user_submit", text=event.text,
                           input_id=event.input_id or uuid.uuid4().hex, audio_ref=event.audio_ref)
        elif event.kind == "camera_changed":
            state.camera_epoch += 1
            state.camera_enabled = event.camera_enabled
            state.latest_frame = None
            state.stable_seconds = 0
            self._clear_candidate()
            if state.pending or state.segment:
                self._pause("camera_changed_during_generation")
        elif event.kind == "view":
            if not state.camera_enabled or not event.evidence or event.evidence.camera_epoch != state.camera_epoch:
                return
            if event.scene_id != state.scene_id:
                state.waiting_observations = 0
                self._clear_candidate()
                # Camera movement cancels quiet analysis, but lets a short spoken
                # explanation finish before considering the latest view.
                if state.pending and state.pending.context.purpose in {"observe", "confirm"}:
                    self._commands.append(Command("cancel", request_id=state.pending.id))
                    state.pending = None
                    state.generation_epoch += 1
                if state.segment and state.segment.visual_scene is not None and not state.segment.audio_started:
                    self._recover_unstarted_presentation()
                state.due_at = now
            state.scene_id, state.stable_seconds = event.scene_id, event.stable_seconds
            state.latest_frame = event.evidence
            state.candidate_match_id = event.candidate_match_id
            state.candidate_checked_id = event.candidate_checked_id
            if (event.content_changed and not state.pending and not state.segment and not state.candidate
                    and now - state.last_observation_at >= 2):
                state.due_at = now
        elif event.kind == "frame":
            frame = event.evidence
            if (state.camera_enabled and frame and frame.camera_epoch == state.camera_epoch
                    and frame.received_mono <= now and (state.latest_frame is None
                    or frame.received_mono > state.latest_frame.received_mono)):
                state.latest_frame = frame
        elif event.kind == "decision_done":
            self._decision(event)
        elif event.kind == "observation_skip" and self._valid_pending(event):
            if state.pending.context.purpose in {"observe", "confirm"}:
                state.pending = None
                state.due_at = now + 1
        elif event.kind == "speech_started" and state.segment and state.segment.id == event.request_id:
            state.segment.audio_started = True
        elif event.kind == "presentation_stale" and state.segment and state.segment.id == event.request_id:
            self._recover_unstarted_presentation()
        elif event.kind == "speech_done" and self._valid_pending(event):
            if state.pending.context.purpose != "speak":
                return
            if event.status != "completed" or event.calls:
                pending, previous = state.pending, state.segment
                if (state.task and state.task.mode == "showcase" and state.task.status not in TERMINAL | {"paused"}
                        and not previous.audio_started and pending.corrections == 0 and
                        event.status in {"completed", "failed"}):
                    for call in event.calls:
                        self._commands.append(Command("tool_result", call_id=call.id,
                            reason="speak_only_no_tools", authoritative_state=self._view(),
                            speech_intent=pending.context.speech_intent))
                    self._commands.append(Command("stop_playback", request_id=previous.id))
                    state.pending = state.segment = None
                    self._playback_deadline = None
                    self._generate("speak", pending.context.trigger, speech_intent=pending.context.speech_intent,
                                   correction="speak_only_no_tools", corrections=1)
                    if state.segment:
                        state.segment.presented = previous.presented
                        state.segment.visual_scene = previous.visual_scene
                        state.segment.visual_context_id = previous.visual_context_id
                        state.segment.dwell_started_at = previous.dwell_started_at
                        state.segment.source_frame_id = previous.source_frame_id
                        state.segment.source_received_mono = previous.source_received_mono
                        state.segment.presentation_scene = previous.presentation_scene
                    return
                self._pause("speech_failed_or_unexpected_tool")
                return
            state.pending = None
            state.segment.generation_done = True
            if event.text and state.segment.presented:
                key, summary, _ = state.segment.presented
                state.segment.presented = (key, summary, event.text[:600])
            self._finish_segment()
        elif event.kind == "playback_done" and state.segment:
            if event.segment_id != state.segment.id:
                return
            if event.status != "completed" or event.playback_source == "unknown":
                self._pause("playback_not_confirmed")
                return
            state.segment.playback_done = True
            self._finish_segment()
        elif event.kind == "tick":
            if state.candidate_fast and now - state.candidate_decided_at >= 2:
                self._settle_candidate_call(reason="fast_check_timeout")
                state.candidate_fast = False
            if state.pending and now - self._generation_times[-1] >= 30:
                self._pause("generation_timeout")
                return
            if self._playback_deadline and now >= self._playback_deadline:
                self._pause("playback_timeout")
                return
            if task and task.status == "waiting_action":
                if task.mode == "showcase":
                    if (self.showcase_enabled and state.input_state == "quiet"
                            and not state.pending and not state.segment and state.camera_enabled
                            and state.latest_frame and state.stable_seconds >= self.early_observe_seconds
                            and 0 <= now - state.latest_frame.received_mono <= self.max_frame_age
                            and state.due_at is not None and now >= state.due_at):
                        if not state.candidate:
                            self._generate("observe", "observation_timer")
                        elif state.latest_frame.id != state.candidate_frame:
                            if (state.candidate_fast and state.stable_seconds >= self.fast_dwell_seconds
                                    and state.candidate_match_id == state.candidate_id
                                    and state.latest_frame.received_mono > state.candidate_received_mono):
                                self._start_presentation(fast=True)
                            elif (state.stable_seconds >= self.stable_seconds and
                                  (not state.candidate_fast or state.candidate_checked_id == state.candidate_id)):
                                self._settle_candidate_call()
                                state.candidate_fast = False
                                self._generate("confirm", "observation_timer")
                    return
                if now - state.progress_at >= self.stall_seconds:
                    self._pause("no_progress")
                    return
                if (self.checks_enabled and state.input_state == "quiet" and not state.pending
                        and not state.segment and state.due_at is not None and now >= state.due_at
                        and state.camera_enabled and state.latest_frame
                        and 0 <= now - state.latest_frame.received_mono <= self.max_frame_age):
                    task.status = "checking"
                    self._changed()
                    self._generate("observe", "observation_timer")
