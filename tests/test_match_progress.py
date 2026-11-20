"""Match selection binds to player progress and current-cycle evidence, independently of list geometry."""
import copy
import dataclasses
import unittest
from unittest.mock import Mock

from agent.core import match_progress as mp
from agent.core import match_task as mt
from tests import test_match_safety as safety
from tests.test_task_launcher import HAVE_MAA


FRAME = "ab" * 32
dialog, profile = safety.dialog, safety.profile


def progress_data():
    return {"cycle_id": "offline-cycle-A", "state": "ready", "stage_list_complete": True,
            "stages": [{"stage_id": "A1-S11", "stage_order": 1, "skip_available": True},
                       {"stage_id": "A2-S1", "stage_order": 2, "skip_available": True}]}


class MatchProgressContractTests(unittest.TestCase):
    def test_strict_profile_rejects_duplicate_identity_order_and_invalid_fields(self):
        source = progress_data()
        self.assertEqual(mp.MatchProgress.from_dict(source).to_dict(), source)
        for key, value in (("stage_id", "A1-S11"), ("stage_order", 1), ("stage_order", True),
                           ("stage_order", 0), ("skip_available", "yes"), ("stage_id", "A0-S1")):
            data = copy.deepcopy(source)
            data["stages"][1][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                mp.MatchProgress.from_dict(data)
        with self.assertRaises(ValueError):
            mp.MatchProgress.from_dict({**source, "input_authorized": True})
        with self.assertRaises(ValueError):
            mt.MatchConfig.from_params({"progress": source})

    def test_missing_or_incomplete_progress_cannot_be_replaced_by_a_complete_view(self):
        for progress in (None, dataclasses.replace(profile(), stage_list_complete=False),
                         dataclasses.replace(profile(), state="unknown"), dataclasses.replace(profile(), stages=())):
            planner = mt.SkipPlanner(mt.MatchConfig(sample_only=False), progress)
            decision = planner.step(dialog())
            with self.subTest(progress=progress):
                self.assertEqual(decision.kind, "abort")
                self.assertEqual(planner.pressed, 0)

    def test_latest_uses_trusted_order_across_areas_and_screen_permutations(self):
        progress = mp.MatchProgress.from_dict(progress_data())
        for stages in (((1, 11), (2, 1)), ((2, 1), (1, 11))):
            view = mt.DialogView(tuple(mt.Slot(i, True, stage, 0, True) for i, stage in enumerate(stages, 1)),
                                 (0, 20), (193, 558))
            planner = mt.SkipPlanner(mt.MatchConfig(count=1, sample_only=False), progress)
            decision = planner.step(view)
            with self.subTest(stages=stages):
                self.assertEqual((decision.kind, planner.target), ("plus", (2, 1)))
                self.assertEqual(decision.slot, stages.index((2, 1)) + 1)

    def test_trusted_unskippable_latest_never_falls_back_to_older_visible_stage(self):
        data = progress_data()
        data["stages"][1]["skip_available"] = False
        progress = mp.MatchProgress.from_dict(data)
        view = mt.DialogView((mt.Slot(1, True, (1, 11), 0, True),),
                             (0, 20), (193, 558))
        planner = mt.SkipPlanner(mt.MatchConfig(count=1, sample_only=False), progress)
        self.assertEqual(planner.step(view).outcome, "latest_not_skippable")
        self.assertEqual(planner.pressed, 0)
        explicit = mt.SkipPlanner(mt.MatchConfig(stage="A1-S11", count=1, sample_only=False), progress)
        self.assertEqual(explicit.step(view).slot, 1)

    def test_open_twenty_skip_fifteen_does_not_fall_back_to_fifteen(self):
        progress = mp.MatchProgress("offline-cycle", "ready", True, tuple(
            mp.StageProgress(f"A1-S{i}", i, i <= 15) for i in range(1, 21)))
        def view(stages, thumb):
            return mt.DialogView(tuple(mt.Slot(slot, True, (1, i), 0, True)
                                       for slot, i in enumerate(stages, 1)), (0, 20), thumb)
        top = view(range(1, 7), (193, 326))
        middle = view(range(5, 11), (300, 433))
        bottom = view(range(10, 16), (426, 558))
        latest = mt.SkipPlanner(mt.MatchConfig(count=1, sample_only=False), progress)
        self.assertEqual(latest.step(top).kind, "scroll_down")
        self.assertEqual(latest.step(middle).kind, "scroll_down")
        self.assertEqual(latest.step(bottom).outcome, "latest_not_skippable")
        self.assertEqual(latest.pressed, 0)
        explicit = mt.SkipPlanner(mt.MatchConfig(stage="A1-S15", count=1, sample_only=False), progress)
        explicit.step(top)
        explicit.step(middle)
        self.assertEqual(explicit.step(bottom).slot, 6)
        for rows in (bottom.slots[:-1], (*bottom.slots[:-1], mt.Slot(6, True, (1, 16), 0, True))):
            incomplete = mt.SkipPlanner(mt.MatchConfig(stage="A1-S15", count=1, sample_only=False), progress)
            incomplete.step(top)
            incomplete.step(middle)
            self.assertEqual(incomplete.step(mt.DialogView(rows, (0, 20), (426, 558))).outcome,
                             "stage_evidence_incomplete")
            self.assertEqual(incomplete.pressed, 0)

    def test_missing_stage_or_duplicate_rows_do_not_prove_complete_progress(self):
        for slots in (dialog().slots[:-1], (dialog().slots[0], dialog().slots[0])):
            planner = mt.SkipPlanner(mt.MatchConfig(count=1, sample_only=False), profile())
            decision = planner.step(mt.DialogView(slots, (0, 20), (193, 558)))
            self.assertEqual((decision.kind, decision.outcome), ("abort", "stage_evidence_incomplete"))
            self.assertEqual(planner.pressed, 0)

    def test_starting_at_bottom_requires_positive_list_start(self):
        planner = mt.SkipPlanner(mt.MatchConfig(count=1, sample_only=False), profile())
        self.assertEqual(planner.step(dialog(thumb=(430, 558))).outcome, "stage_evidence_incomplete")
        self.assertFalse(planner.scan_started)
        self.assertEqual(planner.scrolls, 0)

    def test_duplicate_target_after_preparation_cannot_receive_another_plus(self):
        planner = mt.SkipPlanner(mt.MatchConfig(count=2, sample_only=False), profile())
        self.assertEqual(planner.step(dialog()).kind, "plus")
        duplicate = mt.Slot(1, True, (1, 6), 1, True)
        decision = planner.step(mt.DialogView((duplicate, duplicate), (1, 20), (193, 558)))
        self.assertEqual(decision.outcome, "stage_evidence_incomplete")
        self.assertEqual(planner.pressed, 1)

    def test_reset_stops_even_when_all_numbered_rows_are_present(self):
        progress = dataclasses.replace(profile(), state="reset")
        planner = mt.SkipPlanner(mt.MatchConfig(count=1, sample_only=False), progress)
        self.assertEqual(planner.step(dialog()).outcome, "progress_reset")
        self.assertEqual(planner.pressed, 0)

    def test_cycle_proof_requires_current_cycle_exact_image_and_fresh_timestamp(self):
        progress = profile()
        good = mp.ProgressObservation(progress.cycle_id, "ready", FRAME, 100.0)
        self.assertIsNone(mp.progress_failure(progress, good, FRAME, 100.0))
        cases = [(None, "progress_unreadable"),
                 (dataclasses.replace(good, state="reset"), "progress_reset"),
                 (dataclasses.replace(good, state="unknown"), "progress_unreadable"),
                 (dataclasses.replace(good, cycle_id="new-cycle"), "progress_cycle_changed"),
                 (dataclasses.replace(good, frame_digest="cd" * 32), "progress_frame_mismatch"),
                 (dataclasses.replace(good, observed_at=94.9), "progress_stale"),
                 (dataclasses.replace(good, observed_at=100.1), "progress_stale"),
                 (dataclasses.replace(good, observed_at=float("nan")), "progress_stale"),
                 (dataclasses.replace(good, observed_at=True), "progress_stale")]
        for observation, outcome in cases:
            with self.subTest(observation=observation):
                self.assertEqual(mp.progress_failure(progress, observation, FRAME, 100.0), outcome)


@unittest.skipUnless(HAVE_MAA, "需要 MaaFramework Python 依赖")
class MatchProgressRuntimeTests(unittest.TestCase):
    def runtime(self):
        return safety.MatchRuntimeEvidenceTests().runtime()

    def test_default_runtime_has_no_live_cycle_reader(self):
        from agent.core.match_custom import MatchRuntime
        runtime = MatchRuntime()
        self.assertIsNone(runtime.progress)
        self.assertIsNone(runtime.progress_reader)

    def test_cycle_change_after_plus_stops_without_second_preparation(self):
        runtime, frame = self.runtime(), safety.MatchRuntimeEvidenceTests.Frame()
        self.assertEqual(runtime.decision(None, frame).kind, "plus")
        runtime.advance(None, {})
        runtime.progress_reader = lambda context, image: mp.ProgressObservation(
            "new-cycle", "ready", mt.tc.image_digest(image), runtime.clock())
        decision = runtime.decision(None, frame)
        self.assertEqual((decision.kind, decision.outcome, runtime.run.planner.pressed),
                         ("abort", "progress_cycle_changed", 1))
        self.assertIsNone(runtime.run.intent)

    def test_profile_change_during_run_invalidates_existing_selection(self):
        runtime, frame = self.runtime(), safety.MatchRuntimeEvidenceTests.Frame()
        runtime.progress = dataclasses.replace(runtime.progress, cycle_id="replacement-profile")
        self.assertEqual(runtime.decision(None, frame).outcome, "progress_changed")
        self.assertEqual(runtime.run.planner.pressed, 0)

    def test_reset_between_confirm_decision_and_grant_writes_no_intent(self):
        runtime, frame = self.runtime(), safety.MatchRuntimeEvidenceTests.Frame()
        runtime.read_dialog = lambda context, image: dialog(count=2)
        planner = runtime.run.planner
        planner.scan_done, planner.target, planner.pressed = True, (1, 6), 2
        calls = []

        def reader(context, image):
            calls.append(True)
            return mp.ProgressObservation(runtime.progress.cycle_id, "ready" if len(calls) == 1 else "reset",
                                          mt.tc.image_digest(image), runtime.clock())

        runtime.progress_reader = reader
        runtime.can_spend = lambda: True
        runtime._log = Mock(return_value=True)
        runtime.authorize = Mock(return_value=None)
        self.assertFalse(runtime.check(None, frame, {"stage": "confirm"}))
        self.assertEqual(runtime.run.abort_outcome, "progress_reset")
        self.assertIsNone(runtime.run.intent)
        runtime._log.assert_not_called()
        runtime.authorize.assert_not_called()

    def test_reader_failure_is_a_stop_not_a_fallback(self):
        runtime, frame = self.runtime(), safety.MatchRuntimeEvidenceTests.Frame()
        runtime.progress_reader = Mock(side_effect=ValueError("unreadable cycle"))
        self.assertEqual(runtime.decision(None, frame).outcome, "progress_unreadable")
        self.assertEqual(runtime.run.planner.pressed, 0)


if __name__ == "__main__":
    unittest.main()
