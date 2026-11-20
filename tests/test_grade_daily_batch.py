"""ANN-46: daily remaining Grade/rehearsal lives, fresh verified continuation and shared-rights planning."""
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

from agent.core import fes_allocation as fa, grade_task as gt, match_task as mt, task_common as tc
from agent.core.grade_custom import GradeRuntime
from agent.core.grade_live import LivePlay
from agent.core import webview_controller as wc
from tools import run_tasks, ui_engine

ROOT = Path(__file__).resolve().parents[1]
NOW = dt.datetime(2026, 10, 12, 8, tzinfo=dt.timezone.utc)


class GradeDailyBatchTests(unittest.TestCase):
    def test_live_gate_grants_cover_two_formal_lives_without_widening_shop(self):
        for mode, action, wanted in (("グレードフェス", "grade-tension-confirm", 4),
                                      ("フェスリハーサル", "grade-rehearsal-normal", 2)):
            profile = {"schema_version": 1, "preset": None, "tasks": [{"name": "GradeFes", "enabled": True,
                       "option": {"Grade模式": mode, "Grade每日次数": "2", "Grade自动演出": "开启",
                                  "Grade使用参加权": "Yes"}}]}
            config = ui_engine.daily_run_config(profile)
            limits = run_tasks.spend_grant_limits(config)
            self.assertEqual(limits, {action: wanted})
            gate = wc.SpendGate([action, "shop-exchange-confirm"], max_per_action=3, action_limits=limits)
            for _ in range(wanted):
                gate.authorize(action, "a" * 64, "SC.Grade.Check", capture_seq=1, frame_binding_seq=1)
                self.assertIsNotNone(gate.take_action(action))
            with self.assertRaisesRegex(ValueError, "授权已用尽"):
                gate.authorize(action, "a" * 64, "SC.Grade.Check", capture_seq=1, frame_binding_seq=1)
            for _ in range(3):
                gate.authorize("shop-exchange-confirm", "a" * 64, "SC.Shop.Check", capture_seq=1, frame_binding_seq=1)
                gate.take_action("shop-exchange-confirm")
            with self.assertRaisesRegex(ValueError, "授权已用尽"):
                gate.authorize("shop-exchange-confirm", "a" * 64, "SC.Shop.Check", capture_seq=1, frame_binding_seq=1)

    def test_wing_new_run_config_has_no_grade_grant_limits(self):
        # 2026-10-25 live: every W.I.N.G. run allowed to start a Produce was refused with 「未知任务: Wing」.
        config = {"preset": None, "input_enabled": True, "max_clicks": 200, "task_max_clicks": {"Wing": 200},
                  "tasks": [{"name": "Wing", "option": {}}], "allow_spend": ["wing-produce-start"]}
        self.assertEqual(run_tasks.spend_grant_limits(config), {})

    def setUp(self):
        directory = tempfile.TemporaryDirectory(dir=ROOT / "dev_assets/private")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def runtime(self, mode="grade", target=3, cap=3, live=True, reserve=0):
        config = gt.GradeConfig.from_params({"mode": mode, "runs_per_day": target, "samples_runs_per_day": cap,
                                             "live_play": live, "rights_reserve": reserve})
        runtime = GradeRuntime(log_root=self.root, now=lambda: NOW, sample_root=self.root / "samples",
                               clock=lambda: 1.0)
        runtime.run = gt.GradeRun(config, runtime.log)
        runtime.authority = Mock(can_spend=Mock(return_value=True))
        runtime.started(runtime.run)
        return runtime

    def attempt(self, runtime, before=20, result=None):
        stage = gt.REHEARSAL_STAGE if runtime.run.config.mode == "rehearsal" else gt.LIVE_STAGE
        runtime._log({"event": "intent", "stage": stage, "rights_before": [before, 20], "difficulty": "Normal"})
        if result:
            runtime._log({"event": result, "rights_after": [before - 1, 20]})

    def finish_live(self, runtime, before):
        self.attempt(runtime, before)
        run = runtime.run
        run.phase, run.intent = "awaiting_result", {"stage": gt.LIVE_STAGE, "rights_before": [before, 20]}
        run.live = LivePlay(0, seen=True, ok_pressed=True, report={"score": 1000})
        runtime._start_watch(run, "batch", 0)
        runtime.vision = lambda context: Mock(hit=lambda node, image: node == "SC.Nav.FesMain.Guard")
        runtime.rights = lambda *args: (f"{before - 1}/20", (before - 1, 20))
        self.assertTrue(runtime.watch(None, np.zeros((720, 1280, 3), np.uint8), {}))

    def test_cli_task_filter_keeps_config_options(self):
        # 2026-10-22 live: --tasks GradeFes replaced the config item by a bare name, so 每日次数 "2" fell back to 1.
        config = {"tasks": [{"name": "GradeFes", "enabled": False, "option": {"Grade每日次数": "2"}}, "Startup"]}
        tasks = run_tasks.select_tasks(config, "GradeFes, MissionsClaim")
        self.assertEqual(tasks, [{"name": "GradeFes", "option": {"Grade每日次数": "2"}}, "MissionsClaim"])
        plan = run_tasks.plan_tasks(run_tasks.load_interface(), {"preset": None, "tasks": tasks[:1]})[0]
        self.assertEqual(run_tasks.task_config(plan, "grade.json", gt.GradeConfig).runs_per_day, 2)

    def test_formal_and_rehearsal_execute_remaining_three_lives(self):
        for mode in gt.MODES:
            with self.subTest(mode=mode):
                runtime = self.runtime(mode)
                for index in range(3):
                    self.finish_live(runtime, 20 - index)
                    self.assertEqual(runtime.run.daily_progress.completed, index + 1)
                    self.assertEqual(runtime.run.daily_progress.remaining, 2 - index)
                    self.assertEqual(runtime.run.finished, index == 2)
                    if index < 2:
                        self.assertEqual(runtime.run.phase, "active")
                        self.assertIsNone(runtime.run.live)
                        self.assertIsNone(runtime.run.intent)
                        self.assertTrue(runtime.plan(None, None, {"step": "join"}))
                        self.assertEqual(runtime.run.rights, (19 - index, 20))
                self.assertEqual(runtime.run.outcome, "played")
                self.assertEqual(runtime.run.summary()["completed_in_run"], 3)
                self.assertEqual(len(runtime.run.summary()["lives"]), 3)
                self.assertFalse(runtime.plan(None, None, {"step": "join"}))
                runtime.authority.authorize.assert_not_called()  # continuation itself grants no click

    def test_lives_played_earlier_today_do_not_reduce_this_run(self):
        # The count is per run (until 10-27 an earlier live meant only two more).
        runtime = self.runtime()
        self.attempt(runtime, result="verified")
        runtime.started(runtime.run)
        self.assertEqual(runtime.run.daily_start.planned_runs, 3)
        for before in (19, 18):
            self.finish_live(runtime, before)
            self.assertFalse(runtime.run.finished)
        self.finish_live(runtime, 17)
        self.assertTrue(runtime.run.finished)
        self.assertEqual(runtime.run.summary()["completed_in_run"], 3)
        self.assertEqual(runtime.run.daily_progress.completed, 4)

    def test_sample_cap_and_rights_reserve_stop_verified_batch(self):
        for kwargs, before, reason in (({"cap": 1}, 20, "samples_cap_reached"),
                                       ({"reserve": 1}, 2, "rights_below_reserve"),
                                       ({"live": False}, 20, None)):
            with self.subTest(kwargs=kwargs):
                # Each scenario has an independent daily ledger.
                self.root = self.root / str(before) / str(reason)
                runtime = self.runtime(**kwargs)
                self.finish_live(runtime, before)
                self.assertTrue(runtime.run.finished)
                self.assertEqual(runtime.run.outcome, "played")
                self.assertEqual(runtime.run.daily_progress.remaining, 2)
                if reason:
                    self.assertIn(reason, runtime.run.decisions[-1]["batch_stop"])

    def test_reported_but_no_verified_return_stops_without_next_live(self):
        runtime = self.runtime()
        self.attempt(runtime)
        runtime._log({"event": "live_report", "score": 1000})
        runtime.run.phase = "awaiting_result"
        runtime.run.live = LivePlay(0, seen=True, ok_pressed=True, report={"score": 1000})
        self.assertTrue(runtime.live_unfinished("live_stage_timeout"))
        self.assertEqual(runtime.run.outcome, "played_reported")
        self.assertTrue(runtime.run.halted)
        runtime.report()
        self.assertEqual(runtime.run.daily_progress.completed, 1)
        self.assertEqual(runtime.run.daily_progress.remaining, 2)
        self.assertEqual(runtime.run.batch_results, [])
        self.assertFalse(runtime.plan(None, None, {"step": "join"}))

    def test_uncertain_spend_is_not_completed(self):
        runtime = self.runtime()
        self.attempt(runtime)
        runtime._log({"event": "uncertain"})
        progress = gt.daily_progress(runtime.run.config, self.root, lambda: NOW)
        self.assertEqual((progress.completed, progress.remaining, progress.uncertain), (0, 3, 1))

    def test_spent_unfinished_rehearsal_can_resume_after_the_attempt_limit(self):
        runtime = self.runtime(mode="rehearsal", target=1, cap=1)
        self.attempt(runtime)
        runtime._log({"event": "live_unfinished", "reason": "live_stage_timeout"})
        resumed = GradeRuntime(log_root=self.root, now=lambda: NOW, sample_root=self.root / "samples")
        resumed.run = gt.GradeRun(runtime.run.config, resumed.log)
        resumed.started(resumed.run)
        self.assertFalse(resumed.run.finished)
        # A fresh live screen may be taken over; reaching the main page still cannot spend another right.
        self.assertEqual(gt.rights_reasons(resumed.run.config, resumed.state_now(), (19, 20)), ["samples_cap_reached"])

    def test_manual_mode_reports_takeover_as_soon_as_live_is_seen(self):
        runtime = self.runtime(live=False)
        self.attempt(runtime)
        runtime.run.phase, runtime.run.intent = "awaiting_result", {"rights_before": [20, 20]}
        runtime._start_watch(runtime.run, "manual", 0)
        runtime.vision = lambda context: Mock(hit=lambda node, image: node == "SC.Grade.Live.Screen.Guard")
        self.assertTrue(runtime.watch(None, np.zeros((720, 1280, 3), np.uint8), {}))
        self.assertEqual(runtime.run.outcome, "manual_takeover")
        self.assertTrue(runtime.run.halted)
        self.assertFalse(runtime.run.succeeded)
        summary = runtime.run.summary()
        self.assertEqual((summary["status"], summary["takeover_required"]), ("takeover", True))
        self.assertEqual(summary["daily_progress"]["completed"], 0)
        self.assertEqual(summary["completed_in_run"], 0)
        self.assertFalse(runtime.plan(None, None, {"step": "join"}))
        self.assertEqual(list(runtime.log.events())[-1]["event"], "manual_takeover")

    def test_failed_verified_log_write_cannot_restart(self):
        runtime = self.runtime()
        self.attempt(runtime)
        runtime.run.phase, runtime.run.intent = "awaiting_result", {"rights_before": [20, 20]}
        runtime._start_watch(runtime.run, "batch", 0)
        runtime._log = Mock(return_value=False)
        runtime.vision = lambda context: Mock(hit=lambda node, image: node == "SC.Nav.FesMain.Guard")
        runtime.rights = lambda *args: ("19/20", (19, 20))
        self.assertTrue(runtime.watch(None, np.zeros((720, 1280, 3), np.uint8), {}))
        self.assertEqual(runtime.run.outcome, "log_write_failed")
        self.assertFalse(runtime.run.succeeded)
        self.assertEqual(runtime.run.batch_results, [])

    def test_daily_progress_is_separate_for_modes_and_day(self):
        runtime = self.runtime()
        self.attempt(runtime, result="verified")
        self.attempt(runtime, result="live_report")
        grade = gt.daily_progress(runtime.run.config, self.root, lambda: NOW)
        rehearsal_config = gt.GradeConfig.from_params({"mode": "rehearsal", "runs_per_day": 3})
        rehearsal = gt.daily_progress(rehearsal_config, self.root, lambda: NOW)
        tomorrow = gt.daily_progress(runtime.run.config, self.root, lambda: NOW + dt.timedelta(days=1))
        # per run: the open reported live belongs to the next run, the verified one does not reduce it
        self.assertEqual((grade.completed, grade.remaining, grade.planned_runs), (2, 2, 2))
        self.assertEqual((rehearsal.completed, tomorrow.completed), (0, 0))

    def test_default_attempt_limit_tracks_daily_target_and_keeps_explicit_lower_cap(self):
        for target in (1, 3, 5, 20):
            config = gt.GradeConfig.from_params({"runs_per_day": target})
            self.assertEqual(config.attempt_limit, target)
            progress = gt.progress_from_state(config, gt.LogState(attempts=1, verified=1))
            self.assertEqual(progress.planned_runs, target - 1)
        limited = gt.GradeConfig.from_params({"runs_per_day": 5, "samples_runs_per_day": 3})
        self.assertEqual(gt.progress_from_state(limited, gt.LogState()).planned_runs, 3)

    def test_a_later_run_of_the_same_day_plays_its_count_again(self):
        """「已完成」 only warns in the UI; a new run plays the configured count again."""
        config = gt.GradeConfig.from_params({"runs_per_day": 2, "live_play": True})
        done = gt.LogState(attempts=2, verified=2)
        shifted = gt.per_run_config(config, done)
        self.assertEqual((shifted.runs_per_day, shifted.attempt_limit), (4, 4))
        self.assertEqual(gt.progress_from_state(shifted, done).planned_runs, 2)
        self.assertEqual(gt.rights_reasons(shifted, done, (18, 20)), [])
        # An open attempt (live reported, Fes main not reached) belongs to the new run, not to the baseline.
        reported = gt.LogState(attempts=2, verified=1, reported=1, open={"reported": True, "spent": True})
        self.assertEqual(gt.per_run_config(config, reported).runs_per_day, 3)
        # Runtime: yesterday's behaviour was nothing_to_do at begin; now the run goes on to Fes main.
        runtime = self.runtime(target=1, cap=0)
        self.attempt(runtime, result="verified")
        runtime.run = gt.GradeRun(gt.GradeConfig.from_params({"runs_per_day": 1, "live_play": True}), runtime.log)
        runtime.started(runtime.run)
        self.assertFalse(runtime.run.finished)
        self.assertEqual((runtime.run.config.runs_per_day, runtime.run.daily_progress.planned_runs), (2, 1))

    def test_pending_report_can_be_settled_even_when_daily_attempt_limit_is_reached(self):
        runtime = self.runtime(target=1, cap=0)
        self.attempt(runtime, result="live_report")
        runtime.started(runtime.run)
        self.assertFalse(runtime.run.finished)
        self.assertEqual(runtime.run.daily_progress.completed, 1)
        self.assertEqual(gt.rights_reasons(runtime.run.config, runtime.state_now(), (19, 20)), ["runs_done_today"])

    def test_shared_match_plan_uses_actual_remaining_and_sample_budget(self):
        for mode in gt.MODES:
            config = gt.GradeConfig.from_params({"mode": mode, "runs_per_day": 3, "live_play": True})
            progress = gt.progress_from_state(config, gt.LogState(attempts=1, verified=1))
            allocation = fa.Allocation.from_configs(mt.MatchConfig(sample_only=False), config,
                                                    order=("GradeFes", "MatchSkip"), grade_progress=progress)
            planned = fa.plan(allocation, (19, 20), fa.TicketPolicy())
            self.assertEqual((planned.grade, planned.rehearsal), (2, 0) if mode == "grade" else (0, 2))
            self.assertEqual((planned.match, planned.total), (17, 19))
            capped = gt.progress_from_state(config, gt.LogState(attempts=3, verified=1))
            allocation = fa.Allocation.from_configs(mt.MatchConfig(sample_only=False), config,
                                                    order=("GradeFes", "MatchSkip"), grade_progress=capped)
            self.assertEqual(fa.plan(allocation, (19, 20), fa.TicketPolicy()).match, 19)
        with self.assertRaisesRegex(ValueError, "模式"):
            fa.Allocation.from_configs(None, config, grade_progress=progress.to_dict() | {"mode": "grade"})

    def test_manual_mode_allocates_one_and_resuming_attempt_keeps_its_sample_slot(self):
        config = gt.GradeConfig(runs_per_day=3)
        progress = gt.progress_from_state(config, gt.LogState())
        allocation = fa.Allocation.from_configs(None, config, grade_progress=progress)
        self.assertEqual(allocation.grade_runs, 1)
        state = gt.log_state([{"event": "tension_intent", "difficulty": "Normal", "rights_before": [20, 20]},
                              {"event": "tension_decided"}])
        progress = gt.progress_from_state(gt.GradeConfig(runs_per_day=3, samples_runs_per_day=1), state)
        self.assertEqual((progress.samples_remaining, progress.planned_runs), (0, 1))

    def test_pipeline_batch_keeps_guards_and_clicks_disabled(self):
        nodes = json.loads((ROOT / "assets/resource/base/pipeline/grade.json").read_text(encoding="utf-8"))
        override = gt.batch_pipeline_overrides(nodes, 3)
        self.assertEqual(override["SC.Grade.Unit.Confirm"], {"max_hit": 6})
        self.assertEqual(override["SC.Grade.Difficulty.Normal"], {"max_hit": 3})
        self.assertTrue(all(set(values) == {"max_hit"} for values in override.values()))
        self.assertFalse(nodes["SC.Grade.Unit.Confirm"]["enabled"])
        self.assertEqual(nodes["SC.Grade.Watch.Concluded"]["next"], ["SC.Grade.Main"])


