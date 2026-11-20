"""Explicit live Match rows use current eligibility and actual moving targets."""
import dataclasses
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

import numpy as np

from agent.core import match_task as mt
from agent.core.match_observed import ObservedSkipPlanner
from tests.test_match_scan import page
from tests.maa_probe import NodeProbe, maa_ready

NATIVE = "dev_assets/private/sample-live-20261011/"
NATIVE_PAGES = tuple(NATIVE + name + ".png" for name in (
    "46-match-skip-top", "49-match-skip-5-10-complete", "50-match-skip-overlap-9-14",
    "51-match-skip-bottom-11-15"))
REAL5_REVIEW = "dev_assets/private/live-20261012k/after-real5.png"
REVIEW_9 = "dev_assets/private/wing_samples/2026-10-31/20261031T045822226176Z-review9.png"
REAL5_AFTER = "dev_assets/private/live-20261012k/after-user-skip.png"


class ObservedPlannerTests(unittest.TestCase):
    def planner(self, stage="A1-S5", count=2):
        return ObservedSkipPlanner(mt.MatchConfig(stage=stage, count=count, count_mode="fixed", sample_only=False))

    def scanned(self, planner):
        self.assertEqual(planner.step(page((1, 2, 3), (193, 326), 1)).kind, "scroll_down")
        self.assertEqual(planner.step(page((2, 3, 4), (300, 433), 2)).kind, "scroll_down")
        return page((3, 4, 5), (426, 560), 3)

    def test_explicit_target_scans_then_verifies_each_increment(self):
        planner = self.planner()
        bottom = self.scanned(planner)
        self.assertEqual((planner.step(bottom).kind, planner.pressed), ("plus", 1))
        self.assertEqual(planner.step(bottom).kind, "wait")
        one = dataclasses.replace(bottom, cost=(1, 20), rows=(*bottom.rows[:2], dataclasses.replace(bottom.rows[2], count=1)))
        self.assertEqual(planner.step(one).kind, "plus")
        two = dataclasses.replace(bottom, cost=(2, 20), rows=(*bottom.rows[:2], dataclasses.replace(bottom.rows[2], count=2)))
        self.assertEqual(planner.step(two).kind, "confirm")
        proof = planner.evidence()
        self.assertTrue(proof["skip_list_complete"])
        self.assertIsNone(proof["latest_stage"])
        self.assertIsNone(proof["cycle_id"])

    def test_double_counted_plus_landing_on_the_plan_confirms(self):
        # 2026-11-21 live: one ＋ tap counted twice (18 -> 20 of 20); ＋ is disabled at the cap.
        planner = self.planner(count=3)
        bottom = self.scanned(planner)
        self.assertEqual(planner.step(bottom).kind, "plus")
        one = dataclasses.replace(bottom, cost=(1, 20), rows=(*bottom.rows[:2], dataclasses.replace(bottom.rows[2], count=1)))
        self.assertEqual(planner.step(one).kind, "plus")
        three = dataclasses.replace(bottom, cost=(3, 20), rows=(*bottom.rows[:2],
                                                              dataclasses.replace(bottom.rows[2], count=3, plus_enabled=False)))
        self.assertEqual(planner.step(three).kind, "confirm")
        # beyond the plan still stops
        planner = self.planner(count=3)
        bottom = self.scanned(planner)
        planner.step(bottom)
        four = dataclasses.replace(bottom, cost=(4, 20), rows=(*bottom.rows[:2], dataclasses.replace(bottom.rows[2], count=4)))
        self.assertEqual(planner.step(four).outcome, "counter_mismatch")

    def test_unread_counters_of_other_rows_are_zero_when_the_total_matches(self):
        # 2026-11-21 live: rows 1–2 read their stage but no counter; cost 0/20 and the target row 0 -> they are 0.
        planner = self.planner()
        bottom = self.scanned(planner)
        blind = dataclasses.replace(bottom, rows=(dataclasses.replace(bottom.rows[0], count=None),
                                                  dataclasses.replace(bottom.rows[1], count=None), bottom.rows[2]))
        self.assertEqual(planner.step(blind).kind, "plus")
        # the total disagrees with the target row: still unread
        planner = self.planner()
        bottom = self.scanned(planner)
        odd = dataclasses.replace(blind, cost=(1, 20))
        self.assertEqual(planner.step(odd).kind, "wait")

    def test_latest_is_never_inferred_from_skip_list(self):
        for stage in ("latest", "latest_vo"):
            with self.assertRaises(ValueError):
                self.planner(stage)

    def test_requested_stage_absent_never_falls_back(self):
        planner = self.planner("A1-S20")
        self.assertEqual(planner.step(self.scanned(planner)).outcome, "stage_unavailable")
        self.assertEqual(planner.pressed, 0)

    def test_missing_overlap_fails_before_any_selection(self):
        planner = self.planner()
        planner.step(page((1, 2), (193, 326), 1))
        decision = planner.step(page((3, 4, 5), (426, 560), 2))
        self.assertEqual(decision.outcome, "stage_evidence_incomplete")
        self.assertEqual(planner.pressed, 0)

    def test_target_before_bottom_scrolls_back_before_selecting(self):
        planner = self.planner("A1-S1")
        self.assertEqual(planner.step(self.scanned(planner)).kind, "scroll_up")
        self.assertEqual(planner.step(page((1, 2, 3), (193, 326), 4)).kind, "plus")

    def test_changed_rights_or_other_row_selection_stops(self):
        for changed in ("rights", "other"):
            planner = self.planner()
            bottom = self.scanned(planner)
            planner.step(bottom)
            one = dataclasses.replace(bottom, cost=(1, 19 if changed == "rights" else 20),
                                      rows=(*bottom.rows[:2], dataclasses.replace(bottom.rows[2], count=1)))
            if changed == "other":
                one = dataclasses.replace(one, rows=(dataclasses.replace(one.rows[0], count=1), *one.rows[1:]))
            self.assertEqual(planner.step(one).kind, "abort")

    def test_runtime_uses_current_plus_box_and_no_invented_progress(self):
        from agent.core.match_custom import MatchRuntime, MatchPlanRecognition
        runtime = MatchRuntime()
        runtime.run = mt.MatchRun(mt.MatchConfig(stage="A1-S5", count=1, sample_only=False), runtime.log)
        runtime.started(runtime.run)
        planner = runtime.run.planner
        self.assertIsInstance(planner, ObservedSkipPlanner)
        bottom = self.scanned(planner)
        moved = dataclasses.replace(bottom.rows[2], plus_box=(575, 487, 20, 16))
        bottom = dataclasses.replace(bottom, rows=(*bottom.rows[:2], moved))
        runtime.run.phase, runtime.run.rights = "planning", (20, 20)
        context = SimpleNamespace(override_pipeline=Mock(return_value=True))
        frame = np.zeros((720, 1280, 3), np.uint8)
        argv = SimpleNamespace(image=frame, roi=(0, 0, 1280, 720),
                               custom_recognition_param='{"step":"plus","slot":3}')
        with patch("agent.core.match_scan.read_skip_page", return_value=bottom):
            result = MatchPlanRecognition(runtime, "plan").analyze(context, argv)
        self.assertEqual(tuple(result.box), (575, 487, 20, 16))
        context.override_pipeline.assert_not_called()

    def test_legacy_planner_returns_its_verified_fixed_plus_box(self):
        from agent.core.match_custom import MatchRuntime, MatchPlanRecognition
        runtime = MatchRuntime()
        runtime.run = mt.MatchRun(mt.MatchConfig(stage="A1-S5", sample_only=False), runtime.log)
        runtime.plan = Mock(return_value=True)
        argv = SimpleNamespace(image=None, roi=(0, 0, 1280, 720),
                               custom_recognition_param='{"step":"plus","slot":3}')
        result = MatchPlanRecognition(runtime, "plan").analyze(None, argv)
        self.assertEqual(tuple(result.box), tuple(mt.SLOT_PLUS[3]))


