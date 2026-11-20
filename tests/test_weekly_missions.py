"""Weekly Produce x3 read-only check (agent/core/weekly_missions.py, SC.Weekly.* Pipeline, SC.Weekly.Read)."""
from __future__ import annotations

import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from agent.core import weekly_missions as wm

ROOT = Path(__file__).resolve().parents[1]
NATIVE_DAILY = ROOT / "dev_assets" / "private" / "in_app_browser" / "20261001-daily-research" / "daily-v8-Mission-native.png"
JST = wm.JST
# The native daily frame shows daily 「あと14時間6分」 and weekly 「あと2日14時間」: both agree with 14:54 JST, Thu 2026-10-01.
CAPTURE = dt.datetime(2026, 10, 1, 14, 54, tzinfo=JST)


def ocr(text, x, y, w=200, h=26):
    return {"text": text, "box": [x, y, w, h], "score": 0.9}


def row(title, done, total, y, button="詳細"):
    items = [ocr(title, 392, y), ocr("達成度", 415, y + 48, 60, 24), ocr(f"{done}/{total}", 608, y + 48, 40, 24)]
    if button:
        items.append(ocr(button, 1080, y + 25, 100, 30))
    return items


TIMER = [ocr("あと2日14時間", 40, 210, 130, 22)]


def synthetic_tabs(selected):
    image = np.full((720, 1280, 3), 200, np.uint8)
    x, y, w, h = wm.TAB_TEXT_ROI[selected]
    image[y + 8:y + 24, x + 10:x + 110] = (253, 106, 194)
    return image


class CycleTests(unittest.TestCase):
    def test_next_reset_is_sunday_0500_jst(self):
        self.assertEqual(wm.next_reset(CAPTURE), dt.datetime(2026, 10, 4, 5, 0, tzinfo=JST))
        sunday_after = dt.datetime(2026, 10, 4, 5, 0, 1, tzinfo=JST)
        self.assertEqual(wm.next_reset(sunday_after), dt.datetime(2026, 10, 11, 5, 0, tzinfo=JST))
        self.assertEqual(wm.cycle_id(CAPTURE), "2026-09-27")
        with self.assertRaises(ValueError):
            wm.next_reset(dt.datetime(2026, 10, 1))

    def test_timer_parse_and_floor_bounds(self):
        self.assertEqual(wm.parse_timer(TIMER), (2 * 86400 + 14 * 3600, 2 * 86400 + 15 * 3600))
        self.assertEqual(wm.parse_timer([ocr("あと14時間6分", 0, 0)]), (14 * 3600 + 360, 14 * 3600 + 420))
        self.assertIsNone(wm.parse_timer([]))
        self.assertIsNone(wm.parse_timer(TIMER + [ocr("あと1日2時間", 0, 0)]))  # ambiguous

    def test_cycle_binds_screen_timer_to_clock(self):
        self.assertEqual(wm.check_cycle(wm.parse_timer(TIMER), CAPTURE), ("2026-09-27", []))
        cycle, reasons = wm.check_cycle(wm.parse_timer(TIMER), CAPTURE + dt.timedelta(hours=3))
        self.assertIsNone(cycle)
        self.assertTrue(reasons[0].startswith("weekly_timer_clock_mismatch"))
        near = dt.datetime(2026, 10, 4, 3, 30, tzinfo=JST)
        self.assertEqual(wm.check_cycle(wm.parse_timer([ocr("あと1時間30分", 0, 0)]), near), (None, ["weekly_reset_too_close"]))
        self.assertEqual(wm.check_cycle(None, CAPTURE), (None, ["weekly_timer_unread"]))


class TabTests(unittest.TestCase):
    @unittest.skipUnless(NATIVE_DAILY.is_file(), "private native mission frame absent")
    def test_native_daily_frame_is_daily_not_weekly(self):
        from PIL import Image
        tab = wm.tab_selection(np.array(Image.open(NATIVE_DAILY).convert("RGB")))
        self.assertEqual(tab["selected"], "daily")
        self.assertEqual(tab["weekly"], 0)
        self.assertGreaterEqual(tab["daily"], wm.TAB_SELECTED_MIN)

    def test_synthetic_tabs_and_wrong_size(self):
        self.assertEqual(wm.tab_selection(synthetic_tabs("weekly"))["selected"], "weekly")
        both = synthetic_tabs("weekly")
        both[100:116, 40:140] = (253, 106, 194)
        self.assertIsNone(wm.tab_selection(both)["selected"])
        self.assertIsNone(wm.tab_selection(np.zeros((360, 640, 3), np.uint8))["selected"])


