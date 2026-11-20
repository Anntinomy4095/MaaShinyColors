"""Shared フェス参加権 split between Match skip, Grade Fes and rehearsal.

Offline: pure allocation math, recovery-ticket policy refusals, the rehearsal/Grade config split, PI = Pipeline =
Python defaults, the UI profile preflight and the Match/Grade run-time stops. No host, no game, no input.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from unittest.mock import Mock

from agent.core import fes_allocation as fa
from agent.core import grade_task as gt
from agent.core import match_progress as mp
from agent.core import match_task as mt
from tests.test_task_launcher import HAVE_MAA
from tools import run_tasks, ui_schema

ROOT = Path(__file__).resolve().parent.parent
OFF = fa.TicketPolicy(False, 0)
ON = fa.TicketPolicy(True, 2)


def alloc(order=("MatchSkip",), **fields):
    return fa.Allocation(order=tuple(order), **fields)


class AllocationMathTests(unittest.TestCase):
    def test_all_in_default_uses_every_observed_right(self):
        match = mt.MatchConfig(sample_only=False)
        self.assertEqual((match.count_mode, match.count), ("all", 20))
        a = fa.Allocation.from_configs(match, None)
        full = fa.plan(a, (20, 20), OFF)
        self.assertEqual((full.status, full.match, full.total, full.errors), ("ok", 20, 20, ()))
        part = fa.plan(a, (7, 20), OFF)
        self.assertEqual((part.match, part.total), (7, 7))
        reserved = fa.plan(alloc(match_reserve=5), (7, 20), OFF)
        self.assertEqual(reserved.match, 2)
        empty = fa.plan(a, (0, 20), OFF)
        self.assertEqual((empty.status, empty.match), ("ok", 0))
        self.assertIn("match_all_nothing_left", empty.warnings)

    def test_unknown_rights_are_not_guessed(self):
        plan = fa.plan(alloc(), None, OFF)
        self.assertEqual((plan.status, plan.match, plan.total, plan.available), ("rights_unknown", None, None, None))
        self.assertIn("rights_unknown", plan.warnings)
        # A reading of another capacity (OCR noise, the +99 overflow) is not usable either.
        self.assertEqual(fa.plan(alloc(), (20, 99), OFF).status, "rights_unknown")
        # The +c part of a home reading a/b+c is never counted.
        plus = fa.plan(alloc(), {"count": 5, "max": 20, "plus": 3}, OFF)
        self.assertEqual((plus.match, plus.warnings), (5, ("plus_not_counted",)))

    def test_grade_first_then_match_takes_the_remainder(self):
        a = alloc(("GradeFes", "MatchSkip"), grade_runs=2)
        plan = fa.plan(a, (15, 20), OFF)
        self.assertEqual((plan.status, plan.grade, plan.match, plan.total), ("ok", 2, 13, 15))
        self.assertEqual(fa.plan(a, (2, 20), OFF).match, 0)
        unknown = fa.plan(a, None, OFF)
        self.assertEqual((unknown.status, unknown.grade, unknown.match), ("rights_unknown", 2, None))

    def test_all_in_match_before_grade_is_refused(self):
        plan = fa.plan(alloc(("MatchSkip", "GradeFes"), grade_runs=1), (20, 20), OFF)
        self.assertEqual(plan.errors, ("match_all_before_grade",))
        self.assertTrue(plan.refused)
        # A fixed Match count does not depend on the order.
        fixed = fa.plan(alloc(("MatchSkip", "GradeFes"), match_mode="fixed", match_count=10, grade_runs=1), (20, 20), OFF)
        self.assertEqual((fixed.status, fixed.match, fixed.total), ("ok", 10, 11))

    def test_custom_counts_and_over_allocation(self):
        a = alloc(("GradeFes", "MatchSkip"), match_mode="fixed", match_count=10, grade_runs=3)
        self.assertEqual(fa.plan(a, (13, 20), OFF).status, "ok")
        over = fa.plan(a, (12, 20), OFF)
        self.assertEqual((over.status, over.errors), ("refused", ("over_allocated",)))
        # Unknown rights: only the capacity 20 bound.
        big = alloc(("GradeFes", "MatchSkip"), match_mode="fixed", match_count=19, grade_runs=3)
        self.assertEqual(fa.plan(big, None, OFF).errors, ("over_allocated_capacity",))
        self.assertEqual(fa.plan(a, None, OFF).status, "rights_unknown")
        # Reserves count once (the largest).
        reserve = alloc(("GradeFes", "MatchSkip"), match_mode="fixed", match_count=10, match_reserve=5, grade_runs=3)
        self.assertEqual(fa.plan(reserve, (17, 20), OFF).errors, ("over_allocated",))
        self.assertEqual(fa.plan(reserve, (18, 20), OFF).status, "ok")

    def test_rehearsal_is_planned_in_its_selected_mode(self):
        grade = gt.GradeConfig.from_params({"mode": "rehearsal", "runs_per_day": 2})
        a = fa.Allocation.from_configs(None, grade)
        self.assertEqual((a.grade_runs, a.rehearsal_runs, a.grade_demand()), (0, 2, 2))
        plan = fa.plan(a, (20, 20), OFF)
        self.assertEqual((plan.grade, plan.rehearsal, plan.errors), (0, 2, ()))
        self.assertEqual(fa.plan(fa.Allocation.from_configs(None, gt.GradeConfig()), (20, 20), OFF).status, "ok")
        with self.assertRaises(ValueError):
            gt.GradeConfig.from_params({"rehearsal_runs": gt.REHEARSAL_MAX_RUNS + 1})
        with self.assertRaisesRegex(ValueError, "分开任务"):
            gt.GradeConfig.from_params({"runs_per_day": 1, "rehearsal_runs": 2})
        # Normal rehearsal is verified to use the same one-right pool as formal Grade.
        both = fa.Allocation.from_configs(mt.MatchConfig(sample_only=False), grade, order=("GradeFes", "MatchSkip"))
        self.assertEqual(fa.plan(both, (20, 20), OFF).match, 18)

    def test_sample_only_match_consumes_nothing(self):
        self.assertEqual(fa.Allocation.from_configs(mt.MatchConfig(), None).order, ())
        self.assertEqual(fa.plan(fa.Allocation(), None, OFF).status, "idle")


class RecoveryTicketTests(unittest.TestCase):
    def test_default_off_and_project_policy_disabled(self):
        self.assertFalse(mt.MatchConfig().use_recovery_tickets)
        self.assertFalse(gt.GradeConfig().use_recovery_tickets)
        policy = fa.load_policy()
        self.assertEqual(policy, fa.TicketPolicy(False, 0))  # project_policy.json is NOT changed by this batch
        self.assertIsNone(fa.recovery_refusal(False, 1, policy))
        self.assertEqual(fa.load_policy(ROOT / "no-such-policy.json"), fa.TicketPolicy())

    def test_enabled_switch_is_refused_by_policy_and_never_implemented(self):
        self.assertEqual(fa.recovery_refusal(True, 1, OFF), "recovery_policy_disabled")
        self.assertEqual(fa.recovery_refusal(True, 3, ON), "recovery_cap_exceeds_policy")
        self.assertEqual(fa.recovery_refusal(True, 2, ON), "recovery_not_implemented")
        a = alloc(("GradeFes", "MatchSkip"), match_mode="fixed", match_count=20, match_recovery=True,
                  match_recovery_cap=2, grade_runs=1)
        plan = fa.plan(a, (19, 20), OFF)
        self.assertEqual(plan.errors, ("recovery_policy_disabled",))
        # The plan still states what WOULD be needed (2 tickets for the shortfall), never as an authority.
        self.assertEqual(plan.recovery, {"MatchSkip": 2})
        short = fa.plan(alloc(("GradeFes", "MatchSkip"), match_mode="fixed", match_count=20, match_recovery=True,
                              match_recovery_cap=1, grade_runs=2), (19, 20), ON)
        self.assertIn("over_allocated", short.errors)
        self.assertIn("recovery_not_implemented", short.errors)

    def test_config_fields(self):
        config = mt.MatchConfig.from_params({"use_recovery_tickets": True, "recovery_cap": 3})
        self.assertEqual((config.use_recovery_tickets, config.recovery_cap), (True, 3))
        for bad in ({"recovery_cap": 4}, {"recovery_cap": 0}, {"use_recovery_tickets": "yes"}, {"count_mode": "max"}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                mt.MatchConfig.from_params(bad)
            with self.subTest(grade=bad), self.assertRaises(ValueError):
                gt.GradeConfig.from_params({k: v for k, v in bad.items() if k != "count_mode"} or {"rehearsal_runs": -1})


class MatchCountResolutionTests(unittest.TestCase):
    def test_all_mode_resolves_from_the_match_page_reading(self):
        config = mt.MatchConfig(sample_only=False, rights_reserve=1)
        resolved = config.resolved((7, 20))
        self.assertEqual((resolved.count, resolved.count_mode), (6, "fixed"))
        self.assertEqual(config.resolved((20, 20)).count, 19)
        self.assertIsNone(config.resolved((1, 20)))
        fixed = mt.MatchConfig(count=5, count_mode="fixed")
        self.assertIs(fixed.resolved((2, 20)), fixed)  # fixed keeps its count; the rights check then stops
        self.assertEqual(fa.match_all_count(30, 0), 20)  # never more than one batch of 20


class DefaultsConsistencyTests(unittest.TestCase):
    """PI default_case = Pipeline attach = Python dataclass defaults."""

    @classmethod
    def setUpClass(cls):
        cls.interface = run_tasks.load_interface()

    def plan(self, name, option=None):
        item = {"name": name, **({"option": option} if option else {})}
        return run_tasks.plan_tasks(self.interface, {"preset": None, "tasks": [item]})[0]

    def test_match_and_grade_defaults(self):
        # 2026-10-11: PI = Pipeline = Python default stage is "latest" (the latest stage of the observed progress);
        # 只采样 defaults to No (the skip still needs --allow-match-spend and a frame-bound grant).
        self.assertEqual(ui_schema.task_config(self.plan("MatchSkip"), "match.json", mt.MatchConfig),
                         mt.MatchConfig(stage="latest", sample_only=False))
        self.assertEqual(ui_schema.task_config(self.plan("GradeFes"), "grade.json", gt.GradeConfig),
                         gt.GradeConfig(live_play=True))  # 2026-10-11: 自动演出 on by default
        options = self.interface.options
        self.assertEqual(options["Match次数"]["default_case"], "全部用完")
        self.assertEqual(options["Match回复券"]["default_case"], "No")
        self.assertEqual(options["Grade回复券"]["default_case"], "No")
        self.assertEqual(options["Grade模式"]["default_case"], "グレードフェス")
        self.assertEqual(options["Grade一次消耗"]["default_case"], "1")
        names = [c["name"] for c in options["Match次数"]["cases"]]
        self.assertEqual(names, ["全部用完"] + [str(n) for n in range(20, 0, -1)])

    def test_option_cases_map_to_configs(self):
        fixed = ui_schema.task_config(self.plan("MatchSkip", {"Match次数": "7", "Match回复券": "Yes",
                                                              "Match回复券上限": "3"}), "match.json", mt.MatchConfig)
        self.assertEqual((fixed.count, fixed.count_mode, fixed.use_recovery_tickets, fixed.recovery_cap), (7, "fixed", True, 3))
        grade = ui_schema.task_config(self.plan("GradeFes", {"Grade模式": "フェスリハーサル", "Grade每日次数": "3",
                                                             "Grade回复券": "Yes"}), "grade.json", gt.GradeConfig)
        self.assertEqual((grade.rehearsal_runs, grade.grade_runs, grade.use_recovery_tickets), (3, 0, True))
        bulk = ui_schema.task_config(self.plan("GradeFes", {"Grade每日次数": "20", "Grade一次消耗": "5"}), "grade.json",
                                     gt.GradeConfig)
        self.assertEqual((bulk.grade_runs, bulk.bulk_count), (20, 5))

    def test_daily_preset_runs_grade_before_all_in_match(self):
        preset = self.interface.presets["daily"]["task"]
        names = [t["name"] for t in preset]
        self.assertLess(names.index("GradeFes"), names.index("MatchSkip"))
        match = next(t for t in preset if t["name"] == "MatchSkip")
        self.assertEqual(match["option"]["Match次数"], "全部用完")



def isolate_grade_ledger(test):
    """The preflight reads today's Grade ledger (grade_task.daily_progress); use an empty private one, never the real
    run_logs (2026-10-23: a day with real Grade matches made these expectations depend on the user's play)."""
    root = ROOT / "dev_assets" / "private" / "run_logs" / ("_unittest_fes_preflight_" + test.id().rsplit(".", 1)[-1])
    shutil.rmtree(root, ignore_errors=True)
    test.addCleanup(shutil.rmtree, root, True)
    original = gt.daily_progress
    patcher = mock.patch.object(gt, "daily_progress", lambda config, log_root=None, now=None: original(config, root, now))
    patcher.start()
    test.addCleanup(patcher.stop)


class ProfilePreflightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = ui_schema.export_schema()

    def setUp(self):
        isolate_grade_ledger(self)

    def profile(self, *tasks):
        return {"schema_version": 1, "preset": None, "tasks": list(tasks)}

    MATCH = {"name": "MatchSkip", "enabled": True, "option": {"Match只采样": "No"}}

    def grade(self, **option):
        return {"name": "GradeFes", "enabled": True, "option": {"Grade使用参加权": "Yes", **option}}

    def test_match_and_grade_together_are_allowed(self):
        profile = self.profile(self.grade(), self.MATCH)
        config = ui_schema.profile_to_run_config(self.schema, profile)
        self.assertEqual([t["name"] for t in config["tasks"]], ["GradeFes", "MatchSkip"])
        plan = ui_schema.fes_preflight(self.schema, profile, observed=(20, 20))
        self.assertEqual((plan.status, plan.grade, plan.match, plan.total), ("ok", 1, 19, 20))
        self.assertEqual(json.loads((ROOT / "ui" / "annotations.json").read_text(encoding="utf-8"))["mutual_exclusion"], [])
        self.assertEqual(self.schema["run_config"]["fes_tickets"], {"enabled": False, "max_per_run": 0})

    def test_refusals_carry_clear_reasons(self):
        cases = {
            "回复券": self.profile(self.grade(), {"name": "MatchSkip", "option": {"Match只采样": "No", "Match回复券": "Yes"}}),
            "排在": self.profile(self.MATCH, self.grade()),
            "超过上限": self.profile(self.grade(**{"Grade每日次数": "3"}),
                                 {"name": "MatchSkip", "option": {"Match只采样": "No", "Match次数": "18"}}),
        }
        for needle, profile in cases.items():
            with self.subTest(needle), self.assertRaisesRegex(ValueError, needle):
                ui_schema.profile_to_run_config(self.schema, profile)

    def test_grade_without_rights_consent_and_sample_only_match_are_not_planned(self):
        profile = self.profile(self.MATCH, {"name": "GradeFes", "enabled": True, "option": {"Grade每日次数": "3"}})
        plan = ui_schema.fes_preflight(self.schema, profile, observed=(20, 20))
        self.assertEqual((plan.grade, plan.match), (0, 20))
        sample = self.profile({"name": "MatchSkip", "option": {"Match只采样": "Yes", "Match回复券": "Yes"}})
        self.assertEqual(ui_schema.fes_preflight(self.schema, sample).status, "idle")


@unittest.skipUnless(shutil.which("node"), "node not installed")
class FesPlanJsParityTests(unittest.TestCase):
    """ui/components/fes-plan.js mirrors fes_allocation.plan for the UI warnings that block 开始."""

    def setUp(self):
        isolate_grade_ledger(self)

    def run_js(self, profile, observed):
        script = ("import { readFileSync } from 'node:fs';"
                  "import { fesPlan } from './ui/components/fes-plan.js';"
                  "const schema = JSON.parse(readFileSync('ui/fixtures/schema.json', 'utf8'));"
                  "const [profile, observed] = JSON.parse(process.argv[1]);"
                  "const p = fesPlan({ schema, profile }, observed);"
                  "console.log(JSON.stringify({ status: p.status, match: p.match, grade: p.grade, rehearsal: p.rehearsal,"
                  " total: p.total, errors: p.errors, messages: p.messages, summary: p.summary }));")
        result = subprocess.run(["node", "--input-type=module", "-e", script, json.dumps([profile, observed])], cwd=ROOT,
                                capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_js_matches_python(self):
        schema = ui_schema.export_schema()
        grade = {"name": "GradeFes", "enabled": True, "option": {"Grade使用参加权": "Yes", "Grade每日次数": "2"}}
        match = {"name": "MatchSkip", "enabled": True, "option": {"Match只采样": "No"}}
        profiles = [
            [grade, match], [match, grade], [match],
            [grade, {**match, "option": {"Match只采样": "No", "Match次数": "19"}}],
            [{**grade, "option": {**grade["option"], "Grade模式": "フェスリハーサル", "Grade回复券": "Yes"}}],
            [{**match, "option": {"Match只采样": "Yes"}}],
        ]
        for tasks in profiles:
            for observed in (None, 12, 20):
                profile = {"schema_version": 1, "preset": None, "tasks": tasks}
                with self.subTest(tasks=[t["name"] for t in tasks], observed=observed):
                    py = ui_schema.fes_preflight(schema, profile, observed=None if observed is None else (observed, 20))
                    js = self.run_js(profile, observed)
                    self.assertEqual(js["status"], py.status)
                    self.assertEqual(js["errors"], list(py.errors))
                    self.assertEqual(js["messages"], [fa.MESSAGES.get(code, code) for code in py.errors])
                    self.assertEqual((js["match"], js["grade"], js["rehearsal"], js["total"]),
                                     (py.match, py.grade, py.rehearsal, py.total))
        both = self.run_js({"schema_version": 1, "tasks": [grade, match]}, None)
        # fes-plan.js now appends the daily-progress sentence (copy pass 2026-10-13).
        self.assertEqual(both["summary"], "参加权分配：Grade 2 + Match 用完剩余；本轮 2 局")


@unittest.skipUnless(HAVE_MAA, "需要 MaaFramework Python 依赖")
class RuntimeStopTests(unittest.TestCase):
    class Context:
        def __init__(self, nodes):
            self.nodes = nodes

        def get_node_data(self, name):
            return {"attach": self.nodes.get(name, {})}

    class Frame:
        def tobytes(self):
            return b"observed pixels"

    def setUp(self):
        # Task logs must live under dev_assets/private (task_common.JsonlLog).
        self.root = ROOT / "dev_assets" / "private" / "run_logs" / ("_unittest_fes_" + self.id().rsplit(".", 1)[-1])
        shutil.rmtree(self.root, ignore_errors=True)
        self.addCleanup(shutil.rmtree, self.root, True)
        self.tmp = SimpleNamespace(name=str(self.root))

    def begin(self, runtime_class, nodes, config_nodes, **kwargs):
        runtime = runtime_class(log_root=Path(self.tmp.name), **kwargs)
        runtime.begin(self.Context(nodes), {"config_nodes": list(config_nodes)})
        return runtime

    def test_recovery_switch_stops_match_and_grade_before_any_click(self):
        from agent.core import grade_custom, match_custom
        match = self.begin(match_custom.MatchRuntime, {"SC.Match.Config.Recovery": {"use_recovery_tickets": True}},
                           match_custom.CONFIG_NODES)
        self.assertEqual(match.run.outcome, "recovery_policy_disabled")
        self.assertIn("回复券未开放", match.run.notes[0])  # player wording; no file name (copy pass 2026-10-13)
        grade = self.begin(grade_custom.GradeRuntime, {"SC.Grade.Config.Recovery": {"use_recovery_tickets": True}},
                           grade_custom.CONFIG_NODES)
        self.assertEqual(grade.run.outcome, "recovery_policy_disabled")

    def test_legacy_mixed_rehearsal_count_is_invalid(self):
        from agent.core import grade_custom
        grade = self.begin(grade_custom.GradeRuntime, {"SC.Grade.Config.Rehearsal": {"rehearsal_runs": 1}},
                           grade_custom.CONFIG_NODES)
        self.assertEqual((grade.run.outcome, grade.run.succeeded), ("config_invalid", False))
        self.assertFalse(grade.run.halted)  # the next task still runs
        default = self.begin(grade_custom.GradeRuntime, {}, grade_custom.CONFIG_NODES)
        self.assertNotEqual(default.run.outcome, "rehearsal_not_sampled")

    def match_runtime(self, config, rights_text):
        from agent.core.match_custom import MatchRuntime
        progress = mp.MatchProgress("offline-fixture-cycle", "ready", True,
                                    tuple(mp.StageProgress(f"A1-S{i}", i, True) for i in mt.SLOTS))
        runtime = MatchRuntime(log_root=Path(self.tmp.name), progress=progress,
                               authority=Mock(can_spend=lambda action: action != "match-play-live-start"))  # skip only
        runtime.progress_reader = lambda context, image: mp.ProgressObservation(
            progress.cycle_id, "ready", mt.tc.image_digest(image), runtime.clock())
        runtime.run = mt.MatchRun(config, runtime.log, progress)
        runtime.started(runtime.run)
        runtime.vision = lambda context: SimpleNamespace(number=lambda *args: rights_text)
        return runtime

    def test_all_in_match_takes_its_count_from_the_match_page(self):
        runtime = self.match_runtime(mt.MatchConfig(sample_only=False, rights_reserve=2), "9/20")
        self.assertTrue(runtime._open_skip(None, self.Frame()))
        self.assertEqual((runtime.run.config.count, runtime.run.planner.config.count), (7, 7))
        self.assertEqual(runtime.run.config.count_mode, "fixed")
        empty = self.match_runtime(mt.MatchConfig(sample_only=False, rights_reserve=2), "2/20")
        self.assertFalse(empty._open_skip(None, self.Frame()))
        self.assertEqual((empty.run.outcome, empty.run.succeeded), ("nothing_to_do", True))
        fixed = self.match_runtime(mt.MatchConfig(count=10, count_mode="fixed", sample_only=False), "9/20")
        self.assertFalse(fixed._open_skip(None, self.Frame()))
        self.assertEqual(fixed.run.outcome, "rights_insufficient")


if __name__ == "__main__":
    unittest.main()
