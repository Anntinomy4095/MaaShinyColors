"""Grade Fes host-timed APPEAL tap (opt-in): pooled phase, post-tap reader, staging with a fake authority, the Grade
runtime hook, the config node and the offline report helpers. Synthetic frames only; no input."""
import dataclasses
import datetime as dt
import json
import shutil
import unittest
from pathlib import Path

import numpy as np

from agent.core import grade_task as gt
from agent.core import grade_timing
from agent.core import task_common as tc
from agent.core import wing_live_timing as wlt
from agent.core.grade_live import LivePlay
from tools import appeal_timing_report as report

ROOT = Path(__file__).resolve().parent.parent
WIDE = (("Normal", 543, 716), ("Good", 717, 803), ("Perfect", 804, 890))
NARROW = (("Bad", 543, 627), ("Normal", 628, 716), ("Good", 717, 803), ("Perfect", 804, 838), ("Good", 839, 890))
COLOURS = {"Normal": (40, 200, 200), "Good": (240, 170, 40), "Perfect": (255, 255, 255), "Bad": (150, 50, 220)}
YELLOW = (250, 230, 60)


def bar_rgb(cursor_x, layout=WIDE):
    a = np.zeros((720, 1280, 3), np.uint8)
    for name, x0, x1 in layout:
        a[485:523, x0:x1 + 1] = COLOURS[name]
    if cursor_x is not None:
        a[468:535, int(cursor_x) - 3:int(cursor_x) + 4] = YELLOW
    return a


def bgr(rgb):
    return np.ascontiguousarray(rgb[..., ::-1])


def x_at(t_ms, phase0=0.2, period_ms=965.0):
    return wlt.x_at_phase(phase0 + t_ms / period_ms)


class FitPhaseTests(unittest.TestCase):
    def test_pooled_phase_is_the_cursor_at_the_last_midpoint(self):
        mids = (10_000.0, 11_010.0, 11_985.0)
        noise = (25.0, -30.0, 10.0)          # render time vs capture midpoint
        obs = [(m - 150, m + 150, x_at(m + e)) for m, e in zip(mids, noise)]
        fit = wlt.fit_phase(obs, period_s=0.965)
        truth = x_at(mids[-1])
        self.assertAlmostEqual(fit["cursor_x"], truth, delta=348 * 15 / 965)   # mean noise ~2 ms, well inside 15 ms
        self.assertLess(abs(fit["cursor_x"] - truth), abs(obs[-1][2] - truth))  # better than the last frame alone
        self.assertEqual((fit["frames"], fit["dropped"]), (3, None))
        self.assertLessEqual(fit["spread_ms"], 40)

    def test_one_outlier_dropped_and_disagreement_refused(self):
        mids = (10_000.0, 11_000.0, 12_000.0, 13_000.0)
        obs = [(m - 100, m + 100, x_at(m)) for m in mids]
        obs[1] = (obs[1][0], obs[1][1], x_at(mids[1] + 250))   # a sparkle read as the cursor
        fit = wlt.fit_phase(obs, period_s=0.965)
        self.assertEqual(fit["dropped"], 1)
        self.assertAlmostEqual(fit["cursor_x"], x_at(mids[-1]), delta=1)
        self.assertIsNone(wlt.fit_phase(obs[:3], period_s=0.965))          # three frames, one off: no pooled phase
        self.assertIsNone(wlt.fit_phase(obs[:1], period_s=0.965))
        self.assertIsNone(wlt.fit_phase(obs, period_s=1.2))

    def test_wraps_across_the_bar_end(self):
        obs = [(m - 100, m + 100, x_at(m, phase0=0.0)) for m in (964.0, 1929.0, 2894.0)]  # cursor near 543 / 890
        fit = wlt.fit_phase(obs, period_s=0.965)
        self.assertLess(fit["spread_ms"], 2)


