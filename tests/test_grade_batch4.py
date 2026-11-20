"""Batch 4 (2026-10-08, offline): Grade Fes page flow additions.

- 参加ユニット binding: the user's chosen Fes unit is bound by the evaluation number shown on 参加ユニット (「21801」);
  a configured number must equal the fresh reading before テンション決定, otherwise the run goes back without spending.
- フェスリハーサル difficulty dialog: a recognition-only guard; the formal Grade difficulty guard never holds on it and
  rehearsal uses separate consuming difficulty nodes after the 2026-10-11 live sample.
"""
import json
import shutil
import unittest
from pathlib import Path

from agent.core import grade_task as gt
from tools import run_tasks as launcher
from tests.test_daily_b5_tasks import (DIFF, DIFF_MONEY2X, DIFF_PULSE, FESMAIN, FESMENU, GRADE_ACTIONS, GRADE_CHAIN,
                                       HOME, MODE, NOW, P, REHEARSAL, TENSION, UNIT, frames_present)
from tests.test_task_launcher import HAVE_MAA, TABLE, pipeline_nodes, private_ready

ROOT = Path(__file__).resolve().parent.parent
PRIVATE = ROOT / "dev_assets/private"
REHEARSAL_FRAMES = [P + f"manual-capture-kept-20261002/shop-grade/{i:03d}.png" for i in (56, 58, 59, 60, 61, 62, 63)]
GRADE_DIFF_MANUAL = P + "manual-capture-kept-20261002/shop-grade/066.png"


