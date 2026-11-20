"""Native, end-to-end replay of PresentClaim. Simulated input only; no game connection."""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agent.core import pipeline_actions
from agent.core import present_claim as pc
from tests.maa_probe import NodeProbe, maa_ready
from tools import run_tasks as launcher

ROOT = Path(__file__).resolve().parents[1]
P = "dev_assets/private/fes-match-reader-20261007/"
HOME = P + "home_after_present_settled.png"
BEFORE = P + "present_before_bulk_claim.png"
CONFIRM = P + "present_bulk_confirm.png"
RECEIPT = P + "present_bulk_receipt.png"
EMPTY = P + "present_empty_after_claim.png"
RECEIVED = P + "present_received_after_claim.png"
WRONG_RECEIPT = P + "missions_bulk_result.png"
LONG = "dev_assets/private/wing_samples/2026-10-10/20261010T122051819768Z-capture.png"   # 12 items, scrollbar
NOW = dt.datetime(2026, 10, 8, 0, 0, tzinfo=dt.timezone.utc)
ON = "开启"


@unittest.skipUnless(maa_ready(HOME, BEFORE, CONFIRM, RECEIPT, EMPTY, RECEIVED, WRONG_RECEIPT),
                     "Requires private native gift claim frames and Maa OCR")
class PresentSenderReplayTests(unittest.TestCase):
    def replay(self, frames, *, option=ON, simulate=True):
        table = pipeline_actions.default_table()
        # Initial calibration tests can exercise a disabled catalog in an offline copy.
        activated = dataclasses.replace(table, actions=tuple(dataclasses.replace(a, enabled=True)
            if a.family == "present" else a for a in table.actions))
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / "ledger.json"
            with patch.object(pipeline_actions, "default_table", return_value=activated):
                summary = launcher.run({"task_timeout_s": 45, "tasks": [{"name": "PresentClaim", "option": {"プレゼント领取": option}}]},
                    offline_steps=[(ROOT / frame[0], frame[1]) if isinstance(frame, tuple) else (ROOT / frame, "click")
                                   for frame in frames], simulate_input=simulate,
                    task_options={"present": {"ledger_path": ledger, "clock": lambda: NOW}},
                    summary_path=False, save_diagnostics=False)
            records = json.loads(ledger.read_text(encoding="utf-8"))["records"] if ledger.exists() else []
            return summary, records

    def assert_actions(self, summary, expected):
        self.assertEqual(summary["tasks"][0]["simulated_actions"], expected)
        self.assertEqual(summary["tasks"][0]["simulated_spends"], [])

    def test_complete_bulk_claim_records_received_and_returns_home(self):
        summary, records = self.replay([HOME, BEFORE, CONFIRM, RECEIPT, EMPTY, HOME])
        task, = summary["tasks"]
        self.assertEqual(task["status"], "succeeded", task)
        self.assert_actions(summary, ["home-present", "present-bulk-receive", "present-confirm", "present-result-close", "nav-back"])
        self.assertEqual(records[0]["state"], "received")
        self.assertEqual(records[0]["history"], ["pending", "receipt_observed", "received"])

    def test_delayed_receipt_does_not_classify_the_confirmation_as_the_result(self):
        summary, records = self.replay([BEFORE, CONFIRM, (CONFIRM, 1), RECEIPT, (BEFORE, 1), EMPTY, HOME])
        self.assertEqual(summary["tasks"][0]["status"], "succeeded", summary)
        self.assert_actions(summary, ["present-bulk-receive", "present-confirm", "present-result-close", "nav-back"])
        self.assertEqual(records[0]["state"], "received")

    def test_received_tab_is_switched_before_reading_the_empty_box(self):
        summary, records = self.replay([HOME, RECEIVED, EMPTY, HOME])
        self.assertEqual(summary["tasks"][0]["status"], "succeeded", summary)
        self.assert_actions(summary, ["home-present", "present-unclaimed-tab", "nav-back"])
        self.assertEqual(records, [])

    def test_confirmation_that_did_not_advance_is_not_repeated(self):
        summary, records = self.replay([HOME, BEFORE, CONFIRM, CONFIRM])
        self.assertEqual(summary["tasks"][0]["status"], "failed", summary)
        self.assert_actions(summary, ["home-present", "present-bulk-receive", "present-confirm"])
        self.assertEqual(records[0]["state"], "uncertain")

    def test_another_reward_dialog_cannot_complete_or_close_this_claim(self):
        summary, records = self.replay([HOME, BEFORE, CONFIRM, WRONG_RECEIPT])
        self.assertEqual(summary["tasks"][0]["status"], "failed", summary)
        self.assert_actions(summary, ["home-present", "present-bulk-receive", "present-confirm"])
        self.assertEqual(records[0]["state"], "uncertain")

    @unittest.skipUnless(maa_ready(LONG), "Requires the 2026-10-10 native 12-item gift list")
    def test_long_box_is_bulk_claimed_by_default_without_scrolling(self):
        # 2026-10-12 user decision (live 11 items stopped rows_not_all_visible / scroll_unavailable): 一括受取 once;
        # the receipt may name unread rows, completion is proven by the fresh empty box.
        summary, records = self.replay([HOME, LONG, CONFIRM, RECEIPT, EMPTY, HOME])
        task, = summary["tasks"]
        self.assertEqual(task["status"], "succeeded", task)
        self.assert_actions(summary, ["home-present", "present-bulk-receive", "present-confirm", "present-result-close",
                                      "nav-back"])
        report = [r for r in task["reports"] if r["kind"] == "present"][-1]
        self.assertEqual(report["decision"]["reasons"], ["bulk_receive"])
        self.assertTrue(report["receipt_items"])
        self.assertEqual(records[0]["history"], ["pending", "receipt_observed", "received"])

    @unittest.skipUnless(maa_ready(LONG), "Requires the 2026-10-10 native 12-item gift list")
    def test_long_box_is_not_claimed_when_the_count_does_not_drop(self):
        summary, records = self.replay([HOME, LONG, CONFIRM, RECEIPT, LONG])
        self.assertEqual(summary["tasks"][0]["status"], "failed", summary)
        self.assertEqual(records[0]["state"], "uncertain")
        self.assertEqual(summary["tasks"][0]["simulated_actions"].count("present-bulk-receive"), 1)

    def test_disabled_option_sends_nothing(self):
        summary, records = self.replay([HOME], option="关闭")
        self.assert_actions(summary, [])
        self.assertEqual(records, [])

    def test_read_only_does_not_send_the_bulk_intent(self):
        from agent.core import present_actions as pa
        # This checks disabled input, not OCR wall-clock speed under a full-suite load.
        with patch.object(pa, "time", SimpleNamespace(monotonic=lambda: 100.0)):
            summary, records = self.replay([BEFORE], simulate=False)
        self.assertFalse(summary.get("real_input_attempts", 0))
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["state"], "refused")
        self.assert_actions(summary, [])


