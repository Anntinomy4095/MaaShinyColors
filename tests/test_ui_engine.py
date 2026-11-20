"""U2: the UI engine child tools/ui_engine.py, the fake engine used by the C#
supervisor self-test, and the protocol events the supervisor forwards.

ui_engine converts the host-written UI profile with ui_schema.profile_to_run_config; the read-only path never forwards
allow_spend or --live / --simulate-input (ANN-17: only --daily / --wing run live), and only private paths are accepted. The fake engine must write exactly the
run_control JSONL envelope so the supervisor self-test exercises the real event format.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import unittest
import uuid
from pathlib import Path
from unittest import mock

from agent.core.run_control import RunControl
from tools import ui_engine, ui_schema

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_TMP = ROOT / "dev_assets" / "private" / "ui" / "test-ui-engine"
FAKE = ROOT / "tests" / "fixtures" / "ui" / "fake_engine.py"


class UiEngineTests(unittest.TestCase):
    def setUp(self):
        self.folder = PRIVATE_TMP / uuid.uuid4().hex
        self.folder.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)

    def write_profile(self, tasks):
        path = self.folder / "profile.json"
        path.write_text(json.dumps({"schema_version": 1, "tasks": tasks}, ensure_ascii=False), encoding="utf-8")
        return path

    def argv(self, profile, source=("--session", "session.json")):
        return ["--profile", str(profile), "--events", str(self.folder / "events.jsonl"),
                "--stop-file", str(self.folder / "stop.flag"), "--summary", str(self.folder / "summary.json"),
                source[0], str(self.folder / source[1])]

    def test_build_run_config_drops_spend_and_ui_only_options(self):
        schema = ui_schema.export_schema()
        profile = {"schema_version": 1, "tasks": [
            {"name": "Training", "enabled": True, "option": {"训练使用铜券": "Yes"}},
            {"name": "GradeFes", "enabled": True, "option": {"Grade使用参加权": "Yes"}},
        ]}
        self.assertTrue(ui_schema.profile_to_run_config(schema, profile).get("allow_spend"))
        config, deferred = ui_engine.build_run_config(profile, schema)
        self.assertNotIn("allow_spend", config)
        self.assertNotIn("input_enabled", config)
        self.assertEqual(set(config), {"tasks"})
        self.assertTrue(deferred)
        for task in config["tasks"]:
            self.assertNotIn("训练使用铜券", task.get("option", {}))
            self.assertNotIn("Grade使用参加权", task.get("option", {}))

    def test_build_run_config_needs_an_enabled_task(self):
        with self.assertRaises(ValueError):
            ui_engine.build_run_config({"schema_version": 1, "tasks": [{"name": "Startup", "enabled": False}]})

    def test_unknown_case_runs_with_the_default(self):
        # 2026-10-28: a stale or unknown case (old profile) never fails the run; the option's default applies.
        config = ui_engine.build_run_config({"schema_version": 1, "tasks": [{"name": "GradeFes", "enabled": True,
                                                                             "option": {"Grade难度": "C:\\x"}}]})
        self.assertNotIn("C:\\x", json.dumps(config, ensure_ascii=False))

    def test_main_calls_run_tasks_read_only(self):
        profile = self.write_profile([{"name": "Startup", "enabled": True}, {"name": "Shop", "enabled": False}])
        with mock.patch("tools.run_tasks.main", return_value=0) as run_main:
            self.assertEqual(ui_engine.main(self.argv(profile)), 0)
        argv = run_main.call_args.args[0]
        self.assertEqual(argv[argv.index("--session") + 1], str(self.folder / "session.json"))
        for flag in ("--config", "--events", "--stop-file", "--summary"):
            self.assertIn(flag, argv)
        self.assertFalse([a for a in argv if a in ("--live", "--simulate-input") or a.startswith("--allow")])
        config = json.loads((self.folder / "run-config.json").read_text(encoding="utf-8"))
        self.assertEqual(config, {"tasks": [{"name": "Startup", "enabled": True}, {"name": "Shop", "enabled": False}]})

    def test_main_offline_frames_source(self):
        profile = self.write_profile([{"name": "Startup", "enabled": True}])
        with mock.patch("tools.run_tasks.main", return_value=1) as run_main:
            self.assertEqual(ui_engine.main(self.argv(profile, ("--offline-frames", "frames"))), 1)
        argv = run_main.call_args.args[0]
        self.assertIn("--offline-frames", argv)
        self.assertNotIn("--session", argv)

    def test_main_refuses_public_paths_and_never_overwrites_config(self):
        public = ROOT / "ui" / "fixtures" / "schema.json"
        with mock.patch("tools.run_tasks.main") as run_main, mock.patch("sys.stdout"):
            self.assertEqual(ui_engine.main(self.argv(public)), 2)
            profile = self.write_profile([{"name": "Startup", "enabled": True}])
            (self.folder / "run-config.json").write_text("{}", encoding="utf-8")
            self.assertEqual(ui_engine.main(self.argv(profile)), 2)
        run_main.assert_not_called()

    def test_main_refuses_spend_fields_in_profile(self):
        path = self.folder / "profile.json"
        path.write_text(json.dumps({"schema_version": 1, "allow_spend": ["x"], "tasks": [{"name": "Startup", "enabled": True}]}),
                        encoding="utf-8")
        with mock.patch("tools.run_tasks.main") as run_main, mock.patch("sys.stdout"):
            self.assertEqual(ui_engine.main(self.argv(path)), 2)
        run_main.assert_not_called()


class UiEngineDailyTests(unittest.TestCase):
    """ANN-17: --daily runs the ticked tasks live as one supervised sequence; spend only from the ticked tasks' options."""
    setUp, write_profile, argv = UiEngineTests.setUp, UiEngineTests.write_profile, UiEngineTests.argv

    def test_daily_runs_the_ticked_tasks_live_in_order_with_their_own_spend(self):
        profile = self.write_profile([
            {"name": "Startup", "enabled": True},
            {"name": "Shop", "enabled": True},
            {"name": "Training", "enabled": True, "option": {"训练使用铜券": "No"}},
            {"name": "GradeFes", "enabled": False, "option": {"Grade使用参加权": "Yes"}},
            {"name": "MissionStatus", "enabled": True}])
        with mock.patch("tools.run_tasks.main", return_value=0) as run_main, mock.patch("sys.stderr"):
            self.assertEqual(ui_engine.main(self.argv(profile) + ["--daily"]), 0)
        argv = run_main.call_args.args[0]
        self.assertIn("--live", argv)
        self.assertIn("--supervised-sequence", argv)
        self.assertNotIn("--supervised-task", argv)
        self.assertFalse([a for a in argv if a.startswith("--allow") or a == "--simulate-input"])
        config = json.loads((self.folder / "run-config.json").read_text(encoding="utf-8"))
        self.assertEqual([t["name"] for t in config["tasks"]], ["Startup", "Shop", "Training", "MissionStatus"])
        self.assertEqual(config["allow_spend"], ["shop-exchange-confirm"])   # Training off, Grade not ticked
        self.assertNotIn("task_max_clicks", config)   # no click caps
        self.assertNotIn("max_clicks", config)
        self.assertIsNone(config["preset"])

    def test_daily_refuses_offline_frames_wing_and_unsupported_tasks(self):
        shop = self.write_profile([{"name": "Shop", "enabled": True}])
        with mock.patch("tools.run_tasks.main") as run_main, mock.patch("sys.stdout"):
            self.assertEqual(ui_engine.main(self.argv(shop, ("--offline-frames", "frames")) + ["--daily"]), 2)
            for tasks in ([{"name": "AutoProduce", "enabled": True}, {"name": "Shop", "enabled": True}],
                          [{"name": "Diagnostics", "enabled": True}]):
                (self.folder / "run-config.json").unlink(missing_ok=True)
                self.assertEqual(ui_engine.main(self.argv(self.write_profile(tasks)) + ["--daily"]), 2)
            self.assertEqual(ui_engine.main(self.argv(self.write_profile([{"name": "Shop", "enabled": True}]))
                                            + ["--daily", "--wing"]), 2)
        run_main.assert_not_called()

    def test_end_summary_is_published_for_the_ui(self):
        payload = {"version": 1, "summary": {"rank": "B+"}, "recommendation": {}, "reward_missing": ["book_2"]}
        summary = {"tasks": [{"name": "Wing", "reports": [{"kind": "fail"}, {"kind": "wing", "end_summary": payload}]}]}
        target = self.folder / "produce-end-last.js"
        data = ui_engine.publish_end_summary(summary, target)
        self.assertEqual(data["payload"], payload)
        text = target.read_text(encoding="utf-8")
        self.assertIn("export default", text)
        self.assertEqual(json.loads(text.split("export default ", 1)[1].rstrip().rstrip(";"))["payload"], payload)
        self.assertIsNone(ui_engine.publish_end_summary({"tasks": [{"reports": [{"kind": "wing"}]}]}, target))


