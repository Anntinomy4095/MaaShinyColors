"""APPEAL timing bar reader / tap planner (agent.core.wing_live_timing). Synthetic frames always; recorded private
frames only when present (never committed)."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

import numpy as np

from agent.core import wing_live_timing as T

ROOT = Path(__file__).resolve().parents[1]
DEMO1 = ROOT / "dev_assets/private/recordings/wing-demo-20261005-0058-01"
DEMO2 = ROOT / "dev_assets/private/recordings/wing-demo2-20261006-0106-02"
GRADE = ROOT / "dev_assets/private/browser_tasks"

CYAN, ORANGE, WHITE, PURPLE, YELLOW, DARK = (0, 200, 200), (250, 180, 20), (255, 255, 255), (140, 70, 230), \
    (240, 230, 90), (40, 40, 45)
FULL = [(CYAN, 543, 716), (ORANGE, 718, 803), (WHITE, 805, 890)]
LOW = [(PURPLE, 543, 627), (CYAN, 629, 716), (ORANGE, 718, 803), (WHITE, 805, 838), (ORANGE, 839, 890)]


def synthetic(layout, cursor_x=None):
    a = np.zeros((720, 1280, 3), np.uint8)
    a[460:545, 520:910] = DARK
    for colour, x0, x1 in layout:
        a[485:522, x0:x1 + 1] = colour
    if cursor_x is not None:
        a[455:550, cursor_x - 3:cursor_x + 4] = YELLOW
    return a


def load(path: Path):
    from PIL import Image
    return np.asarray(Image.open(path).convert("RGB"))


class SyntheticBarTest(unittest.TestCase):
    def test_fourth_mental_stage_layouts(self):
        """wikiwiki: stage 4 widens Good and shrinks Bad/Normal; a Bad-less or Normal-less bar still reads."""
        for layout, names in (([(PURPLE, 543, 570), (ORANGE, 571, 760), (WHITE, 761, 775), (ORANGE, 776, 890)],
                               ["Bad", "Good", "Perfect", "Good"]),
                              ([(CYAN, 543, 600), (ORANGE, 601, 860), (WHITE, 861, 890)], ["Normal", "Good", "Perfect"])):
            self.assertEqual([z[0] for z in T.read_timing_bar(synthetic(layout)).zones], names)
        two_perfect = [(ORANGE, 543, 650), (WHITE, 651, 700), (ORANGE, 701, 800), (WHITE, 801, 890)]
        self.assertIsNone(T.read_timing_bar(synthetic(two_perfect)))

    def test_narrow_perfect_strip_and_cursor_cut_bad_zone(self):
        """Live a6 turn 4: Perfect is a 10 px strip between two Good runs; the cursor cuts Bad into 59 + 11 px."""
        layout = [(PURPLE, 543, 601), (PURPLE, 611, 621), (CYAN, 622, 702), (ORANGE, 703, 778), (WHITE, 779, 788),
                  (ORANGE, 789, 890)]
        bar = T.read_timing_bar(synthetic(layout, 606))
        self.assertEqual([z[0] for z in bar.zones], ["Bad", "Normal", "Good", "Perfect", "Good"])
        self.assertEqual(bar.perfect_aim(), 783.5)
        sparkle = [(CYAN, 543, 716), (ORANGE, 718, 760), (WHITE, 761, 764), (ORANGE, 765, 803), (WHITE, 805, 890)]
        self.assertEqual([z[0] for z in T.read_timing_bar(synthetic(sparkle)).zones], ["Normal", "Good", "Perfect"])

    def test_host_deadline_uses_capture_clock_and_phase_without_python_latency(self):
        bar = T.read_timing_bar(synthetic(LOW, 700))
        plan = T.host_timing_plan(bar, capture_started_ms=1_000_000, capture_completed_ms=1_000_040)
        request = plan["request"]
        self.assertEqual(plan["capture_estimated_ms"], 1_000_020)
        self.assertEqual(plan["capture_half_interval_ms"], 20)
        for rolled_cycles in (0, 1, 3):
            effective_ms = request["target_down_ms"] + rolled_cycles * request["period_ms"]
            x = T.predict_x(bar.cursor_x, (effective_ms - 1_000_020) / 1000)
            self.assertAlmostEqual(x, plan["aim_x"], delta=.01)
        up = T.host_timing_plan(bar, capture_started_ms=1_000_000, capture_completed_ms=1_000_040,
                               event_offset_ms=120)
        self.assertAlmostEqual(up["request"]["target_down_ms"], request["target_down_ms"] - 120)
        self.assertEqual(plan["judgement_event"], "uncalibrated")
        self.assertLess(plan["window_ms"], 110)
        at_centre = T.TimingBar((("Perfect", 805, 890),), 847.5)
        wrapped = T.host_timing_plan(at_centre, capture_started_ms=1_000_000,
                                     capture_completed_ms=1_000_040, event_offset_ms=120)
        request = wrapped["request"]
        self.assertLessEqual(request["latest_down_ms"] - request["target_down_ms"], 5000)
        landed = T.predict_x(at_centre.cursor_x,
                             (request["target_down_ms"] + 120 - 1_000_020) / 1000)
        self.assertAlmostEqual(landed, wrapped["aim_x"], delta=.01)

    def test_host_deadline_requires_visible_cursor_and_capture_interval(self):
        bar = T.read_timing_bar(synthetic(FULL, 600))
        for start, end, period in ((None, 40, .97), (40, 20, .97), (0, float("nan"), .97), (0, 40, 2)):
            self.assertIsNone(T.host_timing_plan(bar, capture_started_ms=start, capture_completed_ms=end,
                                                 period_s=period))
        self.assertIsNone(T.host_timing_plan(T.read_timing_bar(synthetic(FULL)),
                                            capture_started_ms=0, capture_completed_ms=40))

    def test_full_layout_and_cursor(self):
        bar = T.read_timing_bar(synthetic(FULL, 651))
        self.assertEqual([z[0] for z in bar.zones], ["Normal", "Good", "Perfect"])
        self.assertAlmostEqual(bar.cursor_x, 651, delta=1)
        self.assertEqual(bar.zone_at(860), "Perfect")
        self.assertAlmostEqual(bar.perfect_aim(), 847, delta=2)

    def test_low_mental_layout_aims_at_the_narrow_perfect(self):
        bar = T.read_timing_bar(synthetic(LOW, 700))
        self.assertEqual([z[0] for z in bar.zones], ["Bad", "Normal", "Good", "Perfect", "Good"])
        self.assertAlmostEqual(bar.perfect_aim(), 821, delta=2)

    def test_cursor_hidden_or_bar_absent(self):
        self.assertIsNone(T.read_timing_bar(synthetic(FULL)).cursor_x)
        self.assertIsNone(T.plan_tap(T.read_timing_bar(synthetic(FULL)), elapsed_s=0, latency_s=0))
        self.assertIsNone(T.read_timing_bar(np.zeros((720, 1280, 3), np.uint8)))
        self.assertIsNone(T.read_timing_bar(np.zeros((360, 640, 3), np.uint8)))
        # an unknown layout (white everywhere: tap flash / overlay) is refused
        self.assertIsNone(T.read_timing_bar(synthetic([(WHITE, 543, 890)], 600)))

    def test_plan_lands_on_perfect_centre(self):
        bar = T.read_timing_bar(synthetic(FULL, 600))
        for elapsed, latency in ((0.0, 0.0), (0.9, 0.85), (1.3, 0.85), (0.2, 2.4)):
            plan = T.plan_tap(bar, elapsed_s=elapsed, latency_s=latency, period_s=0.97)
            self.assertTrue(0 <= plan.delay_s < 0.97)
            self.assertEqual(plan.predicted_zone, "Perfect")
            self.assertAlmostEqual(plan.predicted_x, plan.aim_x, delta=1.5)
            landed = T.predict_x(bar.cursor_x, elapsed + latency + plan.delay_s, 0.97)
            self.assertAlmostEqual(landed, plan.aim_x, delta=1.5)
        self.assertAlmostEqual(T.plan_tap(bar, elapsed_s=0, latency_s=0).window_s, 0.24, delta=0.01)

    def test_tap_now_only_when_already_aligned(self):
        bar = T.read_timing_bar(synthetic(FULL, 600))
        aligned = (T.phase(bar.perfect_aim()) - T.phase(bar.cursor_x)) * 0.97
        self.assertTrue(T.plan_tap(bar, elapsed_s=aligned, latency_s=0, period_s=0.97).tap_now)
        self.assertFalse(T.plan_tap(bar, elapsed_s=aligned - 0.3, latency_s=0, period_s=0.97).tap_now)

    def test_align_delay(self):
        delay, plan = T.align_delay(synthetic(FULL, 600), elapsed_s=1.0)
        self.assertTrue(0 <= delay < T.DEFAULT_PERIOD_S)
        landed = T.predict_x(600, 1.0 + T.CAPTURE_TO_DECIDE_S + T.DEFAULT_LATENCY_S + delay)
        self.assertEqual(plan.predicted_zone, "Perfect")
        self.assertAlmostEqual(landed, plan.aim_x, delta=1.5)
        self.assertEqual(T.align_delay(synthetic(FULL), elapsed_s=1.0), (0.0, None))      # cursor hidden: tap now
        self.assertEqual(T.align_delay(synthetic(FULL, 600), elapsed_s=1.0, max_delay_s=0.0)[0], 0.0)

    def test_expected_mix(self):
        bar = T.read_timing_bar(synthetic(FULL, 600))
        self.assertEqual(T.expected_mix(bar, 847, 0)["Perfect"], 1.0)
        tight, loose = T.expected_mix(bar, 847, 0.05), T.expected_mix(bar, 847, 0.21)
        self.assertGreater(tight["Perfect"], 0.95)
        self.assertTrue(0.38 < loose["Perfect"] < 0.5)
        uniform = T.expected_mix(bar, 847, 5.0)
        self.assertAlmostEqual(uniform["Normal"], 0.5, delta=0.03)

    def test_estimate_period_synthetic(self):
        p = 0.97
        obs = [(t, T.predict_x(560, t, p)) for t in (0.0, 1.51, 3.0, 4.62, 6.1)]
        self.assertAlmostEqual(T.estimate_period(obs)[0], p, delta=0.003)
        self.assertIsNone(T.estimate_period(obs[:2]))


@unittest.skipUnless(DEMO2.is_dir() and DEMO1.is_dir(), "private W.I.N.G. demo recordings not present")
class RecordedWingFramesTest(unittest.TestCase):
    def test_cursor_positions_demo2(self):
        expected = {1954: 651, 1956: 635, 1957: 833, 1958: 698, 2668: 543, 2671: 743}
        for seq, x in expected.items():
            bar = T.read_timing_bar(load(DEMO2 / f"frame-{seq:06d}.png"))
            self.assertEqual([z[0] for z in bar.zones], ["Normal", "Good", "Perfect"], seq)
            self.assertAlmostEqual(bar.cursor_x, x, delta=3, msg=seq)

    def test_not_a_timing_bar(self):
        # #1952 select_judge, #2305 post-tap Perfect (purple fill), #2306 appeal animation
        for seq in (1952, 2305, 2306):
            self.assertIsNone(T.read_timing_bar(load(DEMO2 / f"frame-{seq:06d}.png")), seq)

    def test_low_mental_layout_demo1(self):
        bar = T.read_timing_bar(load(DEMO1 / "frame-000548.png"))   # メンタル 255/398
        self.assertEqual([z[0] for z in bar.zones], ["Bad", "Normal", "Good", "Perfect", "Good"])
        perfect = [z for z in bar.zones if z[0] == "Perfect"][0]
        self.assertAlmostEqual(perfect[1], 805, delta=3)
        self.assertAlmostEqual(perfect[2], 838, delta=3)

    def test_period_from_demo2_turn1(self):
        index = {}
        for line in (DEMO2 / "index.jsonl").read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if row.get("file") and row.get("sequence"):
                index[row["sequence"]] = row
        obs = []
        for seq in (1954, 1956, 1957, 1958):
            bar = T.read_timing_bar(load(DEMO2 / index[seq]["file"]))
            obs.append((index[seq]["elapsed_seconds"], bar.cursor_x))
        period, residual = T.estimate_period(obs)
        self.assertTrue(0.95 <= period <= 1.0, period)
        self.assertLess(residual, 0.05)


@unittest.skipUnless((GRADE / "20261003-002532-701261").is_dir(), "private Grade Fes manual frames not present")
class RecordedGradeFramesTest(unittest.TestCase):
    # file mtimes (s) of the 0.3 s series 2026-10-03; hard-coded so a copy of the frames keeps working
    TURN1 = [(0.000, 1), (0.369, 2), (0.661, 3), (0.976, 4), (1.288, 5), (1.597, 6)]

    def test_dense_series_period_and_linearity(self):
        d = GRADE / "20261003-002532-701261"
        obs = [(t, T.read_timing_bar(load(d / f"frame-{i:03d}.png")).cursor_x) for t, i in self.TURN1]
        period, residual = T.estimate_period(obs)
        self.assertAlmostEqual(period, 0.965, delta=0.01)
        self.assertLess(residual, 0.02)

    def test_tap_outcome_matches_prediction(self):
        # Turn 1: last bar frame 0.297 s before the tap-flash frame -> landed on white, the game showed Perfect.
        bar = T.read_timing_bar(load(GRADE / "20261003-002532-701261/frame-006.png"))
        self.assertEqual(bar.zone_at(T.predict_x(bar.cursor_x, 0.297, 0.965)), "Perfect")
        # Turn 2: 0.302 s before the flash -> wrapped to the left end, the game showed Normal.
        bar = T.read_timing_bar(load(GRADE / "20261003-002559-354475/frame-003.png"))
        self.assertEqual(bar.zone_at(T.predict_x(bar.cursor_x, 0.302, 0.965)), "Normal")


if __name__ == "__main__":
    unittest.main()