class MatchReviewTests(unittest.TestCase):
    def runtime(self):
        from agent.core.match_custom import MatchRuntime
        runtime = MatchRuntime()
        runtime.run = mt.MatchRun(mt.MatchConfig(stage="A5-S15", count=1, count_mode="fixed", sample_only=False), runtime.log)
        runtime.run.phase, runtime.run.rights = "planning", (19, 20)
        runtime.run.planner = ObservedSkipPlanner(runtime.run.config)
        runtime.run.planner.pressed = 1
        runtime.decision = Mock(return_value=mt.Decision("confirm", slot=5))
        runtime.read_dialog = Mock(return_value=mt.DialogView((mt.Slot(5, True, (5, 15), 1, True),), (1, 19)))
        runtime._progress_failure = Mock(return_value=None)
        runtime.can_spend = Mock(return_value=True)
        runtime.authorize = Mock(return_value=None)
        runtime._log = Mock(return_value=True)
        return runtime

    def test_first_button_prepares_without_grant_then_final_start_binds_intent(self):
        runtime = self.runtime()
        image = np.zeros((720, 1280, 3), np.uint8)
        self.assertTrue(runtime.check(None, image, {"stage": "confirm"}))
        self.assertEqual(runtime.run.phase, "review")
        self.assertIsNone(runtime.run.intent)
        runtime.authorize.assert_not_called()
        self.assertEqual(runtime._log.call_args.args[0]["event"], "prepared")
        runtime.read_review = Mock(return_value=((5, 15), 1, (1, 19)))
        self.assertTrue(runtime.check(None, image, {"stage": "start"}))
        self.assertEqual(runtime.run.phase, "awaiting_result")
        self.assertIn("time", runtime.run.intent)
        runtime.authorize.assert_called_once_with(image, mt.APPROVER)
        self.assertFalse(runtime.check(None, image, {"stage": "start"}))

    def test_changed_final_stage_count_or_rights_never_authorizes(self):
        image = np.zeros((720, 1280, 3), np.uint8)
        for review in (((5, 14), 1, (1, 19)), ((5, 15), 2, (2, 19)), ((5, 15), 1, (1, 18))):
            with self.subTest(review=review):
                runtime = self.runtime()
                self.assertTrue(runtime.check(None, image, {"stage": "confirm"}))
                runtime.read_review = Mock(return_value=review)
                self.assertFalse(runtime.check(None, image, {"stage": "start"}))
                runtime.authorize.assert_not_called()
                self.assertIsNone(runtime.run.intent)

    def test_unsettled_intents_are_noted_and_never_block_the_run(self):
        # Only exact settled intents are quiet; the rest add a note, the run plans from screen.
        for resolution, reference, unresolved in (("verified", "t1", False), ("not_spent", "t1", False),
                                                  ("verified", "other", True), ("uncertain", "t1", True)):
            with self.subTest(resolution=resolution, reference=reference):
                runtime = self.runtime()
                runtime.log = Mock(events=Mock(return_value=[{"event": "intent", "time": "t1"},
                                                            {"event": resolution, "intent_time": reference}]))
                runtime.started(runtime.run)
                self.assertFalse(runtime.run.finished)
                self.assertEqual("unresolved_skip_batches_1" in runtime.run.notes, unresolved)



