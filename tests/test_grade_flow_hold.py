"""2026-10-12 grade-auto2 stop: inside Grade's dialogs a frame no Grade node accepts must not reach the shared BackOne.

Live run (sample-live-20261011/grade-auto2-*): Normal difficulty tapped, 参加ユニット rights OCR missed on the first frame,
Check.Unit asked for another frame, no Grade node matched, and ``SC.Nav.BackOne`` (which since 2026-10-11 knows the
Grade difficulty / 参加ユニット back keys) walked back to Home; the run then re-entered Fes and failed with NotHandled.
Not the daily count: 1 manual verified record + this run = target 2, and the Fes-main check passed (reasons []).
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import numpy as np

from agent.core import grade_task as gt
from agent.core.grade_custom import GradeRuntime

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / "dev_assets/private"


def frame(value):
    return np.full((720, 1280, 3), value, dtype=np.uint8)


class FlowHoldRuntimeTests(unittest.TestCase):
    def runtime(self, phase="difficulty_tapped", **config):
        directory = tempfile.TemporaryDirectory(dir=PRIVATE)
        self.addCleanup(directory.cleanup)
        self.samples = Path(directory.name) / "samples"
        runtime = GradeRuntime(log_root=directory.name, sample_root=self.samples)
        runtime.run = gt.GradeRun(gt.GradeConfig(**config), runtime.log)
        runtime.run.phase = phase
        runtime.authority = Mock(can_spend=Mock(return_value=True), authorize=Mock(return_value=None))
        return runtime

    def test_mid_flow_frames_hold_then_stop_without_input_and_keep_samples(self):
        runtime = self.runtime()
        for index in range(gt.FLOW_HOLD_FRAMES):
            self.assertTrue(runtime.extra_state("flow_hold", None, frame(index * 10), {}))
            self.assertFalse(runtime.run.finished)
        self.assertTrue(runtime.extra_state("flow_hold", None, frame(200), {}))
        self.assertEqual(runtime.run.outcome, "page_unrecognized")
        self.assertIn("grade_flow_left_at_difficulty_tapped", runtime.run.notes)
        self.assertFalse(runtime.extra_state("flow_hold", None, frame(0), {}))   # finished: the normal exits take over
        runtime.authority.authorize.assert_not_called()
        saved = sorted(self.samples.glob("grade-unrecognized-*/frame-*.png"))
        self.assertEqual(len(saved), gt.FLOW_HOLD_FRAMES + 1)

    def test_progress_resets_the_bound_and_idle_or_fresh_runs_never_hold(self):
        runtime = self.runtime(phase="joined")
        for _ in range(gt.FLOW_HOLD_FRAMES):
            runtime.extra_state("flow_hold", None, frame(1), {})
        runtime.run.phase = "mode_tapped"   # the next dialog appeared: a new bound
        self.assertTrue(runtime.extra_state("flow_hold", None, frame(1), {}))
        self.assertEqual(runtime.run.hold_frames, 1)
        fresh = self.runtime(phase="active")
        self.assertFalse(fresh.extra_state("flow_hold", None, frame(1), {}))   # unknown start page: BackOne as before
        fresh.run.unreadable = 1                                                # Fes-main rights retry: hold
        self.assertTrue(fresh.extra_state("flow_hold", None, frame(1), {}))
        for phase in ("tension_tapped", "awaiting_result"):
            self.assertFalse(self.runtime(phase=phase).extra_state("flow_hold", None, frame(1), {}))

    def test_home_and_fes_popup_entry_only_before_the_flow_started(self):
        runtime = self.runtime(phase="active")
        self.assertTrue(runtime.state(None, frame(1), {"is": "active"}))
        for phase in gt.FLOW_PHASES:
            runtime.run.phase = phase
            self.assertFalse(runtime.state(None, frame(1), {"is": "active"}), phase)

    def test_stop_saves_recent_frames_but_a_success_does_not(self):
        runtime = self.runtime()
        for value in (10, 10, 80, 160, 240, 250):
            runtime.state(None, frame(value), {"is": "finished"})
        self.assertEqual(len(runtime.run.recent), gt.STOP_SAMPLE_FRAMES)
        runtime.run.finish("timeout", "unknown_or_unhandled_screen")
        runtime.report()
        self.assertEqual(len(list(self.samples.glob("grade-stopped-*/frame-*.png"))), gt.STOP_SAMPLE_FRAMES)
        ok = self.runtime()
        ok.state(None, frame(5), {"is": "finished"})
        ok.run.finish("nothing_to_do", "runs_today_2_verified_2")
        ok.report()
        self.assertEqual(list(self.samples.glob("grade-stopped-*")), [])


class DailyTargetTests(unittest.TestCase):
    """Hypothesis (a) checked against the grade-auto2 log: target 2 was not yet reached; target 1 ends at Begin."""
    EVENTS = [
        {"difficulty": "Normal", "event": "tension_intent", "game_day": "2026-10-11", "rights_before": [20, 20]},
        {"difficulty": "Normal", "event": "tension_uncertain", "game_day": "2026-10-11", "rights_before": [20, 20],
         "reason": "tension_decided_not_observed"},
        {"difficulty": "Normal", "event": "intent", "game_day": "2026-10-11", "mode": "rehearsal",
         "rights_before": [19, 20], "stage": "RehearsalDifficulty", "button": "Normal"},
        {"difficulty": "Normal", "event": "verified", "game_day": "2026-10-11", "mode": "rehearsal",
         "rights_before": [19, 20], "rights_after": [18, 20], "stage": "RehearsalDifficulty", "button": "Normal"},
        {"difficulty": "Normal", "event": "verified", "game_day": "2026-10-11", "mode": "grade", "supervision": "manual",
         "rights_before": [20, 20], "rights_after": [19, 20]},
    ]

    def started(self, runs):
        runtime = GradeRuntime(log_root=tempfile.mkdtemp(dir=PRIVATE))
        self.addCleanup(shutil.rmtree, runtime.log.root, True)
        runtime.log.events = Mock(return_value=self.EVENTS)
        runtime.run = gt.GradeRun(gt.GradeConfig(runs_per_day=runs), runtime.log)
        runtime.started(runtime.run)
        return runtime.run

    def test_the_count_is_per_run_so_a_life_played_today_shifts_the_target(self):
        # 2026-10-28 (per-run counts): the grade life already verified today never ends the run at Begin.
        for runs in (1, 2):
            with self.subTest(runs=runs):
                run = self.started(runs)
                self.assertFalse(run.finished)
                self.assertEqual(run.config.runs_per_day, 1 + runs)


class FlowHoldPipelineTests(unittest.TestCase):
    def test_hold_sits_after_every_grade_node_and_before_home_entry_and_backone(self):
        nodes = json.loads((ROOT / "assets/resource/base/pipeline/grade.json").read_text(encoding="utf-8"))
        successors = nodes["SC.Grade.Main"]["next"]
        hold = successors.index("[JumpBack]SC.Grade.Flow.Hold")
        for earlier in ("[JumpBack]SC.Grade.Unit.Confirm", "[JumpBack]SC.Grade.Unit.Wait", "[JumpBack]SC.Grade.Unit.Back",
                        "[JumpBack]SC.Grade.Difficulty.Back", "[JumpBack]SC.Grade.Difficulty.Normal",
                        "[JumpBack]SC.Grade.Rehearsal.Normal", "[JumpBack]SC.Grade.Mode.Grade",
                        "[JumpBack]SC.Grade.Main.Join", "[JumpBack]SC.Grade.Menu.Grade", "[JumpBack]SC.Grade.Leave",
                        "SC.Grade.Finish"):
            self.assertLess(successors.index(earlier), hold, earlier)
        for later in ("[JumpBack]SC.Grade.AtHome", "[JumpBack]SC.Nav.BackOne"):
            self.assertGreater(successors.index(later), hold, later)
        node = nodes["SC.Grade.Flow.Hold"]
        self.assertNotIn("action", node)
        self.assertNotIn("next", node)
        self.assertEqual(nodes["SC.Grade.State.FlowHold"]["custom_recognition_param"], {"is": "flow_hold"})


try:
    from tests.test_daily_b5_tasks import DIFF, FESMAIN, FESMENU, GRADE_ACTIONS, HOME, MODE, NOW, TENSION, UNIT, frames_present
    from tests.test_task_launcher import HAVE_MAA, private_ready
except Exception:  # pragma: no cover - optional replay prerequisites
    HAVE_MAA = False

    def frames_present():
        return False


@unittest.skipUnless(HAVE_MAA and frames_present(), "需要 MaaFramework、私有模板包、OCR 模型与 Grade 实机帧")
class FlowHoldReplayTests(unittest.TestCase):
    """Native Grade chain with the 参加ユニット rights pill unreadable for the first frames (the grade-auto2 case)."""

    @classmethod
    def setUpClass(cls):
        if not private_ready():
            raise unittest.SkipTest("私有回放数据不完整")

    def setUp(self):
        self.root = PRIVATE / "run_logs" / ("_unittest_grade_hold_" + self.id().rsplit(".", 1)[-1])
        shutil.rmtree(self.root, ignore_errors=True)
        self.root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.root, True)
        from PIL import Image
        image = np.array(Image.open(ROOT / UNIT).convert("RGB"))
        x, y, w, h = gt.RIGHTS_FIELDS["unit"]
        image[y:y + h, x:x + w] = image[y + h // 2, x - 6]   # paint the digits over with the pill colour
        self.unit_blank = self.root / "unit-rights-blank.png"
        Image.fromarray(image).save(self.unit_blank)

    def run_grade(self, steps):
        from tools import run_tasks as launcher
        config = {"preset": None, "tasks": [{"name": "GradeFes", "option": {}}], "allow_spend": ["grade-tension-confirm"]}
        options = {"grade": {"log_root": self.root, "now": lambda: NOW, "sample_root": self.root / "samples"}}
        summary = launcher.run(config, offline_steps=steps, simulate_input=True, policy={"live_input_enabled": False},
                               summary_path=False, save_diagnostics=False, task_options=options)
        task, = summary["tasks"]
        report, = [r for r in task["reports"] if r["kind"] == "grade"]
        return task, report

    def test_unreadable_unit_rights_wait_on_the_page_instead_of_backone(self):
        chain = [(ROOT / f, "click") for f in (HOME, FESMENU, FESMAIN, MODE, DIFF)]
        steps = chain + [(self.unit_blank, 2), (ROOT / UNIT, "click"), (ROOT / TENSION, "click"), (ROOT / HOME, "click")]
        task, report = self.run_grade(steps)
        self.assertNotIn("nav-back", task["simulated_actions"])
        self.assertNotIn("SC.Nav.BackOne", task["node_trace"])
        self.assertEqual(task["simulated_actions"], GRADE_ACTIONS)
        units = [d for d in report["decisions"] if d.get("stage") == "Unit"]
        self.assertEqual(units[0]["reasons"], ["rights_unreadable"])
        self.assertEqual(units[-1]["reasons"], [])

    def test_unreadable_difficulty_rights_hold_then_conclude_before_any_back(self):
        from PIL import Image
        image = np.array(Image.open(ROOT / DIFF).convert("RGB"))
        x, y, w, h = gt.RIGHTS_FIELDS["difficulty"]
        image[y:y + h, x:x + w] = image[y + h // 2, x - 6]
        diff_blank = self.root / "difficulty-rights-blank.png"
        Image.fromarray(image).save(diff_blank)
        chain = [(ROOT / f, "click") for f in (HOME, FESMENU, FESMAIN, MODE)]
        task, report = self.run_grade(chain + [(diff_blank, "click"), (ROOT / MODE, "click"), (ROOT / HOME, "click")])
        trace = task["node_trace"]
        self.assertIn("SC.Grade.Flow.Hold", trace)
        # Back keys only after the run's own conclusion (Leave -> GoHome), never as a mid-flow fallback.
        self.assertLess(trace.index("SC.Grade.Leave"), trace.index("SC.Nav.BackOne"))
        self.assertEqual(report["outcome"], "difficulty_mismatch")
        self.assertIn("rights_unreadable", report["notes"])
        self.assertEqual(task["simulated_spends"], [])
        self.assertTrue(list((self.root / "samples").glob("grade-unrecognized-*/frame-*.png")))

    def test_unknown_page_after_the_difficulty_tap_stops_with_samples_and_no_back(self):
        from PIL import Image
        blank = self.root / "black.png"
        Image.new("RGB", (1280, 720)).save(blank)
        chain = [(ROOT / f, "click") for f in (HOME, FESMENU, FESMAIN, MODE, DIFF)]
        task, report = self.run_grade(chain + [(blank, "click")])
        self.assertEqual(task["status"], "failed")
        self.assertEqual(report["outcome"], "timeout")   # Difficulty.Tapped never saw 参加ユニット: Abort, no click
        self.assertNotIn("nav-back", task["simulated_actions"])
        self.assertEqual(task["simulated_spends"], [])
        stopped = list((self.root / "samples").glob("grade-stopped-*/frame-*.png"))
        self.assertTrue(stopped)
        last = np.array(Image.open(sorted(stopped)[-1]))
        self.assertEqual(int(last.max()), 0)   # the page it stopped on (controller's last capture) is kept

if __name__ == "__main__":
    unittest.main()
