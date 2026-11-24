"""Event-driven test backend: never opens a connection or captures media."""

import json
import uuid

from .types import Event, ToolCall


class FakeClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class FakeBackend:
    def __init__(self, controller):
        self.controller = controller
        self.commands = []
        self.active = set()
        self.peak_generations = 0

    def send(self, event):
        if event.kind in {"decision_done", "speech_done"}:
            self.active.discard(event.request_id)
        commands = self.controller.handle(event)
        for command in commands:
            if command.kind == "cancel":
                # This fake acknowledges cancellation immediately. A real executor
                # must await cancellation/drain before executing the next command.
                self.active.discard(command.request_id)
            elif command.kind == "generate":
                if self.active:
                    raise AssertionError("Concurrent generation")
                self.active.add(command.request_id)
                self.peak_generations = max(self.peak_generations, len(self.active))
        self.commands.extend(commands)
        return commands

    @property
    def pending(self):
        return self.controller.state.pending

    def decide(self, decision, *, call_id=None, status="completed", request_id=None):
        tool = ("confirm_presentation" if self.pending and self.pending.context.purpose == "confirm" else
                "assess_presentation" if self.pending and self.pending.context.purpose == "observe"
                and self.pending.context.task and self.pending.context.task.mode == "showcase"
                else "submit_task_decision")
        return self.send(Event("decision_done", request_id=request_id or self.pending.id,
                               status=status, calls=(ToolCall(call_id or uuid.uuid4().hex,
                               tool, json.dumps(decision)),)))

    def finish_speech(self, *, playback=True):
        request_id = self.pending.id
        commands = self.send(Event("speech_done", request_id=request_id))
        if playback:
            commands += self.send(Event("playback_done", segment_id=request_id,
                                        playback_source="server_playout"))
        return commands