class ResultReaderTests(unittest.TestCase):
    def stopped(self, x, extra=()):
        a = np.zeros((720, 1280, 3), np.uint8)
        for c in (x, *extra):
            a[527:535, c - 2:c + 2] = YELLOW
        return a

    def test_single_stop_column_maps_to_the_planned_zone(self):
        r = wlt.read_appeal_result(self.stopped(655), WIDE)
        self.assertEqual((r["stop_x"], r["judgement"]), (654.5, "Normal"))
        self.assertEqual(wlt.read_appeal_result(self.stopped(820), NARROW)["judgement"], "Perfect")
        self.assertEqual(wlt.read_appeal_result(self.stopped(845), NARROW)["judgement"], "Good")
        self.assertIsNone(wlt.read_appeal_result(self.stopped(655))["judgement"])   # no layout: position only

    def test_ring_crossing_makes_the_stop_ambiguous(self):
        r = wlt.read_appeal_result(self.stopped(655, extra=(788,)), WIDE)
        self.assertEqual((r["stop_x"], r["candidates"], r["judgement"]), (None, [654.5, 787.5], None))

    def test_confetti_is_perfect_even_with_a_stray_column(self):
        a = self.stopped(744)
        a[432:462, 600:640] = (255, 120, 240)   # pink stars
        a[432:462, 700:740] = (150, 150, 240)   # lavender stars
        r = wlt.read_appeal_result(a, WIDE)
        self.assertTrue(r["perfect_confetti"])
        self.assertEqual(r["judgement"], "Perfect")
        self.assertIsNone(wlt.read_appeal_result(np.zeros((10, 10, 3), np.uint8)))


class FakeAuthority:
    def __init__(self, supported=True, refusals=()):
        self.supported, self.refusals = supported, list(refusals)
        self.required, self.staged, self.t = None, [], 1_000_000.0

    def appeal_timing_supported(self, action):
        return self.supported and action == "live-appeal-timing"

    def configure_appeal_timing(self, enabled):
        self.required = enabled

    def capture_interval(self, image):
        self.t += 1000.0
        return self.t - 150, self.t + 150

    def stage_appeal_timing(self, image, bar, *, period_s, event_offset_ms, action):
        self.staged.append({"bar": bar, "period_s": period_s, "offset": event_offset_ms, "action": action,
                            "required": self.required})
        if self.refusals:
            return self.refusals.pop(0), None
        return None, wlt.host_timing_plan(bar, capture_started_ms=self.t - 150, capture_completed_ms=self.t + 150,
                                          period_s=period_s, event_offset_ms=event_offset_ms)


class FakeRT:
    def __init__(self, authority):
        self.authority, self.records = authority, []

    def _log(self, record):
        self.records.append(record)
        return True


