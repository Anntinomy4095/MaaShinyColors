"""Match preparation must advance from observed state, never from a stale action."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from agent.core import match_task as mt
from agent.core import match_progress as mp
from tests.test_task_launcher import HAVE_MAA


def profile():
    return mp.MatchProgress("offline-fixture-cycle", "ready", True,
                            tuple(mp.StageProgress(f"A1-S{i}", i, True) for i in mt.SLOTS))


def dialog(*, count=0, available=20, thumb=(193, 558)):
    return mt.DialogView(tuple(mt.Slot(i, True, (1, i), count if i == 6 else 0, True)
                               for i in mt.SLOTS), (count, available), thumb)


class MatchPlannerEvidenceTests(unittest.TestCase):
    def planner(self):
        return mt.SkipPlanner(mt.MatchConfig(count=2, sample_only=False), profile())

    def test_unchanged_count_exhausts_wait_budget_without_another_plus(self):
        planner = self.planner()
        self.assertEqual(planner.step(dialog()).kind, "plus")
        for _ in range(mt.MAX_UNREADABLE_FRAMES):
            self.assertEqual(planner.step(dialog()).kind, "wait")
        decision = planner.step(dialog())
        self.assertEqual((decision.kind, decision.outcome, planner.pressed), ("abort", "counter_mismatch", 1))

    def test_count_progress_resets_the_wait_budget(self):
        planner = self.planner()
        planner.step(dialog())
        for _ in range(mt.MAX_UNREADABLE_FRAMES):
            self.assertEqual(planner.step(dialog()).kind, "wait")
        self.assertEqual(planner.step(dialog(count=1)).kind, "plus")
        for _ in range(mt.MAX_UNREADABLE_FRAMES):
            self.assertEqual(planner.step(dialog(count=1)).kind, "wait")
        self.assertEqual(planner.step(dialog(count=2)).kind, "confirm")

    def test_unknown_scrollbar_never_proves_latest_stage(self):
        planner = self.planner()
        unknown = dialog(thumb=None)
        self.assertFalse(unknown.at_top())
        self.assertFalse(unknown.at_bottom())
        for _ in range(mt.MAX_UNREADABLE_FRAMES):
            self.assertEqual(planner.step(unknown).kind, "wait")
        decision = planner.step(unknown)
        self.assertEqual((decision.kind, decision.outcome), ("abort", "dialog_unreadable"))
        self.assertFalse(planner.scan_done)
        self.assertIsNone(planner.target)
        self.assertEqual(planner.pressed, 0)


@unittest.skipUnless(HAVE_MAA, "需要 MaaFramework Python 依赖")
class MatchRuntimeEvidenceTests(unittest.TestCase):
    class Frame:
        def tobytes(self):
            return b"same observed pixels"

    def runtime(self, view=None, rights=(20, 20)):
        from agent.core.match_custom import MatchRuntime
        progress = profile()
        runtime = MatchRuntime(progress=progress)
        runtime.progress_reader = lambda context, image: mp.ProgressObservation(
            progress.cycle_id, "ready", mt.tc.image_digest(image), runtime.clock())
        runtime.run = mt.MatchRun(mt.MatchConfig(count=2, sample_only=False), runtime.log, progress)
        runtime.run.phase, runtime.run.rights = "planning", rights
        runtime.read_dialog = lambda context, image: dialog() if view is None else view
        return runtime

    def test_same_frame_queries_share_decision_but_next_observation_does_not_repeat_it(self):
        runtime, frame = self.runtime(), self.Frame()
        first = runtime.decision(None, frame)
        self.assertEqual(first.kind, "plus")
        self.assertIs(runtime.decision(None, frame), first)
        self.assertEqual(runtime.run.planner.pressed, 1)
        for _ in range(mt.MAX_UNREADABLE_FRAMES):
            self.assertTrue(runtime.advance(None, {}))
            decision = runtime.decision(None, frame)
            self.assertEqual(decision.kind, "wait")
            self.assertIs(runtime.decision(None, frame), decision)
        runtime.advance(None, {})
        decision = runtime.decision(None, frame)
        self.assertEqual((decision.kind, decision.outcome, runtime.run.planner.pressed),
                         ("abort", "counter_mismatch", 1))

    def test_impossible_or_decreased_rights_stop_before_preparation(self):
        for available in (99, 19):
            with self.subTest(available=available):
                runtime = self.runtime(dialog(available=available))
                decision = runtime.decision(None, self.Frame())
                self.assertEqual((decision.kind, decision.outcome), ("abort", "rights_changed_or_insufficient"))
                self.assertEqual(runtime.run.planner.pressed, 0)
                self.assertIsNone(runtime.run.intent)

    def test_changed_rights_within_capacity_still_require_reobservation(self):
        runtime = self.runtime(dialog(available=20), rights=(18, 20))
        self.assertEqual(runtime.decision(None, self.Frame()).outcome, "rights_changed_or_insufficient")
        self.assertEqual(runtime.run.planner.pressed, 0)

    def test_matching_rights_are_accepted(self):
        runtime = self.runtime(dialog(available=18), rights=(18, 20))
        self.assertEqual(runtime.decision(None, self.Frame()).kind, "plus")

    def test_unknown_main_capacity_never_opens_the_skip_dialog(self):
        runtime = self.runtime()
        runtime.run.phase = "active"
        runtime.vision = lambda context: SimpleNamespace(number=lambda *args: "20/99")
        for _ in range(mt.MAX_UNREADABLE_FRAMES + 1):
            self.assertFalse(runtime._open_skip(None, self.Frame()))
        self.assertEqual(runtime.run.outcome, "rights_unreadable")

    def test_result_requires_the_same_verified_resource_capacity(self):
        runtime = self.runtime(rights=(18, 20))
        runtime.log = Mock()
        runtime.run.phase, runtime.run.intent = "awaiting_result", {"rights_before": [2, 18]}
        runtime.run.saver = SimpleNamespace(saved=[], save=lambda image: None)
        vision = SimpleNamespace(hit=lambda *args: True, number=lambda *args: "16/99")
        runtime.vision = lambda context: vision
        self.assertFalse(runtime.watch(None, self.Frame(), {}))
        runtime.log.append.assert_not_called()
        vision.number = lambda *args: "16/20"
        self.assertTrue(runtime.watch(None, self.Frame(), {}))
        self.assertEqual(runtime.run.outcome, "skipped")
        self.assertEqual(runtime.log.append.call_args.args[0]["rights_after"], [16, 20])


if __name__ == "__main__":
    unittest.main()
