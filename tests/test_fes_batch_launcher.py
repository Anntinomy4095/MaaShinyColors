"""Daily Grade batching, the shared rights plan, and manual takeover at the launcher boundary."""
import datetime as dt
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from agent.core import grade_task as gt, task_common as tc
from agent.core.run_control import RunControl
from tools import run_tasks as launcher
from tests.test_task_launcher import FakeTasker

ROOT = Path(__file__).resolve().parents[1]
NOW = dt.datetime(2026, 10, 12, 8, tzinfo=dt.timezone.utc)

class FesBatchLauncherTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(dir=ROOT / "dev_assets/private")
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
    def config(self, live="开启", mode="グレードフェス"):
        return {"preset": None, "tasks": [
            {"name": "GradeFes", "option": {"Grade每日次数": "3", "Grade自动演出": live, "Grade模式": mode}},
            {"name": "MatchSkip", "option": {"Match只采样": "No"}}],
            "allow_spend": ["grade-tension-confirm", "match-skip-confirm"]}
    def test_completed_life_reduces_batch_and_match_reservation(self):
        log = tc.JsonlLog("grade", self.root, lambda: NOW)
        log.append({"event": "intent", "mode": "grade", "stage": gt.LIVE_STAGE, "rights_before": [20,20]})
        log.append({"event": "verified", "mode": "grade", "rights_after": [19,20]})
        config = self.config()
        plans = launcher.plan_tasks(launcher.load_interface(), config)
        batched, progress = launcher.grade_batch_plan(plans, {"grade": {"log_root": self.root, "now": lambda: NOW}})
        # 2026-10-28: the count is per run (grade_task.per_run_config): 1 played today + 3 this run
        self.assertEqual((progress.target, progress.completed, progress.planned_runs), (4,1,3))
        self.assertEqual(batched[0].pipeline_override["SC.Grade.Unit.Confirm"]["max_hit"], 6)
        allocation = launcher.fes_preflight(batched, config["allow_spend"], (19,20), grade_progress=progress)
        self.assertEqual((allocation.grade, allocation.match, allocation.total), (3,16,19))
    def test_no_click_cap_unless_one_is_named_explicitly(self):
        config = self.config()
        self.assertIsNone(launcher.task_click_cap(config, "GradeFes"))
        config["task_max_clicks"] = {"GradeFes": 50}
        self.assertEqual(launcher.task_click_cap(config, "GradeFes"), 50)
        config.pop("task_max_clicks")
        actual = launcher.sequence_run_config(config)
        self.assertNotIn("task_max_clicks", actual)
        self.assertNotIn("max_clicks", actual)
    def test_manual_mode_reserves_only_one_life(self):
        config=self.config("关闭")
        plans, progress=launcher.grade_batch_plan(launcher.plan_tasks(launcher.load_interface(),config),
                                                {"grade":{"log_root":self.root,"now":lambda:NOW}})
        allocation=launcher.fes_preflight(plans,config["allow_spend"],(20,20),grade_progress=progress)
        self.assertEqual((allocation.grade,launcher.task_click_cap(config,"GradeFes")),(1,None))

    def test_five_lives_are_not_cut_to_three_by_hidden_defaults(self):
        config = self.config()
        config["tasks"][0]["option"]["Grade每日次数"] = "5"
        plans, progress = launcher.grade_batch_plan(launcher.plan_tasks(launcher.load_interface(), config),
            {"grade": {"log_root": self.root, "now": lambda: NOW}})
        self.assertEqual(progress.planned_runs, 5)
        self.assertIsNone(launcher.task_click_cap(config, "GradeFes"))
        allocation = launcher.fes_preflight(plans, config["allow_spend"], (20,20), grade_progress=progress)
        self.assertEqual((allocation.grade, allocation.match), (5,15))
    def test_takeover_stops_following_task_with_or_without_run_control(self):
        for controlled in (False,True):
            with self.subTest(controlled=controlled):
                control=RunControl() if controlled else None
                if control: self.addCleanup(control.close)
                tasker=FakeTasker({"SC.A.Entry":"fail","SC.B.Entry":"ok"})
                reports=[]
                def drain():
                    result=list(reports); reports.clear(); return result
                def finish(plan): reports.append({"kind":"grade","status":"takeover","takeover_required":True})
                plans=[launcher.TaskPlan("A","SC.A.Entry",{}),launcher.TaskPlan("B","SC.B.Entry",{})]
                result=launcher.execute_plan(plans,tasker,sink=SimpleNamespace(drain=drain),
                                            after_task=finish,control=control)
                self.assertEqual([r["status"] for r in result],["takeover","skipped"])
                self.assertEqual([entry for entry,_ in tasker.posted],["SC.A.Entry"])
                if control: self.assertEqual(control.reason,"manual_takeover")

if __name__ == "__main__": unittest.main()
