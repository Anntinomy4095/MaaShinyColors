"""2026-10-12: Grade live ends early (all judges 審査終了 after TURN 3) and the 「NEW RECORD」/「グレードランキング」 overlay.

Live evidence (formal Normal, game day 2026-10-11): the live ended after TURN 3; LIVE REPORT showed for ~1.5 s
(samples frame-034), then the overlay (frame-036..096, stopped frame-004, wing capture 14:07:57) stayed unchanged for
>100 s; the run timed out (live_stage_timeout). An earlier supervised manual tap on the same overlay
(sample-live-20261011/40 -> 41) closed it.

The click action ``live-record-dismiss`` and its Click node are staged in
``dev_assets/private/pending-20261012-grade/`` while a live run holds the action table; until they are applied the
replays below run against a temporary copy of the base bundle with the staged files (same content).
"""
import contextlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent.core import fes_live_policy as pol
from agent.core import grade_task as gt
from tests import test_grade_live as tgl
from tests.test_grade_live import FakeContext, FakeVision, LiveRuntimeTests, NOW, PRIVATE, ROOT
from tests.test_task_launcher import HAVE_MAA, private_ready

PENDING = PRIVATE / "pending-20261012-grade"
SEQ2 = PRIVATE / "run_logs/samples/grade-after-live-20261011T140449427595Z"
CLEAN_REPORT = SEQ2 / "frame-034.png"     # LIVE REPORT 1位 ★90 45,131pt マニー451 所持17,744,057 (before the overlay)
FADE_IN = SEQ2 / "frame-035.png"
OVERLAY = SEQ2 / "frame-040.png"          # NEW RECORD 43,261→45,131pt + グレードランキング 1,208→1,112位
OVERLAY_CAPTURE = PRIVATE / "wing_samples/2026-10-11/20261011T140757019899Z-capture.png"
OVERLAY_FIRST = PRIVATE / "sample-live-20261011/40-grade-report.png"   # first record: 0pt→43,261pt, 圏外→1,150位
CLEAN_FIRST = PRIVATE / "sample-live-20261011/41-grade-report-clean.png"
DISMISS_RECT = (604, 248, 32, 30)


def _frames_present():
    return all(p.is_file() for p in (CLEAN_REPORT, FADE_IN, OVERLAY, OVERLAY_CAPTURE, OVERLAY_FIRST, CLEAN_FIRST))


@contextlib.contextmanager
def staged_record_dismiss():
    """Run with ``live-record-dismiss`` + ``SC.Grade.Live.Record.Dismiss`` (real files once applied, else staged)."""
    from agent.core import pipeline_actions as pa
    from agent.core import resource_bundles
    from tools import run_tasks
    if "live-record-dismiss" in pa.default_table().ids:
        yield pa.default_table()
        return
    nodes = json.loads((PENDING / "grade_nodes.json").read_text(encoding="utf-8"))
    table = pa.parse_table((PENDING / "pipeline_actions.json").read_bytes())
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / "base"
        shutil.copytree(resource_bundles.BASE_BUNDLE, base)
        grade_path = base / "pipeline" / "grade.json"
        grade = json.loads(grade_path.read_text(encoding="utf-8"))
        grade.update(nodes["nodes"])
        loop = grade["SC.Grade.Live.Loop"]["next"]
        if nodes["insert"] not in loop:
            loop.insert(loop.index(nodes["loop_next_insert_after"]) + 1, nodes["insert"])
        grade_path.write_text(json.dumps(grade, ensure_ascii=False, indent=4) + "\n", encoding="utf-8")
        original = run_tasks.click_node_actions
        saved = pa._DEFAULT
        pa._DEFAULT = table
        try:
            with mock.patch.object(resource_bundles, "BASE_BUNDLE", base), \
                    mock.patch.object(run_tasks, "click_node_actions",
                                      lambda pipeline_dir=None, table=None: original(base / "pipeline", table)):
                yield table
        finally:
            pa._DEFAULT = saved


