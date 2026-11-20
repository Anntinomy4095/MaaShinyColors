"""CLI/UI share the same Fes allocation check before a controller can be built."""
import unittest
from agent.core import grade_task as gt
from unittest.mock import patch

import numpy as np

from agent.core import fes_allocation as fa
from agent.core.run_control import RunControl
from tools import run_tasks as launcher, ui_schema


class FesLauncherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.interface = launcher.load_interface()
        cls.schema = ui_schema.export_schema()

    def setUp(self):
        progress = patch.object(gt, "daily_progress", side_effect=lambda config, **kwargs: gt.progress_from_state(config, gt.log_state(())))
        progress.start()
        self.addCleanup(progress.stop)

    def config(self, order=("GradeFes", "MatchSkip"), match=None, grade=None, spend=True):
        options = {"MatchSkip": {"Match只采样": "No", **(match or {})}, "GradeFes": grade or {}}
        return {"preset": None,
                "tasks": [{"name": n, "option": options[n]} for n in order],
                "allow_spend": ["match-skip-confirm", "grade-tension-confirm"] if spend else []}

    def preflight(self, config, observed=None):
        plans = launcher.plan_tasks(self.interface, config)
        return launcher.fes_preflight(plans, config["allow_spend"], observed, fa.TicketPolicy())

    def test_grade_first_leaves_the_observed_remainder_for_match(self):
        result = self.preflight(self.config(grade={"Grade每日次数": "3"}), (20, 20))
        self.assertEqual((result.status, result.grade, result.match, result.total), ("ok", 3, 17, 20))

    def test_unknown_rights_remain_unknown(self):
        result = self.preflight(self.config())
        self.assertEqual((result.status, result.available, result.match, result.total),
                         ("rights_unknown", None, None, None))

    def test_fixed_counts_use_merged_options_and_presets(self):
        config = self.config(match={"Match次数": "7"}, grade={"Grade每日次数": "2"})
        config["preset"] = "daily"
        result = self.preflight(config, (9, 20))
        self.assertEqual((result.match, result.grade, result.total, result.errors), (7, 2, 9, ()))
        self.assertEqual(self.preflight(config, (8, 20)).errors, ("over_allocated",))

    def test_all_match_before_grade_and_fixed_over_capacity_are_refused(self):
        result = self.preflight(self.config(order=("MatchSkip", "GradeFes")))
        self.assertEqual(result.errors, ("match_all_before_grade",))
        result = self.preflight(self.config(match={"Match次数": "19"}, grade={"Grade每日次数": "3"}))
        self.assertEqual(result.errors, ("over_allocated_capacity",))

    def test_disabled_task_and_unconsented_grade_do_not_take_rights(self):
        config = self.config(order=("MatchSkip", "GradeFes"))
        config["tasks"][1]["enabled"] = False
        self.assertEqual(self.preflight(config, (20, 20)).match, 20)
        config["tasks"][1]["enabled"] = True
        config["allow_spend"] = ["match-skip-confirm"]
        result = self.preflight(config, (20, 20))
        self.assertEqual((result.match, result.grade, result.errors), (20, 0, ()))

    def test_sample_only_and_no_spend_are_idle(self):
        config = self.config(order=("MatchSkip",), match={"Match只采样": "Yes", "Match回复券": "Yes"})
        self.assertEqual(self.preflight(config).status, "idle")
        self.assertEqual(self.preflight(self.config(spend=False)).status, "idle")

    def test_repeated_match_cannot_hide_the_first_allocation(self):
        config = self.config(order=("MatchSkip", "GradeFes"), match={"Match次数": "20"})
        config["tasks"].insert(1, {"name": "MatchSkip", "option": {"Match只采样": "No", "Match次数": "1"}})
        with patch.object(launcher, "create_replay_controller") as controller, \
                patch.object(launcher, "build_tasker") as tasker:
            with self.assertRaisesRegex(ValueError, "不能重复任务：MatchSkip"):
                launcher.run(config, offline_images=([np.zeros((720, 1280, 3), np.uint8)], ["click"]),
                             simulate_input=True, summary_path=False, save_diagnostics=False)
            controller.assert_not_called()
            tasker.assert_not_called()

    def test_disabled_duplicate_does_not_conflict_with_the_enabled_task(self):
        config = self.config(match={"Match次数": "7"})
        config["tasks"].insert(0, {"name": "MatchSkip", "enabled": False,
                                    "option": {"Match只采样": "No", "Match次数": "20"}})
        result = self.preflight(config, (8, 20))
        self.assertEqual((result.grade, result.match, result.total, result.errors), (1, 7, 8, ()))

    def test_recovery_refusal_and_verified_rehearsal_are_shared_with_ui(self):
        for options, error in (({"Match回复券": "Yes"}, "recovery_policy_disabled"),
                               ({"Match次数": "19"}, "over_allocated_capacity")):
            config = self.config(match=options, grade={"Grade每日次数": "3"})
            self.assertIn(error, self.preflight(config).errors)
        config = self.config(grade={"Grade模式": "フェスリハーサル"})
        config["allow_spend"] = ["match-skip-confirm", "grade-rehearsal-normal"]
        result = self.preflight(config, (20, 20))
        self.assertEqual((result.rehearsal, result.match, result.errors), (1, 19, ()))

    def test_ui_and_launcher_plans_are_identical(self):
        for config in (self.config(), self.config(order=("MatchSkip", "GradeFes")),
                       self.config(match={"Match次数": "7"}),
                       self.config(grade={"Grade模式": "フェスリハーサル"})):
            profile = {"schema_version": 1, "preset": None, "tasks": config["tasks"]}
            for task in profile["tasks"]:
                if task["name"] == "GradeFes":
                    task["option"] = {"Grade使用参加权": "Yes", **task["option"]}
            cli_config = ui_schema._profile_to_run_config(self.schema, profile)
            self.assertEqual(self.preflight(cli_config, (20, 20)),
                             ui_schema.fes_preflight(self.schema, profile, (20, 20), fa.TicketPolicy()))

    def test_refused_plan_never_constructs_a_controller(self):
        from maa.tasker import Tasker  # project regression environment includes Maa

        config = self.config(order=("MatchSkip", "GradeFes"))
        with patch.object(launcher, "create_replay_controller") as controller, \
                patch.object(launcher, "build_tasker") as tasker, \
                patch.object(Tasker, "set_stdout_level") as logging:
            with self.assertRaisesRegex(ValueError, "把 Grade 排在 Match 前面"):
                launcher.run(config, offline_images=([np.zeros((720, 1280, 3), np.uint8)], ["click"]),
                             simulate_input=True, summary_path=False, save_diagnostics=False)
            controller.assert_not_called()
            tasker.assert_not_called()
            logging.assert_not_called()

    def test_read_only_run_does_not_allocate_and_stopped_run_keeps_plan(self):
        images = ([np.zeros((720, 1280, 3), np.uint8)], ["click"])
        for simulated, status in ((False, "idle"), (True, "rights_unknown")):
            control = RunControl()
            control.request_stop()
            with patch.object(launcher, "build_tasker") as tasker:
                summary = launcher.run(self.config(), offline_images=images, simulate_input=simulated,
                                       control=control, summary_path=False, save_diagnostics=False)
            self.assertEqual(summary["fes_allocation"]["status"], status)
            self.assertEqual(summary["real_input_attempts"], 0)
            tasker.assert_not_called()


if __name__ == "__main__":
    unittest.main()