class UnitBindingRuleTests(unittest.TestCase):
    def test_config_accepts_only_empty_or_a_plain_number(self):
        self.assertEqual(gt.GradeConfig.from_params({}).unit_score, "")
        self.assertEqual(gt.GradeConfig.from_params({"unit_score": "21801"}).unit_score, "21801")
        for bad in ("21,801", "12", "1234567", "abc", 21801, None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                gt.GradeConfig.from_params({"unit_score": bad})

    def test_reading_and_reasons(self):
        self.assertEqual(gt.unit_score_reading("21801"), "21801")
        self.assertEqual(gt.unit_score_reading(" ２１８０１ "), "21801")
        self.assertEqual(gt.unit_score_reading("21,801"), "21801")
        self.assertIsNone(gt.unit_score_reading("S+"))
        self.assertIsNone(gt.unit_score_reading(""))
        unbound, bound = gt.GradeConfig(), gt.GradeConfig(unit_score="21801")
        self.assertEqual(gt.unit_binding_reasons(unbound, "garbage"), [])
        self.assertEqual(gt.unit_binding_reasons(bound, "21801"), [])
        self.assertEqual(gt.unit_binding_reasons(bound, "21800"), ["unit_score_mismatch"])
        self.assertEqual(gt.unit_binding_reasons(bound, None), ["unit_score_unreadable"])
        notice = gt.ONE_RIGHT + gt.NO_RETURN
        # The unit page checks themselves are unchanged (TensionDecided / ライブ開始 re-checks never read the score).
        self.assertEqual(gt.unit_reasons(bound, "参加ユニット(Normal)", notice, (20, 20), None), [])

    def test_pipeline_option_and_python_defaults_agree(self):
        nodes = pipeline_nodes()
        self.assertEqual(nodes["SC.Grade.Config.Unit"]["attach"], {"unit_score": ""})
        self.assertIn("SC.Grade.Config.Unit", nodes["SC.Grade.Entry"]["custom_action_param"]["config_nodes"])
        tasks = json.loads((ROOT / "assets/tasks/grade_fes.json").read_text(encoding="utf-8"))
        option = tasks["option"]["Grade参加ユニット"]
        self.assertEqual(option["type"], "input")
        self.assertEqual(option["inputs"][0]["default"], "")
        self.assertEqual(option["pipeline_override"]["SC.Grade.Config.Unit"]["attach"], {"unit_score": "{score}"})
        self.assertIn("Grade参加ユニット", tasks["task"][0]["option"])

    def test_rehearsal_guard_is_recognition_only(self):
        nodes = pipeline_nodes()
        guard = nodes["SC.Grade.Rehearsal.Difficulty.Guard"]
        self.assertNotIn("action", guard)
        self.assertFalse(any("SC.Grade.Rehearsal.Difficulty.Guard" in json.dumps(node.get("next", []))
                             for node in nodes.values()))
        self.assertNotIn("SC.Grade.Anchor.Rehearsal.Title", nodes["SC.Grade.Difficulty.Guard"]["all_of"])


def batch4_frames_present():
    return frames_present() and all((ROOT / f).is_file() for f in REHEARSAL_FRAMES + [GRADE_DIFF_MANUAL])


@unittest.skipUnless(HAVE_MAA and batch4_frames_present(), "需要 MaaFramework v5.12.3、私有模板包、OCR 模型与 Grade 实机帧")
class GradeBatch4ReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not private_ready():
            raise unittest.SkipTest("私有回放数据不完整")

    def setUp(self):
        self.root = PRIVATE / "run_logs" / ("_unittest_grade_b4_" + self.id().rsplit(".", 1)[-1])
        shutil.rmtree(self.root, ignore_errors=True)
        self.addCleanup(shutil.rmtree, self.root, True)

    def run_grade(self, frames, option=None, spend=("grade-tension-confirm",)):
        config = {"preset": None, "tasks": [{"name": "GradeFes", "option": option or {}}]}
        if spend:
            config["allow_spend"] = list(spend)
        options = {"grade": {"log_root": self.root, "now": lambda: NOW, "sample_root": self.root / "samples"}}
        summary = launcher.run(config, offline_steps=[(ROOT / f, "click") for f in frames], simulate_input=True,
                               policy={"live_input_enabled": False}, summary_path=False, save_diagnostics=False,
                               task_options=options)
        task, = summary["tasks"]
        report, = [r for r in task["reports"] if r["kind"] == "grade"]
        return task, report

    def unit_decision(self, report):
        return [d for d in report["decisions"] if d.get("stage") == "Unit"][0]

    def test_unbound_run_reads_and_reports_the_unit_score(self):
        task, report = self.run_grade(GRADE_CHAIN + [TENSION, HOME])
        self.assertEqual(task["simulated_actions"], GRADE_ACTIONS)
        unit = self.unit_decision(report)
        self.assertEqual((unit["unit_score"], unit["unit_score_bound"], unit["reasons"]), ("21801", None, []))

    def test_bound_unit_that_matches_proceeds_to_tension(self):
        task, report = self.run_grade(GRADE_CHAIN + [TENSION, HOME], option={"Grade参加ユニット": "21801"})
        self.assertEqual(task["simulated_actions"], GRADE_ACTIONS)
        self.assertEqual(task["simulated_spends"][0], ["grade-tension-confirm", "sent"])
        unit = self.unit_decision(report)
        self.assertEqual((unit["unit_score"], unit["unit_score_bound"], unit["reasons"]), ("21801", "21801", []))

    def test_bound_unit_that_differs_goes_back_without_spending(self):
        task, report = self.run_grade([HOME, FESMENU, FESMAIN, MODE, DIFF, UNIT, DIFF, MODE, HOME],
                                      option={"Grade参加ユニット": "21800"})
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task["simulated_actions"], ["home-fes", "fes-grade-entry", "grade-join-open", "grade-mode-grade",
                                                     "grade-difficulty-normal", "nav-back", "nav-back", "nav-back"])
        for point, action in zip(task["simulated_clicks"], task["simulated_actions"]):
            self.assertTrue(TABLE.get(action).contains(point), (point, action))
        self.assertEqual(task["simulated_spends"], [])
        self.assertEqual(report["outcome"], "unit_mismatch")
        self.assertIn("unit_score_mismatch", report["notes"])
        self.assertFalse((self.root / "grade-2026-10-02.jsonl").is_file())  # no tension_intent was ever written

    def test_rehearsal_and_grade_difficulty_guards_never_overlap(self):
        from maa.custom_action import CustomAction
        from agent.core.task_actions import ReportSink
        from tools.replay_screenshots import read_png

        grade_frames = [DIFF, DIFF_MONEY2X, DIFF_PULSE, GRADE_DIFF_MANUAL]
        others = [HOME, FESMENU, FESMAIN, MODE, UNIT, TENSION]
        frames = REHEARSAL_FRAMES + grade_frames + others
        want = {"SC.Grade.Rehearsal.Difficulty.Guard": set(REHEARSAL_FRAMES),
                "SC.Grade.Difficulty.Guard": {DIFF, DIFF_MONEY2X, DIFF_PULSE}}
        images = [read_png(ROOT / f) for f in frames]
        found = {}

        class Probe(CustomAction):
            def run(self, context, argv):
                for frame, image in zip(frames, images):
                    for guard in want:
                        detail = context.run_recognition(guard, image)
                        if detail is not None and detail.hit:
                            found.setdefault(guard, set()).add(frame)
                return True

        scenario = launcher.ReplayScenario(images[:1], ["click"], allow_input=False)
        tasker = launcher.build_tasker(launcher.create_replay_controller(scenario), ReportSink(), save_diagnostics=False)
        resource = tasker._sc_keepalive[0]
        self.assertTrue(resource.register_custom_action("SC.Test.Probe", Probe()))
        tasker.post_task("SC.Test.Probe.Entry", {"SC.Test.Probe.Entry": {
            "recognition": "DirectHit", "action": "Custom", "custom_action": "SC.Test.Probe"}}).wait()
        self.assertEqual(found.get("SC.Grade.Rehearsal.Difficulty.Guard", set()), want["SC.Grade.Rehearsal.Difficulty.Guard"])
        # The manual 066 Grade dialog is at a prompt-bubble phase outside the bright-bubble color guard; what matters
        # here is that no rehearsal frame is ever taken for the Grade difficulty dialog.
        self.assertFalse(found.get("SC.Grade.Difficulty.Guard", set()) & set(REHEARSAL_FRAMES))
        self.assertTrue(want["SC.Grade.Difficulty.Guard"] <= found.get("SC.Grade.Difficulty.Guard", set()))

    def test_rehearsal_uses_consuming_nodes_separate_from_formal_difficulty(self):
        nodes = pipeline_nodes()
        for difficulty in gt.DIFFICULTIES:
            formal = nodes["SC.Grade.Difficulty." + difficulty]
            rehearsal = nodes["SC.Grade.Rehearsal." + difficulty]
            self.assertIn("SC.Grade.Difficulty.Guard", formal["all_of"])
            self.assertNotIn("SC.Grade.Rehearsal.Difficulty.Guard", formal["all_of"])
            self.assertIn("SC.Grade.Rehearsal.Difficulty.Guard", rehearsal["all_of"])
            self.assertEqual(rehearsal["next"], ["SC.Grade.Live.Loop"])
            self.assertEqual(rehearsal["on_error"], ["SC.Grade.SpendError"])


if __name__ == "__main__":
    unittest.main()
