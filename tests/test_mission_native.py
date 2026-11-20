"""ミッション page readers on native frames (private): daily rows incl. the claimed 「CLEAR!!」 state, the counter,
the tab colour, the weekly timer under the ウィークリー label and the enabled / dimmed 一括受取.

No native frame with the ウィークリー tab selected exists yet; the weekly row
reader is therefore only checked to refuse daily frames here.
"""
from __future__ import annotations

import datetime as dt
import unittest

from agent.core import mission_status as ms
from agent.core import present_claim as pc
from agent.core import weekly_missions as wm
from tests.maa_probe import maa_ready, ocr_items, rgb

P = "dev_assets/private/"
FRAMES = {
    # frame: (counter, [(title, status)], weekly timer seconds lower bound)
    P + "browser_tasks/20261001-155427-754827/frame-001.png":
        ((1, 5), [("ゲームにログインしよう", "claimable"), ("フェスをプレイしよう", "incomplete"),
                  ("マニーショップで買い物をしよう", "unknown")], 2 * 86400 + 14 * 3600),
    P + "in_app_browser/20260930-session01/replay/007-mission-list.png":
        ((5, 5), [("ゲームにログインしよう", "claimed"), ("フェスをプレイしよう", "claimed"),
                  ("マニーショップで買い物をしよう", "unknown")], 3 * 86400 + 8 * 3600),
    P + "nav-v19-mission-back-execute-frames/frame-001.png":
        ((2, 5), [("ゲームにログインしよう", "claimable"), ("サポートアイドルをトレーニングしよう", "claimable"),
                  ("フェスをプレイしよう", "unknown")], 2 * 86400 + 6 * 3600),
    P + "nav-v26-mission-back-20261002-frames/frame-001.png":
        ((1, 5), [("ゲームにログインしよう", "claimable"), ("フェスをプレイしよう", "incomplete"),
                  ("マニーショップで買い物をしよう", "unknown")], 1 * 86400 + 21 * 3600),
}
ALL_CLAIMED = P + "in_app_browser/20260930-session01/replay/007-mission-list.png"
ENABLED_BULK = P + "nav-v26-mission-back-20261002-frames/frame-001.png"
LIST_ROI = (255, 225, 975, 370)
COUNTER_ROI = (950, 572, 320, 32)
BULK_ROI = (1095, 622, 165, 80)


@unittest.skipUnless(maa_ready(*FRAMES), "需要 MaaFramework、OCR 模型与ミッション原生帧")
class MissionNativeTests(unittest.TestCase):
    def test_daily_rows_counter_and_claimed_stamp(self):
        for frame, (counter, rows, _) in FRAMES.items():
            with self.subTest(frame=frame):
                report = ms.build_report(ocr_items(frame, LIST_ROI), ocr_items(frame, COUNTER_ROI))
                self.assertEqual(tuple(report["counter"]), counter)
                self.assertEqual([(r["title"], r["status"]) for r in report["rows"]], rows)
                self.assertTrue(report["ok"])
        report = ms.build_report(ocr_items(ALL_CLAIMED, LIST_ROI), ocr_items(ALL_CLAIMED, COUNTER_ROI))
        self.assertEqual(report["claimed"], ["ゲームにログインしよう", "フェスをプレイしよう"])
        self.assertEqual(report["claimable"], [])

    def test_tab_colour_and_weekly_timer(self):
        for frame, (_, _, low) in FRAMES.items():
            with self.subTest(frame=frame):
                tabs = wm.tab_selection(rgb(frame))
                self.assertEqual((tabs["selected"], tabs["weekly"]), ("daily", 0))
                self.assertEqual(wm.parse_timer(ocr_items(frame, tuple(wm.ROIS["weekly_timer"]))), (low, low + 3600))

    def test_daily_frames_never_give_a_weekly_count(self):
        now = dt.datetime(2026, 10, 2, 9, 0, tzinfo=wm.JST)
        for frame in FRAMES:
            status = wm.evaluate(list_results=ocr_items(frame, tuple(wm.ROIS["list"])),
                                 timer_results=ocr_items(frame, tuple(wm.ROIS["weekly_timer"])), now=now,
                                 tab=wm.tab_selection(rgb(frame)), page_guard=True)
            self.assertEqual(status.status, "unknown")
            self.assertIn("weekly_tab_not_proven", status.reasons)

    def test_bulk_button_enabled_and_dimmed(self):
        self.assertGreaterEqual(pc.pink_count(rgb(ENABLED_BULK), BULK_ROI), 2000)
        self.assertEqual(pc.pink_count(rgb(ALL_CLAIMED), BULK_ROI), 0)     # nothing to receive: dimmed 一括受取


if __name__ == "__main__":
    unittest.main()
