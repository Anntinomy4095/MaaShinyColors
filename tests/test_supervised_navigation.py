import copy
import unittest
from unittest.mock import Mock, patch
from pathlib import Path

from tools.run_supervised_navigation import ANCHORS, ROOT, announcement_matches, click_once, fixed_pipeline, home_matches, private_output


class SupervisedNavigationTests(unittest.TestCase):
    def hits(self):
        return [{"scene": "SC.Research.Announcement." + name, "hit": True, "score": 0.97} for name in ANCHORS]

    def test_shared_return_target_is_not_page_guard(self):
        hits = self.hits()
        hits[0].update(hit=False, score=0.3)
        hits[1].update(hit=False, score=None)
        self.assertFalse(announcement_matches(hits))

    def test_three_unique_high_confidence_anchors_required(self):
        self.assertTrue(announcement_matches(self.hits()))
        for index in range(3):
            hits = self.hits()
            hits[index]["score"] = 0.949
            self.assertFalse(announcement_matches(hits))
        self.assertFalse(announcement_matches(self.hits()[:2]))
        self.assertFalse(announcement_matches([self.hits()[0]] * 3))

    def test_default_pipeline_never_reaches_click(self):
        pipeline = fixed_pipeline()
        self.assertFalse(pipeline["SC.SupervisedNav.Announcement.Click"]["enabled"])
        self.assertNotIn("SC.SupervisedNav.Announcement.Click", pipeline["SC.SupervisedNav.Observe"]["next"])

    def test_unknown_and_error_stop_and_target_is_fixed(self):
        pipeline = fixed_pipeline(True)
        self.assertEqual(pipeline["SC.SupervisedNav.Announcement.Click"]["target"], [57, 662, 1, 1])
        self.assertEqual(pipeline["SC.SupervisedNav.Unknown"]["action"], "StopTask")
        self.assertEqual(pipeline["SC.SupervisedNav.Error"]["action"], "StopTask")

    def test_disk_pipeline_cannot_weaken_conjunction_or_enable_input(self):
        for mutation in ("all_of", "enabled", "target"):
            data = copy.deepcopy(fixed_pipeline())
            node = data["SC.SupervisedNav.Announcement.Click"]
            node[mutation] = {"all_of": node["all_of"][:1], "enabled": True, "target": [600, 400, 1, 1]}[mutation]
            with patch("tools.run_supervised_navigation.load_json", return_value=data):
                with self.assertRaises(ValueError):
                    fixed_pipeline(True)

    def test_numeric_truthy_execute_is_rejected(self):
        with self.assertRaises(ValueError):
            fixed_pipeline(1)

    def test_postcondition_requires_both_unique_home_anchors(self):
        hits = [{"scene": "SC.Research.HomePostcondition." + name, "hit": True, "score": .97}
                for name in ("Mission", "FesText")]
        self.assertTrue(home_matches(hits))
        self.assertFalse(home_matches(hits[:1]))
        self.assertFalse(home_matches([hits[0], hits[0]]))
        hits[1]["hit"] = False
        self.assertFalse(home_matches(hits))

    def test_lost_response_is_one_attempt_and_cannot_retry(self):
        report = {"input_attempts": 0}
        guard = Mock(return_value="current-binding")
        send = Mock(side_effect=TimeoutError("response lost"))
        self.assertFalse(click_once(report, True, (57, 662), guard, send))
        self.assertEqual(report["input_attempts"], 1)
        self.assertFalse(click_once(report, True, (57, 662), guard, send))
        self.assertEqual(send.call_count, 1)
        self.assertEqual(guard.call_count, 1)

    def test_refused_fresh_guard_cannot_send_or_spend_budget(self):
        for reason in ("partial_foreground", "expired_frame_hash", "capture_failed"):
            report = {"input_attempts": 0}
            send = Mock()
            self.assertFalse(click_once(report, True, (57, 662), Mock(side_effect=ValueError(reason)), send))
            self.assertEqual(report["input_attempts"], 0)
            send.assert_not_called()

    def test_disabled_or_arbitrary_point_never_checks_guard_or_sends(self):
        for enabled, point in ((False, (57, 662)), (True, (500, 500)), (1, (57, 662))):
            guard, send = Mock(), Mock()
            self.assertFalse(click_once({"input_attempts": 0}, enabled, point, guard, send))
            guard.assert_not_called()
            send.assert_not_called()

    def test_capture_outputs_cannot_escape_private(self):
        with self.assertRaises(ValueError):
            private_output(ROOT / "navigation-public.json")
        with self.assertRaises(ValueError):
            private_output(ROOT / "dev_assets/private/../../navigation-public.json")
        report, frames = private_output(ROOT / "dev_assets/private/test-unused-navigation-report.json")
        self.assertTrue(frames.is_relative_to(ROOT / "dev_assets/private"))


if __name__ == "__main__":
    unittest.main()