class EvaluateTests(unittest.TestCase):
    def evaluate(self, rows, **kw):
        args = dict(list_results=rows, timer_results=TIMER, now=CAPTURE, tab={"selected": "weekly"}, page_guard=True,
                    overlay=False, frame_sha256="a" * 64)
        args.update(kw)
        return wm.evaluate(**args)

    def test_remaining_from_exact_row(self):
        rows = row("フェスアイドルを誕生させよう", 1, 1, 120, "受け取る") + row(wm.PRODUCE_TITLE, 1, 3, 258)
        status = self.evaluate(rows)
        self.assertEqual((status.status, status.completed, status.target, status.remaining), ("remaining", 1, 3, 2))
        self.assertEqual(status.cycle_id, "2026-09-27")
        self.assertEqual(wm.weekly_remaining(status, now=CAPTURE).remaining, 2)

    def test_complete_with_and_without_claim_button(self):
        claimable = self.evaluate(row(wm.PRODUCE_TITLE, 3, 3, 258, "受け取る"))
        self.assertEqual((claimable.status, claimable.remaining, claimable.claimable), ("complete", 0, True))
        done = self.evaluate(row(wm.PRODUCE_TITLE, 3, 3, 258, None))
        self.assertEqual((done.status, done.remaining, done.claimable), ("complete", 0, False))
        result = wm.weekly_remaining(done, now=CAPTURE)
        self.assertEqual((result.remaining, result.reason), (0, "complete"))

    def test_unknown_is_never_zero_or_three(self):
        cases = {
            "not_visible": row("フェスアイドルを誕生させよう", 1, 1, 120),
            "ocr_mismatch": row("フェスアイドルを3人誕生させよ", 1, 3, 258),
            "progress_unread": row(wm.PRODUCE_TITLE, 1, 3, 258)[:2] + row(wm.PRODUCE_TITLE, 1, 3, 258)[3:],
            "target_mismatch": row(wm.PRODUCE_TITLE, 1, 5, 258),
            "contradiction": row(wm.PRODUCE_TITLE, 1, 3, 258, "受け取る"),
            "duplicate": row(wm.PRODUCE_TITLE, 1, 3, 120) + row(wm.PRODUCE_TITLE, 2, 3, 258),
        }
        for name, rows in cases.items():
            with self.subTest(name):
                status = self.evaluate(rows)
                self.assertEqual(status.status, "unknown")
                self.assertIsNone(status.remaining)
                self.assertIsNone(wm.weekly_remaining(status, now=CAPTURE).remaining)

    def test_guards_tab_overlay_and_timer(self):
        rows = row(wm.PRODUCE_TITLE, 1, 3, 258)
        for kw, reason in (({"tab": {"selected": "daily"}}, "weekly_tab_not_proven"), ({"overlay": True}, "overlay_present"),
                           ({"page_guard": False}, "mission_page_guard_failed"), ({"timer_results": []}, "weekly_timer_unread")):
            with self.subTest(reason):
                status = self.evaluate(rows, **kw)
                self.assertEqual(status.status, "unknown")
                self.assertIn(reason, status.reasons)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "weekly_status.json"
        self.status = wm.evaluate(list_results=row(wm.PRODUCE_TITLE, 2, 3, 258), timer_results=TIMER, now=CAPTURE,
                                  tab={"selected": "weekly"}, page_guard=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_round_trip_and_query(self):
        wm.save_status(self.status, self.path)
        self.assertEqual(wm.load_status(self.path), self.status)
        self.assertEqual(wm.weekly_remaining(now=CAPTURE + dt.timedelta(minutes=5), path=self.path).remaining, 1)

    def test_missing_stale_cycle_and_corrupt(self):
        self.assertEqual(wm.weekly_remaining(now=CAPTURE, path=self.path).reason, "no_reading")
        wm.save_status(self.status, self.path)
        self.assertEqual(wm.weekly_remaining(now=CAPTURE + dt.timedelta(hours=1), path=self.path).reason, "reading_stale")
        later = wm.weekly_remaining(now=CAPTURE + dt.timedelta(days=4), path=self.path, max_age_s=10 ** 7)
        self.assertEqual((later.remaining, later.reason), (None, "cycle_changed"))
        future = wm.weekly_remaining(now=CAPTURE - dt.timedelta(hours=1), path=self.path)
        self.assertEqual(future.reason, "observed_in_future")
        data = json.loads(self.path.read_text(encoding="utf-8"))
        data["status"]["remaining"] = 0  # inconsistent with 2/3
        self.path.write_text(json.dumps(data), encoding="utf-8")
        bad = wm.weekly_remaining(now=CAPTURE, path=self.path)
        self.assertIsNone(bad.remaining)
        self.assertTrue(bad.reason.startswith("store_invalid"))

    def test_unknown_reading_overwrites_trusted(self):
        wm.save_status(self.status, self.path)
        wm.save_status(wm.unknown(["produce_row_not_visible"]), self.path)
        self.assertIsNone(wm.weekly_remaining(now=CAPTURE, path=self.path).remaining)

    def test_from_dict_rejects_unknown_with_count(self):
        data = wm.unknown(["x"]).to_dict() | {"remaining": 0}
        with self.assertRaises(ValueError):
            wm.WeeklyProduceStatus.from_dict(data)


def _detail(items, hit=True):
    return SimpleNamespace(hit=hit, all_results=[SimpleNamespace(text=i["text"], box=i["box"], score=0.9) for i in items])


class FakeContext:
    def __init__(self, image, results, overlay=False):
        self.tasker = SimpleNamespace(controller=SimpleNamespace(cached_image=image))
        self.results, self.overlay = results, overlay

    def run_recognition(self, node, image):
        if node.endswith("Overlay"):
            return _detail([], hit=True) if self.overlay else None
        return _detail(self.results.get(node, []))


class WeeklyReadActionTests(unittest.TestCase):
    def run_action(self, image, results, overlay=False):
        from agent.core.present_actions import WeeklyReadAction
        from agent.core.task_actions import ReportSink

        sink = ReportSink()
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "w.json"
            action = WeeklyReadAction(sink, store, clock=lambda: CAPTURE)
            argv = SimpleNamespace(custom_action_param=json.dumps({}), node_name="SC.Weekly.Read")
            ok = action.run(FakeContext(image, results, overlay), argv)
            saved = wm.load_status(store)
        return ok, sink.drain(), saved

    def test_weekly_tab_reads_remaining_without_input(self):
        bgr = synthetic_tabs("weekly")[:, :, ::-1].copy()
        ok, reports, saved = self.run_action(bgr, {"SC.Weekly.ListOCR": row(wm.PRODUCE_TITLE, 0, 3, 258),
                                                   "SC.Weekly.TimerOCR": TIMER})
        self.assertTrue(ok)
        self.assertEqual(reports[-1]["remaining"], 3)
        self.assertEqual(saved.remaining, 3)

    def test_daily_tab_or_overlay_is_unknown(self):
        bgr = synthetic_tabs("daily")[:, :, ::-1].copy()
        results = {"SC.Weekly.ListOCR": row(wm.PRODUCE_TITLE, 0, 3, 258), "SC.Weekly.TimerOCR": TIMER}
        ok, reports, saved = self.run_action(bgr, results)
        self.assertFalse(ok)
        self.assertIsNone(reports[-1]["remaining"])
        self.assertEqual(saved.status, "unknown")
        ok, _, _ = self.run_action(synthetic_tabs("weekly")[:, :, ::-1].copy(), results, overlay=True)
        self.assertFalse(ok)


class WeeklyPipelineContractTests(unittest.TestCase):
    def test_weekly_pipeline_is_read_only(self):
        nodes = json.loads((ROOT / "assets/resource/base/pipeline/weekly.json").read_text(encoding="utf-8"))
        for name, node in nodes.items():
            self.assertTrue(name.startswith("SC.Weekly."), name)
            self.assertIn(node.get("action", "DoNothing"), ("DoNothing", "Custom"), name)
        self.assertEqual(nodes["SC.Weekly.Read"]["custom_action"], "SC.Weekly.Read")
        self.assertEqual(nodes["SC.Weekly.Read"]["recognition"], "And")
        self.assertIn("SC.Weekly.Guard.TabText", nodes["SC.Weekly.Read"]["all_of"])
        self.assertNotIn("next", nodes["SC.Weekly.Read"])  # never navigates back (back key is input)
        task = json.loads((ROOT / "assets/tasks/weekly_check.json").read_text(encoding="utf-8"))["task"][0]
        self.assertFalse(task["default_check"])
        self.assertEqual(task["entry"], "SC.Weekly.Entry")


if __name__ == "__main__":
    unittest.main()
