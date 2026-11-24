"""Passing views, two fresh observations, playout history and bounded scheduling."""

import base64
import io
import json
from pathlib import Path
import sys
import unittest

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from livekit_vlm_agent.harness import Controller, Event
from livekit_vlm_agent.harness.fake import FakeBackend, FakeClock
from livekit_vlm_agent.harness.types import Evidence, ToolCall
from livekit_vlm_agent.media.presentation import PresentationFilter, fingerprint


class FilterTests(unittest.TestCase):
    def signature(self, color):
        stream = io.BytesIO()
        Image.new("RGB", (64, 48), color).save(stream, format="JPEG")
        return fingerprint(base64.b64encode(stream.getvalue()))

    def test_color_changes_and_gaps_restart_stability(self):
        detector = PresentationFilter()
        red, blue = self.signature("red"), self.signature("blue")
        scene, _ = detector.update(red, 100)
        for t in (100.3, 100.6, 100.9, 101.2, 101.5):
            current, stable = detector.update(red, t)
        self.assertEqual(scene, current)
        self.assertEqual(stable, 1.5)
        current, stable = detector.update(blue, 101.8)
        self.assertNotEqual(scene, current)
        self.assertEqual(stable, 0)
        _, stable = detector.update(blue, 104)
        self.assertEqual(stable, 0)

    def test_small_shake_and_exposure_are_tolerated_but_slow_pan_accumulates(self):
        detector = PresentationFilter()
        pixels = np.zeros((24, 32, 3), dtype=np.float32)
        pixels[5:15, 6:20] = 0.7
        hist = np.zeros(3, dtype=np.float32)
        scene, _ = detector.update((pixels, hist), 100)
        same, _ = detector.update((np.roll(pixels, 1, axis=1), hist), 100.3)
        self.assertEqual(scene, same)
        current, _ = detector.update((pixels + .09, hist), 100.6)
        self.assertEqual(current, scene)
        for n in range(1, 14):
            current, _ = detector.update((np.roll(pixels, n * 2, axis=1), hist), 100.6 + n * 0.3)
        self.assertGreater(current, scene)

    def test_moderately_changed_but_settled_view_does_not_keep_resetting(self):
        pixels = np.full((24, 32, 3), .3, dtype=np.float32)
        base = pixels, np.zeros(3, dtype=np.float32)
        changed = pixels.copy()
        changed[:, :, 0] += .14
        shifted = changed, np.array([.0933, -.0467, -.0467], dtype=np.float32)
        detector = PresentationFilter()
        scene, _ = detector.update(base, 100)
        self.assertEqual(detector.update(shifted, 100.3)[0], scene)
        self.assertEqual(detector.update(base, 100.6)[0], scene)
        self.assertEqual(detector.update(base, 100.9)[0], scene)
        detector.update(shifted, 101.2)
        detector.update(shifted, 101.5)
        detector.update(shifted, 101.8)
        current, stable = detector.update(shifted, 102.1)
        self.assertEqual(current, scene)
        self.assertAlmostEqual(stable, 2.1)
        self.assertEqual(detector.metrics["motion_state"], "dwelling")

    def texture(self, offset):
        pixels = np.random.default_rng(42).uniform(.1, .8, (24, 128, 3)).astype(np.float32)
        view = pixels[:, 40 + offset:72 + offset]
        colour = view.mean(axis=(0, 1))
        return view, colour - colour.mean()

    def test_natural_back_and_forth_handheld_movement_counts_as_dwell(self):
        detector = PresentationFilter()
        first = None
        for n, offset in enumerate((0, 2, 4, 2, 0, -2, -4, -2, 0, 1, 3, 4, 3, 1, 0)):
            scene, stable = detector.update(self.texture(offset), 100 + n / 3)
            first = scene if first is None else first
            self.assertEqual(scene, first, detector.metrics)
            self.assertEqual(detector.metrics["motion_state"], "dwelling")
        self.assertGreater(stable, 4)

    def test_bounded_diagonal_drift_can_reverse_without_being_a_sweep(self):
        pixels = np.random.default_rng(15).uniform(.1, .8, (48, 128, 3)).astype(np.float32)
        detector = PresentationFilter()
        first = None
        for n, (dx, dy) in enumerate(((0,0),(2,1),(3,1),(5,2),(6,2),(4,1),(2,0),(0,0),(-2,-1),(0,0))):
            view = pixels[12 + dy:36 + dy, 40 + dx:72 + dx]
            colour = view.mean(axis=(0,1)); colour -= colour.mean()
            scene, _ = detector.update((view, colour), 100 + n / 3)
            first = scene if first is None else first
            self.assertEqual(scene, first)
            self.assertEqual(detector.metrics["motion_state"], "dwelling")

    def test_continuous_directional_pan_is_moving_then_recovers_after_stop(self):
        detector = PresentationFilter()
        first, _ = detector.update(self.texture(0), 100)
        for n in range(1, 13):
            scene, stable = detector.update(self.texture(2 * n), 100 + n / 3)
        self.assertGreater(scene, first)
        self.assertEqual(stable, 0)
        self.assertEqual(detector.metrics["motion_state"], "moving")
        for n in range(1, 14):
            scene, stable = detector.update(self.texture(24), 104 + n / 3)
        self.assertGreater(stable, 1.5)
        self.assertEqual(detector.metrics["motion_state"], "dwelling")

    def test_recent_stop_does_not_wait_for_entire_motion_history_to_expire(self):
        detector = PresentationFilter()
        for n in range(13):
            detector.update(self.texture(2 * n), 100 + n / 3)
        self.assertEqual(detector.metrics["motion_state"], "moving")
        for n in range(1, 4):
            _, stable = detector.update(self.texture(24), 104 + n / 3)
        self.assertEqual(detector.metrics["motion_state"], "dwelling")
        self.assertGreaterEqual(stable, .5)
        detector.update(self.texture(26), 105 + 1 / 3)
        self.assertEqual(detector.metrics["motion_state"], "moving")