class StagedFilesTests(unittest.TestCase):
    def test_staged_action_and_node(self):
        nodes = json.loads((PENDING / "grade_nodes.json").read_text(encoding="utf-8")) if PENDING.is_dir() else None
        from agent.core import pipeline_actions as pa
        table = pa.default_table()
        if "live-record-dismiss" not in table.ids:
            if nodes is None:
                self.skipTest("staged files missing")
            table = pa.parse_table((PENDING / "pipeline_actions.json").read_bytes())
            node = nodes["nodes"]["SC.Grade.Live.Record.Dismiss"]
        else:
            node = tgl.pipeline_nodes()["SC.Grade.Live.Record.Dismiss"]
        action = table.get("live-record-dismiss")
        self.assertEqual((action.family, action.consuming, action.rect, action.nodes),
                         ("grade-live", False, DISMISS_RECT, ("SC.Grade.Live.Record.Dismiss",)))
        self.assertEqual(node["target"], list(DISMISS_RECT))
        self.assertEqual(node["all_of"], ["SC.Grade.Live.Record.Guard", "SC.Grade.Live.Step.Record"])
        self.assertEqual((node["enabled"], node["max_hit"], node["on_error"]), (False, 4, ["SC.Grade.SpendError"]))
        # Disjoint from every other Grade/Fes/nav/popup rect (only node-bound popup keys overlap).
        for point in ((604, 248), (635, 248), (604, 277), (635, 277)):
            self.assertEqual([a.id for a in table.at(point, families=("grade-fes", "grade-live", "fes-menu", "nav", "popup"))],
                             ["live-record-dismiss"])

    def test_record_nodes_in_grade_json(self):
        nodes = tgl.pipeline_nodes()
        self.assertEqual(nodes["SC.Grade.Live.Record.Guard"]["all_of"],
                         ["SC.Grade.Live.Anchor.ReportTitle", "SC.Grade.Live.Anchor.RecordAny"])
        self.assertEqual(nodes["SC.Grade.Live.Anchor.RecordAny"]["any_of"],
                         ["SC.Grade.Live.Anchor.RecordBanner", "SC.Grade.Live.Anchor.RankingBanner"])
        self.assertEqual(nodes["SC.Grade.Live.Step.Record"]["custom_recognition_param"], {"step": "record"})