@unittest.skipUnless(maa_ready(*NATIVE_PAGES), "需要 Maa、OCR 模型及 10-11 原生 Match 滚动帧")
class NativeObservedRowsTests(unittest.TestCase):
    @unittest.skipUnless(maa_ready("dev_assets/private/live-20261019/match-review-one.png"), "需要本轮1/1最终确认帧")
    def test_current_low_rights_final_review_reads_one_of_one(self):
        from maa.custom_recognition import CustomRecognition
        from agent.core.match_custom import MatchRuntime
        found = []
        runtime = MatchRuntime()

        class Probe(CustomRecognition):
            def analyze(self, context, argv):
                found.append(runtime.read_review(context, argv.image))
                return CustomRecognition.AnalyzeResult(box=(0, 0, 1, 1), detail="read_only")

        NodeProbe.resource().register_custom_recognition("SC.Test.MatchReviewOne", Probe())
        NodeProbe().run("dev_assets/private/live-20261019/match-review-one.png", {
            "SC.Test.Start": {"next": ["SC.Test.MatchReviewOne"]},
            "SC.Test.MatchReviewOne": {"recognition": "Custom", "custom_recognition": "SC.Test.MatchReviewOne"}})
        self.assertEqual(found, [((5, 15), 1, (1, 1))])

    @unittest.skipUnless(maa_ready(REVIEW_9), "需要 10-31 9/9 最终确认帧")
    def test_review_whose_slash_reads_as_one(self):
        """Live 2026-10-31: A5-S15 x9 review, the fixed field read "9 / 9" as "919" -> cost None -> counter_mismatch."""
        from maa.custom_recognition import CustomRecognition
        from agent.core.match_custom import MatchRuntime
        found = []
        runtime = MatchRuntime()

        class Probe(CustomRecognition):
            def analyze(self, context, argv):
                found.append(runtime.read_review(context, argv.image))
                return CustomRecognition.AnalyzeResult(box=(0, 0, 1, 1), detail="read_only")

        NodeProbe.resource().register_custom_recognition("SC.Test.MatchReview9", Probe())
        NodeProbe().run(REVIEW_9, {"SC.Test.Start": {"next": ["SC.Test.MatchReview9"]},
                                   "SC.Test.MatchReview9": {"recognition": "Custom",
                                                            "custom_recognition": "SC.Test.MatchReview9"}})
        self.assertEqual(found, [((5, 15), 9, (9, 9))])

    @unittest.skipUnless(maa_ready(NATIVE + "68-match-final-confirm.png", NATIVE + "70-match-skip-receipt.png"), "需要原生确认与结果帧")
    def test_native_final_review_and_receipt_guards(self):
        from maa.custom_recognition import CustomRecognition
        from agent.core.match_custom import MatchRuntime
        from agent.core.task_custom import Vision
        found = []
        runtime = MatchRuntime()

        class Probe(CustomRecognition):
            def analyze(self, context, argv):
                found.append(runtime.read_review(context, argv.image))
                return CustomRecognition.AnalyzeResult(box=(0, 0, 1, 1), detail="read_only")

        resource = NodeProbe.resource()
        resource.register_custom_recognition("SC.Test.MatchReview", Probe())
        NodeProbe().run(NATIVE + "68-match-final-confirm.png", {
            "SC.Test.Start": {"next": ["SC.Test.MatchReview"]},
            "SC.Test.MatchReview": {"recognition": "Custom", "custom_recognition": "SC.Test.MatchReview"}})
        self.assertEqual(found, [((5, 15), 1, (1, 19))])
        self.assertTrue(NodeProbe().hits(NATIVE + "70-match-skip-receipt.png", "SC.Match.Result.Guard"))
        self.assertFalse(NodeProbe().hits(NATIVE + "68-match-final-confirm.png", "SC.Match.Result.Guard"))
        self.assertFalse(NodeProbe().hits(NATIVE + "70-match-skip-receipt.png", mt.REVIEW_GUARD))

    @unittest.skipUnless(maa_ready(REAL5_REVIEW), "需要 real5 最终确认帧")
    def test_real5_dimmed_two_digit_review_verifies_then_binds_intent(self):
        """Live real5 2026-10-13: A5-S15 x20 review, dimmed "20 / 20" scored 0.905 -> cost None -> counter_mismatch."""
        from maa.custom_recognition import CustomRecognition
        from agent.core.match_custom import MatchRuntime
        found, image = [], []
        runtime = MatchRuntime()

        class Probe(CustomRecognition):
            def analyze(self, context, argv):
                found.append(runtime.read_review(context, argv.image))
                image.append(argv.image.copy())
                return CustomRecognition.AnalyzeResult(box=(0, 0, 1, 1), detail="read_only")

        NodeProbe.resource().register_custom_recognition("SC.Test.MatchReview20", Probe())
        NodeProbe().run(REAL5_REVIEW, {"SC.Test.Start": {"next": ["SC.Test.MatchReview20"]},
                                      "SC.Test.MatchReview20": {"recognition": "Custom",
                                                                "custom_recognition": "SC.Test.MatchReview20"}})
        self.assertEqual(found, [((5, 15), 20, (20, 20))])
        for config_count, rights, accepted in ((20, (20, 20), True), (19, (20, 20), False), (20, (19, 20), False)):
            with self.subTest(count=config_count, rights=rights):
                run = MatchRuntime()
                run.run = mt.MatchRun(mt.MatchConfig(stage="A5-S15", count=config_count, count_mode="fixed",
                                                     sample_only=False), run.log)
                run.run.phase, run.run.rights = "review", rights
                run.run.prepared = {"stage": "A5-S15", "count": config_count, "rights_before": [20, 20]}
                run.read_review = Mock(return_value=found[0])
                run.can_spend = Mock(return_value=True)
                run.authorize = Mock(return_value=None)
                run._log = Mock(return_value=True)
                self.assertEqual(run._check_start(None, image[0]), accepted)
                self.assertEqual(run.authorize.called, accepted)

    @unittest.skipUnless(maa_ready(REAL5_AFTER), "需要 real5 跳过后主页帧")
    def test_real5_post_skip_main_page_verifies_rights_minus_count(self):
        """User pressed スキップ開始 on after-real5; Match main then showed 0/20 (Now Loading overlay)."""
        from maa.custom_recognition import CustomRecognition
        from agent.core.match_custom import MatchRuntime
        results = []
        for count, expected in ((20, True), (19, False)):  # 19 would expect 1/20, not the observed 0/20
            runtime = MatchRuntime(clock=lambda: 100.0)
            runtime.run = mt.MatchRun(mt.MatchConfig(stage="A5-S15", count=count, count_mode="fixed",
                                                     sample_only=False), runtime.log)
            runtime.run.phase, runtime.run.rights = "awaiting_result", (20, 20)
            runtime.run.intent = {"stage": "A5-S15", "count": count, "rights_before": [count, 20], "time": "t"}
            runtime.run.saver = Mock(saved=[])
            runtime._log = Mock(return_value=True)

            class Probe(CustomRecognition):
                def analyze(self, context, argv, runtime=runtime):
                    results.append(runtime.watch(context, argv.image, {}))
                    return CustomRecognition.AnalyzeResult(box=(0, 0, 1, 1), detail="read_only")

            NodeProbe.resource().register_custom_recognition("SC.Test.MatchWatch", Probe())
            NodeProbe().run(REAL5_AFTER, {"SC.Test.Start": {"next": ["SC.Test.MatchWatch"]},
                                         "SC.Test.MatchWatch": {"recognition": "Custom",
                                                                "custom_recognition": "SC.Test.MatchWatch"}})
            self.assertEqual(results[-1], expected)
            if expected:
                self.assertEqual((runtime.run.outcome, runtime.run.rights), ("skipped", (0, 20)))
                self.assertEqual(runtime._log.call_args.args[0]["event"], "verified")

    def test_oct11_complete_skip_survey_locates_explicit_fifteen(self):
        from maa.custom_recognition import CustomRecognition
        from agent.core.task_custom import Vision
        from agent.core.match_scan import read_skip_page
        observations = []

        class Probe(CustomRecognition):
            def analyze(self, context, argv):
                observations.append(read_skip_page(Vision(context), argv.image, len(observations) + 1.0))
                return CustomRecognition.AnalyzeResult(box=(0, 0, 1, 1), detail="read_only")

        NodeProbe.resource().register_custom_recognition("SC.Test.MatchObserved", Probe())
        override = {"SC.Test.Start": {"next": ["SC.Test.MatchObserved"]},
                    "SC.Test.MatchObserved": {"recognition": "Custom", "custom_recognition": "SC.Test.MatchObserved"}}
        planner = ObservedSkipPlanner(mt.MatchConfig(stage="A5-S15", count=1, count_mode="fixed", sample_only=False))
        for frame in NATIVE_PAGES:
            NodeProbe().run(frame, override)
            decision = planner.step(observations[-1])
            self.assertNotEqual(decision.kind, "abort", (frame, decision, observations[-1]))
        self.assertEqual((decision.kind, planner.target, planner.pressed), ("plus", (5, 15), 1))
        self.assertEqual(len(planner.scan.stages), 15)
        self.assertEqual(planner.scan.pages[0].cost, (0, 19))
        row = observations[-1].rows[decision.slot - 1]
        self.assertEqual(row.stage, (5, 15))
        self.assertTrue(188 <= row.plus_box[1] < 562)

    @unittest.skipUnless(maa_ready(NATIVE + "65-match-explicit-stopped-bottom.png"), "需要第 65 帧")
    def test_native_plus_five_returns_the_exact_moving_target(self):
        import tempfile
        from pathlib import Path
        from maa.custom_action import CustomAction
        from agent.core import match_scan as ms, task_common as tc
        from agent.core.match_custom import MatchRuntime, MatchPlanRecognition
        from agent.core.task_custom import Vision
        root = Path(__file__).resolve().parents[1]
        directory = tempfile.TemporaryDirectory(dir=root / "dev_assets/private")
        self.addCleanup(directory.cleanup)
        runtime = MatchRuntime(log_root=directory.name)
        runtime.run = mt.MatchRun(mt.MatchConfig(stage="A5-S15", count=1, count_mode="fixed", sample_only=False), runtime.log)
        runtime.run.phase, runtime.run.rights = "planning", (19, 20)
        runtime.run.planner = ObservedSkipPlanner(runtime.run.config)
        found = {}

        class Probe(CustomAction):
            def run(self, context, argv):
                from tools.replay_screenshots import read_png
                image = read_png(root / NATIVE / "65-match-explicit-stopped-bottom.png")
                page = ms.read_skip_page(Vision(context), image, 100.0)
                runtime.run.planner.current_page = page
                runtime.run._decision_key = ("decision", tc.image_digest(image))
                runtime.run._decision = mt.Decision("plus", slot=5)
                before = context.get_node_data("SC.Match.Skip.Plus5")
                detail = context.run_recognition("SC.Match.Skip.Plus5", image)
                found.update(hit=bool(detail and detail.hit), box=None if detail is None else tuple(detail.box),
                             action_unchanged=before["action"] == context.get_node_data("SC.Match.Skip.Plus5")["action"])
                return True

        resource = NodeProbe.resource()
        resource.register_custom_recognition("SC.Test.MatchPlanTarget", MatchPlanRecognition(runtime, "plan"))
        resource.register_custom_action("SC.Test.MatchTargetProbe", Probe())
        override = {"SC.Test.Start": {"recognition": "DirectHit", "next": ["SC.Test.MatchTargetProbe"]},
                    "SC.Test.MatchTargetProbe": {"recognition": "DirectHit", "action": "Custom", "custom_action": "SC.Test.MatchTargetProbe"},
                    "SC.Match.Plan.Plus5": {"custom_recognition": "SC.Test.MatchPlanTarget"},
                    "SC.Match.Skip.Plus5": {"enabled": True}}
        NodeProbe().run(NATIVE + "65-match-explicit-stopped-bottom.png", override)
        self.assertEqual(found, {"hit": True, "box": (575, 507, 20, 16), "action_unchanged": True})



if __name__ == "__main__":
    unittest.main()