@unittest.skipUnless(maa_ready(CONFIRM, RECEIPT, BEFORE, EMPTY, RECEIVED, WRONG_RECEIPT), "Requires native gift frames")
class PresentSenderGuardTests(unittest.TestCase):
    @unittest.skipUnless(maa_ready(
        "dev_assets/private/live-capture-20261008-knowhow/present_live_stopped.png",
        "dev_assets/private/live-capture-20261008-knowhow/present_list_second_observation.png"),
        "Requires the two live animated gift-list frames")
    def test_live_list_animation_does_not_change_the_read_item_text(self):
        import numpy as np
        from PIL import Image
        from tests.maa_probe import ocr_items
        from agent.core import present_actions as pa, present_claim as pc

        base = ROOT / "dev_assets/private/live-capture-20261008-knowhow"
        paths = [base / name for name in ("present_live_stopped.png", "present_list_second_observation.png")]
        images = [np.asarray(Image.open(path).convert("RGB"))[:, :, ::-1] for path in paths]
        page = pc.parse_page(ocr_items(paths[0]), tab=pa.page_tab(images[0]))
        self.assertEqual((page.count, len(page.rows)), (2, 2))
        self.assertFalse(np.array_equal(*images))
        self.assertIsNotNone(pa.row_pixels_digest(images[0], page))
        self.assertEqual(pa.row_pixels_digest(images[0], page), pa.row_pixels_digest(images[1], page))
        for path in paths:
            self.assertEqual(pc.parse_count(ocr_items(path, roi=pc.LAYOUT["count_roi"])), page.count)

    def test_confirmation_and_result_are_distinct_positive_states(self):
        probe = NodeProbe()
        for frame, node, expected in (
            (CONFIRM, "SC.Present.Guard.ConfirmTitle", True),
            (CONFIRM, "SC.Present.Guard.ConfirmBody", True),
            (CONFIRM, "SC.Present.Guard.ConfirmQuestion", True),
            (CONFIRM, "SC.Present.Guard.ConfirmOK", True),
            (CONFIRM, "SC.Present.Color.ConfirmOK", True),
            (RECEIPT, "SC.Present.Guard.ResultBody", True),
            (RECEIPT, "SC.Present.Guard.ResultOK", True),
            (RECEIPT, "SC.Present.Color.ResultOK", True),
            (BEFORE, "SC.Present.Guard.ConfirmTitle", False),
            (RECEIPT, "SC.Present.Guard.ConfirmBody", False),
            (CONFIRM, "SC.Present.Guard.ResultBody", False),
            (WRONG_RECEIPT, "SC.Present.Guard.ResultBody", False),
            (RECEIVED, "SC.Present.Color.ReceivedTab", True),
            (EMPTY, "SC.Present.Color.ReceivedTab", False)):
            with self.subTest(frame=frame, node=node):
                self.assertEqual(probe.hits(frame, node), expected)


if __name__ == "__main__":
    unittest.main()
