"""Grade / rehearsal mode selection and their isolated attempt history."""
import json
import tempfile
import unittest
from unittest.mock import Mock
from pathlib import Path

from agent.core import grade_task as gt
from agent.core.grade_custom import GradeRuntime
from tests.maa_probe import NodeProbe, maa_ready

ROOT = Path(__file__).resolve().parents[1]
TREND_FRAME = "dev_assets/private/run_logs/samples/grade-after-rehearsal-20261020T095528555276Z/frame-064.png"


class GradeRehearsalTests(unittest.TestCase):
    @unittest.skipUnless(maa_ready(TREND_FRAME), "需要本地新赛期彩排画面")
    def test_waiting_guard_accepts_a_different_trend_order(self):
        probe = NodeProbe()
        self.assertTrue(probe.hits(TREND_FRAME, "SC.Grade.Live.Screen.Guard"))
        self.assertTrue(probe.hits(TREND_FRAME, "SC.Grade.Live.Anchor.TrendWord"))
        self.assertTrue(probe.hits(TREND_FRAME, "SC.Grade.Live.Wait.Guard"))

    def runtime(self, mode):
        directory = tempfile.TemporaryDirectory(dir=ROOT / "dev_assets/private")
        self.addCleanup(directory.cleanup)
        runtime = GradeRuntime(log_root=directory.name)
        runtime.run = gt.GradeRun(gt.GradeConfig.from_params({"mode": mode}), runtime.log)
        return runtime

    def test_mode_selection_uses_config_and_leaves_other_mode_untouched(self):
        for mode in gt.MODES:
            runtime = self.runtime(mode)
            runtime.run.phase = "joined"
            other = "rehearsal" if mode == "grade" else "grade"
            self.assertFalse(runtime.plan(None, None, {"step": "mode", "mode": other}))
            self.assertEqual(runtime.run.phase, "joined")
            self.assertTrue(runtime.plan(None, None, {"step": "mode", "mode": mode}))
            self.assertEqual(runtime.run.phase, "mode_tapped")

    def test_formal_open_attempt_does_not_become_a_rehearsal_attempt(self):
        runtime = self.runtime("rehearsal")
        runtime.log.append({"event": "tension_intent", "difficulty": "Normal", "rights_before": [20, 20]})
        self.assertEqual(runtime.state_now().attempts, 0)
        runtime._log({"event": "tension_intent", "difficulty": "Normal", "rights_before": [20, 20]})
        self.assertEqual(runtime.state_now().attempts, 1)
        self.assertEqual(list(runtime.log.events())[-1]["mode"], "rehearsal")
        runtime.run.config = gt.GradeConfig()
        self.assertEqual(runtime.state_now().attempts, 1)

    def test_each_mode_only_accepts_its_difficulty_title(self):
        titles = {"grade": gt.DIFFICULTY_TITLE, "rehearsal": gt.REHEARSAL_DIFFICULTY_TITLE}
        for mode, title in titles.items():
            config = gt.GradeConfig.from_params({"mode": mode})
            self.assertEqual(gt.difficulty_reasons(config, "Normal", title, gt.ONE_RIGHT, (20, 20), (20, 20)), [])
            other = titles["rehearsal" if mode == "grade" else "grade"]
            self.assertTrue(gt.difficulty_reasons(config, "Normal", other, gt.ONE_RIGHT, (20, 20), (20, 20)))

    def test_mode_buttons_have_separate_plans_and_start_disabled(self):
        nodes = json.loads((ROOT / "assets/resource/base/pipeline/grade.json").read_text(encoding="utf-8"))
        for mode, name, plan in (("grade", "Grade", "Mode"), ("rehearsal", "Rehearsal", "Rehearsal")):
            node = nodes["SC.Grade.Mode." + name]
            self.assertIs(node["enabled"], False)
            self.assertIn("SC.Grade.Mode.Guard", node["all_of"])
            self.assertIn("SC.Grade.Abort", node["on_error"])
            self.assertEqual(nodes["SC.Grade.Plan." + plan]["custom_recognition_param"]["mode"], mode)
        self.assertEqual(nodes["SC.Grade.Mode.Rehearsal"]["target"], [930, 410, 115, 90])


    def test_rehearsal_difficulty_is_a_one_right_spend_without_tension(self):
        runtime = self.runtime("rehearsal")
        runtime.run.phase = "mode_tapped"
        runtime.run.rights = (20, 20)
        runtime.vision = lambda context: Mock(text=Mock(side_effect=[gt.REHEARSAL_DIFFICULTY_TITLE, gt.ONE_RIGHT]))
        runtime.rights = lambda *args: ("20/20", (20, 20))
        runtime.authority = Mock(can_spend=Mock(return_value=True), authorize=Mock(return_value=None))
        image = object()
        self.assertTrue(runtime.check_rehearsal(None, image, {"difficulty": "Normal"}))
        self.assertEqual(runtime.run.phase, "awaiting_result")
        self.assertEqual(runtime.run.intent["rights_before"], [20, 20])
        runtime.authority.authorize.assert_called_once_with("grade-rehearsal-normal", image, "SC.Grade.Check.RehearsalNormal")
        events = list(runtime.log.events())
        self.assertEqual(events[-1]["event"], "intent")
        self.assertEqual(events[-1]["stage"], gt.REHEARSAL_STAGE)
        self.assertEqual(runtime.state_now().attempts, 1)
        self.assertIsNone(gt.resume_kind(runtime.state_now()))
        # An identical replay cannot issue a second grant after entry.
        self.assertFalse(runtime.check_rehearsal(None, image, {"difficulty": "Normal"}))
        runtime.authority.authorize.assert_called_once()

    def test_unconfigured_difficulty_and_missing_authority_never_start_rehearsal(self):
        runtime = self.runtime("rehearsal")
        runtime.run.phase = "mode_tapped"
        self.assertFalse(runtime.check_rehearsal(None, None, {"difficulty": "Hard"}))
        self.assertEqual(runtime.run.phase, "mode_tapped")
        runtime.vision = lambda context: Mock(text=Mock(side_effect=[gt.REHEARSAL_DIFFICULTY_TITLE, gt.ONE_RIGHT]))
        runtime.rights = lambda *args: ("20/20", (20, 20))
        runtime.run.rights = (20, 20)
        self.assertFalse(runtime.check_rehearsal(None, None, {"difficulty": "Normal"}))
        self.assertEqual(runtime.run.outcome, "spend_not_enabled")
        self.assertEqual(list(runtime.log.events()), [])

    def test_formal_difficulty_plan_never_starts_a_rehearsal(self):
        runtime = self.runtime("rehearsal")
        runtime.run.phase = "mode_tapped"
        self.assertFalse(runtime.plan(None, None, {"step": "difficulty", "difficulty": "Normal"}))
        self.assertEqual(runtime.run.phase, "mode_tapped")

    def test_rehearsal_turns_use_ten_turns_and_formal_stays_at_seven(self):
        from agent.core import fes_live_reader as reader
        runtime = self.runtime("rehearsal")
        self.assertEqual(runtime.turn_limit(), 10)
        for turn in range(1, 11):
            self.assertEqual(reader.parse_turn(f"{turn}/10", max_turns=10), turn)
        self.assertIsNone(reader.parse_turn("8/10"))
        self.assertIsNone(reader.parse_turn("11/10", max_turns=10))
        self.assertIsNone(reader.parse_turn("1/7", max_turns=10))
        runtime.run.config = gt.GradeConfig()
        # 2026-10-22: official Grade reads its total from the screen (new season x/6, earlier x/7) and keeps it.
        self.assertEqual(runtime.turn_limit(), (6, 7))

    def test_observed_rehearsal_title_ocr_tail_is_accepted_only_in_rehearsal(self):
        config = gt.GradeConfig.from_params({"mode": "rehearsal"})
        observed = gt.REHEARSAL_DIFFICULTY_TITLE[:-1] + "し"
        self.assertEqual(gt.difficulty_reasons(config, "Normal", observed, gt.ONE_RIGHT, (20, 20), (20, 20)), [])
        self.assertIn("title_not_grade_fes", gt.difficulty_reasons(gt.GradeConfig(), "Normal", observed,
                                                                gt.ONE_RIGHT, (20, 20), (20, 20)))
