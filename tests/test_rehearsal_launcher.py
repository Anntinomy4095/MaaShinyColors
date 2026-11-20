"""Selected Grade/rehearsal mode has one launcher authority, shared by CLI and GUI."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools import run_tasks as launcher, ui_schema


def item(difficulty="Normal", mode="フェスリハーサル"):
    return {"name": "GradeFes", "option": {"Grade模式": mode, "Grade难度": difficulty}}


def config(difficulty="Normal", mode="フェスリハーサル"):
    return {"preset": None, "tasks": [item(difficulty, mode)], "max_clicks": 50,
            "task_max_clicks": {"GradeFes": 50}}


class RehearsalLauncherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.interface = launcher.load_interface()
        cls.schema = ui_schema.export_schema()

    def test_selected_mode_and_difficulty_resolve_one_action(self):
        for difficulty in ("Easy", "Normal", "Hard"):
            with self.subTest(difficulty=difficulty):
                self.assertEqual(launcher.item_spend_action(item(difficulty)),
                                 "grade-rehearsal-" + difficulty.lower())
                self.assertEqual(launcher.item_spend_action(item(difficulty, "グレードフェス")),
                                 "grade-tension-confirm")

    def test_single_task_accepts_only_its_selected_difficulty(self):
        for difficulty in ("Easy", "Normal", "Hard"):
            action = "grade-rehearsal-" + difficulty.lower()
            good = {**config(difficulty), "allow_spend": [action]}
            actual = launcher.supervised_task_config(good, live=True, offline=False, simulate_input=False)
            self.assertEqual(actual["allow_spend"], [action])
            for wrong in ("grade-tension-confirm", *launcher.GRADE_REHEARSAL_SPEND_ACTIONS):
                if wrong != action:
                    with self.subTest(difficulty=difficulty, wrong=wrong), self.assertRaises(ValueError):
                        launcher.supervised_task_config({**good, "allow_spend": [wrong]}, live=True,
                                                       offline=False, simulate_input=False)

    def test_sequence_keeps_only_selected_action(self):
        actual = launcher.sequence_run_config({"tasks": [item("Hard"), {"name": "Shop"}],
            "allow_spend": ["shop-exchange-confirm", "grade-tension-confirm", *launcher.GRADE_REHEARSAL_SPEND_ACTIONS]})
        self.assertEqual(actual["allow_spend"], ["grade-rehearsal-hard", "shop-exchange-confirm"])
        launcher.supervised_sequence_config(actual, live=True, offline=False, simulate_input=False)
        with self.assertRaises(ValueError):
            launcher.supervised_sequence_config({**actual, "allow_spend": ["grade-rehearsal-normal"]},
                                                live=True, offline=False, simulate_input=False)

    def test_sequence_filters_other_grade_consuming_nodes(self):
        from types import SimpleNamespace

        plan, = launcher.plan_tasks(self.interface, config("Hard"))
        nodes = {"SC.Grade.Tension.Confirm": "grade-tension-confirm", "SC.Grade.Rehearsal.Easy": "grade-rehearsal-easy",
                 "SC.Grade.Rehearsal.Normal": "grade-rehearsal-normal", "SC.Grade.Rehearsal.Hard": "grade-rehearsal-hard"}
        table = SimpleNamespace(get=lambda _: SimpleNamespace(family="grade-fes", consuming=True))
        with patch.object(launcher, "click_node_actions", return_value=nodes), \
                patch("agent.core.pipeline_actions.default_table", return_value=table):
            override = launcher.sequence_task_override("GradeFes", {"grade-fes"}, ["grade-rehearsal-hard"], plan=plan)
        self.assertNotIn("SC.Grade.Rehearsal.Hard", override)
        for node in set(nodes) - {"SC.Grade.Rehearsal.Hard"}:
            self.assertEqual(override[node], {"enabled": False})

    def test_ui_profile_spend_switch_derives_selected_difficulty(self):
        for difficulty in ("Easy", "Normal", "Hard"):
            raw = item(difficulty)
            raw["option"]["Grade使用参加权"] = "Yes"
            profile = {"schema_version": 1, "tasks": [raw]}
            self.assertEqual(ui_schema.profile_to_run_config(self.schema, profile)["allow_spend"],
                             ["grade-rehearsal-" + difficulty.lower()])
            self.assertEqual(ui_schema.spend_flags_for_profile(self.schema, profile), ["allow_grade_spend"])
            raw["option"]["Grade使用参加权"] = "No"
            self.assertNotIn("allow_spend", ui_schema.profile_to_run_config(self.schema, profile))

    def test_fes_preflight_includes_rehearsal_only_with_its_authority(self):
        from agent.core import fes_allocation as fa

        plans = launcher.plan_tasks(self.interface, config("Hard"))
        allocation = fa.Allocation.from_configs
        with patch.object(fa.Allocation, "from_configs", wraps=allocation) as make:
            launcher.fes_preflight(plans, ["grade-rehearsal-hard"], observed=(20, 20))
            self.assertEqual(make.call_args.args[1].mode, "rehearsal")
        with patch.object(fa.Allocation, "from_configs", wraps=allocation) as make:
            launcher.fes_preflight(plans, ["grade-tension-confirm"], observed=(20, 20))
            self.assertIsNone(make.call_args.args[1])

    def test_cli_grade_flag_passes_only_selected_rehearsal_action(self):
        fake = {"order": [], "input_mode": "live_input", "tasks": [], "summary_path": None,
                "fes_allocation": {"status": "idle"}, "run_control": {"stop_requested": False, "events_complete": True},
                "failed": 0}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(config("Easy")), encoding="utf-8")
            with patch.object(launcher, "run", return_value=fake) as run, patch("sys.stdout", new=io.StringIO()):
                self.assertEqual(launcher.main(["--config", str(path), "--session", "unused.json", "--live",
                                               "--supervised-task", "--allow-grade-spend"]), 0)
            self.assertEqual(run.call_args.args[0]["allow_spend"], ["grade-rehearsal-easy"])


if __name__ == "__main__":
    unittest.main()
