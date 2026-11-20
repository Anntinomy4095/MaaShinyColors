"""B3: PI v2 task files, preset order, the thin launcher (tools/run_tasks.py) and offline task replay."""
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from agent.core import pipeline_actions as pa
from tools import build_native_resource as bundle_tool
from tools import run_tasks as launcher
from tools.check_project import load_jsonc

ROOT = Path(__file__).resolve().parent.parent
PIPELINE = ROOT / "assets/resource/base/pipeline"
PRIVATE = ROOT / "dev_assets/private"
# 2026-10-11: 礼物箱 / 任务奖励 one-click claims on by default (claims after Fes); 营业 (再営業へ → 決定) before the read-only status.
DAILY_ORDER = ["Startup", "PresentClaim", "Shop", "Training", "GradeFes", "MatchSkip", "MissionsClaim", "Business",
               "MissionStatus", "CollaboFes"]
# 2026-11-19 user: 礼物箱, Grade Fes and Collabo Fes stay in the 方案 order but start unchecked.
DAILY_UNCHECKED = {"PresentClaim", "GradeFes", "CollaboFes"}
# 2026-10-22: WeeklyProduce left the placeholders (real flow, every click disabled; tests/test_weekly_auto.py).
PLACEHOLDERS = {"AutoProduce": "SC.Produce.Entry"}
# Ordinary daily input includes the three rehearsal difficulty entries. Match consumes on Start;
# Confirm only prepares its amount. Wing is supervised-only and excluded by ordinary_click_nodes().
SPENDING_NODES = {"SC.Shop.Confirm.Exchange", "SC.Training.Confirm.Start", "SC.Grade.Unit.Confirm",
                  "SC.Grade.Rehearsal.Easy", "SC.Grade.Rehearsal.Normal", "SC.Grade.Rehearsal.Hard",
                  "SC.Match.Skip.Start", "SC.WeeklyProduce.Dialog.Start", "SC.WeeklyProduce.Recovery.Start",
                  "SC.Match.Play.LiveStart", "SC.Collabo.Do.Tension"}
ALL_SPEND = ("shop-exchange-confirm", "training-start", "grade-tension-confirm", "match-skip-confirm",
             "grade-rehearsal-easy", "grade-rehearsal-normal", "grade-rehearsal-hard", "weekly-auto-start",
             "weekly-auto-soda-start", "match-play-live-start", "collabo-tension-decide")

V24 = "dev_assets/private/nav-v24-game-start-rep-20261002-frames/"
V25 = "dev_assets/private/nav-v25-reward-ack-20261002-frames/"
V25B = "dev_assets/private/nav-v25-reward-ack2-20261002-frames/"
V26 = "dev_assets/private/nav-v26-mission-back-20261002-frames/"
HOME = "dev_assets/private/nav-v27-pickup-close-20261002-frames/frame-005.png"
# Live Home 2026-10-03 05:15 without the ミッション badge (MissionStatus was refused at entry before the fix).
HOME_NO_BADGE = "dev_assets/private/manual-capture-kept-20261002/home-20261003-nobadge.png"
# Title → Now Loading → login board → reward OK ×2 (board between) → ミッション達成 OK → ピックアップ × → Home.
# Real successors except the Pickup (from the mission-back run of the same day) and the final Home.
STARTUP_STEPS = [(V24 + "frame-001.png", "click"), (V24 + "frame-004.png", 3), (V24 + "frame-015.png", 3),
                 (V24 + "frame-016.png", "click"), (V25 + "frame-004.png", 3), (V25 + "frame-013.png", "click"),
                 (V25B + "frame-004.png", 3), (V25B + "frame-005.png", "click"), (V26 + "frame-009.png", "click"),
                 (HOME, "click")]
STARTUP_ACTIONS = ["game-start", "popup-reward-ok", "popup-reward-ok", "popup-mission-completed-ok", "popup-pickup-close"]
MISSION_ACTIONS = ["home-mission", "nav-back"]
TABLE = pa.default_table()
RISK_POLICY = {"live_input_enabled": True,
               "risk_acceptance_reference": launcher.load_policy().get("risk_acceptance_reference")}
MISSION_STEPS = [(V26 + "frame-001.png", "click"), (HOME, "click")]


def ordinary_click_nodes():
    """Click nodes an ordinary (non-supervised) launch may enable: supervised-only scans (SC.CardScan.*) never."""
    return {n for n in launcher.click_nodes() if not n.startswith(launcher.SUPERVISED_ONLY_NODE_PREFIXES)}


def pipeline_nodes():
    nodes = {}
    for path in sorted(PIPELINE.rglob("*.json")):
        nodes.update(load_jsonc(path))
    return nodes