TEAM = {"p_card": "1040020010", "s_cards": ["2040030100", "2040080070", "2040200060", "2040250060"],
        "guest": "2040040130", "idols": [], "mastery": {}, "notes": []}


class KnowhowTargetTests(unittest.TestCase):
    """剧本「按目标词条」: the runner swaps in the planner's scenario before the run config is built."""

    @staticmethod
    def profile(case, enabled=True):
        return {"tasks": [{"name": "WeeklyProduce", "enabled": enabled, "option": {"Auto核对剧本": case}}]}

    def test_target_case_becomes_the_planned_scenario(self):
        from agent.core.knowhow_books import KnowhowBookRecord, WantedItem

        books = [KnowhowBookRecord.from_dict({"title": "【x】櫻木真乃", "rows": [{"name": "スロースターター", "level": 5}]})]
        wanted = [WantedItem("スロースターター"), WantedItem("スタートダッシュ"), WantedItem("施設効率相談")]
        profile = self.profile(ui_engine.TARGET_CASE)
        target = ui_engine.knowhow_target(profile, wanted=wanted, books=books)
        self.assertEqual(profile["tasks"][0]["option"]["Auto核对剧本"], "G.R.A.D.")
        self.assertEqual(target, {"scenario": "GRAD", "targets": ["スタートダッシュ"], "colors": {"Da": 3, "Vi": 2},
                                  "fallback": False, "manual": ["施設効率相談"]})
        # nothing Auto can find -> W.I.N.G.
        profile = self.profile(ui_engine.TARGET_CASE)
        self.assertTrue(ui_engine.knowhow_target(profile, wanted=wanted[2:], books=books)["fallback"])
        self.assertEqual(profile["tasks"][0]["option"]["Auto核对剧本"], "W.I.N.G.")
        for other in (self.profile("S.T.E.P."), self.profile(ui_engine.TARGET_CASE, enabled=False)):
            self.assertIsNone(ui_engine.knowhow_target(other, wanted=wanted, books=books))

    def test_scenario_cases_match_the_panel(self):
        task = json.loads((ROOT / "assets" / "tasks" / "weekly_produce.json").read_text(encoding="utf-8"))
        cases = {c["name"]: c["pipeline_override"]["SC.WeeklyProduce.Config.Scenario"]["attach"]["expect_scenario"]
                 for c in task["option"][ui_engine.SCENARIO_OPTION]["cases"]}
        self.assertEqual({v: k for k, v in ui_engine.SCENARIO_CASES.items()},
                         {k: v for k, v in cases.items() if k != ui_engine.TARGET_CASE})
        self.assertEqual(cases[ui_engine.TARGET_CASE], "TARGET")