# ======================================================================== runtime (fake screen)
class RecordRuntimeTests(LiveRuntimeTests):
    """Inherits the fake-screen setup; the inherited tests run again (cheap) — only the new ones matter here."""
    RECORD = {"SC.Grade.Live.Record.Guard"}

    def show_record(self):
        FakeVision.hits, FakeVision.texts = set(self.RECORD), {}

    def test_overlay_is_tapped_once_after_settle_then_report_ok_then_fes_main(self):
        FakeVision.hits = {"SC.Grade.Live.Screen.Guard"}
        self.round()
        self.show_report(held="17678700")                                 # report briefly, still counting
        self.assertEqual(self.round({"step": "report"}, {"step": "record"}), [False, False])
        self.show_record()
        self.assertEqual(self.round({"step": "report"}, {"step": "record"}), [False, False])  # first seen
        self.assertEqual(self.round({"step": "report"}, {"step": "record"}), [False, False])  # 1 s
        self.assertEqual(self.round({"step": "report"}, {"step": "record"}), [False, True])   # 2 s, two frames
        self.assertEqual(self.round({"step": "report"}, {"step": "record"}), [False, False])  # once
        self.assertEqual(self.rt.run.live.stage, "wait")                  # no OK stage: overlay only
        self.show_report()
        results = [self.round({"step": "report"}, {"step": "record"}) for _ in range(4)]
        self.assertEqual([r[0] for r in results], [False, False, False, True])  # fresh 3 s stable readings
        self.assertEqual(self.round({"step": "after_report"}), [True])
        FakeVision.hits, FakeVision.texts = {"SC.Nav.FesMain.Guard"}, {"rights": "19/20"}
        self.assertEqual(self.round(), "finished")
        self.assertEqual(self.rt.run.outcome, "played")
        events = [e["event"] for e in self.rt.log.events()]
        self.assertEqual(events, ["live_record_overlay", "live_report", "verified"])
        self.assertEqual(self.rt.log.events()[-1]["live"]["record_taps"], 1)

    def test_overlay_without_effect_is_retapped_once_then_stops(self):
        FakeVision.hits = {"SC.Grade.Live.Screen.Guard"}
        self.round()
        self.show_record()
        taps = []
        for _ in range(25):
            result = self.round({"step": "report"}, {"step": "record"})
            if result == "finished":
                break
            taps += [self.time[0] for hit in result if hit]
        self.assertEqual(len(taps), 2)
        self.assertGreaterEqual(taps[1] - taps[0], gt.LIVE_RETAP_SECONDS)
        self.assertEqual((self.rt.run.outcome, self.rt.run.notes[-1]), ("live_unfinished", "record_overlay_no_effect"))
        self.assertEqual([e["event"] for e in self.rt.log.events()],
                         ["live_record_overlay", "live_retap", "live_unfinished"])

    def test_overlay_after_report_ok_is_never_tapped(self):
        self.press_report_ok()
        self.show_record()
        self.assertEqual(self.round({"step": "record"}), [False])
        self.assertEqual(self.round({"step": "record"}), [False])

    def test_last_turn_with_all_judges_finished_is_not_stuck(self):
        """TURN 3 with every judge 審査終了 after the timing tap: the live ends; no timing_tapped_no_effect."""
        from agent.core import fes_live_reader as reader
        live = self.rt._live()
        live.stage, live.turn, live.seen = "timing_tapped", 3, True
        texts = tgl.wait_texts(3)
        for attribute in ("Vo", "Da", "Vi"):
            texts[tuple(reader.JUDGE_TEXT[attribute])] = "審査終了"
        FakeVision.hits, FakeVision.texts = set(tgl.WAIT), texts
        for _ in range(int(gt.LIVE_STUCK_SECONDS) + 4):
            self.image[::8, ::8] = (self.image[::8, ::8].astype(int) + 9) % 255
            self.assertNotEqual(self.round(*self.CARDS), "finished")
        self.assertFalse(self.rt.run.finished)
        # The same frames with a judge still alive would be stuck (unchanged rule).
        FakeVision.texts = tgl.wait_texts(3)
        for _ in range(int(gt.LIVE_STUCK_SECONDS) + 4):
            if self.round(*self.CARDS) == "finished":
                break
        self.assertEqual(self.rt.run.notes[-1], "timing_tapped_no_effect")

    def test_live_stage_timeout_after_the_report_counts_as_played(self):
        """A report read and closed, then the Loop times out: played_reported (succeeded, counted, not retried)."""
        self.press_report_ok()
        self.assertTrue(self.rt.spend_error(self.ctx, {}))    # SpendError ends the task as succeeded
        self.assertEqual(self.rt.run.outcome, "played_reported")
        self.assertTrue(self.rt.run.succeeded and self.rt.run.halted)
        self.assertEqual(self.rt.log.events()[-1]["reason"], "live_stage_timeout")


