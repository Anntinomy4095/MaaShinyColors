"""Daily-login sender replay. Native guards plus an explicitly synthetic silver receipt.

The synthetic receipt exercises wiring before the first supervised login claim. It is never
reported as a native receipt or used to grant real input. Every controller here is offline.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.core import mission_claim_actions as actions, mission_claim_reader as reader, pipeline_actions
from agent.core.frame_permit import FrameCapture
from agent.core.mission_claim_store import PRIVATE, DurableClaimTracker, initialize_claim_journal
from tests.maa_probe import ROOT, NodeProbe, maa_ready, ocr_items, rgb
from tools import run_tasks as launcher

BASE = "dev_assets/private/fes-match-reader-20261007/"
HOME = BASE + "home_after_present_settled.png"
INITIAL = BASE + "missions_daily_initial.png"
CLAIMED = BASE + "missions_after_bulk_bottom.png"
CLIPPED = BASE + "missions_after_bulk_top.png"
WRONG = BASE + "missions_single_receipt.png"
BULK = BASE + "missions_bulk_result.png"
CURRENT = "dev_assets/private/live-capture-20261008-knowhow/missions_daily_current.png"
NOW = dt.datetime(2026, 10, 7, 13, 34, 20, tzinfo=dt.timezone.utc)
ON = "开启（每日登录，单次）"
FONT = next((p for p in (Path("C:/Windows/Fonts/meiryob.ttc"), Path("C:/Windows/Fonts/YuGothB.ttc"))
             if p.exists()), Path("C:/Windows/Fonts/meiryob.ttc"))


def synthetic_receipt(path):
    """Copy calibrated popup layout, substitute one reviewed silver icon and render a test label."""
    from PIL import Image, ImageDraw, ImageFont

    image = Image.open(ROOT / WRONG).convert("RGB")
    draw = ImageDraw.Draw(image)
    draw.rectangle((390, 305, 885, 440), fill=(240, 235, 242))
    icon = Image.open(ROOT / CURRENT).convert("RGB").crop((274, 232, 381, 339))
    image.paste(icon, (394, 315))
    draw.text((514, 350), "トレーニングチケット x1", fill=(97, 90, 101),
              font=ImageFont.truetype(str(FONT), 23))
    image.save(path)
    return path


@unittest.skipUnless(maa_ready(HOME, INITIAL, CLAIMED, CLIPPED, WRONG, BULK, CURRENT)
                     and actions.SILVER_TEMPLATE_PATH.exists() and FONT.exists(),
                     "Requires private native mission frames, reviewed template, Maa OCR and Japanese font")
class MissionSenderTests(unittest.TestCase):
    def replay(self, frames, *, option=ON, simulate=True, initialize=True, pending=False, timeout=40,
               expected_reward="silver_ticket", frame_transform=None, receipt_factory=synthetic_receipt):
        table = pipeline_actions.default_table()
        activated = dataclasses.replace(table, actions=tuple(
            dataclasses.replace(a, enabled=True) if a.family == "mission-claim" else a
            for a in table.actions))
        with tempfile.TemporaryDirectory(dir=PRIVATE) as directory:
            directory = Path(directory)
            journal = directory / "login.sqlite3"
            if initialize:
                initialize_claim_journal(journal, "daily:2026-10-07")
            if pending:
                import numpy as np
                image = rgb(INITIAL)
                digest = hashlib.sha256(np.ascontiguousarray(image[:, :, ::-1]).tobytes()).hexdigest()
                frame = FrameCapture("a" * 64, 10.0, (720, 1280, 3), digest, 1, 1, 1)
                reading = reader.read_login(image_rgb=image, list_results=ocr_items(INITIAL, reader.ROIS["list"]),
                    timer_results=ocr_items(INITIAL, reader.ROIS["daily_timer"]), observed_at=NOW,
                    silver_template=actions.load_silver_template(), expected_reward="silver_ticket",
                    page_guard=True, overlay=False, frame=frame)
                tracker = DurableClaimTracker(journal, "daily:2026-10-07", enabled=True)
                decision = tracker.begin(reading.observation(frame), reader.LOGIN_ID, frame=frame, now=10.0)
                self.assertEqual(decision.state, "prepared")
            receipt = receipt_factory(directory / "synthetic-login-receipt.png")
            steps = []
            for index, frame_path in enumerate(frames):
                path = receipt if frame_path == "synthetic" else ROOT / frame_path
                if frame_transform is not None and frame_path != "synthetic":
                    path = frame_transform(path, directory / f"synthetic-frame-{index}.png")
                steps.append((path, "click"))
            with patch.object(pipeline_actions, "default_table", return_value=activated):
                summary = launcher.run({"task_timeout_s": timeout, "tasks": [
                    {"name": "MissionsClaim", "option": {"任务奖励领取": option}}]},
                    offline_steps=steps, simulate_input=simulate,
                    task_options={"missions": {"journal_path": journal, "clock": lambda: NOW,
                                              "expected_reward": expected_reward,
                                              "sample_root": Path(directory) / "samples"}},
                    summary_path=False, save_diagnostics=False)
            events = []
            if journal.exists():
                connection = sqlite3.connect(journal)
                try:
                    events = [v[0] for v in connection.execute("SELECT outcome FROM claim_events ORDER BY seq")]
                finally:
                    connection.close()
            return summary, events

    def assert_actions(self, summary, expected):
        task, = summary["tasks"]
        self.assertEqual(task["simulated_actions"], expected, task)
        self.assertEqual(task["simulated_spends"], [])
        self.assertEqual(summary["real_input_attempts"], 0)

    def test_synthetic_positive_receipt_and_native_clear_complete_the_flow(self):
        summary, events = self.replay([HOME, INITIAL, "synthetic", CLAIMED, HOME])
        self.assertEqual(summary["tasks"][0]["status"], "succeeded", summary)
        self.assert_actions(summary, ["home-mission", "mission-login-claim", "mission-result-close", "nav-back"])
        self.assertEqual(events, ["pending", "claimed"])

    def test_clipped_terminal_is_sought_once_before_completion(self):
        summary, events = self.replay([INITIAL, "synthetic", CLIPPED, CLAIMED, HOME])
        self.assertEqual(summary["tasks"][0]["status"], "succeeded", summary)
        self.assert_actions(summary, ["mission-login-claim", "mission-result-close", "mission-list-bottom", "nav-back"])
        self.assertEqual(events[-1], "claimed")

    def test_wrong_single_reward_is_not_closed_or_retried(self):
        summary, events = self.replay([INITIAL, WRONG])
        self.assertEqual(summary["tasks"][0]["status"], "failed", summary)
        self.assert_actions(summary, ["mission-login-claim"])
        self.assertEqual(events, ["pending", "uncertain"])

    def test_bulk_receipt_cannot_replace_single_login_receipt(self):
        summary, events = self.replay([INITIAL, BULK], timeout=60)
        self.assertEqual(summary["tasks"][0]["status"], "failed", summary)
        self.assert_actions(summary, ["mission-login-claim"])
        self.assertEqual(events, ["pending", "uncertain"])

    def test_failed_terminal_does_not_repeat_the_claim(self):
        summary, events = self.replay([INITIAL, "synthetic", CLIPPED, CLIPPED], timeout=60)
        # A bounded read timeout is also a stopped task; both must preserve the uncertain intent.
        self.assertIn(summary["tasks"][0]["status"], ("failed", "timeout"), summary)
        self.assert_actions(summary, ["mission-login-claim", "mission-result-close", "mission-list-bottom"])
        self.assertEqual(events[-1], "uncertain")

    def test_missing_journal_is_not_replaced(self):
        summary, events = self.replay([INITIAL], initialize=False)
        self.assert_actions(summary, [])
        self.assertEqual(events, [])

    def test_restart_with_pending_record_sends_nothing_and_preserves_it(self):
        summary, events = self.replay([INITIAL], pending=True)
        self.assert_actions(summary, [])
        self.assertEqual(events, ["pending"])
        self.assertEqual(summary["tasks"][0]["status"], "failed")

    def test_disabled_or_read_only_task_does_not_reserve_or_send(self):
        for option, simulate in (("关闭", True), (ON, False)):
            with self.subTest(option=option, simulate=simulate):
                summary, events = self.replay([HOME], option=option, simulate=simulate)
                self.assert_actions(summary, [])
                self.assertEqual(events, [])

    def test_positive_existing_clear_returns_home_without_claim(self):
        summary, events = self.replay([CLAIMED, HOME])
        self.assertEqual(summary["tasks"][0]["status"], "succeeded", summary)
        self.assert_actions(summary, ["nav-back"])
        self.assertEqual(events, [])

    def test_native_guards_distinguish_page_and_single_receipt(self):
        probe = NodeProbe()
        for frame, node, expected in (
            (INITIAL, "SC.Missions.DailyPage", True), (CLAIMED, "SC.Missions.DailyPage", True),
            (WRONG, "SC.Missions.DailyPage", False), (INITIAL, "SC.Missions.Color.Claim", True),
            (INITIAL, "SC.Missions.Guard.ClaimText", True),
            (INITIAL, "SC.Missions.Guard.LoginTitle", True),
            (INITIAL, "SC.Missions.Color.Scrollbar", True), (CLAIMED, "SC.Missions.Color.Scrollbar", True),
            (WRONG, "SC.Missions.Guard.ReceiptTitle", True),
            (BULK, "SC.Missions.Guard.ReceiptTitle", False)):
            with self.subTest(frame=frame, node=node):
                self.assertEqual(probe.hits(frame, node), expected)

    def test_synthetic_receipt_positive_and_two_native_reward_negatives(self):
        import numpy as np
        from PIL import Image

        template = actions.load_silver_template()
        with tempfile.TemporaryDirectory(dir=PRIVATE) as directory:
            path = synthetic_receipt(Path(directory) / "synthetic.png")
            image = np.ascontiguousarray(np.asarray(Image.open(path))[:, :, ::-1])
            self.assertTrue(actions.receipt_matches(image, ocr_items(path, actions.RECEIPT_RECT), template,
                                                   expected_reward="silver_ticket"))
        for path in (WRONG, BULK):
            image = np.ascontiguousarray(rgb(path)[:, :, ::-1])
            self.assertFalse(actions.receipt_matches(image, ocr_items(path, actions.RECEIPT_RECT), template,
                                                    expected_reward="silver_ticket"))


if __name__ == "__main__":
    unittest.main()