class AutoTeamDailyTests(unittest.TestCase):
    """Auto 自动编队: --daily splits around the W.I.N.G. team-edit prep on one event stream."""

    def setUp(self):
        self.folder = PRIVATE_TMP / uuid.uuid4().hex
        self.folder.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)
        memory = mock.patch("tools.ui_wing.TEAM_PREP_MEMORY_FILE", self.folder / "auto-team-memory.json")
        memory.start()
        self.addCleanup(memory.stop)
        self.calls = []

    def args(self, tasks):
        profile = self.folder / "profile.json"
        profile.write_text(json.dumps({"schema_version": 1, "tasks": tasks,
                                       "owned_cards": {"p": [TEAM["p_card"]], "s": TEAM["s_cards"] + [TEAM["guest"]]}},
                                      ensure_ascii=False), encoding="utf-8")
        from types import SimpleNamespace
        return SimpleNamespace(profile=profile, events=self.folder / "events.jsonl", stop_file=self.folder / "stop.flag",
                               summary=self.folder / "summary.json", session=self.folder / "session.json",
                               offline_frames=None)

    def fake_run(self, prep_reasons, fail=()):
        def run(config, *, control, **kw):
            names = [t["name"] for t in config["tasks"]]
            self.calls.append((names, kw, config))
            control.emit("run.started")
            if kw.get("supervised_task"):
                control.emit("task.started", task="Wing", entry="SC.Wing.Entry")
                control.emit("task.finished", task="Wing", status="failed")
                tasks = [{"name": "Wing", "status": "failed", "fail_reasons": list(prep_reasons), "live_clicks": []}]
            else:
                tasks = []
                for name in names:
                    control.emit("task.started", task=name, entry="SC." + name)
                    status = "failed" if name in fail else "succeeded"
                    control.emit("task.finished", task=name, status=status)
                    tasks.append({"name": name, "status": status, "fail_reasons": ["maintenance"] if name in fail else []})
            control.emit("run.finished", status="succeeded")
            control.close()
            return {"order": names, "tasks": tasks, "input_mode": "live_input", "succeeded": len(tasks), "failed": 0}
        return run

    def daily(self, tasks, prep_reasons=("wing:team_edit_done",), team=TEAM, fail=()):
        args = self.args(tasks)
        with mock.patch("tools.ui_wing.auto_team_choice", return_value=dict(team)), mock.patch("sys.stderr"), \
                mock.patch("sys.stdout"):
            code = ui_engine.daily_main(args, run=self.fake_run(prep_reasons, fail), sleep=lambda s: None)
        events = ([json.loads(line) for line in args.events.read_text(encoding="utf-8").splitlines()]
                  if args.events.exists() else [])
        summary = json.loads(args.summary.read_text(encoding="utf-8")) if args.summary.exists() else None
        return code, events, summary

    def test_prep_runs_between_the_parts(self):
        code, events, summary = self.daily([{"name": "Shop", "enabled": True},
                                            {"name": "WeeklyProduce", "enabled": True}])
        self.assertEqual(code, 0)
        self.assertEqual([names for names, _, _ in self.calls], [["Shop"], ["Wing"], ["WeeklyProduce"]])
        _, prep_kw, prep = self.calls[1]
        option = prep["tasks"][0]["option"]
        self.assertTrue(prep_kw["supervised_task"])
        self.assertEqual((option["mode"], option["team_edit"], option["edit_only"], prep["allow_spend"]),
                         ("start", True, True, []))
        self.assertEqual(option["produce_config"]["team"]["p_card"], TEAM["p_card"])
        self.assertEqual(option["produce_config"]["unit_preset"], "unit1")
        self.assertEqual(self.calls[2][1]["task_options"], {"weekly_auto": {"expect_p_card": TEAM["p_card"]}})
        kinds = [e["event"] for e in events]
        self.assertEqual((kinds.count("run.started"), kinds.count("run.finished")), (1, 1))
        self.assertEqual(kinds[-1], "run.finished")
        self.assertNotIn("Wing", {e.get("task") for e in events})
        self.assertEqual([e["task"] for e in events if e["event"] == "task.finished"], ["Shop", "WeeklyProduce"])
        self.assertEqual([(t["name"], t["status"]) for t in summary["tasks"]],
                         [("Shop", "succeeded"), ("WeeklyProduce", "succeeded")])
        self.assertEqual(summary["auto_team"]["result"], "team_edit_done")
        self.assertTrue((self.folder / "team-prep-config.json").is_file())

    def test_failed_prep_fails_weekly_and_skips_the_rest(self):
        code, events, summary = self.daily([{"name": "WeeklyProduce", "enabled": True},
                                            {"name": "MissionStatus", "enabled": True}],
                                           prep_reasons=("wing:team_edit_slot_unverified",))
        self.assertEqual(code, 1)
        self.assertEqual([names for names, _, _ in self.calls], [["Wing"]])
        self.assertEqual([(t["name"], t["status"], t["fail_reasons"]) for t in summary["tasks"]],
                         [("WeeklyProduce", "failed", ["auto_team:wing:team_edit_slot_unverified"]),
                          ("MissionStatus", "skipped", ["auto_team_failed"])])
        self.assertEqual(events[-1]["status"], "failed")

    def test_failed_startup_skips_the_prep(self):
        # 2026-10-30 live: メンテナンス stopped Startup; the prep must not start W.I.N.G. navigation off Home
        code, events, summary = self.daily([{"name": "Startup", "enabled": True},
                                            {"name": "WeeklyProduce", "enabled": True},
                                            {"name": "MissionStatus", "enabled": True}], fail=("Startup",))
        self.assertEqual(code, 1)
        self.assertEqual([names for names, _, _ in self.calls], [["Startup"]])
        self.assertEqual([(t["name"], t["status"], t["fail_reasons"]) for t in summary["tasks"]],
                         [("Startup", "failed", ["maintenance"]), ("WeeklyProduce", "skipped", ["startup_failed"]),
                          ("MissionStatus", "skipped", ["startup_failed"])])
        self.assertEqual(summary["auto_team"]["result"], "startup_failed")
        self.assertEqual(events[-1]["event"], "run.finished")

    def test_no_team_fails_weekly_only(self):
        team = {**TEAM, "p_card": None, "notes": ["owned_cards_missing"]}
        code, events, summary = self.daily([{"name": "WeeklyProduce", "enabled": True},
                                            {"name": "MissionStatus", "enabled": True}], team=team)
        self.assertEqual(code, 1)
        self.assertEqual([names for names, _, _ in self.calls], [["MissionStatus"]])
        self.assertEqual(summary["tasks"][0]["fail_reasons"], ["auto_team_owned_cards_missing"])
        self.assertEqual(summary["tasks"][1]["status"], "succeeded")

    def test_unit_team_runs_weekly_alone_then_the_rest(self):
        code, events, summary = self.daily([{"name": "Shop", "enabled": True},
                                            {"name": "WeeklyProduce", "enabled": True,
                                             "option": {"Auto编队来源": "指定ユニット"}},
                                            {"name": "MissionStatus", "enabled": True}])
        self.assertEqual(code, 0)
        self.assertEqual([names for names, _, _ in self.calls], [["Shop"], ["WeeklyProduce"], ["MissionStatus"]])
        self.assertNotIn("task_options", self.calls[1][1])
        self.assertNotIn("auto_team", summary)
        self.assertEqual([e["task"] for e in events if e["event"] == "task.finished"],
                         ["Shop", "WeeklyProduce", "MissionStatus"])

    def test_a_collected_auto_is_read_and_logged(self):
        from agent.core import knowhow_auto_log as kal

        log, record = self.folder / "auto-log.jsonl", self.folder / "knowhow-newest.json"
        kal.append({"kind": "start", "at": "t0", "scenario": "LP", "p_idol": "黛冬優子"}, log)
        kal.append({"kind": "start", "at": "t1", "scenario": "LP", "p_idol": "黛冬優子"}, log)
        record.write_text(json.dumps({"books": [{"title": "[<5<5】三峰結華", "rows": [
            {"name": "思い出(ノウハウ)++", "level": 1}, {"name": "疲れに<い", "level": 2}]}]}, ensure_ascii=False),
            encoding="utf-8")

        def run(config, *, control, **kw):
            names = [t["name"] for t in config["tasks"]]
            self.calls.append((names, kw, config))
            control.emit("run.started")
            reports = []
            if names == ["WeeklyProduce"]:
                reports = [{"kind": "weeklyauto", "reason": "started", "collected": True}]
            elif names == ["KnowhowScan"]:
                reports = [{"kind": "knowhow_scan", "outcome": "newest_read", "record": str(record),
                            "topup": {"n": 1}}]
            for name in names:
                control.emit("task.started", task=name, entry="SC." + name)
                control.emit("task.finished", task=name, status="succeeded")
            control.emit("run.finished", status="succeeded")
            control.close()
            tasks = [{"name": n, "status": "succeeded", "fail_reasons": [], "reports": reports} for n in names]
            return {"order": names, "tasks": tasks, "succeeded": len(tasks), "failed": 0}

        args = self.args([{"name": "WeeklyProduce", "enabled": True, "option": {"Auto编队来源": "指定ユニット"}},
                          {"name": "MissionStatus", "enabled": True}])
        with mock.patch.object(kal, "LOG_PATH", log), mock.patch("sys.stderr"), mock.patch("sys.stdout"),                 mock.patch("agent.core.knowhow_books.load_wanted_from_profile", return_value=[]):
            code = ui_engine.daily_main(args, run=run, sleep=lambda s: None)
        self.assertEqual(code, 0)
        self.assertEqual([names for names, _, _ in self.calls], [["WeeklyProduce"], ["KnowhowScan"], ["MissionStatus"]])
        self.assertTrue(self.calls[1][1]["supervised_task"])
        events = [json.loads(line) for line in args.events.read_text(encoding="utf-8").splitlines()]
        self.assertNotIn("KnowhowScan", {e.get("task") for e in events})
        reading = next(e for e in events if e.get("kind") == "auto_read")
        self.assertEqual((reading["task"], reading["readings"]["logged"]), ("WeeklyProduce", True))
        rows = kal.load(log)
        read = next(r for r in rows if r["kind"] == "read")
        self.assertEqual(read["start"], "t1")                                   # the Auto just collected
        self.assertEqual(rows[-1]["kind"], "start")                             # then the new Auto's start
        self.assertEqual(read["books"][0]["title"], "【くらくら】三峰結華")
        self.assertEqual(read["books"][0]["rows"], [["思い出(ノウハウ)++", 1], ["疲れにくい", 2]])
        summary = json.loads(args.summary.read_text(encoding="utf-8"))
        self.assertTrue(summary["auto_read"]["logged"])
        self.assertEqual([(t["name"], t["status"]) for t in summary["tasks"]],
                         [("WeeklyProduce", "succeeded"), ("MissionStatus", "succeeded")])