# ======================================================================== native replay
@unittest.skipUnless(HAVE_MAA and _frames_present() and PENDING.is_dir() or False, "需要 2026-10-11 叠层原生帧与暂存动作")
class RecordReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not private_ready() or len(tgl.live_frames()) < 396:
            raise unittest.SkipTest("私有回放数据不完整")
        tgl.build_fes_main_19()

    def setUp(self):
        self.root = PRIVATE / "run_logs" / ("_unittest_record_" + self.id().rsplit(".", 1)[-1])
        shutil.rmtree(self.root, ignore_errors=True)
        self.addCleanup(shutil.rmtree, self.root, True)

    def run_live(self, tail, spend=True, prefix_steps=None):
        from tools import run_tasks as launcher
        steps = (prefix_steps if prefix_steps is not None else tgl.live_steps(tgl.FES_MAIN_19)[:-2]) + tail
        options = {k: {"log_root": self.root, "now": lambda: NOW, "sample_root": self.root / "samples"}
                   for k in ("training", "grade", "match")}
        options["grade"]["live_decide"] = tgl.user_choice
        config = {"tasks": [{"name": "GradeFes", "option": {"Grade自动演出": "开启"}}]}
        if spend:
            config["allow_spend"] = ["grade-tension-confirm"]
        with staged_record_dismiss() as table:
            summary = launcher.run(config, offline_steps=steps, simulate_input=True,
                                   policy={"live_input_enabled": False}, summary_path=False, save_diagnostics=False,
                                   task_options=options)
        task, = summary["tasks"]
        report = [r for r in task["reports"] if r["kind"] == "grade"][0]
        return task, report, table

    def events(self):
        path = self.root / "grade-2026-10-02.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def test_overlay_dismissed_then_report_ok_then_fes_main(self):
        """The 2026-10-11 stop: report -> fade-in -> NEW RECORD overlay (tap once) -> report -> OK -> Fes main 19/20."""
        tail = [(FADE_IN, 1), (OVERLAY, "click"), (CLEAN_REPORT, "click"), (tgl.FES_MAIN_19, "click")]
        task, report, table = self.run_live(tail)
        self.assertEqual((task["status"], report["outcome"]), ("succeeded", "played"), report)
        actions = task["simulated_actions"]
        self.assertEqual(actions[-2:], ["live-record-dismiss", "live-report-ok"])
        self.assertEqual(actions.count("live-record-dismiss"), 1)
        for point, action in zip(task["simulated_clicks"], actions):
            self.assertTrue(table.get(action).contains(point))
        self.assertEqual(task["simulated_spends"], [["grade-tension-confirm", "sent"]] * 2)  # ライブ開始 once
        events = self.events()
        kinds = [e["event"] for e in events]
        self.assertEqual(kinds[-3:], ["live_record_overlay", "live_report", "verified"])
        live_report = events[-2]
        self.assertEqual({k: live_report[k] for k in ("rank", "stars", "score", "money", "held")},
                         {"rank": 1, "stars": 90, "score": 45131, "money": 451, "held": 17744057})
        self.assertEqual(events[-1]["rights_after"], [19, 20])
        state = gt.log_state(events)
        self.assertEqual((state.attempts, state.verified, state.open), (1, 1, None))

    def test_overlay_without_effect_is_retapped_once_and_never_presses_ok(self):
        tail = [(FADE_IN, 1)] + [(OVERLAY, "click")] * 3
        task, report, _ = self.run_live(tail)
        self.assertEqual(task["simulated_actions"][-2:], ["live-record-dismiss", "live-record-dismiss"])
        self.assertNotIn("live-report-ok", task["simulated_actions"])
        self.assertEqual((task["status"], report["outcome"]), ("failed", "live_unfinished"))
        self.assertEqual(self.events()[-1]["reason"], "record_overlay_no_effect")
        state = gt.log_state(self.events())
        self.assertEqual((state.attempts, state.uncertain), (1, 1))   # spent, counted, never retried

    def test_record_guard_on_native_frames(self):
        """Overlay guard: the 2026-10-11 overlay frames (both runs) only; never a clean report, ranking or live turn."""
        from maa.custom_action import CustomAction
        from agent.core.task_actions import ReportSink
        from tools import run_tasks as launcher
        from tools.replay_screenshots import read_png
        overlays = [OVERLAY, OVERLAY_CAPTURE, OVERLAY_FIRST] + [SEQ2 / f"frame-{i:03d}.png" for i in range(36, 97, 6)]
        others = [CLEAN_REPORT, FADE_IN, CLEAN_FIRST, tgl.REPORT_FRAME, tgl.RANKING_FRAME, tgl.frame(380)] + [
            SEQ2 / f"frame-{i:03d}.png" for i in range(1, 34, 4)] + tgl.live_frames()[:372:40]
        others = [f for f in others if Path(f).is_file()]
        found = set()
        node = "SC.Grade.Live.Record.Guard"

        class Probe(CustomAction):
            def run(self, context, argv):
                for path in overlays + others:
                    detail = context.run_recognition(node, read_png(path))
                    if detail is not None and detail.hit:
                        found.add(path)
                return True

        scenario = launcher.ReplayScenario([read_png(overlays[0])], ["click"], allow_input=False)
        tasker = launcher.build_tasker(launcher.create_replay_controller(scenario), ReportSink(), save_diagnostics=False)
        self.assertTrue(tasker._sc_keepalive[0].register_custom_action("SC.Test.Probe", Probe()))
        tasker.post_task("SC.Test.Probe.Entry", {"SC.Test.Probe.Entry": {
            "recognition": "DirectHit", "action": "Custom", "custom_action": "SC.Test.Probe"}}).wait()
        self.assertEqual(found, set(overlays))


if __name__ == "__main__":
    unittest.main()
