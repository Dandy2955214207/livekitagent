"""Application-owned provenance is bound outside the model's small result schema."""

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class MonitorAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    result: Literal["matched", "not_matched", "unknown"]
    observation_summary: str = Field(min_length=1, max_length=500)
    evidence_summary: str = Field(min_length=1, max_length=500)
    uncertainty: str = Field(max_length=300)


class ObservationTask(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    task_id: str
    revision: int = Field(ge=1)
    call_id: str
    source_user_item_id: str
    kind: Literal["condition_monitor", "live_narration"] = "condition_monitor"
    goal: str = Field(min_length=1, max_length=1000)
    condition: str = Field(min_length=1, max_length=1000)
    selected_source: str = "camera"
    modalities: tuple[str, ...] = ("image",)
    status: Literal["active", "paused", "completed", "cancelled", "failed"] = "active"
    repeat: Literal["once", "repeat_on_reentry"] = "once"
    cooldown_ms: int = Field(default=0, ge=0)
    created_at: float
    expires_at: float


class ObservationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    observation_id: str
    task_id: str
    revision: int
    call_epoch: int
    monitor_epoch: int
    camera_epoch: int
    frame_ids: tuple[int, ...] = Field(min_length=1)
    sampled_at_mono: float
    evaluated_at_mono: float
    answer: MonitorAnswer


class ProactiveEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    event_id: str
    observation_id: str
    task_id: str
    revision: int
    call_epoch: int
    created_at_mono: float
    expires_at_mono: float
    dedupe_key: str
    observation_summary: str
    evidence_summary: str
    delivery: Literal["queued", "preparing", "speaking", "delivered", "interrupted",
                      "expired", "dropped", "failed"] = "queued"
