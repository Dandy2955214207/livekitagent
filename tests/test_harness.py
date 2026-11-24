"""H1 behavior and adversarial scheduling; no model or RTC calls."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from livekit_vlm_agent.harness import Controller, Event
from livekit_vlm_agent.harness.fake import FakeBackend, FakeClock
from livekit_vlm_agent.harness.types import Evidence, ToolCall


def start(mode="guidance"):
    return dict(action="start_task", mode=mode, goal="组装三个积木",
                plan=[dict(id=f"s{i}", instruction=f"放第{i}个积木",
                           completion_condition=f"第{i}个积木放好") for i in range(1, 4)],
                current_step_id="s1", speech_intent="先放第一个积木")


def progress(step, action="advance", basis="user_reported", evidence="u1"):
    return dict(action=action, current_step_id=step, speech_intent="继续下一步",
                assessment=dict(result="complete", basis=basis,
                                evidence_ids=[evidence], reason="本步骤已完成"))


class HarnessTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.controller = Controller("s1", clock=self.clock, checks_enabled=True)
        self.backend = FakeBackend(self.controller)

    @property
    def state(self):
        return self.controller.state

    def begin(self, mode="guidance"):
        self.backend.send(Event("user_turn", text="教我组装积木"))
        self.backend.decide(start(mode))
        self.backend.finish_speech()

    def frame(self, name="f1", age=0):
        self.backend.send(Event("frame", evidence=Evidence(name, self.state.camera_epoch,
                               self.clock() - age, "memory://" + name)))

    def observe(self):
        self.backend.send(Event("camera_changed"))
        self.clock.advance(5)
        self.frame()
        self.backend.send(Event("tick"))

    def test_three_guidance_steps_require_confirmation_and_playback(self):
        self.begin()
        for number in range(1, 4):
            self.assertEqual(self.state.task.status, "waiting_action")
            self.assertEqual(self.state.task.step.id, f"s{number}")
            self.backend.send(Event("user_turn", text="好了", input_id="u1"))
            action = "complete" if number == 3 else "advance"
            commands = self.backend.decide(progress(f"s{number}", action))
            self.assertEqual([c.kind for c in commands], ["tool_result", "generate"])
            self.assertTrue(commands[0].accepted)
            if number < 3:
                self.assertEqual(self.state.task.status, "active")
            self.backend.finish_speech()
        self.assertEqual(self.state.task.status, "completed")
        self.assertEqual(self.state.task.completed,
                         [("s1", "user_reported"), ("s2", "user_reported"),
                          ("s3", "user_reported")])
        self.assertEqual(self.backend.peak_generations, 1)
        self.clock.advance(5)
        self.assertFalse(self.backend.send(Event("tick")))

    def test_narration_continues_three_distinct_segments_then_stops(self):
        self.begin("narration")
        for number in (2, 3):
            self.assertEqual(self.backend.pending.context.purpose, "narrate")
            self.assertEqual(self.backend.pending.context.task.current_step_id, f"s{number}")
            self.backend.decide(dict(action="explain", current_step_id=f"s{number}",
                                     speech_intent=f"讲第{number}个积木"))
            self.backend.finish_speech()
        self.assertEqual(self.state.task.status, "completed")
        self.assertEqual(len(self.state.played), 3)
        self.assertIsNone(self.backend.pending)

    def test_response_done_does_not_mean_playback_done(self):
        self.backend.send(Event("user_turn", text="指导我"))
        self.backend.decide(start())
        segment = self.backend.pending.id
        self.backend.finish_speech(playback=False)
        self.clock.advance(6)
        self.backend.send(Event("tick"))
        self.assertEqual(self.state.task.status, "active")
        self.assertIsNotNone(self.state.segment)
        self.backend.send(Event("playback_done", segment_id=segment,
                                playback_source="client_ack"))
        self.assertEqual(self.state.task.status, "waiting_action")

    def test_playback_before_response_done_waits_for_both(self):
        self.backend.send(Event("user_turn", text="指导我"))
        self.backend.decide(start())
        segment = self.backend.pending.id
        self.backend.send(Event("playback_done", segment_id=segment,
                                playback_source="server_playout"))
        self.assertIsNotNone(self.backend.pending)
        self.backend.finish_speech(playback=False)
        self.assertEqual(self.state.task.status, "waiting_action")

    def test_unconfirmed_playback_pauses_instead_of_guessing(self):
        self.backend.send(Event("user_turn", text="指导我"))
        self.backend.decide(start("narration"))
        segment = self.backend.pending.id
        self.backend.finish_speech(playback=False)
        self.backend.send(Event("playback_done", segment_id=segment))
        self.assertEqual(self.state.task.status, "paused")
        self.assertEqual(len(self.state.task.completed), 0)

    def test_interrupted_instruction_is_reexplained_on_resume(self):
        self.backend.send(Event("user_turn", text="指导我"))
        self.backend.decide(start("narration"))
        segment = self.backend.pending.id
        self.backend.send(Event("pause"))
        self.backend.send(Event("playback_done", segment_id=segment,
                                playback_source="client_ack"))
        self.backend.send(Event("resume"))
        self.assertEqual(self.backend.pending.context.task.current_step_id, "s1")
        self.assertEqual(self.backend.pending.context.interrupted_segments, (segment,))
        self.assertEqual(self.backend.pending.context.played_segments, ())

    def test_silent_incomplete_and_unknown_checks_do_not_advance_or_speak(self):
        self.begin()
        self.observe()
        for result in ("incomplete", "unknown"):
            self.assertEqual(self.state.task.status, "checking")
            self.backend.decide(dict(action="wait", current_step_id="s1",
                assessment=dict(result=result, basis="visual", evidence_ids=["f1"],
                                reason="还没完成或者被遮挡")))
            self.assertIsNone(self.backend.pending)
            self.assertEqual(self.state.task.step.id, "s1")
            self.clock.advance(5)
            self.frame()
            self.backend.send(Event("tick"))

    def test_visual_complete_uses_current_frame_and_new_step(self):
        self.begin()
        self.observe()
        self.backend.decide(progress("s1", basis="visual", evidence="f1"))
        self.assertEqual(self.state.task.step.id, "s2")
        self.assertEqual(self.state.task.completed, [("s1", "visual")])

    def test_old_frame_cannot_prove_completion(self):
        self.begin()
        self.observe()
        self.clock.advance(2)
        commands = self.backend.decide(progress("s1", basis="visual", evidence="f1"))
        self.assertFalse(commands[0].accepted)
        self.assertEqual(self.state.task.step.id, "s1")
        self.assertEqual(self.backend.pending.corrections, 1)

    def test_camera_switch_and_user_interruption_invalidate_old_decisions(self):
        for event in (Event("camera_changed"), Event("user_started")):
            with self.subTest(kind=event.kind):
                self.setUp()
                self.begin()
                self.observe()
                old = self.backend.pending.id
                self.backend.send(event)
                self.backend.decide(progress("s1", basis="visual", evidence="f1"), request_id=old)
                self.assertEqual(self.state.task.step.id, "s1")
                self.assertIsNone(self.backend.pending)

    def test_duplicate_callback_cannot_advance_twice(self):
        self.begin()
        self.backend.send(Event("user_turn", text="好了", input_id="u1"))
        old = self.backend.pending.id
        self.backend.decide(progress("s1"), call_id="c1")
        self.backend.decide(progress("s1"), call_id="c1", request_id=old)
        self.assertEqual(self.state.task.step.id, "s2")
        self.assertEqual(len(self.state.task.completed), 1)

    def test_no_task_no_frame_disabled_or_busy_means_no_observation(self):
        self.assertFalse(self.backend.send(Event("tick")))
        self.begin()
        self.clock.advance(5)
        self.assertFalse(self.backend.send(Event("tick")))
        self.controller.checks_enabled = False
        self.backend.send(Event("camera_changed"))
        self.frame()
        self.assertFalse(self.backend.send(Event("tick")))
        self.controller.checks_enabled = True
        self.backend.send(Event("tick"))
        pending = self.backend.pending.id
        for _ in range(100):
            self.backend.send(Event("tick"))
        self.assertEqual(self.backend.pending.id, pending)

    def test_timer_is_coalesced_and_interruption_has_priority(self):
        self.begin()
        self.backend.send(Event("camera_changed"))
        self.clock.advance(5)
        self.frame()
        for _ in range(100):
            self.controller.post(Event("tick"))
        self.assertEqual(len(self.controller._queue), 1)
        self.controller.post(Event("user_started"))
        commands = self.controller.drain()
        self.assertFalse(any(c.kind == "generate" for c in commands))

    def test_bad_missing_or_conflicting_tools_get_one_correction_then_pause(self):
        invalid_events = [Event("decision_done"),
            Event("decision_done", calls=(ToolCall("a", "submit_task_decision", "{"),)),
            Event("decision_done", calls=(ToolCall("a", "submit_task_decision", "{}"),
                                          ToolCall("b", "submit_task_decision", "{}")))]
        for event in invalid_events:
            with self.subTest(event=event):
                self.setUp()
                self.begin()
                self.backend.send(Event("user_turn", text="好了"))
                for _ in range(2):
                    bound = Event("decision_done", request_id=self.backend.pending.id,
                                  calls=event.calls)
                    self.backend.send(bound)
                self.assertEqual(self.state.task.status, "paused")
                self.assertIsNone(self.backend.pending)

    def test_cancelled_response_never_accepts_partial_decision(self):
        self.backend.send(Event("user_turn", text="指导我"))
        self.backend.decide(start(), status="cancelled")
        self.assertIsNone(self.state.task)
        self.assertIsNone(self.backend.pending)

    def test_wrong_step_early_complete_and_fake_evidence_are_rejected(self):
        for decision in (progress("s2"), progress("s1", "complete"),
                         progress("s1", evidence="not-supplied"),
                         progress("s1", basis="visual", evidence="u1")):
            with self.subTest(decision=decision):
                self.setUp()
                self.begin()
                self.backend.send(Event("user_turn", text="好了", input_id="u1"))
                self.backend.decide(decision)
                self.assertEqual(self.state.task.step.id, "s1")
                self.assertFalse(self.state.task.completed)

    def test_snapshots_and_histories_are_bounded_and_immutable(self):
        self.begin()
        self.backend.send(Event("user_turn", text="好了", input_id="u1"))
        snapshot = self.backend.pending.context
        self.backend.decide(progress("s1"))
        self.assertEqual(snapshot.task.current_step_id, "s1")
        self.assertEqual(snapshot.task.completed, ())
        for i in range(20):
            self.backend.send(Event("user_turn", text=f"问题{i}"))
        self.assertLessEqual(len(self.state.turns), 6)
        self.assertLessEqual(len(self.state.played), 12)

    def test_stall_and_generation_and_playback_timeouts_pause(self):
        self.begin()
        self.clock.advance(120)
        self.backend.send(Event("tick"))
        self.assertEqual(self.state.task.status, "paused")
        self.backend.send(Event("resume"))
        self.backend.send(Event("user_turn", text="接下来呢"))
        self.clock.advance(30)
        self.backend.send(Event("tick"))
        self.assertEqual(self.state.task.status, "paused")
        self.setUp()
        self.backend.send(Event("user_turn", text="指导我"))
        self.backend.decide(start())
        self.backend.finish_speech(playback=False)
        self.clock.advance(60)
        self.backend.send(Event("tick"))
        self.assertEqual(self.state.task.status, "paused")

    def test_cancel_stops_background_but_keeps_qa_close_releases_state(self):
        self.begin()
        self.backend.send(Event("cancel"))
        self.clock.advance(10)
        self.assertFalse(self.backend.send(Event("tick")))
        self.backend.send(Event("user_turn", text="这是什么"))
        self.assertIn("answer", self.backend.pending.context.allowed_actions)
        self.backend.send(Event("close"))
        self.assertTrue(self.state.closed)
        self.assertFalse(self.state.turns)
        self.assertFalse(self.backend.send(Event("user_turn", text="你好")))

    def test_budget_stops_unbounded_generation(self):
        self.controller.limit = 2
        self.begin()
        self.backend.send(Event("user_turn", text="好了"))
        self.assertEqual(self.state.task.status, "paused")
        self.assertIsNone(self.backend.pending)

    def test_speech_while_explicitly_paused_does_not_resume_task(self):
        self.begin()
        self.backend.send(Event("pause"))
        self.backend.send(Event("user_started"))
        self.backend.send(Event("user_turn", text="我问个问题"))
        self.assertEqual(self.state.task.status, "paused")
        self.assertNotIn("advance", self.backend.pending.context.allowed_actions)

    def test_user_input_cancels_observation_before_starting_new_generation(self):
        self.begin()
        self.observe()
        old_id = self.backend.pending.id
        commands = self.backend.send(Event("user_turn", text="是这里吗"))
        self.assertEqual([c.kind for c in commands], ["cancel", "generate"])
        self.assertNotEqual(old_id, self.backend.pending.id)
        self.assertEqual(self.backend.peak_generations, 1)

    def test_speech_cannot_smuggle_a_control_decision(self):
        self.backend.send(Event("user_turn", text="指导我"))
        self.backend.decide(start())
        self.backend.send(Event("speech_done", request_id=self.backend.pending.id,
                                calls=(ToolCall("bad", "submit_task_decision", "{}"),)))
        self.assertEqual(self.state.task.status, "paused")
        self.assertFalse(self.state.task.completed)

    def test_resume_cancels_an_answer_in_progress_without_stale_pending_work(self):
        self.begin()
        self.backend.send(Event("pause"))
        self.backend.send(Event("user_turn", text="还有一个问题"))
        self.backend.decide(dict(action="answer", speech_intent="补充回答"))
        old_segment = self.backend.pending.id
        commands = self.backend.send(Event("resume"))
        self.assertEqual([command.kind for command in commands], ["cancel", "stop_playback"])
        self.assertEqual(self.state.task.status, "waiting_action")
        self.assertIsNone(self.backend.pending)
        self.backend.send(Event("speech_done", request_id=old_segment))
        self.assertEqual(self.state.task.status, "waiting_action")

    def test_close_preserves_a_completed_task_outcome(self):
        self.begin("narration")
        for number in (2, 3):
            self.backend.decide(dict(action="explain", current_step_id=f"s{number}",
                                     speech_intent="继续讲解"))
            self.backend.finish_speech()
        self.backend.send(Event("close"))
        self.assertEqual(self.state.task.status, "completed")

    def test_duplicate_speech_start_keeps_the_original_resume_point(self):
        self.begin()
        self.backend.send(Event("user_started"))
        self.backend.send(Event("user_started"))
        self.backend.send(Event("user_turn", text="我已经做好了", input_id="u1"))
        self.assertEqual(self.state.task.status, "checking")
        self.backend.decide(progress("s1"))
        self.assertEqual(self.state.task.step.id, "s2")


if __name__ == "__main__":
    unittest.main()