class PrepareTests(unittest.TestCase):
    def setUp(self):
        self.live = LivePlay(started=0.0)
        self.live.turn = 1

    def frames(self, rt, xs, memory=False):
        return [grade_timing.prepare(rt, self.live, bgr(bar_rgb(x)), f"d{i}-{x}", memory=memory)
                for i, x in enumerate(xs)]

    def test_three_frames_pooled_then_one_plan_for_the_grade_action(self):
        auth = FakeAuthority()
        rt = FakeRT(auth)
        xs = [x_at(1_000_000.0 + 1000 * (i + 1), phase0=0.3) for i in range(3)]  # FakeAuthority: one midpoint a second
        self.assertEqual(self.frames(rt, xs), [False, False, True])
        (staged,) = auth.staged
        self.assertEqual((staged["action"], staged["period_s"], staged["offset"], staged["required"]),
                         ("live-appeal-timing", grade_timing.PERIOD_S, grade_timing.EVENT_OFFSET_MS, True))
        plan = rt.records[-1]
        self.assertEqual((plan["event"], plan["refusal"], plan["fit"]["frames"]), ("live_timing_plan", None, 3))
        self.assertEqual(len(plan["observations"]), 3)
        self.assertIsNotNone(self.live.timing["plan"]["request"]["target_down_ms"])
        # Frames that disagree (no common sawtooth phase): the last frame's own reading carries the plan.
        self.live.timing, auth2 = None, FakeAuthority()
        rt2 = FakeRT(auth2)
        self.assertEqual(self.frames(rt2, (600.0, 650.0, 700.0)), [False, False, True])
        self.assertIsNone(rt2.records[-1]["fit"])
        self.assertEqual(auth2.staged[0]["bar"].cursor_x, 700.0)

    def test_same_screenshot_is_not_a_new_frame_and_a_new_turn_resets(self):
        auth = FakeAuthority()
        rt = FakeRT(auth)
        image = bgr(bar_rgb(600.0))
        self.assertEqual([grade_timing.prepare(rt, self.live, image, "same") for _ in range(4)], [False] * 4)
        self.assertEqual(len(self.live.timing["obs"]), 1)
        self.live.turn = 2
        self.assertFalse(grade_timing.prepare(rt, self.live, image, "same"))
        self.assertEqual((self.live.timing["turn"], len(self.live.timing["obs"])), (2, 1))

    def test_untimed_fallbacks_never_block_the_tap(self):
        for authority, memory, reason in ((FakeAuthority(supported=False), False, "host_without_grade_timing"),
                                          (object(), False, "host_without_grade_timing"),
                                          (FakeAuthority(), True, "memory_appeal")):
            with self.subTest(reason=reason, memory=memory):
                self.live.timing = None
                rt = FakeRT(authority)
                self.assertTrue(grade_timing.prepare(rt, self.live, bgr(bar_rgb(600.0)), "d", memory=memory))
                self.assertEqual(rt.records[-1], {"event": "live_timing_fallback", "turn": 1, "reason": reason,
                                                  "frames": 0, "readable": 0})
                if isinstance(authority, FakeAuthority):
                    self.assertIs(authority.required, False)
                    self.assertEqual(authority.staged, [])

    def test_unreadable_bar_falls_back_after_max_frames(self):
        auth = FakeAuthority()
        rt = FakeRT(auth)
        blank = bgr(np.zeros((720, 1280, 3), np.uint8))
        out = [grade_timing.prepare(rt, self.live, blank, f"b{i}") for i in range(grade_timing.MAX_FRAMES)]
        self.assertEqual(out, [False] * (grade_timing.MAX_FRAMES - 1) + [True])
        self.assertEqual(rt.records[-1]["reason"], "bar_unread")
        self.assertIs(auth.required, False)

    def test_late_frames_use_what_was_read_and_refusals_retry_on_the_next_frame(self):
        auth = FakeAuthority(refusals=["analysed_image_is_not_the_latest_frame"])
        rt = FakeRT(auth)
        self.assertEqual(self.frames(rt, (600.0, 650.0, 700.0, 750.0)), [False, False, False, True])
        self.assertEqual([r["refusal"] for r in rt.records], ["analysed_image_is_not_the_latest_frame", None])
        self.assertEqual(len(auth.staged), 2)
        # Unreadable frames in between: the plan comes from the readable ones once MAX_FRAMES is reached.
        self.live.timing, auth2 = None, FakeAuthority()
        rt2 = FakeRT(auth2)
        blank = bgr(np.zeros((720, 1280, 3), np.uint8))
        seq = [blank, blank, blank, blank, bgr(bar_rgb(600.0)), bgr(bar_rgb(650.0))]
        out = [grade_timing.prepare(rt2, self.live, img, f"m{i}") for i, img in enumerate(seq)]
        self.assertEqual(out, [False] * 5 + [True])
        self.assertEqual(rt2.records[-1]["fit"]["frames"], 2)


