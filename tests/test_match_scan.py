"""Moving native Skip rows and honest list coverage; all replay controllers refuse input."""
import dataclasses
import json
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

from agent.core import match_scan as scan
from tests.maa_probe import NodeProbe, maa_ready

BASE = "dev_assets/private/fes-match-reader-20261007/"
FRAMES = tuple(BASE + name + ".png" for name in (
    "match_skip_top", "match_skip_step_1", "match_skip_step_2_settled",
    "match_skip_step_3", "match_skip_step_4", "match_skip_step_5_settled", "match_skip_bottom"))
LOW_RIGHTS = "dev_assets/private/run_logs/samples/match-skip-dialog-20261012T184613672931Z/frame-001.png"


def page(stages, thumb, stamp, **kwargs):
    rows = tuple(scan.SkipRow((1, stage), 0, True, (70, 190 + i * 127, 300, 35),
                              (575, 257 + i * 127, 20, 16)) for i, stage in enumerate(stages))
    return scan.SkipPage("ab" * 32, stamp, rows, kwargs.get("cost", (0, 20)), thumb)


def blank(p, *indices):
    """``p`` with the header of the given row indices unread."""
    return dataclasses.replace(p, rows=tuple(dataclasses.replace(r, stage=None) if i in indices else r
                                             for i, r in enumerate(p.rows)))