class GradeBatchNativeReplayTests(unittest.TestCase):
    def test_two_lives_follow_the_same_guarded_pipeline(self):
        """Recorded native live twice, with synthetic 19/20 and 18/20 footers for the second entry/result."""
        from tests import test_grade_live as tgl
        from tests.test_task_launcher import HAVE_MAA, private_ready
        from tools import run_tasks as launcher
        from PIL import Image, ImageDraw, ImageFont

        if not HAVE_MAA or not private_ready() or len(tgl.live_frames()) < 396:
            self.skipTest("需要Maa与2026-10-03私有演出帧")
        if not all((ROOT / f).is_file() for f in tgl.GRADE_CHAIN):
            self.skipTest("Grade入口原生帧不完整")
        with tempfile.TemporaryDirectory(dir=ROOT / "dev_assets/private") as directory:
            root = Path(directory)
            font = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 15)

            def rights_frame(source, count, name, where="fes_main"):
                image = Image.open(source).convert("RGB")
                draw = ImageDraw.Draw(image)
                x, y, width, height = gt.RIGHTS_FIELDS[where]
                # Preserve all page anchors and use the same synthetic numeric glyph procedure as Grade replays.
                draw.rectangle((x - 5, y - 1, x + width + 9, y + height + 1), fill=(245, 92, 22))
                draw.text((x + width / 2, y + height / 2), f"{count}/20", font=font, fill=(255, 250, 245), anchor="mm")
                path = root / name
                image.save(path)
                return path

            fes19 = rights_frame(ROOT / tgl.FES_MAIN, 19, "fes19.png")
            fes18 = rights_frame(ROOT / tgl.FES_MAIN, 18, "fes18.png")
            prefix = [ROOT / tgl.GRADE_CHAIN[3],
                      rights_frame(ROOT / tgl.GRADE_CHAIN[4], 19, "difficulty19.png", "difficulty"),
                      rights_frame(ROOT / tgl.GRADE_CHAIN[5], 19, "unit19.png", "unit"),
                      rights_frame(ROOT / tgl.GRADE_CHAIN[6], 19, "tension19.png", "unit")]
            steps = tgl.live_steps(fes19) + tgl.live_steps(fes18, prefix=prefix)
            options = {kind: {"log_root": root / "logs", "now": lambda: tgl.NOW,
                              "sample_root": root / "samples"} for kind in ("training", "grade", "match")}
            options["grade"]["live_decide"] = tgl.user_choice
            config = {"preset": None, "tasks": [{"name": "GradeFes", "option": {"Grade每日次数": "2"}}],
                      "allow_spend": ["grade-tension-confirm"]}
            result = launcher.run(config, offline_steps=steps, simulate_input=True,
                                      policy={"live_input_enabled": False}, summary_path=False,
                                      save_diagnostics=False, task_options=options)
            task, = result["tasks"]
            report = next(r for r in task["reports"] if r["kind"] == "grade")
            self.assertEqual((task["status"], report["outcome"]), ("succeeded", "played"), report)
            self.assertEqual(report["daily_progress"]["completed"], 2)
            self.assertEqual(report["completed_in_run"], 2)
            self.assertEqual(len(report["lives"]), 2)
            self.assertEqual(task["simulated_actions"].count("grade-tension-confirm"), 4)
            self.assertEqual(task["simulated_actions"].count("grade-difficulty-normal"), 2)
            self.assertEqual(task["refused_clicks"], [])


if __name__ == "__main__":
    unittest.main()