class FakeEngineFormatTests(unittest.TestCase):
    def test_fake_events_use_the_run_control_envelope(self):
        folder = PRIVATE_TMP / uuid.uuid4().hex
        folder.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        control = RunControl(events_path=folder / "real.jsonl")
        control.emit("run.started")
        control.emit("task.finished", task="Startup", status="succeeded", reports_complete=True)
        control.close()
        real = [json.loads(line) for line in (folder / "real.jsonl").read_text(encoding="utf-8").splitlines()]
        profile = folder / "profile.json"
        profile.write_text(json.dumps({"schema_version": 1, "tasks": [{"name": "Startup", "enabled": True},
                                                                      {"name": "Shop", "enabled": True}]}), encoding="utf-8")
        result = subprocess.run([sys.executable, str(FAKE), "--scenario", "ok", "--profile", str(profile),
                                 "--events", str(folder / "fake.jsonl"), "--stop-file", str(folder / "stop.flag"),
                                 "--summary", str(folder / "summary.json"), "--offline-frames", "x"],
                                capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        fake = [json.loads(line) for line in (folder / "fake.jsonl").read_text(encoding="utf-8").splitlines()]
        envelope = set(real[0])
        self.assertEqual(envelope, {"version", "run_id", "sequence", "event", "elapsed_seconds", "timestamp"})
        self.assertEqual(set(fake[0]), envelope)
        self.assertEqual(set(real[1]) - envelope, set(next(r for r in fake if r["event"] == "task.finished")) - envelope)
        self.assertEqual([r["sequence"] for r in fake], list(range(1, len(fake) + 1)))
        self.assertEqual(len({r["run_id"] for r in fake}), 1)
        summary = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual([t["status"] for t in summary["tasks"]], ["succeeded", "succeeded"])
        readings = next(r for r in fake if r["event"] == "readings")
        self.assertEqual(readings, {**readings, "task": "Shop", "kind": "shop"})
        self.assertEqual(RunControl.readings({"kind": "shop", "ok": True, "decisions": [{"money_before": 1, "money_after": 0}]})["kind"], "shop")


class ProtocolEventTests(unittest.TestCase):
    def test_supervisor_events_are_whitelisted(self):
        protocol = json.loads((ROOT / "ui" / "protocol.json").read_text(encoding="utf-8"))
        self.assertEqual(set(protocol["events"]), {"status.update", "log.append", "run.update", "readings.update", "run.history"})
        outcome = protocol["events"]["run.update"]["data"]["properties"]["outcome"]
        self.assertEqual(set(outcome["enum"]), {"succeeded", "failed", "stopped", "uncertain", "refused"})
        self.assertEqual(protocol["events"]["run.history"]["data"]["properties"]["runs"]["maxItems"], 20)
        self.assertEqual(protocol["commands"]["run.start"]["params"]["properties"]["mode"]["enum"], ["read_only", "offline_replay", "supervised_wing", "supervised_daily"])
        module = (ROOT / "ui" / "protocol.js").read_text(encoding="utf-8")
        self.assertEqual(module, ui_schema.protocol_module())

    def test_page_handles_engine_events(self):
        app = (ROOT / "ui" / "app.js").read_text(encoding="utf-8")
        for event in ("readings.update", "run.history", "run.update", "log.append", "status.update"):
            self.assertIn(f'addEventListener("{event}"', app)
        self.assertIn("data.outcome", app)
        resource = (ROOT / "ui" / "components" / "resource-panel.js").read_text(encoding="utf-8")
        self.assertIn("export function readingTiles", resource)
        daily = (ROOT / "ui" / "components" / "daily-panel.js").read_text(encoding="utf-8")
        self.assertIn("export const DEFAULT_DAILY", daily)
        self.assertIn('hour: 5, verified: true', daily)


if __name__ == "__main__":
    unittest.main()