class SkipScanTests(unittest.TestCase):
    def test_unread_rows_above_covered_ones_do_not_stop_the_scan(self):
        """Live 2026-10-28: after a half-notch wheel the top-left header (read on the page before) came back empty on
        every settled frame and 「最新」 stopped dialog_unreadable."""
        value = scan.SkipListScan()
        self.assertTrue(value.add(page((1, 2, 3, 4, 5, 6), (195, 326), 1.0)))
        self.assertTrue(value.add(blank(page((5, 6, 7, 8), (257, 388), 2.0), 0)), value.reason)
        self.assertEqual(value.stages, [(1, s) for s in range(1, 9)])
        # an unread row after a covered one, or with nothing covered yet, still stops the scan
        for pages in ((page((1, 2, 3, 4, 5, 6), (195, 326), 1.0), blank(page((5, 6, 7, 8), (257, 388), 2.0), 2)),
                      (blank(page((1, 2, 3, 4, 5, 6), (195, 326), 1.0), 0),)):
            other = scan.SkipListScan()
            results = [other.add(p) for p in pages]
            self.assertFalse(results[-1])
            self.assertEqual(other.reason, "dialog_unreadable")
        # the unread row must sit above a row the scan already holds
        other = scan.SkipListScan()
        other.add(page((1, 2, 3, 4), (195, 326), 1.0))
        self.assertFalse(other.add(blank(page((5, 6, 7, 8), (257, 388), 2.0), 0)))

    def test_blue_and_red_rows_move_with_the_viewport(self):
        image = np.zeros((720, 1280, 3), np.uint8)
        for x, top, color in ((400, 223, (238, 150, 44)), (966, 223, (66, 51, 242)),
                              (400, 477, (238, 150, 44))):
            image[top:top + 36, x] = color
        image[275:408, 1198] = 255
        def number(image, box, *args, **kwargs):
            return "エリア2ステージ5" if box[0] == 70 else "エリア2ステージ6" if box[0] == 636 else "0"
        vision = SimpleNamespace(hit=lambda *args: True, number=number, text=lambda *args: "0/20")
        result = scan.read_skip_page(vision, image, 1.0)
        self.assertEqual([r.stage for r in result.rows], [(2, 5), (2, 6)])
        self.assertEqual([r.plus_box[1] for r in result.rows], [291, 291])
        self.assertEqual(result.thumb, (275, 407))
        self.assertFalse(result.to_dict()["input_authorized"])

    def test_same_visual_row_reads_left_before_right_despite_pixel_jitter(self):
        # Live 2026-10-13 real2: blue A3-S9 (left) and red A4-S10 (right) share a row; the red top was 1 px higher
        # and a plain (y, x) sort listed A4-S10 first, so the overlap check refused the page.
        image = np.zeros((720, 1280, 3), np.uint8)
        for x, top, color in ((400, 218, (238, 150, 44)), (966, 217, (66, 51, 242)),
                              (400, 345, (66, 51, 242)), (966, 346, (66, 51, 242))):
            image[top:top + 35, x] = color
        image[369:500, 1198] = 255
        names = {(70, 221): "エリア3ステージ9", (636, 220): "エリア4ステージ10",
                 (70, 348): "エリア4ステージ11", (636, 349): "エリア4ステージ12"}
        def number(image, box, *args, **kwargs):
            return names.get(tuple(box[:2]), "0")
        vision = SimpleNamespace(hit=lambda *args: True, number=number, text=lambda *args, **kwargs: "0/20")
        result = scan.read_skip_page(vision, image, 1.0)
        self.assertEqual([r.stage for r in result.rows], [(3, 9), (4, 10), (4, 11), (4, 12)])
        value = scan.SkipListScan()
        self.assertTrue(value.add(page((7, 8, 9, 10), (193, 326), 1.0)))
        value.stages[-2:] = [(3, 9), (4, 10)]
        self.assertTrue(value.add(dataclasses.replace(result, observed_at=2.0)), value.reason)
        self.assertEqual(value.stages[-4:], [(3, 9), (4, 10), (4, 11), (4, 12)])

    def test_wrong_page_size_or_time_does_not_provide_rows(self):
        image = np.zeros((720, 1280, 3), np.uint8)
        vision = SimpleNamespace(hit=lambda *args: False)
        self.assertIsNone(scan.read_skip_page(vision, image, 1.0))
        self.assertIsNone(scan.read_skip_page(vision, image[:700], 1.0))
        vision.hit = lambda *args: True
        for stamp in (True, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                scan.read_skip_page(vision, image, stamp)

    def test_low_score_cost_needs_slash_split_digits_and_text_to_agree(self):
        # Live 2026-10-13: 1 right left -> "0 / 1" scores 0.941 det+rec, under the 0.95 full-read threshold.
        image = np.full((720, 1280, 3), 240, np.uint8)
        for left, right in ((1078, 1097), (1124, 1136), (1167, 1174)):
            image[619:649, left:right + 1] = 90
        def vision(strict="unread", loose="0/1", digits=("0", "1"), image=image):
            def text(image, box, threshold=0.95):
                return (None if strict == "unread" else strict) if threshold >= 0.95 else loose
            number = lambda image, box, *args, **kwargs: digits[0] if box[0] < scan.COST_SLASH[0] else digits[1]
            return SimpleNamespace(text=text, number=number)
        self.assertEqual(scan.read_cost(vision(strict="0/20"), image), (0, 20))
        self.assertEqual(scan.read_cost(vision(), image), (0, 1))
        for kwargs in ({"loose": "0/7"}, {"loose": None}, {"digits": ("0", None)}, {"digits": ("1", "1")}):
            with self.subTest(**kwargs):
                self.assertIsNone(scan.read_cost(vision(**kwargs), image))
        no_slash = image.copy()
        no_slash[619:649, 1124:1137] = 240
        self.assertIsNone(scan.read_cost(vision(), no_slash))
        merged = image.copy()
        merged[630:632, 1097:1125] = 90          # a wide numerator reaching into the slash columns
        self.assertIsNone(scan.read_cost(vision(), merged))

    def test_orange_reward_bands_are_not_stage_headers(self):
        image = np.zeros((720, 1280, 3), np.uint8)
        image[190:225, 400] = (52, 148, 255)
        self.assertEqual(scan._header_spans(image, 400), [])

    def test_full_skip_list_is_not_full_progress_or_latest_stage(self):
        value = scan.SkipListScan()
        self.assertTrue(value.add(page((1, 2, 3), (193, 326), 1.0)))
        self.assertTrue(value.add(page((2, 3, 4), (300, 433), 2.0)))
        self.assertTrue(value.add(page((3, 4, 5), (426, 560), 3.0)))
        result = value.to_dict()
        self.assertTrue(result["skip_list_complete"])
        self.assertEqual(result["stages"], [f"A1-S{i}" for i in range(1, 6)])
        self.assertFalse(result["stage_list_complete"])
        self.assertIsNone(result["latest_stage"])
        self.assertIsNone(result["cycle_id"])
        self.assertFalse(result["input_authorized"])
        self.assertFalse(value.add(page((4, 5, 6), (426, 560), 4.0)))
        json.dumps(result, allow_nan=False)

    def test_no_top_thumb_or_valid_rows_cannot_complete(self):
        for source, reason in ((None, "dialog_unreadable"),
                               (page((1, 2), (426, 560), 1.0), "list_start_not_observed"),
                               (dataclasses.replace(page((1,), (193, 560), 1.0), thumb=None), "dialog_unreadable"),
                               (page((1, 1), (193, 560), 1.0), "duplicate_stage_rows"),
                               (page((1,), (193, 560), 1.0, cost=(1, 20)), "counts_not_zero")):
            value = scan.SkipListScan()
            self.assertFalse(value.add(source))
            self.assertEqual(value.reason, reason)
            self.assertFalse(value.to_dict()["skip_list_complete"])

    def test_changed_resource_order_state_or_missing_overlap_stops_scan(self):
        first = page((1, 2, 3), (193, 326), 1.0)
        changed = page((2, 3, 4), (300, 433), 2.0)
        changed = dataclasses.replace(changed, rows=(dataclasses.replace(changed.rows[0], plus_enabled=False),
                                                     *changed.rows[1:]))
        cases = ((page((4, 5, 6), (426, 560), 2.0), "page_overlap_missing_or_changed"),
                 (page((3, 2, 4), (300, 433), 2.0), "page_overlap_missing_or_changed"),
                 (page((2, 3, 4), (300, 433), 1.0), "frame_order_invalid"),
                 (page((2, 3, 4), (300, 433), 2.0, cost=(0, 19)), "rights_changed"),
                 (page((2, 3, 4), (193, 325), 2.0), "scroll_direction_changed"),
                 (changed, "stage_state_changed"))
        for source, reason in cases:
            value = scan.SkipListScan()
            self.assertTrue(value.add(first))
            self.assertFalse(value.add(source))
            self.assertEqual(value.reason, reason)
            self.assertFalse(value.to_dict()["skip_list_complete"])


class ProgressCandidateTests(unittest.TestCase):
    """2026-10-11: a complete sample-only survey yields an untrusted candidate; never MatchProgress or authority."""
    def test_candidate_needs_a_complete_survey(self):
        from agent.core.match_custom import MatchRuntime, progress_candidate
        from agent.core import match_reader as mr
        self.assertTrue(MatchRuntime().scan_sample)  # the full survey is the sample-only default
        main = SimpleNamespace(frame_digest="cd" * 32, ranking_deadline=mr.RankingDeadline("10/14 23:59まで", 10, 14, 23, 59))
        value = scan.SkipListScan()
        self.assertTrue(value.add(page((1, 2, 3, 4, 5, 6), (193, 326), 1.0)))
        partial = progress_candidate(value, main)
        self.assertEqual((partial["skip_list_complete"], partial["latest_skippable_stage"], partial["stage_count"]),
                         (False, None, None))
        self.assertTrue(value.add(page((5, 6, 7, 8), (560, 693), 2.0)))
        self.assertTrue(value.complete)
        done = progress_candidate(value, main)
        self.assertEqual((done["latest_skippable_stage"], done["stage_count"], done["cycle_hint"]),
                         ("A1-S8", 8, "ranking_until_10-14T23:59"))
        self.assertEqual((done["trusted"], done["input_authorized"]), (False, False))
        self.assertIsNone(progress_candidate(value, None)["cycle_hint"])


@unittest.skipUnless(all(Path(f).is_file() for f in FRAMES), "需要 Match 原生滚动帧")
class ListSettleTests(unittest.TestCase):
    """2026-10-24: the Skip wheel waits 1 s; the list is read once two consecutive captures show the same rows."""

    @staticmethod
    def frame(path):
        from PIL import Image
        return np.asarray(Image.open(path).convert("RGB"))[:, :, ::-1].copy()   # Maa images are BGR

    def test_identical_captures_settle_and_a_moving_list_waits_then_stops(self):
        from agent.core import match_custom as mc

        top, step1, step2 = (self.frame(FRAMES[i]) for i in range(3))
        settle = mc.ListSettle()
        self.assertEqual(settle.observe(step1), "moving")          # first capture after the wheel: no proof yet
        self.assertEqual(settle.observe(step1), "settled")         # unchanged rows
        settle = mc.ListSettle()
        moving = [settle.observe(f) for f in (top, step1, step2, step1, top)]
        self.assertEqual(moving, ["moving"] * 4 + ["exhausted"])
        noisy = step1.copy()
        noisy[200:202, 100:300] = 255                              # a few changed pixels (< 0.2 %) still settle
        settle = mc.ListSettle()
        settle.observe(step1)
        self.assertEqual(settle.observe(noisy), "settled")


@unittest.skipUnless(maa_ready(*FRAMES), "需要 Maa、OCR 模型及 Match 原生滚动帧")
class NativeSkipScanTests(unittest.TestCase):
    def test_survey_resource_or_selection_change_stops_before_wheel(self):
        from agent.core.match_custom import MatchRuntime
        from agent.core.match_task import MatchRun, MatchConfig
        for cost, reason in (((0, 19), "rights_changed_or_insufficient"), ((1, 20), "counts_not_zero")):
            runtime = MatchRuntime(scan_sample=True)
            runtime.run = MatchRun(MatchConfig(), Mock())
            runtime.started(runtime.run)
            runtime.run.phase, runtime.run.rights, runtime.run.saver = "skip_opened", (20, 20), Mock()
            with patch("agent.core.match_scan.read_skip_page", return_value=page((1, 2), (193, 326), 1.0, cost=cost)):
                self.assertFalse(runtime._sample_scroll(None, np.zeros((720, 1280, 3), np.uint8)))
            self.assertEqual(runtime.run.abort_outcome, reason)
            self.assertTrue(runtime.sample_scan_ready)
            self.assertEqual(runtime.sample_scrolls, 0)
            runtime.run.saver.save.assert_called_once()  # retain the refused post-action observation

    def survey(self, frames):
        from tools import run_tasks as launcher
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(dir=root / "dev_assets/private") as directory:
            # 2026-10-11: the full survey is the sample-only mode's default (no scan_sample option passed); 只采样
            # itself defaults to No since 2026-10-11, so it is chosen explicitly here.
            options = {"match": {"log_root": Path(directory), "sample_root": Path(directory) / "frames"}}
            return launcher.run({"preset": None, "tasks": [{"name": "MatchSkip", "option": {"Match只采样": "Yes"}}]},
                                offline_steps=[(root / frame, "click") for frame in frames],
                                simulate_input=True, policy={"live_input_enabled": False},
                                summary_path=False, save_diagnostics=False, task_options=options)

    def test_native_survey_uses_only_wheel_cancel_and_navigation(self):
        from tests.test_task_launcher import HOME
        summary = self.survey([BASE + "match_main_top.png", *FRAMES,
                               BASE + "match_skip_cancelled.png", HOME])
        task, = summary["tasks"]
        self.assertEqual(task["status"], "succeeded", task)
        self.assertEqual(task["simulated_actions"], ["match-skip-open", *(["match-skip-scroll-down"] * 6),
                                                     "match-skip-cancel", "nav-back"])
        self.assertEqual(task["simulated_spends"], [])
        report, = [r for r in task["reports"] if r["kind"] == "match"]
        value, = [d["skip_list_observation"] for d in report["decisions"] if "skip_list_observation" in d]
        self.assertTrue(value["skip_list_complete"])
        self.assertEqual(len(value["stages"]), 15)
        self.assertFalse(value["input_authorized"])
        candidate, = [d["progress_candidate"] for d in report["decisions"] if "progress_candidate" in d]
        self.assertEqual((candidate["trusted"], candidate["input_authorized"]), (False, False))
        self.assertTrue(candidate["skip_list_complete"])
        self.assertEqual(candidate["stage_count"], 15)
        self.assertEqual(candidate["latest_skippable_stage"], value["stages"][-1])

    def test_unchanged_thumb_sends_one_wheel_then_cancels(self):
        from tests.test_task_launcher import HOME
        summary = self.survey([BASE + "match_main_top.png", FRAMES[0], FRAMES[0],
                               BASE + "match_skip_cancelled.png", HOME])
        task, = summary["tasks"]
        self.assertEqual(task["simulated_actions"], ["match-skip-open", "match-skip-scroll-down",
                                                     "match-skip-cancel", "nav-back"])
        self.assertEqual(task["simulated_spends"], [])
        report, = [r for r in task["reports"] if r["kind"] == "match"]
        self.assertEqual(report["outcome"], "scroll_no_effect")
        self.assertIsNone(report.get("intent"))
        candidate, = [d["progress_candidate"] for d in report["decisions"] if "progress_candidate" in d]
        self.assertEqual((candidate["skip_list_complete"], candidate["latest_skippable_stage"]), (False, None))

    def test_overlapping_native_pages_include_red_headers(self):
        from maa.custom_recognition import CustomRecognition
        from agent.core.task_custom import Vision
        values = []
        class Probe(CustomRecognition):
            def analyze(self, context, argv):
                values.append(scan.read_skip_page(Vision(context), argv.image, float(len(values) + 1)))
                return CustomRecognition.AnalyzeResult(box=(0, 0, 1, 1), detail="read_only")
        resource = NodeProbe.resource()
        resource.register_custom_recognition("SC.Test.MatchSkipScan", Probe())
        override = {"SC.Test.Start": {"next": ["SC.Test.MatchSkipScan"]},
                    "SC.Test.MatchSkipScan": {"recognition": "Custom", "custom_recognition": "SC.Test.MatchSkipScan"}}
        value = scan.SkipListScan()
        for frame in FRAMES:
            NodeProbe().run(frame, override)
            self.assertTrue(value.add(values[-1]), (frame, value.reason, values[-1]))
        self.assertTrue(value.complete)
        self.assertEqual(len(value.stages), 15)
        self.assertEqual(value.stages[-1], (5, 15))
        self.assertIsNone(value.to_dict()["latest_stage"])


@unittest.skipUnless(maa_ready(LOW_RIGHTS), "需要 Maa、OCR 模型及 1 参加权 Skip 原生帧")
class NativeLowRightsSkipTests(unittest.TestCase):
    def test_one_right_dialog_is_readable_and_starts_the_survey(self):
        """Live 2026-10-13 sample stopped dialog_unreadable: "0 / 1" fell under the det+rec threshold."""
        from maa.custom_recognition import CustomRecognition
        from agent.core.task_custom import Vision
        values = []
        class Probe(CustomRecognition):
            def analyze(self, context, argv):
                values.append(scan.read_skip_page(Vision(context), argv.image, 1.0))
                return CustomRecognition.AnalyzeResult(box=(0, 0, 1, 1), detail="read_only")
        NodeProbe.resource().register_custom_recognition("SC.Test.MatchSkipLowRights", Probe())
        NodeProbe().run(LOW_RIGHTS, {"SC.Test.Start": {"next": ["SC.Test.MatchSkipLowRights"]},
                                     "SC.Test.MatchSkipLowRights": {
                                         "recognition": "Custom", "custom_recognition": "SC.Test.MatchSkipLowRights"}})
        value, = values
        self.assertEqual(value.cost, (0, 1))
        self.assertEqual([row.stage for row in value.rows], [(1, 1), (1, 2), (1, 3), (2, 4), (2, 5), (2, 6)])
        self.assertEqual({row.count for row in value.rows}, {0})
        self.assertTrue(value.readable and value.at_top())
        survey = scan.SkipListScan()
        self.assertTrue(survey.add(value), survey.reason)


REAL4_COST = tuple(f"dev_assets/private/live-20261012k/match-real4-cost/video-count-{n}.png" for n in range(9))
NATIVE_ZERO_OF_20 = "dev_assets/private/match-skip-native-20261002-frames/frame-001.png"


@unittest.skipUnless(maa_ready(*REAL4_COST, NATIVE_ZERO_OF_20, LOW_RIGHTS), "需要 Maa、OCR 模型及 real4 参加权消费数帧")
class NativeCostCountTests(unittest.TestCase):
    """Live real4 2026-10-13: after the 8th + the cost line "8 / 20" fell below the full-read threshold and the
    0.90 cross-check, so the planner waited 3 frames and aborted dialog_unreadable (Clear + Cancel, no spend).
    Frames are panel-video crops (no native guard hit), so read_cost is probed directly."""
    def costs(self, images):
        from maa.custom_recognition import CustomRecognition
        from agent.core.task_custom import Vision
        values = []
        class Probe(CustomRecognition):
            def analyze(self, context, argv):
                values.append(scan.read_cost(Vision(context), argv.image))
                return CustomRecognition.AnalyzeResult(box=(0, 0, 1, 1), detail="read_only")
        NodeProbe.resource().register_custom_recognition("SC.Test.MatchCost", Probe())
        override = {"SC.Test.Start": {"next": ["SC.Test.MatchCost"]},
                    "SC.Test.MatchCost": {"recognition": "Custom", "custom_recognition": "SC.Test.MatchCost"}}
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / "dev_assets/private") as temp:
            for index, image in enumerate(images):
                path = Path(temp) / f"cost-{index}.png"
                _save(path, image)
                NodeProbe().run(str(path.relative_to(Path(__file__).resolve().parents[1])), override)
        return values

    def test_real4_counts_0_to_8_read_exactly(self):
        images = [_load(frame) for frame in REAL4_COST]
        self.assertEqual(self.costs(images), [(n, 20) for n in range(9)])

    def test_centered_two_digit_count_and_full_20_of_20(self):
        # No native two-digit sample yet: rebuild N from native glyphs, centered like the single digit at x~1088.
        source, low = _load(NATIVE_ZERO_OF_20), _load(LOW_RIGHTS)
        zero, one, two = source[614:652, 1076:1100], low[614:652, 1165:1177], source[614:652, 1150:1171]
        images = []
        for glyphs in ((one, zero), (one, two), (two, zero)):
            image = source.copy()
            image[614:652, 1050:1121] = source[630, 1110]
            x = 1088 - (sum(g.shape[1] for g in glyphs) + 4) // 2
            for glyph in glyphs:
                image[614:652, x:x + glyph.shape[1]] = glyph
                x += glyph.shape[1] + 4
            images.append(image)
        self.assertEqual(self.costs(images), [(10, 20), (12, 20), (20, 20)])


def _load(path):
    from PIL import Image
    rgb = np.array(Image.open(Path(__file__).resolve().parents[1] / path).convert("RGB"))
    return np.ascontiguousarray(rgb[:, :, ::-1])


def _save(path, image):
    from PIL import Image
    Image.fromarray(np.ascontiguousarray(image[:, :, ::-1])).save(path)


if __name__ == "__main__":
    unittest.main()
