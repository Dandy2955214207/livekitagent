"""Bounded application contracts, independent of provider and RTC SDKs."""

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_core import PydanticCustomError


class Value(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class Step(Value):
    id: str = Field(min_length=1, max_length=64)
    instruction: str = Field(min_length=1, max_length=500)
    completion_condition: str = Field(min_length=1, max_length=300)


class Assessment(Value):
    result: Literal["complete", "incomplete", "unknown"]
    basis: Literal["visual", "user_reported", "mixed"]
    evidence_ids: tuple[str, ...] = Field(default=(), max_length=12)
    reason: str = Field(min_length=1, max_length=300)


class PresentationConfirmation(Value):
    candidate_id: str = Field(min_length=1, max_length=64)
    same_subject: bool
    still_presented: bool


class Decision(Value):
    schema_version: Literal[1] = 1
    action: Literal[
        "start_task", "explain", "wait", "advance", "clarify", "pause",
        "complete", "cancel", "answer", "observe",
    ]
    mode: Literal["qa", "narration", "guidance", "showcase"] | None = None
    goal: str | None = Field(default=None, min_length=1, max_length=500)
    plan: tuple[Step, ...] | None = Field(default=None, min_length=1, max_length=5)
    current_step_id: str | None = Field(default=None, min_length=1, max_length=64)
    assessment: Assessment | None = None
    speech_intent: str | None = Field(default=None, min_length=1, max_length=1000)
    next_check_after_ms: int = Field(default=5000, ge=5000, le=120000)
    presentation: Literal["wait", "presented", "known", "detail"] | None = None
    object_key: str | None = Field(default=None, min_length=1, max_length=80)
    object_summary: str | None = Field(default=None, min_length=1, max_length=200)
    covered: str | None = Field(default=None, min_length=1, max_length=300)
    needs_confirmation: bool = True

    @model_validator(mode="after")
    def action_contract(self):
        if self.action == "start_task":
            if self.mode not in {"guidance", "narration", "showcase"} or not self.goal or not self.plan:
                raise PydanticCustomError("start_task_fields_required", "start_task requires mode, goal and plan")
            if len({step.id for step in self.plan}) != len(self.plan):
                raise PydanticCustomError("unique_step_ids_required", "step IDs must be unique")
            if self.current_step_id != self.plan[0].id:
                raise PydanticCustomError("first_step_required", "start_task must begin with the first step")
        elif self.plan is not None or self.goal is not None or self.mode is not None:
            raise PydanticCustomError("omit_plan_goal_mode_except_start_task", "omit plan/goal/mode except on start_task")
        if self.action in {"start_task", "explain", "advance", "clarify", "answer"}:
            if not self.speech_intent:
                raise PydanticCustomError("speech_intent_required", "this action requires speech_intent")
        if self.action in {"advance", "complete"}:
            if not self.current_step_id:
                raise PydanticCustomError("current_step_required", "progress must identify the current step")
        if self.action == "observe":
            if self.presentation is None:
                raise PydanticCustomError("presentation_required", "observe requires presentation")
            brief_new_object = self.presentation == "presented" and not self.needs_confirmation
            if self.presentation in {"presented", "detail"} and not all((self.object_key, self.object_summary)):
                raise PydanticCustomError("presentation_identity_required", "presented/detail requires object_key and object_summary")
            if self.presentation in {"presented", "detail"} and not brief_new_object and not all(
                    (self.covered, self.speech_intent)):
                raise PydanticCustomError("presentation_identity_coverage_speech_required", "presented/detail requires object_key, object_summary, covered, speech_intent")
            if self.speech_intent and len(self.speech_intent) > 120:
                raise PydanticCustomError("shorten_speech_to_120_characters", "object introduction must be at most 120 characters")
            if self.presentation in {"wait", "known"} and self.speech_intent:
                raise PydanticCustomError("omit_speech_for_wait_known", "wait/known must omit speech_intent")
            if self.presentation == "known" and not self.object_key:
                raise PydanticCustomError("known_object_key_required", "known requires an introduced object_key")
        elif any((self.presentation, self.object_key, self.object_summary, self.covered)):
            raise PydanticCustomError("presentation_fields_only_for_observe", "presentation fields only belong to observe")
        return self


@dataclass(frozen=True)
class Evidence:
    id: str
    camera_epoch: int
    received_mono: float
    media_ref: str  # Local opaque handle; never implies a provider-side image binding.


@dataclass(frozen=True)
class TaskView:
    id: str
    revision: int
    mode: str
    status: str
    goal: str
    plan: tuple[Step, ...]
    current_step_id: str
    completed: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class ContextEnvelope:
    id: str
    session_id: str
    connection_epoch: int
    generation_epoch: int
    state_revision: int
    camera_epoch: int
    purpose: str
    trigger: str
    task: TaskView | None
    input_text: str | None
    input_id: str | None
    evidence: tuple[Evidence, ...]
    played_segments: tuple[str, ...]
    interrupted_segments: tuple[str, ...]
    recent_turns: tuple[str, ...]
    allowed_actions: tuple[str, ...]
    audio_ref: str | None = None
    speech_intent: str | None = None
    correction: str | None = None
    policy_version: str = "h1-v1"
    presentation: dict | None = None


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str


@dataclass(frozen=True)
class Event:
    kind: Literal[
        "user_started", "user_turn", "decision_done", "speech_done", "playback_done",
        "tick", "camera_changed", "frame", "view", "observation_skip", "speech_started", "presentation_stale",
        "pause", "resume", "cancel", "close", "failed",
    ]
    request_id: str | None = None  # Bound by the backend, never trusted from model JSON.
    response_id: str | None = None
    status: str = "completed"
    calls: tuple[ToolCall, ...] = ()  # Only complete function items after response.done.
    text: str | None = None
    input_id: str | None = None
    audio_ref: str | None = None
    segment_id: str | None = None
    evidence: Evidence | None = None
    camera_enabled: bool = True
    playback_source: Literal["server_playout", "client_ack", "unknown"] = "unknown"
    scene_id: int = 0
    stable_seconds: float = 0
    candidate_match_id: str | None = None
    candidate_checked_id: str | None = None
    content_changed: bool = False


@dataclass(frozen=True)
class Command:
    kind: Literal["generate", "tool_result", "cancel", "stop_playback", "notice"]
    request_id: str | None = None
    context: ContextEnvelope | None = None
    call_id: str | None = None
    accepted: bool = False
    reason: str | None = None
    authoritative_state: TaskView | None = None
    speech_intent: str | None = None
