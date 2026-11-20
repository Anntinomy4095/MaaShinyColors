"""Conservative classification of ambiguous daily mission OCR evidence."""
import unittest

from agent.core.mission_status import build_report, classify, parse_counter


class MissionStatusEvidenceTests(unittest.TestCase):
    TITLE = {"text": "mission", "box": [391, 250, 247, 25]}
    PROGRESS = {"text": "1/1", "box": [605, 310, 36, 26]}
    CLAIM = {"text": "受け取る", "box": [1064, 267, 123, 33]}
    DETAIL = {"text": "詳細", "box": [1091, 267, 70, 41]}
    COUNTER = {"text": "1/5", "box": [1218, 576, 39, 26]}

    def test_multiple_buttons_are_unknown_even_when_progress_is_complete(self):
        for extra in (self.DETAIL, self.CLAIM):
            with self.subTest(extra=extra["text"]):
                report = build_report([self.TITLE, self.PROGRESS, self.CLAIM, extra], [self.COUNTER])
                self.assertEqual(report["rows"][0]["status"], "unknown")
                self.assertEqual(report["complete"], [])
                self.assertEqual(report["claimable"], [])
                self.assertFalse(report["ok"])

    def test_missing_button_keeps_existing_completed_classification(self):
        report = build_report([self.TITLE, self.PROGRESS], [self.COUNTER])
        self.assertEqual(report["rows"][0]["status"], "completed")
        self.assertTrue(report["ok"])

    def test_contradictory_progress_is_unknown(self):
        for done, total in ((9, 1), (-1, 1), (0, 0)):
            for button in (None, "受け取る", "詳細"):
                with self.subTest(done=done, total=total, button=button):
                    self.assertEqual(classify(done, total, button), "unknown")
        report = build_report([self.TITLE, dict(self.PROGRESS, text="9/1"), self.CLAIM], [self.COUNTER])
        self.assertEqual(report["complete"], [])
        self.assertFalse(report["ok"])

    def test_counter_requires_one_ratio_candidate(self):
        for extra in ("5/5", "1/5", "9/1", "0/0"):
            with self.subTest(extra=extra):
                self.assertIsNone(parse_counter([self.COUNTER, dict(self.COUNTER, text=extra)]))
        self.assertEqual(parse_counter([self.COUNTER]), (1, 5))
        self.assertIsNone(parse_counter([dict(self.COUNTER, text="9/1")]))
        self.assertIsNone(parse_counter([dict(self.COUNTER, text="0/0")]))

    def test_all_unknown_rows_do_not_make_successful_read(self):
        report = build_report([self.TITLE], [self.COUNTER])
        self.assertEqual(report["unknown"], ["mission"])
        self.assertFalse(report["ok"])

    def test_known_row_with_clipped_unknown_row_still_reads_successfully(self):
        clipped = dict(self.TITLE, text="clipped", box=[391, 528, 247, 25])
        report = build_report([self.TITLE, self.PROGRESS, self.CLAIM, clipped], [self.COUNTER])
        self.assertEqual(report["complete"], ["mission"])
        self.assertEqual(report["unknown"], ["clipped"])
        self.assertTrue(report["visible_rows_only"])
        self.assertTrue(report["ok"])


if __name__ == "__main__":
    unittest.main()
