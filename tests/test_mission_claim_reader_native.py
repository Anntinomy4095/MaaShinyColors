"""Daily login identity/reward/cycle on native private frames. Every probe refuses input."""
import datetime as dt
import json
import unittest

from agent.core import mission_claim_reader as reader, mission_claim_actions as actions
from tests.maa_probe import ROOT, NodeProbe, maa_ready, ocr_items, rgb

CURRENT = "dev_assets/private/live-capture-20261008-knowhow/missions_daily_current.png"
BASE = "dev_assets/private/fes-match-reader-20261007/"
INITIAL = BASE + "missions_daily_initial.png"
CLAIMED = BASE + "missions_after_bulk_bottom.png"
CLIPPED = BASE + "missions_after_bulk_top.png"
SINGLE_OTHER = BASE + "missions_single_receipt.png"
BULK_RESULT = BASE + "missions_bulk_result.png"


@unittest.skipUnless(maa_ready(CURRENT, INITIAL, CLAIMED, CLIPPED, SINGLE_OTHER, BULK_RESULT),
                     "需要Maa、私有OCR模型与每日任务原生帧")
class LoginNativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = actions.load_silver_template()
        cls.probe = NodeProbe()

    def reading(self, path):
        meta = json.loads((ROOT / path).with_suffix(".json").read_text(encoding="utf-8"))
        stamp = meta.get("captured_at_utc", meta.get("read_started_at_utc"))
        return reader.read_login(image_rgb=rgb(path), list_results=ocr_items(path, reader.ROIS["list"]),
                                 timer_results=ocr_items(path, reader.ROIS["daily_timer"]),
                                 observed_at=dt.datetime.fromisoformat(stamp), silver_template=self.template,
                                 expected_reward="silver_ticket",
                                 page_guard=self.probe.hits(path, "SC.Nav.Mission.Guard"), overlay=False)

    def test_login_ready_on_current_and_initial_cycle(self):
        for path, cycle in ((CURRENT, "daily:2026-10-08"), (INITIAL, "daily:2026-10-07")):
            with self.subTest(path=path):
                reading = self.reading(path)
                self.assertEqual((reading.state, reading.cycle_id, reading.progress, reading.reward),
                                 ("claimable", cycle, (1, 1), ("silver_ticket", 1)), reading.to_dict())
                x, y, w, h = reading.target
                self.assertEqual((x, w, h), (1042, 168, 80))
                buttons = [i for i in ocr_items(path, reader.ROIS["list"]) if i["text"] == "受け取る"
                           and reader._inside(i["box"], reading.target)]
                self.assertEqual(len(buttons), 1)  # ROI changes may shift title OCR; the full button remains bound.
                self.assertTrue(reader._inside(reading.target, reader.ROIS["list"]))

    def test_reordered_login_terminal_uses_its_row_deadline(self):
        reading = self.reading(CLAIMED)
        # The screenshot was taken at 22:36 JST, but the label still said 6h27m from page entry at 22:32.
        self.assertEqual((reading.state, reading.cycle_id, reading.row_state, reading.progress, reading.reward,
                          reading.target),
                         ("claimed", "daily:2026-10-07", "claimed", (1, 1), ("silver_ticket", 1), None),
                         reading.to_dict())
        self.assertEqual(reading.reasons, ())
        self.assertIsNone(reading.observation(None))

    def test_clipped_login_terminal_remains_unknown(self):
        reading = self.reading(CLIPPED)
        self.assertEqual(reading.state, "unknown")
        self.assertIn("login_row_clipped", reading.reasons)
        self.assertIsNone(reading.target)

    def test_default_bronze_on_native_silver_login_names_the_observed_kind(self):
        # 2026-10-12 live stop login_reward_unverified (progress 1/1, claimable, reward null): the native login row shows
        # Silver x1, the runtime default expects Bronze x1. The reader now says which reviewed ticket it saw.
        meta = json.loads((ROOT / CURRENT).with_suffix(".json").read_text(encoding="utf-8"))
        stamp = meta.get("captured_at_utc", meta.get("read_started_at_utc"))
        reading = reader.read_login(image_rgb=rgb(CURRENT), list_results=ocr_items(CURRENT, reader.ROIS["list"]),
                                    timer_results=ocr_items(CURRENT, reader.ROIS["daily_timer"]),
                                    observed_at=dt.datetime.fromisoformat(stamp),
                                    ticket_template=actions.load_ticket_template("bronze_ticket"),
                                    other_templates={"silver_ticket": self.template},
                                    page_guard=self.probe.hits(CURRENT, "SC.Nav.Mission.Guard"), overlay=False)
        self.assertEqual((reading.state, reading.progress, reading.reward, reading.row_state, reading.target),
                         ("unknown", (1, 1), None, "claimable", None), reading.to_dict())
        self.assertEqual(reading.reasons, ("login_reward_unverified", "login_reward_kind_mismatch:silver_ticket"))
        self.assertEqual((reading.reward_quantity, reading.observed_reward), (1, "silver_ticket"))
        self.assertIsNone(reading.observation(None))

    def test_same_training_label_on_bronze_is_not_silver(self):
        image = rgb(CURRENT)
        self.assertTrue(reader.silver_matches(image, 250, self.template))
        self.assertFalse(reader.silver_matches(image, 389, self.template))

    def test_other_single_and_bulk_receipts_never_become_login_candidates(self):
        for path in (SINGLE_OTHER, BULK_RESULT):
            with self.subTest(path=path):
                reading = self.reading(path)
                self.assertEqual(reading.state, "unknown")
                self.assertIsNone(reading.target)
                self.assertIn("daily_page_unverified", reading.reasons)


if __name__ == "__main__":
    unittest.main()
