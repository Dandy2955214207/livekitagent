"""Immutable per-generation snapshots and explicit current-evidence boundaries."""

from .state import State, TERMINAL
from .types import ContextEnvelope


def allowed_actions(state: State) -> tuple[str, ...]:
    task = state.task
    if task is None or task.status in TERMINAL:
        return ("start_task", "answer", "clarify")
    if task.status == "paused":
        return ("answer", "clarify", "cancel")
    if task.mode == "showcase":
        return ("observe", "answer", "clarify", "pause", "cancel")
    if task.status == "checking":
        return ("wait", "advance", "complete", "clarify", "answer", "pause", "cancel")
    return ("explain", "answer", "clarify", "pause", "cancel")


def build_context(state: State, *, context_id: str, purpose: str, trigger: str,
                  now: float, max_frame_age: float, text: str | None = None,
                  input_id: str | None = None, speech_intent: str | None = None,
                  correction: str | None = None, audio_ref: str | None = None,
                  fast_enabled: bool = False) -> ContextEnvelope:
    frame = state.latest_frame
    evidence = ()
    if (state.camera_enabled and frame and frame.camera_epoch == state.camera_epoch
            and 0 <= now - frame.received_mono <= max_frame_age):
        evidence = (frame,)
    return ContextEnvelope(
        id=context_id, session_id=state.session_id, connection_epoch=state.connection_epoch,
        generation_epoch=state.generation_epoch, state_revision=state.revision,
        camera_epoch=state.camera_epoch, purpose=purpose, trigger=trigger,
        task=state.task.view() if state.task else None, input_text=text, input_id=input_id,
        evidence=evidence, played_segments=tuple(state.played),
        interrupted_segments=tuple(state.interrupted), recent_turns=tuple(state.turns),
        allowed_actions=() if purpose == "speak" else allowed_actions(state),
        speech_intent=speech_intent, correction=correction,
        audio_ref=audio_ref,
        presentation=({"scene_id": state.scene_id, "stable_seconds": round(state.stable_seconds, 2),
                       "candidate": state.candidate, "candidate_id": state.candidate_id,
                       "fast_path_enabled": fast_enabled,
                       "introduced": tuple(state.introduced)}
                      if state.task and state.task.mode == "showcase" else None),
    )