def observation(key="cup", result="presented", covered="用途"):
    data = {"action": "observe", "presentation": result}
    if result in {"presented", "detail"}:
        data.update(object_key=key, object_summary=key + "外观", covered=covered,
                    speech_intent="这是" + key + "。")
    return data


class ShowcaseTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.controller = Controller("showcase-test", clock=self.clock,
            showcase_enabled=True, allowed_modes=("guidance", "showcase"))
        self.backend = FakeBackend(self.controller)
        self.backend.send(Event("camera_changed"))
        self.backend.send(Event("user_turn", text="逐个介绍我展示的物品"))
        self.backend.decide(dict(action="start_task", mode="showcase", goal="逐个介绍物品",
            plan=[dict(id="showcase", instruction="逐个介绍物品", completion_condition="用户结束")],
            current_step_id="showcase", speech_intent="好的，请展示物品。"))
        self.backend.finish_speech()
        self.serial = 0

    @property
    def state(self):
        return self.controller.state

    def view(self, scene=1, stable=2, advance=2, match=None, changed=False):
        self.clock.advance(advance)
        self.serial += 1
        self.backend.send(Event("view", evidence=Evidence(str(self.serial), self.state.camera_epoch,
            self.clock(), "memory://frame"), scene_id=scene, stable_seconds=stable,
            candidate_match_id=self.state.candidate_id if match is True else match,
            candidate_checked_id=self.state.candidate_id if self.state.candidate_fast else None,
            content_changed=changed))
        return self.backend.send(Event("tick"))

    def confirm(self, key="cup", scene=1, result="presented", covered="用途"):
        self.view(scene, changed=result == "detail")
        commands = self.backend.decide(observation(key, result, covered))
        self.assertEqual([c.kind for c in commands], ["tool_result"])
        self.view(scene)
        self.assertEqual(self.state.pending.context.purpose, "confirm")
        return self.backend.decide(dict(candidate_id=self.state.candidate_id,
            same_subject=True, still_presented=True))

    def test_three_objects_continue_without_user_turns_and_do_not_repeat(self):
        for scene, key in enumerate(("cup", "keys", "book"), 1):
            commands = self.confirm(key, scene)
            self.assertEqual([c.kind for c in commands], ["tool_result", "generate"])
            self.assertFalse(any(item[0] == key for item in self.state.introduced))
            self.backend.finish_speech()
        self.assertEqual([item[0] for item in self.state.introduced], ["cup", "keys", "book"])
        self.view(3, advance=self.controller.recheck_seconds)
        self.backend.decide(observation("book"))
        self.assertIsNone(self.state.pending)
        self.assertIsNone(self.state.candidate)
        self.assertEqual(self.backend.peak_generations, 1)

    def test_passed_views_do_not_trigger_and_new_view_discards_old_result(self):
        for scene in range(1, 8):
            self.assertFalse(self.view(scene, stable=0.3, advance=0.4))
        self.view(8)
        old = self.state.pending.id
        commands = self.view(9, stable=0, advance=0.3)
        self.assertIsNone(self.state.pending)
        self.backend.decide(observation(), request_id=old)
        self.assertIsNone(self.state.pending)
        self.assertFalse(self.state.introduced)

    def test_uncertain_and_background_decisions_remain_silent(self):
        self.view()
        commands = self.backend.decide(observation(result="wait"))
        self.assertEqual([c.kind for c in commands], ["tool_result"])
        self.assertFalse(self.view(advance=2))
        self.assertIsNone(self.state.pending)

    def test_candidate_must_survive_second_observation(self):
        self.view()
        self.backend.decide(observation())
        self.view(scene=2)
        commands = self.backend.decide(observation("keys"))
        self.assertEqual([c.kind for c in commands], ["tool_result"])
        self.assertEqual(self.state.candidate[0], "keys")

    def test_camera_movement_during_speech_finishes_current_then_uses_latest(self):
        self.confirm()
        request = self.state.pending.id
        self.view(2)
        self.assertEqual(self.state.pending.id, request)
        self.backend.finish_speech(playback=False)
        self.assertFalse(self.state.introduced)
        for _ in range(3):
            self.view(2)
            self.assertIsNone(self.state.pending)
        self.backend.send(Event("playback_done", segment_id=request, playback_source="server_playout"))
        self.assertEqual(self.state.introduced[0][0], "cup")
        self.view(2)
        self.assertEqual(self.state.pending.context.presentation["scene_id"], 2)

    def test_interrupted_speech_is_not_remembered_and_pause_resume_cancel(self):
        self.confirm()
        self.backend.finish_speech(playback=False)
        self.backend.send(Event("pause"))
        self.assertFalse(self.state.introduced)
        self.assertFalse(self.view())
        self.backend.send(Event("resume"))
        self.view()
        self.assertEqual(self.state.pending.context.purpose, "observe")
        self.backend.send(Event("cancel"))
        self.assertFalse(self.view(2))
        self.assertIsNone(self.state.pending)

    def test_detail_uses_known_identity_and_supplement_is_not_repeated(self):
        self.confirm()
        self.backend.finish_speech()
        self.confirm(result="detail", covered="杯底刻字")
        self.backend.finish_speech()
        self.view(advance=self.controller.recheck_seconds)
        self.backend.decide(observation(result="detail", covered="杯底刻字"))
        self.assertIsNone(self.state.pending)
        self.assertEqual(len(self.state.introduced), 1)

    def test_budget_defers_background_without_pausing_task(self):
        self.controller.limit = 2
        self.assertFalse(self.view())
        self.assertEqual(self.state.task.status, "waiting_action")
        self.assertIsNone(self.state.pending)

    def test_unplayed_candidate_cannot_be_labeled_as_known(self):
        self.view()
        self.backend.decide(observation())
        # A new observe assessment cannot claim an unplayed object is known.
        self.backend.send(Event("pause"))
        self.backend.send(Event("resume"))
        self.view()
        commands = self.backend.decide(dict(action="observe", presentation="known", object_key="cup"))
        self.assertFalse(commands[0].accepted)
        self.assertEqual(commands[0].reason, "known_requires_played_object_key")
        self.assertEqual(self.state.pending.context.purpose, "observe")
        self.assertFalse(self.state.introduced)

    def prepare_confirmation(self):
        self.view()
        self.backend.decide(observation())
        self.view()
        return self.state.candidate_id

    def test_confirmation_rejects_wrong_candidate_and_false_never_speaks(self):
        self.prepare_confirmation()
        commands = self.backend.decide(dict(candidate_id="wrong", same_subject=True, still_presented=True))
        self.assertEqual(commands[0].reason, "wrong_candidate")
        commands = self.backend.decide(dict(candidate_id=self.state.candidate_id,
            same_subject=True, still_presented=False))
        self.assertEqual([c.kind for c in commands], ["tool_result"])
        self.assertIsNone(self.state.pending)
        self.assertIsNone(self.state.candidate)
        self.assertFalse(self.state.introduced)

    def test_small_wobble_defers_confirmation_without_losing_candidate(self):
        token = self.prepare_confirmation()
        self.view(stable=.3, advance=.3)
        commands = self.backend.decide(dict(candidate_id=token, same_subject=True, still_presented=True))
        self.assertEqual(commands[0].reason, "view_changed")
        self.assertIsNone(self.state.pending)
        self.assertEqual(self.state.candidate_id, token)
        self.view()
        self.assertEqual(self.state.pending.context.purpose, "confirm")

    def test_real_scene_change_cancels_confirmation_and_discards_draft(self):
        token = self.prepare_confirmation()
        request = self.state.pending.id
        self.view(scene=2, stable=0)
        self.assertIsNone(self.state.candidate_speech)
        self.backend.decide(dict(candidate_id=token, same_subject=True, still_presented=True), request_id=request)
        self.assertIsNone(self.state.pending)
        self.assertFalse(self.state.introduced)

    def test_expired_video_does_not_authorize_confirmed_speech(self):
        token = self.prepare_confirmation()
        self.clock.advance(2)
        commands = self.backend.decide(dict(candidate_id=token, same_subject=True, still_presented=True))
        self.assertEqual(commands[0].reason, "view_changed")
        self.assertIsNone(self.state.pending)
        self.assertFalse(self.state.introduced)

    def test_closed_camera_or_stale_frame_cannot_start_observation(self):
        self.view(stable=0)
        self.clock.advance(10)
        self.assertFalse(self.backend.send(Event("tick")))
        self.backend.send(Event("camera_changed", camera_enabled=False))
        self.assertFalse(self.view())
        self.assertIsNone(self.state.pending)

    def test_observation_cannot_use_the_user_task_tool(self):
        self.view()
        commands = self.backend.send(Event("decision_done", request_id=self.state.pending.id,
            calls=(ToolCall("wrong-tool", "submit_task_decision", json.dumps(observation())),)))
        self.assertFalse(commands[0].accepted)
        self.assertEqual(commands[0].reason, "invalid_tool")
        self.assertFalse(self.state.introduced)

    def enable_fast(self):
        self.controller.early_observe_seconds = .5
        self.controller.fast_enabled = True
        self.controller.fast_dwell_seconds = 1.2

    def prepare_fast(self):
        self.enable_fast()
        self.view(stable=.6, advance=.6)
        data = observation()
        data["needs_confirmation"] = False
        self.backend.decide(data)

    def test_early_observation_overlaps_dwell_but_speech_waits_for_new_matching_frame(self):
        self.enable_fast()
        self.assertFalse(self.view(stable=.3, advance=.3))
        self.view(stable=.6, advance=.3)
        self.assertEqual(self.state.pending.context.purpose, "observe")
        data = observation()
        data["needs_confirmation"] = False
        self.backend.decide(data)
        self.assertIsNone(self.state.pending)
        self.view(stable=.9, advance=.3, match=True)
        self.assertIsNone(self.state.pending)
        self.view(stable=1.3, advance=.4, match=True)
        self.assertEqual(self.state.pending.context.purpose, "speak")
        self.assertEqual(self.state.segment.purpose, "presentation_fast")
        self.assertFalse(self.state.introduced)

    def test_uncertain_or_missing_matching_proof_retains_model_confirmation(self):
        self.prepare_fast()
        self.view(stable=1.3, advance=.7, match="old-candidate")
        self.assertIsNone(self.state.pending)
        self.view(stable=1.6, advance=.3, match=None)
        self.assertEqual(self.state.pending.context.purpose, "confirm")

    def test_default_observation_never_skips_confirmation(self):
        self.enable_fast()
        self.view(stable=.6, advance=.6)
        self.backend.decide(observation())
        self.view(stable=1.6, advance=1, match=True)
        self.assertEqual(self.state.pending.context.purpose, "confirm")

    def test_tick_before_first_post_decision_frame_does_not_prematurely_fallback(self):
        self.enable_fast()
        self.view()
        data = observation(); data["needs_confirmation"] = False
        self.backend.decide(data)
        self.backend.send(Event("tick"))
        self.assertIsNone(self.state.pending)
        self.view(advance=.33, match=True)
        self.assertEqual(self.state.segment.purpose, "presentation_fast")

    def test_sweep_before_first_audio_discards_fast_speech_but_playing_segment_can_finish(self):
        self.prepare_fast()
        self.view(advance=.33, match=True)
        self.assertEqual(self.state.pending.context.purpose, "speak")
        self.view(scene=2, stable=0, advance=.1)
        self.assertIsNone(self.state.pending)
        self.assertIsNone(self.state.segment)
        self.assertFalse(self.state.introduced)
        self.assertFalse(self.state.candidate_fast)
        self.view(scene=2)
        self.assertEqual(self.state.pending.context.purpose, "confirm")
        self.backend.decide(dict(candidate_id=self.state.candidate_id, same_subject=False, still_presented=False))
        self.view(scene=2, advance=3)
        data = observation("keys"); data["needs_confirmation"] = False
        self.backend.decide(data)
        self.view(scene=2, advance=.33, match=True)
        self.backend.send(Event("speech_started", request_id=self.state.pending.id))
        self.view(scene=3, stable=0)
        self.assertIsNotNone(self.state.segment)
        self.backend.finish_speech()
        self.assertEqual(self.state.introduced[0][0], "keys")

    def test_protocol_corrected_observation_cannot_take_fast_path(self):
        self.enable_fast()
        self.view()
        self.backend.send(Event("decision_done", request_id=self.state.pending.id))
        data = observation(); data["needs_confirmation"] = False
        self.backend.decide(data)
        self.view(match=True)
        self.assertEqual(self.state.pending.context.purpose, "confirm")

    def test_delayed_fast_check_closes_tool_once_and_preserves_full_confirmation(self):
        self.prepare_fast()
        self.clock.advance(2.1)
        commands = self.backend.send(Event("tick"))
        self.assertEqual([c.kind for c in commands], ["tool_result"])
        self.assertEqual(commands[0].reason, "fast_check_timeout")
        self.assertIsNone(self.state.candidate_call_id)
        self.assertFalse(self.backend.send(Event("tick")))
        self.view()
        self.assertEqual(self.state.pending.context.purpose, "confirm")

    def test_changed_content_can_wake_idle_observation_but_keeps_cooldown(self):
        self.enable_fast()
        self.confirm()
        self.backend.finish_speech()
        self.view(advance=self.controller.recheck_seconds)
        self.backend.decide(dict(action="observe", presentation="known", object_key="cup"))
        self.assertFalse(self.view(advance=.5, changed=True))
        self.view(advance=2, changed=True)
        self.assertEqual(self.state.pending.context.purpose, "observe")

    def test_uncertain_background_rechecks_back_off_after_two_short_rechecks(self):
        self.enable_fast()
        for expected_delay in (2, 2, 12):
            self.view(advance=2)
            self.backend.decide(observation(result="wait"))
            self.assertAlmostEqual(self.state.due_at - self.clock(), expected_delay)
        self.assertFalse(self.view(advance=2))

    def test_speech_that_already_started_is_never_automatically_replayed(self):
        self.prepare_fast()
        self.view(advance=.33, match=True)
        request = self.state.pending.id
        self.backend.send(Event("speech_started", request_id=request))
        self.backend.send(Event("speech_done", request_id=request,
            calls=(ToolCall("unexpected", "submit_task_decision", "{}"),)))
        self.assertIsNone(self.state.pending)
        self.assertEqual(self.state.task.status, "paused")

    def test_brief_clear_observation_drafts_only_in_speech_and_records_played_content(self):
        self.enable_fast()
        self.view(stable=.6, advance=.6)
        self.backend.decide(dict(action="observe", presentation="presented", object_key="cup",
                                object_summary="蓝色带柄杯子", needs_confirmation=False))
        self.view(advance=.8, stable=1.4, match=True)
        self.assertTrue(self.state.pending.context.speech_intent.startswith("介绍：蓝色带柄杯子"))
        request = self.state.pending.id
        self.backend.send(Event("speech_done", request_id=request, text="这是蓝色带柄杯子，可以盛放饮品。"))
        self.assertFalse(self.state.introduced)
        self.backend.send(Event("playback_done", segment_id=request, playback_source="server_playout"))
        self.assertEqual(self.state.introduced[0][2], "这是蓝色带柄杯子，可以盛放饮品。")