class RuntimeHookTests(unittest.TestCase):
    def setUp(self):
        from agent.core import grade_custom
        from tests.test_grade_live import FakeContext, FakeVision
        self.FakeVision = FakeVision
        self.root = ROOT / "dev_assets/private/run_logs/_unittest_grade_timing"
        shutil.rmtree(self.root, ignore_errors=True)
        self.addCleanup(shutil.rmtree, self.root, True)
        now = dt.datetime(2026, 10, 24, 8, 0, tzinfo=dt.timezone.utc)
        self.auth = FakeAuthority()
        rt = grade_custom.GradeRuntime(authority=self.auth, log_root=self.root, now=lambda: now, vision=FakeVision,
                                       sample_root=self.root / "samples", clock=lambda: 0.0)
        rt.run = gt.GradeRun(gt.GradeConfig(live_play=True, live_timed_click=True), rt.log)
        rt.run.phase = "awaiting_result"
        rt.run.intent = {"difficulty": "Normal", "rights_before": [20, 20], "stage": gt.LIVE_STAGE}
        live = rt._live()
        live.stage, live.turn = "judge_tapped", 1
        self.rt, self.ctx = rt, FakeContext()
        self.saved_hits = FakeVision.hits
        FakeVision.hits = {"SC.Grade.Live.Screen.Guard", "SC.Grade.Live.Anchor.AppealLabel"}
        self.addCleanup(setattr, FakeVision, "hits", self.saved_hits)

    def taps(self, xs):
        return [self.rt.live(self.ctx, bgr(bar_rgb(x)), {"step": "timing"}) for x in xs]

    def test_timing_step_waits_for_pooled_frames_then_commits_once(self):
        self.assertEqual(self.taps((600.0, 650.0, 700.0, 750.0)), [False, False, True, False])
        self.assertEqual(self.rt.run.live.stage, "timing_tapped")
        events = [e["event"] for e in self.rt.log.events()]
        self.assertEqual(events, ["live_timing_plan"])
        self.assertEqual(self.auth.staged[0]["action"], "live-appeal-timing")

    def test_off_by_default_taps_on_the_first_frame(self):
        self.rt.run.config = dataclasses.replace(self.rt.run.config, live_timed_click=False)
        self.assertEqual(self.taps((600.0,)), [True])
        self.assertEqual(self.auth.staged, [])
        self.assertIsNone(self.auth.required)


class ConfigTests(unittest.TestCase):
    def test_option_is_a_strict_bool_and_off_by_default(self):
        self.assertFalse(gt.GradeConfig().live_timed_click)
        self.assertFalse(gt.GradeConfig.from_params({}).live_timed_click)
        self.assertTrue(gt.GradeConfig.from_params({"live_timed_click": True}).live_timed_click)
        for bad in ("true", 1, None):
            with self.assertRaises(ValueError):
                gt.GradeConfig.from_params({"live_timed_click": bad})

    def test_pipeline_config_node_is_read_and_defaults_off(self):
        from agent.core import grade_custom
        nodes = json.loads((ROOT / "assets/resource/base/pipeline/grade.json").read_text(encoding="utf-8"))
        self.assertEqual(nodes["SC.Grade.Config.Timing"]["attach"], {"live_timed_click": False})
        self.assertIn("SC.Grade.Config.Timing", nodes["SC.Grade.Entry"]["custom_action_param"]["config_nodes"])
        self.assertIn("SC.Grade.Config.Timing", grade_custom.CONFIG_NODES)


class ReportHelperTests(unittest.TestCase):
    def reading(self, *candidates, confetti=False):
        return {"candidates": list(candidates), "perfect_confetti": confetti}

    def test_combine_prefers_confetti_then_the_persistent_column(self):
        self.assertEqual(report.combine([self.reading(744, confetti=True)], 847)["judgement"], "Perfect")
        persistent = report.combine([self.reading(656.5, 788), self.reading(656.5, 788), self.reading(656.5)], 847)
        self.assertEqual((persistent["stop_x"], persistent["stop_source"]), (656.5, "persistent"))
        self.assertEqual(report.combine([self.reading(801)], 821)["stop_source"], "single")
        by_aim = report.combine([self.reading(712.5, 756, 795.5)], 821)
        self.assertEqual((by_aim["stop_x"], by_aim["stop_source"]), (795.5, "by_aim"))
        self.assertEqual(report.combine([None], 821)["stop_source"], None)

    def test_stop_error_wraps_and_signs_late_positive(self):
        self.assertAlmostEqual(report.stop_error_ms(801, 821, 970), -55.7, places=1)
        self.assertGreater(report.stop_error_ms(558.5, 847, 970), 0)     # wrapped past the bar end: late
        self.assertEqual(report.stop_error_ms(847, 847, 970), 0)


if __name__ == "__main__":
    unittest.main()