class InterfaceTaskFilesTests(unittest.TestCase):
    def setUp(self):
        self.interface = launcher.load_interface()

    def test_interface_imports_every_task_file_and_the_preset(self):
        data = load_jsonc(ROOT / "assets/interface.json")
        self.assertNotIn("task", data)
        imported = {Path(p).as_posix() for p in data["import"]}
        files = {f"tasks/{p.name}" for p in (ROOT / "assets/tasks").glob("*.json")}
        self.assertLessEqual(files, imported)
        self.assertIn("presets/daily.json", imported)
        for name in DAILY_ORDER + ["MissionsClaim", "Business", "AutoProduce", "Diagnostics"]:
            self.assertIn(name, self.interface.tasks)

    def test_daily_preset_lists_the_default_order(self):
        preset = self.interface.presets["daily"]
        self.assertEqual([item["name"] for item in preset["task"]], DAILY_ORDER)
        plans = launcher.plan_tasks(self.interface, {})
        # MatchSkip uses the remaining rights at the latest stage of the observed progress.
        self.assertEqual([p.name for p in plans], [n for n in DAILY_ORDER if n not in DAILY_UNCHECKED])
        self.assertEqual([p.entry for p in plans], ["SC.Startup.Entry", "SC.Shop.Entry",
                                                    "SC.Training.Entry", "SC.Match.Entry", "SC.Missions.Entry",
                                                    "SC.Business.Entry", "SC.MissionStatus.Entry"])
        business = next(p for p in plans if p.name == "Business")
        self.assertEqual(business.pipeline_override["SC.Business.Config.Redispatch"]["attach"], {"redispatch": "decide"})
        self.assertLess(DAILY_ORDER.index("MatchSkip"), DAILY_ORDER.index("MissionsClaim"))

    def test_run_config_order_is_honored(self):
        order = ["Diagnostics", "MissionStatus", "Shop", "Startup"]
        plans = launcher.plan_tasks(self.interface, {"tasks": order})
        self.assertEqual([p.name for p in plans], order)
        plans = launcher.plan_tasks(self.interface, {"tasks": [{"name": "Startup", "enabled": False}, "Diagnostics"]})
        self.assertEqual([p.name for p in plans], ["Diagnostics"])
        with self.assertRaises(ValueError):
            launcher.plan_tasks(self.interface, {"tasks": ["NoSuchTask"]})
        with self.assertRaises(ValueError):
            launcher.plan_tasks(self.interface, {"unknown_key": 1})

    def test_task_options_resolve_to_pipeline_override(self):
        plan, = launcher.plan_tasks(self.interface, {"tasks": [{"name": "ProduceDryRun", "option": {"Produce策略": "稳健保体力"}}]})
        self.assertEqual(plan.pipeline_override["SC.Produce.DryRun"]["custom_action_param"], {"profile": "safe"})
        plan, = launcher.plan_tasks(self.interface, {"tasks": ["ProduceDryRun"]})
        self.assertEqual(plan.pipeline_override["SC.Produce.DryRun"]["custom_action_param"], {"profile": "balanced"})

    def test_mission_claim_uses_its_own_entry_and_bulk_by_default(self):
        # 2026-10-10 user: デイリー一括 claim on by default (claiming spends nothing); single/off stay developer cases.
        task = self.interface.tasks["MissionsClaim"]
        self.assertEqual(task["entry"], "SC.Missions.Entry")
        self.assertTrue(task["default_check"])
        self.assertIn("任务奖励领取", task["option"])
        option = self.interface.options["任务奖励领取"]
        self.assertEqual(option["default_case"], "开启（デイリー一括）")
        self.assertEqual([case["name"] for case in option["cases"]], ["关闭", "开启（デイリー一括）", "开启（仅デイリー一括）", "开启（每日登录，单次）"])

    def test_placeholders_are_unchecked_described_and_fail_without_input(self):
        nodes = pipeline_nodes()
        for name, entry in PLACEHOLDERS.items():
            task = self.interface.tasks[name]
            self.assertEqual(task["entry"], entry)
            self.assertFalse(task.get("default_check", False), name)
            # 2026-10-11: W.I.N.G. runs only from the 任务 page (supervised Wing entry); its queue entry still fails.
            self.assertIn("未迁移" if name != "AutoProduce" else "普通任务队列不运行", task["description"], name)
            node = nodes[entry]
            self.assertEqual((node["action"], node["custom_action"]), ("Custom", "SC.FailTask"), name)
            self.assertTrue(node["custom_action_param"]["reason"].startswith("not_migrated:"), name)
            self.assertNotIn("next", node)

    def test_new_click_nodes_are_disabled_guarded_and_bounded(self):
        nodes = pipeline_nodes()
        expected = {"SC.Startup.TouchToStart": [725, 604, 130, 52], "SC.MissionStatus.OpenMission": [25, 394, 76, 70]}
        for name, target in expected.items():
            node = nodes[name]
            self.assertIs(node["enabled"], False, name)
            self.assertEqual(node["target"], target, name)
            self.assertEqual(node["max_hit"], 1, name)
            self.assertEqual(node["on_error"], ["SC.Common.FailTask"], name)
        self.assertLessEqual(set(expected), set(launcher.click_nodes()))

    def test_every_click_node_targets_exactly_one_action_rect(self):
        nodes = pipeline_nodes()
        bound = launcher.click_node_actions()
        reserved = {n for a in TABLE.actions if not a.enabled for n in a.nodes}
        self.assertEqual(set(bound), {n for n, v in nodes.items() if isinstance(v, dict)
                                     and v.get("action") in ("Click", "Swipe") and n not in reserved})
        for name, action in bound.items():
            from agent.core.pipeline_actions import is_drag_node, is_match_row_target
            self.assertTrue(nodes[name].get("target") == list(TABLE.get(action).rect)
                            or is_match_row_target(name, nodes[name], action)
                            or is_drag_node(nodes[name], TABLE.get(action)), name)
        self.assertEqual(sorted(set(bound.values())), sorted(a.id for a in TABLE.actions if a.enabled))
        self.assertFalse(set(bound) & {n for a in TABLE.actions if not a.enabled for n in a.nodes})
        self.assertEqual(bound["SC.Popup.Announcement.Close"], "nav-back")
        self.assertEqual({bound[n] for n in ("SC.Popup.MissionHelp.Close", "SC.Popup.MatchHelp.Close",
                                              "SC.Popup.TrainingHelp.Close", "SC.Popup.FesHelp.Close")}, {"popup-help-close"})

    def test_mission_home_clean_matches_the_shared_list(self):
        nodes = pipeline_nodes()
        self.assertEqual(nodes["SC.MissionStatus.HomeClean"]["any_of"], nodes["SC.Common.HomeClean"]["any_of"])
        self.assertIs(nodes["SC.MissionStatus.HomeClean"]["inverse"], True)

    def test_startup_templates_are_built_by_the_bundle_tool(self):
        templates = bundle_tool.pipeline_templates(bundle_tool.PIPELINE_DIR)
        for name in ("start_screen/touch_to_start.png", "start_screen/publisher.png"):
            self.assertIn(name, bundle_tool.CROPPED)
            crop = bundle_tool.CROPPED[name][1]
            if name == "start_screen/publisher.png":
                # Padded ROI: the bubble-interior crop must sit inside it (webview offsets shift the frame by px).
                (roi,) = templates[name]
                self.assertTrue(roi[0] <= crop[0] and roi[1] <= crop[1] and roi[0] + roi[2] >= crop[0] + crop[2]
                                and roi[1] + roi[3] >= crop[1] + crop[3], roi)
            else:
                self.assertEqual(templates[name], [crop])

    @unittest.skipUnless(importlib.util.find_spec("jsonschema"), "需要 jsonschema")
    def test_interface_and_task_files_are_schema_valid(self):
        result = subprocess.run(
            [sys.executable, "tools/validate_schema.py", "--schema-dir", "deps/tools", "--resource-dirs",
             "assets/resource", "--interface-files", "assets/interface.json", "--task-dirs", "assets/tasks",
             "assets/presets"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
        self.assertEqual(result.returncode, 0, result.stdout[-2000:])


class FakeJob:
    def __init__(self, ok, done=True):
        self.succeeded, self.done = ok, done

    def get(self):
        return None


class FakeTasker:
    def __init__(self, outcomes):
        self.outcomes, self.posted, self.stopped = outcomes, [], 0

    def post_task(self, entry, override):
        self.posted.append((entry, override))
        outcome = self.outcomes[entry]
        if isinstance(outcome, Exception):
            raise outcome
        return FakeJob(outcome == "ok", done=outcome != "hang")

    def post_stop(self):
        self.stopped += 1
        return type("Stop", (), {"wait": lambda self: None})()


class LauncherExecutionTests(unittest.TestCase):
    def plans(self, *entries):
        return [launcher.TaskPlan(entry.split(".")[1], entry, {}) for entry in entries]

    def test_failure_is_isolated_and_the_next_task_runs(self):
        tasker = FakeTasker({"SC.A.Entry": "ok", "SC.B.Entry": "fail", "SC.C.Entry": "ok"})
        results = launcher.execute_plan(self.plans("SC.A.Entry", "SC.B.Entry", "SC.C.Entry"), tasker)
        self.assertEqual([e for e, _ in tasker.posted], ["SC.A.Entry", "SC.B.Entry", "SC.C.Entry"])
        self.assertEqual([r["status"] for r in results], ["succeeded", "failed", "succeeded"])

    def test_exception_and_timeout_are_isolated(self):
        ticks = iter(range(0, 10000, 5))
        tasker = FakeTasker({"SC.A.Entry": RuntimeError("boom"), "SC.B.Entry": "hang", "SC.C.Entry": "ok"})
        results = launcher.execute_plan(self.plans("SC.A.Entry", "SC.B.Entry", "SC.C.Entry"), tasker,
                                        timeout_s=12, clock=lambda: next(ticks), sleep=lambda s: None)
        self.assertEqual([r["status"] for r in results], ["error", "timeout", "succeeded"])
        self.assertEqual(tasker.stopped, 1)

    def test_task_timeouts_replace_the_default_for_that_task(self):
        ticks = iter(range(0, 10000, 5))
        tasker = FakeTasker({"SC.A.Entry": "hang", "SC.B.Entry": "hang"})
        results = launcher.execute_plan(self.plans("SC.A.Entry", "SC.B.Entry"), tasker, timeout_s=12,
                                        task_timeouts={"A": 40}, clock=lambda: next(ticks), sleep=lambda s: None)
        self.assertEqual([r["status"] for r in results], ["timeout", "timeout"])
        self.assertIn("40", results[0]["fail_reasons"][0] if results[0].get("fail_reasons") else str(results[0]))
        self.assertEqual(launcher.collabo_task_timeouts({}, launcher.LIVE_INPUT),
                         {"CollaboFes": launcher.COLLABO_TASK_TIMEOUT_S})
        self.assertEqual(launcher.collabo_task_timeouts({}, launcher.OFFLINE_SIMULATED), {})

    def test_mode_override_is_merged_under_each_task_override(self):
        tasker = FakeTasker({"SC.A.Entry": "ok"})
        plan = launcher.TaskPlan("A", "SC.A.Entry", {"SC.X": {"custom_action_param": {"p": 1}}})
        launcher.execute_plan([plan], tasker, base_override={"SC.Y": {"enabled": True}})
        self.assertEqual(tasker.posted[0][1], {"SC.Y": {"enabled": True}, "SC.X": {"custom_action_param": {"p": 1}}})


class ReadOnlyInputTests(unittest.TestCase):
    def test_read_only_replay_refuses_clicks_and_keeps_the_frame(self):
        frames = [np.zeros((720, 1280, 3), np.uint8), np.ones((720, 1280, 3), np.uint8)]
        scenario = launcher.ReplayScenario(frames, ["click", "click"], allow_input=False)
        scenario.screencap()
        self.assertFalse(scenario.click(760, 630))
        self.assertEqual((scenario.index, scenario.clicks, scenario.refused), (0, [], [(760, 630)]))
        self.assertFalse(scenario.deny("swipe"))
        # W.I.N.G. actions overlap daily rects by design (nodes bind actions): an unscoped scenario refuses an
        # ambiguous point; a run scoped to its families (as every launcher run is) resolves exactly one action.
        unscoped = launcher.ReplayScenario(frames, ["click", "click"], allow_input=True)
        self.assertGreater(len(unscoped.table.at((760, 630))), 1)
        self.assertFalse(unscoped.click(760, 630))
        self.assertEqual((unscoped.index, unscoped.clicks), (0, []))
        simulated = launcher.ReplayScenario(frames, ["click", "click"], allow_input=True,
                                            families=launcher.SUPERVISED_STARTUP_FAMILIES)
        self.assertFalse(simulated.click(150, 450))  # outside every startup/popup/nav rect: refused, frame kept
        self.assertEqual(simulated.index, 0)
        self.assertFalse(simulated.click(0, 0))  # canvas corner outside every action rect, including business slots
        self.assertTrue(simulated.click(760, 630))
        self.assertEqual((simulated.index, simulated.actions), (1, ["game-start"]))

    def test_input_mode_requires_live_flag_policy_risk_reference_and_explicit_tasks(self):
        closed = {"live_input_enabled": False, "risk_acceptance_reference": RISK_POLICY["risk_acceptance_reference"]}
        tasks = {"tasks": ["Startup", "MissionStatus"]}
        self.assertEqual(launcher.resolve_input_mode({}, RISK_POLICY, offline=False, simulate_input=False)[0],
                         launcher.READ_ONLY)
        with self.assertRaisesRegex(ValueError, "--live"):  # the run config alone never enables input
            launcher.resolve_input_mode({"input_enabled": True, **tasks}, RISK_POLICY, offline=False, simulate_input=False)
        with self.assertRaisesRegex(ValueError, "project_policy"):
            launcher.resolve_input_mode(tasks, closed, offline=False, simulate_input=False, live=True)
        with self.assertRaisesRegex(ValueError, "ADR-011"):
            launcher.resolve_input_mode(tasks, {"live_input_enabled": True}, offline=False, simulate_input=False, live=True)
        with self.assertRaisesRegex(ValueError, "ADR-011"):
            launcher.resolve_input_mode(tasks, {"live_input_enabled": True, "risk_acceptance_reference": "docs/SAFETY.md#nope"},
                                        offline=False, simulate_input=False, live=True)
        for config in ({}, {"preset": "daily"}, {"tasks": []}):
            with self.assertRaisesRegex(ValueError, "显式任务列表"):
                launcher.resolve_input_mode(config, RISK_POLICY, offline=False, simulate_input=False, live=True)
        with self.assertRaises(ValueError):
            launcher.resolve_input_mode({"input_enabled": False, **tasks}, RISK_POLICY, offline=False, simulate_input=False,
                                        live=True)
        self.assertEqual(launcher.resolve_input_mode(tasks, RISK_POLICY, offline=False, simulate_input=False, live=True)[0],
                         launcher.LIVE_INPUT)
        self.assertEqual(launcher.resolve_input_mode(tasks, launcher.load_policy(), offline=False, simulate_input=False,
                                                     live=True)[0], launcher.LIVE_INPUT)
        with self.assertRaises(ValueError):
            launcher.resolve_input_mode(tasks, RISK_POLICY, offline=True, simulate_input=True, live=True)
        with self.assertRaises(ValueError):
            launcher.resolve_input_mode({"input_enabled": True}, RISK_POLICY, offline=True, simulate_input=False)
        with self.assertRaises(ValueError):
            launcher.resolve_input_mode({}, RISK_POLICY, offline=False, simulate_input=True)
        self.assertEqual(launcher.resolve_input_mode({}, closed, offline=True, simulate_input=True)[0],
                         launcher.OFFLINE_SIMULATED)

    def test_live_override_enables_only_the_chosen_families(self):
        everything = launcher.mode_override(launcher.LIVE_INPUT, sorted(TABLE.families))
        self.assertEqual(set(everything), ordinary_click_nodes() - SPENDING_NODES)
        shop_only = launcher.mode_override(launcher.LIVE_INPUT, sorted(TABLE.families), ("shop-exchange-confirm",))
        self.assertEqual(set(shop_only), ordinary_click_nodes() - SPENDING_NODES | {"SC.Shop.Confirm.Exchange"})
        spending = launcher.mode_override(launcher.LIVE_INPUT, sorted(TABLE.families), ALL_SPEND)
        self.assertEqual(set(spending), ordinary_click_nodes())
        self.assertTrue(all(fields == {"enabled": True} for fields in everything.values()))
        startup_only = launcher.mode_override(launcher.LIVE_INPUT, ["startup"])
        # 2026-11-22: Startup also closes a leftover Collabo LIVE REPORT
        self.assertEqual(set(startup_only), {"SC.Startup.TouchToStart", "SC.Startup.CollaboReport.Close"})
        self.assertEqual(launcher.live_families({}), sorted(TABLE.families))
        for families in (["daily"], ["popup", "popup"], ["shop"]):
            with self.assertRaises(ValueError):
                launcher.live_families({"families": families})

    def test_no_click_caps_unless_named_explicitly(self):
        # No click caps; placeholders (never click) keep 0; an explicit cap still works.
        launcher.validate_run_config({"max_clicks": 30, "task_max_clicks": {"Startup": 10}, "session_max_minutes": 15})
        for name in ("Startup", "MissionStatus", "Shop", "MissionsClaim", "Training", "MatchSkip", "GradeFes"):
            self.assertIsNone(launcher.task_click_cap({}, name), name)
        self.assertEqual(launcher.task_click_cap({}, "AutoProduce"), 0)
        self.assertEqual(launcher.task_click_cap({}, "Diagnostics"), 0)
        launcher.validate_run_config({"allow_spend": ["shop-exchange-confirm"]})
        launcher.validate_run_config({"allow_spend": list(ALL_SPEND)})
        for bad in ({"allow_spend": ["shop-exchange-confirm-1"]}, {"allow_spend": "shop-exchange-confirm"},
                    {"allow_spend": ["training-confirm-one-bronze"]}, {"allow_spend": ["grade-difficulty-normal"]},
                    {"allow_spend": ["training-start", "training-start"]}):
            with self.assertRaises(ValueError):
                launcher.validate_run_config(bad)
        self.assertEqual(launcher.task_click_cap({"task_max_clicks": 5}, "MissionStatus"), 5)
        self.assertEqual(launcher.task_click_cap({"task_max_clicks": {"Startup": 3}}, "Startup"), 3)
        launcher.validate_run_config({"max_clicks": 5000, "task_max_clicks": {"Startup": 700}})
        for bad in ({"max_clicks": -1}, {"task_max_clicks": -1}, {"task_max_clicks": {"Startup": "70"}},
                    {"session_max_minutes": 721}, {"session_max_minutes": 0}, {"max_clicks": True}):
            with self.assertRaises(ValueError):
                launcher.validate_run_config(bad)

    def test_live_gate_requires_the_pipeline_session_file(self):
        root = PRIVATE / "run_logs" / "_unittest_launcher_session"
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, root, True)
        session = root / "capture.json"
        session.write_text(json.dumps({"version": 1, "host": "127.0.0.1", "port": 1, "token": "a" * 64, "pid": 7,
                                       "input_enabled": False}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "pipeline-input"):
            launcher.build_live_gate({"tasks": ["Startup"]}, session)

    def test_live_start_refuses_a_host_past_its_session_cap(self):
        """2026-10-03 14:46: the host (first gesture ~03:46 JST) refused GradeFes' only click with
        session_time_cap_reached; --live now refuses to start against such a host (restart it first)."""
        import datetime as dt

        root = PRIVATE / "run_logs" / "_unittest_host_session_cap"
        shutil.rmtree(root, ignore_errors=True)
        self.addCleanup(shutil.rmtree, root, True)
        root.mkdir(parents=True)
        pipeline = root / "s.json.pipeline-input.json"
        pipeline.write_text("﻿" + json.dumps({"pid": 42, "audit_root": str(root),
                                                   "limits": {"session_max_ms": 3_600_000}}), encoding="utf-8")
        now = dt.datetime(2026, 10, 3, 4, 46, tzinfo=dt.timezone.utc)  # JST 13:46, game day 2026-10-03
        self.assertIsNone(launcher.check_host_session(pipeline, now))  # no gesture yet: the cap has not started
        lines = [{"time": "2026-10-02T18:46:00+00:00", "pid": 7, "stage": "sent"},     # another host run
                 {"time": "2026-10-02T19:50:00+00:00", "pid": 42, "stage": "refused"},  # refusals do not start it
                 {"time": "2026-10-03T04:00:00+00:00", "pid": 42, "stage": "intent"},
                 {"time": "2026-10-03T04:00:01+00:00", "pid": 42, "stage": "sent"}]
        (root / "host-2026-10-03.jsonl").write_text("".join(json.dumps(r) + "\n" for r in lines), encoding="utf-8")
        self.assertAlmostEqual(launcher.check_host_session(pipeline, now), 46 * 60)
        with self.assertRaisesRegex(ValueError, "session_time_cap_reached"):
            launcher.check_host_session(pipeline, now + dt.timedelta(minutes=13))  # within the 2-minute margin
        old = [{"time": "2026-10-02T19:46:00+00:00", "pid": 42, "stage": "sent"}]  # JST 04:46: previous game day file
        (root / "host-2026-10-02.jsonl").write_text("".join(json.dumps(r) + "\n" for r in old), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "重启宿主"):
            launcher.check_host_session(pipeline, now)

    def test_host_session_age_counts_from_the_latest_run_window(self):
        """2026-10-11: a launcher's pipeline-run-begin opens a new host run window (audited as stage run_begin); only
        gestures after the latest one count toward the host's 60-minute cap. Refused begins do not start a window."""
        import datetime as dt

        root = PRIVATE / "run_logs" / "_unittest_host_run_window"
        shutil.rmtree(root, ignore_errors=True)
        self.addCleanup(shutil.rmtree, root, True)
        root.mkdir(parents=True)
        pipeline = root / "s.json.pipeline-input.json"
        pipeline.write_text(json.dumps({"pid": 42, "audit_root": str(root), "run_begin_command": "pipeline-run-begin",
                                        "limits": {"session_max_ms": 3_600_000}}), encoding="utf-8")
        now = dt.datetime(2026, 10, 3, 4, 46, tzinfo=dt.timezone.utc)
        lines = [{"time": "2026-10-03T02:00:00.1234567+00:00", "pid": 42, "stage": "sent"},          # old window
                 {"time": "2026-10-03T03:59:00+00:00", "pid": 42, "stage": "run_begin_refused"},
                 {"time": "2026-10-03T04:00:00+00:00", "pid": 7, "stage": "run_begin"},          # another host
                 {"time": "2026-10-03T04:10:00+00:00", "pid": 42, "stage": "run_begin"}]
        path = root / "host-2026-10-03.jsonl"
        path.write_text("".join(json.dumps(r) + "\n" for r in lines), encoding="utf-8")
        self.assertIsNone(launcher.host_session_age(pipeline, now))   # new window, nothing sent yet
        lines.append({"time": "2026-10-03T04:16:00+00:00", "pid": 42, "stage": "intent"})
        path.write_text("".join(json.dumps(r) + "\n" for r in lines), encoding="utf-8")
        self.assertAlmostEqual(launcher.check_host_session(pipeline, now), 30 * 60)
        lines[-1] = {"time": "2026-10-03T03:46:00+00:00", "pid": 42, "stage": "sent"}            # before the window
        path.write_text("".join(json.dumps(r) + "\n" for r in lines), encoding="utf-8")
        self.assertIsNone(launcher.host_session_age(pipeline, now))
        lines.pop(-2)                                                                            # no window: lifetime
        path.write_text("".join(json.dumps(r) + "\n" for r in lines), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "30 秒后重试"):
            launcher.check_host_session(pipeline, now)

    def test_begin_host_run_is_a_launcher_handshake(self):
        sent = []

        def send(session, request):
            sent.append((session, request))
            return {"ok": True, "attempted": False, "reason": "run_window_opened"}

        session = {"token": "a" * 64, "host": "127.0.0.1", "port": 1}
        self.assertIsNone(launcher.begin_host_run(session, send=send))           # old host: lifetime window, nothing sent
        self.assertEqual(sent, [])
        session["run_begin_command"] = "pipeline-run-begin"
        self.assertTrue(launcher.begin_host_run(session, send=send)["ok"])
        request = sent[-1][1]
        self.assertEqual(set(request), {"command", "protocol_version", "token", "nonce"})
        self.assertEqual((request["command"], request["protocol_version"], request["token"]), ("pipeline-run-begin", 2, "a" * 64))
        self.assertRegex(request["nonce"], "^[0-9a-f]{32}$")
        launcher.begin_host_run(session, send=send)
        self.assertNotEqual(sent[-1][1]["nonce"], request["nonce"])
        refused = launcher.begin_host_run(session, send=lambda *_: {"ok": False, "attempted": False,
                                                                     "reason": "run_begin_refused_recent_input"})
        self.assertFalse(refused["ok"])                                          # not fatal: the current window continues
        for bad in ({"ok": True}, {"ok": True, "attempted": True, "reason": "x"}, None):
            with self.assertRaises(ValueError):
                launcher.begin_host_run(session, send=lambda *_, b=bad: b)

    def test_after_task_runs_after_every_task(self):
        tasker = FakeTasker({"SC.A.Entry": "fail", "SC.B.Entry": "ok"})
        plans = [launcher.TaskPlan("A", "SC.A.Entry", {}), launcher.TaskPlan("B", "SC.B.Entry", {})]
        flushed = []
        results = launcher.execute_plan(plans, tasker, after_task=lambda plan: flushed.append(plan.name))
        self.assertEqual(flushed, ["A", "B"])  # unreported task runs are flushed after every task, success or not
        self.assertEqual([r["status"] for r in results], ["failed", "succeeded"])

    def test_session_cap_skips_remaining_tasks(self):
        tasker = FakeTasker({"SC.A.Entry": "ok", "SC.B.Entry": "ok"})
        plans = [launcher.TaskPlan("A", "SC.A.Entry", {}), launcher.TaskPlan("B", "SC.B.Entry", {})]
        results = launcher.execute_plan(plans, tasker, before_task=lambda plan: "session_time_cap_reached"
                                        if plan.name == "B" else None)
        self.assertEqual([r["status"] for r in results], ["succeeded", "skipped"])
        self.assertEqual(results[1]["fail_reasons"], ["session_time_cap_reached"])
        self.assertEqual([e for e, _ in tasker.posted], ["SC.A.Entry"])

    def test_read_only_override_enables_no_click_node(self):
        override = launcher.mode_override(launcher.READ_ONLY)
        self.assertFalse(any(fields.get("enabled") is True for fields in override.values()))
        simulated = launcher.mode_override(launcher.OFFLINE_SIMULATED)
        enabled = {name for name, fields in simulated.items() if fields.get("enabled") is True}
        self.assertEqual(enabled, ordinary_click_nodes() - SPENDING_NODES)
        spending = launcher.mode_override(launcher.OFFLINE_SIMULATED, allow_spend=ALL_SPEND)
        self.assertEqual({n for n, f in spending.items() if f.get("enabled") is True}, ordinary_click_nodes())

    def test_summary_and_frames_stay_private(self):
        with self.assertRaises(ValueError):
            launcher.write_summary({}, ROOT / "summary.json")
        with self.assertRaises(ValueError):
            launcher.private_png(str(ROOT / "README.md"))


class MissionStatusParserTests(unittest.TestCase):
    # OCR boxes measured on nav-v26-mission-back-20261002-frames/frame-001.png (ppocr_v6 small).
    ROWS = [{"text": "ゲームにログインしよう", "box": [391, 250, 247, 25]}, {"text": "達成度", "box": [408, 309, 62, 26]},
            {"text": "1/1", "box": [605, 310, 36, 26]}, {"text": "受け取る", "box": [1064, 267, 123, 33]},
            {"text": "フェスをプレイしよう", "box": [392, 390, 223, 25]}, {"text": "0/1", "box": [603, 448, 39, 28]},
            {"text": "詳細", "box": [1091, 401, 70, 41]},
            {"text": "マニーショップで買い物をしよう", "box": [393, 528, 335, 24]}, {"text": "詳細", "box": [1092, 541, 68, 35]}]
    COUNTER = [{"text": "ミッション達成数", "box": [980, 575, 148, 19]}, {"text": "1/5", "box": [1218, 576, 39, 26]}]

    def test_rows_are_classified(self):
        from agent.core.mission_status import build_report

        report = build_report(self.ROWS, self.COUNTER)
        self.assertTrue(report["ok"])
        self.assertEqual(report["counter"], [1, 5])
        self.assertEqual([r["status"] for r in report["rows"]], ["claimable", "incomplete", "unknown"])
        self.assertEqual(report["complete"], ["ゲームにログインしよう"])

    def test_contradictions_and_missing_counter_are_not_ok(self):
        from agent.core.mission_status import build_report, classify

        self.assertEqual(classify(0, 1, "受け取る"), "unknown")
        self.assertEqual(classify(1, 1, "詳細"), "unknown")
        self.assertEqual(classify(1, 1, None), "completed")
        self.assertFalse(build_report(self.ROWS, [])["ok"])
        self.assertFalse(build_report([], self.COUNTER)["ok"])


try:
    from maa.library import Library
    HAVE_MAA = Library.version() == launcher.FRAMEWORK
except Exception:  # pragma: no cover - environment without MaaFramework
    HAVE_MAA = False


def frames_present(steps):
    return all((ROOT / image).is_file() for image, _ in steps)


def private_ready():
    if not HAVE_MAA or bundle_tool.verify() or not frames_present(STARTUP_STEPS + MISSION_STEPS):
        return False
    from agent.core.resource_bundles import BASE_OCR_MODEL, ocr_model_dir
    return ocr_model_dir() is not None or (BASE_OCR_MODEL / "rec.onnx").is_file()


@unittest.skipUnless(HAVE_MAA, "需要 MaaFramework v5.12.3")
class MissionRuntimeLauncherTests(unittest.TestCase):
    def test_default_runtime_has_no_input_authority_or_frame_source(self):
        from agent.core import mission_claim_actions
        with patch.object(mission_claim_actions, "MissionRuntime") as runtime:
            launcher.task_runtimes(Mock())
        self.assertEqual(runtime.call_args.kwargs, {"input_enabled": False, "authority": None})

    def test_replay_scopes_claim_families_binds_raw_frame_and_flushes_only_claim_task(self):
        for name in ("Startup", "PresentClaim", "MissionsClaim"):
            for simulate in (False, True):
                with self.subTest(name=name, simulate=simulate):
                    mission = Mock(run=SimpleNamespace(reported=False, notes=[]))
                    captured = {}

                    def build(controller, *args, **kwargs):
                        captured["scenario"] = controller.scenario
                        return object()

                    def execute(plans, tasker, **kwargs):
                        scenario = captured["scenario"]
                        scenario.screencap()
                        kwargs["after_task"](plans[0])
                        return []

                    with patch.object(launcher, "task_runtimes", return_value={"missions": mission}) as runtimes, \
                         patch.object(launcher, "build_tasker", side_effect=build), \
                         patch.object(launcher, "execute_plan", side_effect=execute):
                        launcher._run({"tasks": [name]}, offline_images=([np.zeros((720, 1280, 3), np.uint8)], ["click"]),
                                      simulate_input=simulate, policy={"live_input_enabled": False},
                                      summary_path=False, save_diagnostics=False)
                    scenario = captured["scenario"]
                    options = runtimes.call_args.args[2]["missions"]
                    self.assertIs(options["frame_source"](), scenario._frame)
                    self.assertEqual(options["frame_clock"](), float(scenario.captures))
                    self.assertIs(runtimes.call_args.kwargs["missions_input"], simulate)
                    self.assertEqual("mission-claim" in scenario.families, name == "MissionsClaim")
                    self.assertEqual("present" in scenario.families, name == "PresentClaim")
                    self.assertEqual(mission.flush_on_task_end.call_count, int(name == "MissionsClaim"))
                    mission.report.assert_not_called()


class OfflineReplayIsolationTests(unittest.TestCase):
    """An offline replay must never append to the live ledgers (run_logs) the daily planner counts as real attempts."""

    def replay(self, config=None, **kwargs):
        from agent.core import task_common as tc

        captured, appended = {}, []
        real_append = tc.JsonlLog.append

        def spy(log, record):
            path = real_append(log, record)
            appended.append(path.resolve())
            return path

        def build(controller, *args, **options):
            captured["runtimes"] = options["runtimes"]
            captured["weekly_store"] = options.get("weekly_store")
            return object()

        def execute(plans, tasker, **options):
            runtimes = captured["runtimes"]
            for kind in ("grade", "match", "training", "business"):
                runtimes[kind].log.append({"event": "live_turn", "kind": kind})
            runtimes["shop"].log.append({"event": "probe"})
            tc.FrameSaver("grade-probe", runtimes["grade"].sample_root).save(np.zeros((720, 1280, 3), np.uint8))
            return []

        with patch.object(launcher, "build_tasker", side_effect=build), \
             patch.object(launcher, "execute_plan", side_effect=execute), \
             patch.object(tc.JsonlLog, "append", spy):
            summary = launcher.run(config or {"tasks": ["GradeFes", "MissionStatus"]},
                                   offline_images=([np.zeros((720, 1280, 3), np.uint8)], ["click"]),
                                   simulate_input=True, policy={"live_input_enabled": False},
                                   summary_path=False, save_diagnostics=False, **kwargs)
        root = Path(summary["offline"]["isolated_root"])
        self.addCleanup(shutil.rmtree, root, True)
        return summary, captured, root, appended

    def test_ledgers_and_samples_go_to_the_isolated_root_not_the_live_ones(self):
        from agent.core import task_common as tc

        live = tc.LOG_ROOT.resolve()
        summary, captured, root, appended = self.replay()
        self.assertTrue(root.resolve().is_relative_to((PRIVATE / "offline-replay").resolve()))
        self.assertFalse(root.resolve().is_relative_to(live))
        self.assertEqual(len(appended), 4)  # grade, match, training, business (the shop writes its own SpendLog)
        for path in appended:
            self.assertTrue(path.is_relative_to(root.resolve()), path)
            self.assertFalse(path.is_relative_to(live), path)
            self.assertTrue(path.is_file())
        shop = captured["runtimes"]["shop"].log
        self.assertTrue(shop.path().resolve().is_relative_to(root.resolve()))
        self.assertTrue(shop.path().is_file())
        for kind in ("grade", "match", "training", "business"):
            runtime = captured["runtimes"][kind]
            self.assertTrue(Path(runtime.sample_root).resolve().is_relative_to(root.resolve()))
        self.assertTrue(any(root.glob("samples/grade-probe-*/frame-001.png")))
        mission = captured["runtimes"]["missions"]
        for value in (mission.sample_root, mission.journal_path, mission.bulk_ledger_path,
                      captured["runtimes"]["present"].ledger_path, captured["weekly_store"],
                      captured["runtimes"]["card_scan"].run_dir, captured["runtimes"]["knowhow_scan"].run_dir):
            self.assertTrue(Path(value).resolve().is_relative_to(root.resolve()), value)
        self.assertFalse(captured["runtimes"]["card_scan"].write_import)
        self.assertFalse(captured["runtimes"]["knowhow_scan"].write_inventory)

    def test_two_replays_do_not_share_a_root_and_start_from_an_empty_ledger(self):
        _, _, first, _ = self.replay()
        _, _, second, _ = self.replay()
        self.assertNotEqual(first, second)

    def test_explicit_options_still_win(self):
        from agent.core import task_common as tc

        base = PRIVATE / "run_logs" / "_unittest_explicit_roots"
        self.addCleanup(shutil.rmtree, base, True)
        options = launcher.offline_replay_options({"grade": {"log_root": base, "now": None}}, {"log_root": base / "s"},
                                                  PRIVATE / "offline-replay" / "x")
        self.assertEqual(options["grade"]["log_root"], base)
        self.assertEqual(options["shop"]["log_root"], base / "s")
        self.assertEqual(options["match"]["log_root"], PRIVATE / "offline-replay" / "x" / "run_logs")
        self.assertNotEqual(options["match"]["log_root"], tc.LOG_ROOT)


@unittest.skipUnless(private_ready(), "需要 MaaFramework v5.12.3、私有模板包、OCR 模型与私有原生帧")
class OfflineTaskReplayTests(unittest.TestCase):
    def run_offline(self, tasks, steps, simulate_input=True):
        return launcher.run({"tasks": tasks}, offline_steps=[(ROOT / image, advance) for image, advance in steps],
                            simulate_input=simulate_input, policy={"live_input_enabled": False},
                            summary_path=False, save_diagnostics=False)

    def test_startup_from_title_through_popups_to_clean_home(self):
        summary = self.run_offline(["Startup"], STARTUP_STEPS)
        task, = summary["tasks"]
        self.assertEqual(task["status"], "succeeded", task)
        self.assertEqual(task["simulated_actions"], STARTUP_ACTIONS)
        for point, action in zip(task["simulated_clicks"], task["simulated_actions"]):
            self.assertTrue(TABLE.get(action).contains(point), (point, action))  # Maa samples inside the rect
        self.assertEqual(task["node_trace"][-1], "SC.Common.HomeClean")
        self.assertEqual(summary["offline"]["other_input_calls"], 0)
        self.assertEqual(summary["real_input_attempts"], 0)

    def test_read_only_startup_on_title_never_clicks(self):
        summary = self.run_offline(["Startup"], STARTUP_STEPS[:1], simulate_input=False)
        task, = summary["tasks"]
        self.assertEqual(summary["input_mode"], launcher.READ_ONLY)
        self.assertEqual((task["status"], task["simulated_clicks"]), ("failed", []))
        self.assertIn("SC.Common.FailTask", task["node_trace"])

    def test_queue_continues_after_a_failed_task_and_reads_missions(self):
        summary = self.run_offline(["Startup", "AutoProduce", "MissionStatus"], STARTUP_STEPS + MISSION_STEPS)
        self.assertEqual([t["status"] for t in summary["tasks"]], ["succeeded", "failed", "succeeded"])
        placeholder, missions = summary["tasks"][1], summary["tasks"][2]
        self.assertEqual(placeholder["fail_reasons"], ["not_migrated:produce"])
        self.assertEqual(missions["simulated_actions"], MISSION_ACTIONS)
        report, = [r for r in missions["reports"] if r["kind"] == "mission_status"]
        self.assertEqual(report["counter"], [1, 5])
        self.assertEqual(report["claimable"], ["ゲームにログインしよう"])
        self.assertEqual(report["incomplete"], ["フェスをプレイしよう"])

    def test_read_only_mission_status_reads_without_returning(self):
        summary = self.run_offline(["MissionStatus"], MISSION_STEPS[:1], simulate_input=False)
        task, = summary["tasks"]
        self.assertEqual((task["status"], task["simulated_clicks"], task["refused_clicks"]), ("succeeded", [], []))
        self.assertEqual(task["node_trace"][-1], "SC.MissionStatus.Return")

    def test_mission_status_accepts_home_without_badge(self):
        summary = self.run_offline(["MissionStatus"], [(HOME_NO_BADGE, "click"), (V26 + "frame-001.png", "click"),
                                                       (HOME, "click")])
        task, = summary["tasks"]
        self.assertIn("SC.MissionStatus.AtHome", task["node_trace"])
        self.assertEqual((task["status"], task["simulated_actions"]), ("succeeded", MISSION_ACTIONS))

    def test_diagnostics_classifies_title_and_home(self):
        for image, expected in ((V24 + "frame-001.png", "SC.Startup.StartScreen.Guard"), (HOME, "home_clean"),
                                (HOME_NO_BADGE, "home_clean")):
            summary = self.run_offline(["Diagnostics"], [(image, "click")], simulate_input=False)
            report, = [r for r in summary["tasks"][0]["reports"] if r["kind"] == "diagnostics"]
            self.assertEqual(report["screen"], expected, image)


class FakePipelineHost:
    """Replays private PNG frames as if they came from the host; ``send`` records the pipeline-click request and
    advances the frame like the host gesture would. No socket, no browser, no input."""
    PID = 4343

    def __init__(self, steps):
        self.blobs = [(ROOT / image).read_bytes() for image, _ in steps]
        self.advances = [advance for _, advance in steps]
        self.index = self.here = 0
        self.served = None
        self.session = {"pid": self.PID}
        self.requests = []
        self.capture_seq = self.frame_binding_seq = 0

    def status(self):
        blob = self.served if self.served is not None else self.blobs[self.index]
        return {"ready": True, "browser_demo": False, "input_enabled": False, "width": 1280, "height": 720,
                "composition_game": True, "minimized": False, "window_visible": True, "webview_visible": True,
                "supervised_navigation": None, "pipeline_input": True, "frame_sha256": hashlib.sha256(blob).hexdigest(),
                "frame_age_ms": 10.0, "source_capture_width": 1280, "source_capture_height": 720,
                "capture_seq": self.capture_seq, "frame_binding_seq": self.frame_binding_seq, "capture_pending": False}

    def screencap(self):
        self.capture_seq += 1
        self.frame_binding_seq += 2
        advance = self.advances[self.index]
        if isinstance(advance, int) and self.here >= advance and self.index + 1 < len(self.blobs):
            self.index, self.here = self.index + 1, 0
        self.here += 1
        self.served = self.blobs[self.index]
        return self.served

    def send(self, session, request):
        self.requests.append(request)
        if self.advances[self.index] == "click" and self.index + 1 < len(self.blobs):
            self.index, self.here = self.index + 1, 0
        return {"ok": True, "attempted": True, "reason": "native_gesture_sent_postcondition_required"}


@unittest.skipUnless(private_ready(), "需要 MaaFramework v5.12.3、私有模板包、OCR 模型与私有原生帧")
class LivePathOfflineTests(unittest.TestCase):
    """The --live code path (WebView2 gate, rect -> action mapping, pipeline-click requests) against a fake host."""

    def test_startup_and_mission_status_through_the_live_gate(self):
        from agent.core import webview_controller as wc

        audit_root = PRIVATE / "run_logs" / "_unittest_launcher_live"
        shutil.rmtree(audit_root, ignore_errors=True)
        self.addCleanup(shutil.rmtree, audit_root, True)
        host = FakePipelineHost(STARTUP_STEPS + MISSION_STEPS)
        session = {"version": 2, "frame_binding_protocol": wc.PIPELINE_FRAME_BINDING_PROTOCOL, "host": "127.0.0.1", "port": 50009, "token": "a" * 64, "pid": host.PID,
                   "input_enabled": True, "browser_demo_only": False, "transport": wc.PIPELINE_TRANSPORT,
                   "actions": TABLE.ids, "consuming_actions": TABLE.consuming_ids, "action_table_sha256": TABLE.sha256,
                   "spend_secret": "07" * 32, "audit_root": str(PRIVATE / "run_logs"),
                   "limits": {"min_interval_ms": 800, "interval_jitter_max_ms": 600, "per_action_per_minute": 12,
                              "run_max": 600, "run_spend_max": 8, "grant_max_ttl_ms": 5000, "session_max_ms": 3_600_000}}
        gate = wc.WebViewInputGate(host, wc.RunConfig(True, frozenset(TABLE.families)),
                                   {"pipeline": wc.Endpoint("pipeline", session)}, wc.SpendGate(),
                                   wc.AuditLog(audit_root), host.send, policy=RISK_POLICY)
        summary = launcher.run({"tasks": ["Startup", "MissionStatus"]}, live=True, gate=gate, policy=RISK_POLICY,
                               summary_path=False, save_diagnostics=False, frame_guard_enabled=True)
        self.assertEqual(summary["input_mode"], launcher.LIVE_INPUT)
        self.assertEqual([t["status"] for t in summary["tasks"]], ["succeeded", "succeeded"], summary["tasks"])
        startup, missions = summary["tasks"]
        self.assertEqual([c["action"] for c in startup["live_clicks"]], STARTUP_ACTIONS)
        self.assertEqual([c["action"] for c in missions["live_clicks"]], MISSION_ACTIONS)
        self.assertTrue(all(c["result"] == "sent" and c["task"] in ("Startup", "MissionStatus")
                            for c in startup["live_clicks"] + missions["live_clicks"]))
        self.assertEqual([r["action"] for r in host.requests], STARTUP_ACTIONS + MISSION_ACTIONS)
        self.assertTrue(all(set(r) == {"token", "command", "action", "frame_sha256", "source_width", "source_height",
                                        "nonce", "protocol_version", "capture_seq", "frame_binding_seq", "node_name"}
                            and r["command"] == "pipeline-click" and r["protocol_version"] == 2 for r in host.requests))
        self.assertTrue(all(r["node_name"] in TABLE.get(r["action"]).nodes for r in host.requests))
        self.assertEqual(summary["real_input_attempts"], 7)
        self.assertEqual(summary["live"]["task_caps"], {"Startup": None, "MissionStatus": None})
        report, = [r for r in missions["reports"] if r["kind"] == "mission_status"]
        self.assertEqual(report["counter"], [1, 5])
        self.assertEqual(missions["node_trace"][-1], "SC.Common.HomeClean")

    def test_live_task_cap_stops_the_task_without_more_requests(self):
        from agent.core import webview_controller as wc

        audit_root = PRIVATE / "run_logs" / "_unittest_launcher_live_cap"
        shutil.rmtree(audit_root, ignore_errors=True)
        self.addCleanup(shutil.rmtree, audit_root, True)
        host = FakePipelineHost(STARTUP_STEPS)
        session = {"version": 2, "frame_binding_protocol": wc.PIPELINE_FRAME_BINDING_PROTOCOL, "host": "127.0.0.1", "port": 50009, "token": "a" * 64, "pid": host.PID,
                   "input_enabled": True, "browser_demo_only": False, "transport": wc.PIPELINE_TRANSPORT,
                   "actions": TABLE.ids, "consuming_actions": TABLE.consuming_ids, "action_table_sha256": TABLE.sha256,
                   "spend_secret": "07" * 32, "audit_root": str(PRIVATE / "run_logs"),
                   "limits": {"min_interval_ms": 800, "interval_jitter_max_ms": 600, "per_action_per_minute": 12,
                              "run_max": 600, "run_spend_max": 8, "grant_max_ttl_ms": 5000, "session_max_ms": 3_600_000}}
        gate = wc.WebViewInputGate(host, wc.RunConfig(True, frozenset(TABLE.families)),
                                   {"pipeline": wc.Endpoint("pipeline", session)}, wc.SpendGate(),
                                   wc.AuditLog(audit_root), host.send, policy=RISK_POLICY)
        summary = launcher.run({"tasks": ["Startup"], "task_max_clicks": {"Startup": 2}}, live=True, gate=gate,
                               policy=RISK_POLICY, summary_path=False, save_diagnostics=False, frame_guard_enabled=True)
        task, = summary["tasks"]
        self.assertEqual(task["status"], "failed")
        self.assertEqual(len(host.requests), 2)
        self.assertIn("task_click_cap_reached", [c["reason"] for c in task["live_clicks"]])

    def test_host_session_cap_refusal_is_named_and_skips_the_rest(self):
        """Replay of 2026-10-03 14:46: on Fes main the host refused nav-back with session_time_cap_reached. The summary
        names the refusal, still carries the Grade report, and no later task posts any click."""
        from agent.core import webview_controller as wc

        audit_root = PRIVATE / "run_logs" / "_unittest_launcher_host_cap"
        shutil.rmtree(audit_root, ignore_errors=True)
        self.addCleanup(shutil.rmtree, audit_root, True)
        fes_main = "dev_assets/private/manual-capture-kept-20261002/fes-main-nothing-to-do-20261003.png"
        if not (ROOT / fes_main).is_file():
            self.skipTest("缺少 2026-10-03 14:46 Fes 主页帧")

        class CappedHost(FakePipelineHost):
            def send(self, session, request):
                self.requests.append(request)
                return {"ok": False, "attempted": False, "reason": "session_time_cap_reached"}

        host = CappedHost([(fes_main, "click"), (HOME_NO_BADGE, "click")])
        session = {"version": 2, "frame_binding_protocol": wc.PIPELINE_FRAME_BINDING_PROTOCOL, "host": "127.0.0.1", "port": 50009, "token": "a" * 64, "pid": host.PID,
                   "input_enabled": True, "browser_demo_only": False, "transport": wc.PIPELINE_TRANSPORT,
                   "actions": TABLE.ids, "consuming_actions": TABLE.consuming_ids, "action_table_sha256": TABLE.sha256,
                   "spend_secret": "07" * 32, "audit_root": str(PRIVATE / "run_logs"),
                   "limits": {"min_interval_ms": 800, "interval_jitter_max_ms": 600, "per_action_per_minute": 12,
                              "run_max": 600, "run_spend_max": 8, "grant_max_ttl_ms": 5000, "session_max_ms": 3_600_000}}
        gate = wc.WebViewInputGate(host, wc.RunConfig(True, frozenset(TABLE.families)),
                                   {"pipeline": wc.Endpoint("pipeline", session)}, wc.SpendGate(),
                                   wc.AuditLog(audit_root), host.send, policy=RISK_POLICY,
                                   limits=wc.ClickLimits(task_max=60))
        options = {"grade": {"log_root": audit_root, "sample_root": audit_root / "samples"}}
        summary = launcher.run({"tasks": ["GradeFes", "MissionStatus"]}, live=True, gate=gate, policy=RISK_POLICY,
                               summary_path=False, save_diagnostics=False, task_options=options, frame_guard_enabled=True)
        grade, missions = summary["tasks"]
        self.assertEqual(grade["status"], "failed")
        self.assertEqual([r["action"] for r in host.requests], ["nav-back"])
        self.assertIn("click_refused:nav-back:session_time_cap_reached", grade["fail_reasons"])
        report, = [r for r in grade["reports"] if r["kind"] == "grade"]
        self.assertIn("task_ended_before_report", report["notes"])
        self.assertEqual((missions["status"], missions["fail_reasons"]), ("skipped", ["host_session_time_cap_reached"]))


if __name__ == "__main__":
    unittest.main()
