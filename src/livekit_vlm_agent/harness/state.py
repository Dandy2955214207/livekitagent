"""Private mutable state owned exclusively by the serial controller."""

from collections import deque
from dataclasses import dataclass, field

from .types import ContextEnvelope, Evidence, Step, TaskView

TERMINAL = {"completed", "cancelled", "failed"}


@dataclass
class Task:
    id: str
    mode: str
    goal: str
    plan: tuple[Step, ...]
    status: str = "active"
    revision: int = 1
    index: int = 0
    completed: list[tuple[str, str]] = field(default_factory=list)
    resume_status: str = "active"
    check_delay: float = 5

    @property
    def step(self):
        return self.plan[self.index]

    def view(self):
        return TaskView(self.id, self.revision, self.mode, self.status, self.goal,
                        self.plan, self.step.id, tuple(self.completed))


@dataclass
class Pending:
    id: str
    context: ContextEnvelope
    corrections: int = 0


@dataclass
class Segment:
    id: str
    task_id: str | None
    step_id: str | None
    purpose: str
    generation_done: bool = False
    playback_done: bool = False
    presented: tuple[str, str, str] | None = None
    visual_scene: int | None = None
    visual_context_id: str | None = None
    source_frame_id: str | None = None
    source_received_mono: float = 0
    presentation_scene: int | None = None
    audio_started: bool = False
    dwell_started_at: float | None = None


@dataclass
class State:
    session_id: str
    connection_epoch: int = 1
    generation_epoch: int = 0
    revision: int = 0
    task: Task | None = None
    input_state: str = "quiet"
    pending: Pending | None = None
    segment: Segment | None = None
    camera_enabled: bool = False
    camera_epoch: int = 0
    latest_frame: Evidence | None = None
    due_at: float | None = None
    progress_at: float = 0
    played: deque = field(default_factory=lambda: deque(maxlen=12))
    interrupted: deque = field(default_factory=lambda: deque(maxlen=12))
    turns: deque = field(default_factory=lambda: deque(maxlen=6))
    closed: bool = False
    scene_id: int = 0
    stable_seconds: float = 0
    candidate: tuple[str, str, str] | None = None
    candidate_frame: str | None = None
    candidate_id: str | None = None
    candidate_speech: str | None = None
    candidate_fast: bool = False
    candidate_match_id: str | None = None
    candidate_checked_id: str | None = None
    candidate_received_mono: float = 0
    candidate_context_id: str | None = None
    candidate_call_id: str | None = None
    candidate_decided_at: float = 0
    last_observation_at: float = 0
    waiting_observations: int = 0
    introduced: deque = field(default_factory=lambda: deque(maxlen=12))
