import copy
import unittest
from unittest.mock import Mock, patch

import numpy as np

from agent.core.daily_navigation import GROUPS, ROIS
from agent.core.daily_scroll import (ACTION_POINTS, DELTAS, GUARD_ACTIONS, PREFIX, allowed,
                                     fixed_pipeline, fixed_target_binding, observe_change)
from tools.run_daily_scroll import guarded_send, validate_offline_proof, verify_scroll_protocol


def hits(group):
    return [{"scene": name, "roi": roi, "hit": name in GROUPS[group],
             "score": .99 if name in GROUPS[group] else .1,
             "raw_detail": {"filtered": [{"box": roi}] if name in GROUPS[group] else []}}
            for name, roi in ROIS.items()]


class DailyScrollTests(unittest.TestCase):
    def test_six_fixed_scrolls_default_disabled_and_no_clicks(self):
        for action in GUARD_ACTIONS:
            pipeline = fixed_pipeline(action)
            self.assertFalse(pipeline[PREFIX + action + ".Scroll"]["enabled"])
            self.assertEqual(pipeline[PREFIX + action + ".Scroll"]["target"], [*ACTION_POINTS[action], 1, 1])
            self.assertFalse(any(node["action"] == "Click" for node in pipeline.values()))
            executing = fixed_pipeline(action, True)
            self.assertEqual([name for name, node in executing.items() if node.get("enabled") is True],
                             [PREFIX + action + ".Scroll"])
            self.assertEqual(executing[PREFIX + action + ".Scroll"]["dy"], DELTAS[action][1])

    def test_changed_disk_delta_target_guard_or_enabled_refused(self):
        original = fixed_pipeline("mission-scroll-down")
        for key, value in (("dy", -480), ("target", [0, 0, 1, 1]), ("all_of", []), ("enabled", True)):
            altered = copy.deepcopy(original)
            altered[PREFIX + "mission-scroll-down.Scroll"][key] = value
            with patch("agent.core.daily_scroll.load_json", return_value=altered), self.assertRaises(ValueError):
                fixed_pipeline("mission-scroll-down", True)
        with self.assertRaises(ValueError): fixed_pipeline("arbitrary-scroll")
        with self.assertRaises(ValueError): fixed_pipeline("mission-scroll-down", 1)

    def test_maa_target_binding_accepts_only_fixed_zero_pressure_move(self):
        for action, (x, y) in ACTION_POINTS.items():
            self.assertTrue(fixed_target_binding(action, 0, x, y, 0))
            self.assertFalse(fixed_target_binding(action, 0, x + 1, y, 0))
            self.assertFalse(fixed_target_binding(action, 1, x, y, 0))
            self.assertFalse(fixed_target_binding(action, 0, x, y, 1))
            self.assertFalse(fixed_target_binding(action, False, x, y, 0))

    def test_scroll_needs_complete_unique_fixed_roi_and_unobstructed_group(self):
        foreground = {"state": "no_known_popup"}
        for action, group in (("mission-scroll-down", "Mission"), ("match-area-scroll-up", "MatchMain"),
                              ("match-skip-scroll-down", "MatchSkip")):
            observed = hits(group)
            self.assertTrue(allowed(action, observed, foreground))
            self.assertFalse(allowed(action, observed, {"state": "known_popup"}))
            selected = next(item for item in observed if item["scene"] == GROUPS[group][0])
            selected["raw_detail"]["filtered"] = [{"box": [0, 0, 1, 1]}]
            self.assertFalse(allowed(action, observed, foreground))
            selected["raw_detail"]["filtered"] = [{"box": ROIS[selected["scene"]]}] * 2
            self.assertFalse(allowed(action, observed, foreground))

    def test_unique_smaller_template_box_is_valid_only_within_fixed_roi(self):
        foreground = {"state": "no_known_popup"}
        observed = hits("Mission")
        selected = next(item for item in observed if item["scene"] == GROUPS["Mission"][0])
        selected["raw_detail"]["filtered"] = [{"box": [53, 27, 132, 27]}]
        self.assertTrue(allowed("mission-scroll-down", observed, foreground))
        for box in ([37, 27, 132, 27], [53, 27, 200, 27], [53, 27, 0, 27],
                    [53, 27, 132, 99], [53.0, 27, 132, 27], [True, 27, 132, 27]):
            selected["raw_detail"]["filtered"] = [{"box": box}]
            self.assertFalse(allowed("mission-scroll-down", observed, foreground))

    def test_content_change_is_pixel_evidence_without_progress_or_boundary_claims(self):
        before = np.zeros((720, 1280, 3), dtype=np.uint8)
        outside = before.copy()
        outside[0, 0] = 255
        observed = observe_change("mission-scroll-down", before, outside, hits("Mission"), {"state": "no_known_popup"})
        self.assertFalse(observed["content_changed"])
        inside = before.copy()
        inside[230, 270] = 255
        observed = observe_change("mission-scroll-down", before, inside, hits("Mission"), {"state": "no_known_popup"})
        self.assertTrue(observed["content_changed"])
        self.assertTrue(observed["screen_guard_verified"])
        for key in ("semantic_progress_verified", "latest_stage_verified", "list_boundary_verified"):
            self.assertFalse(observed[key])
        observed = observe_change("mission-scroll-down", before, inside, hits("Mission"), {"state": "partial"})
        self.assertFalse(observed["screen_guard_verified"])

    def test_one_attempt_persisted_before_send_and_lost_response_never_retries(self):
        report = {"input_attempts": 0}
        events = []
        def send(request):
            events.append("send")
            raise TimeoutError("unknown")
        call = lambda: guarded_send(report, True, "mission-scroll-down", (0, -240), lambda: {}, send,
                                    lambda: events.append("checkpoint"))
        self.assertFalse(call())
        self.assertFalse(call())
        self.assertEqual(events, ["checkpoint", "send"])
        self.assertEqual(report["input_attempts"], 1)

    def test_disabled_wrong_delta_and_guard_failure_send_nothing(self):
        send = Mock()
        for execute, delta, guard in ((False, (0, -240), Mock()), (True, (0, -480), Mock()),
                                     (True, (0, -240), Mock(side_effect=ValueError("overlay")))):
            report = {"input_attempts": 0}
            self.assertFalse(guarded_send(report, execute, "mission-scroll-down", delta, guard, send))
            self.assertEqual(report["input_attempts"], 0)
        send.assert_not_called()

    def test_cached_proof_rejects_other_action_code_missing_samples_or_wrong_direction(self):
        proof = {"passed": True, "offline_only": True, "input_enabled": False, "input_attempts": 0,
                 "action": "mission-scroll-down", "configuration_sha256": "a", "foreground_sha256": "b",
                 "implementation_sha256": "c", "offline": [{"id": "positive", "passed": True,
                 "expected": True, "allowed": True, "scrolls": [[0, -240]], "unexpected_inputs": 0}]}
        self.assertEqual(validate_offline_proof(proof, "mission-scroll-down", "a", "b", ["positive"], "c"), proof["offline"])
        for update in ({"action": "mission-scroll-up"}, {"implementation_sha256": "d"},
                       {"input_attempts": True}, {"offline": []},
                       {"offline": [{**proof["offline"][0], "scrolls": [[0, 240]]}]}):
            with self.assertRaises(ValueError):
                validate_offline_proof({**proof, **update}, "mission-scroll-down", "a", "b", ["positive"], "c")

    def test_unintegrated_native_protocol_fails_closed(self):
        with patch("tools.run_daily_scroll.navigation_protocol.DAILY_SCROLL_ACTION_POINTS", None, create=True), self.assertRaises(ValueError):
            verify_scroll_protocol("mission-scroll-down")


if __name__ == "__main__":
    unittest.main()
