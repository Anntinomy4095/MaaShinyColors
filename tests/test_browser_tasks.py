from __future__ import annotations

import copy
import unittest
from pathlib import Path

from agent.core.browser_tasks import ENTRY, PREFIX, interpret_trace, load_pipeline, require_demo, validate_pipeline


def trace(suffix):
    return [{"name": name, "completed": True, "recognition_hit": True, "action_success": True, "action": "DoNothing"} for name in (ENTRY, PREFIX + suffix)]


class BrowserTaskTests(unittest.TestCase):
    def test_owned_pipeline_is_valid(self):
        self.assertEqual(len(load_pipeline()), 6)

    def test_other_resource_directory_rejected(self):
        with self.assertRaises(ValueError):
            load_pipeline(Path("assets/resource/base"))

    def test_actions_extensions_and_cycles_rejected(self):
        for name, key, value in [("Ready", "action", "Click"), ("Ready", "action", "Custom"), ("Ready", "action", "Command"), ("Ready", "recognition", "Custom"), ("Ready", "shell", "anything"), ("Ready", "next", [ENTRY]), ("Ready", "inverse", True), ("Observe", "timeout", -1), ("Observe", "pre_delay", 5000), ("Unknown", "inverse", False), ("Ambiguous", "all_of", [PREFIX + "Ready"])]:
            data = copy.deepcopy(load_pipeline())
            data[PREFIX + name][key] = value
            with self.subTest(name=name, key=key, value=value), self.assertRaises(ValueError):
                validate_pipeline(data)

    def test_branch_order_and_external_references_rejected(self):
        data = copy.deepcopy(load_pipeline())
        data[ENTRY]["next"].reverse()
        with self.assertRaises(ValueError):
            validate_pipeline(data)
        data = copy.deepcopy(load_pipeline())
        data[PREFIX + "Ambiguous"]["all_of"] = ["SC.External", PREFIX + "Ready"]
        with self.assertRaises(ValueError):
            validate_pipeline(data)

    def test_invalid_roi_and_color_rejected(self):
        for key, value in [("roi", [0, 0, 1281, 720]), ("roi", [True, 0, 10, 10]), ("count", True), ("count", 10000000), ("lower", [-1, 0, 0]), ("method", 0)]:
            data = copy.deepcopy(load_pipeline())
            data[PREFIX + "Ready"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_pipeline(data)

    def test_framework_success_is_not_business_success(self):
        for suffix, reason in [("Unknown", "unknown_state"), ("Ambiguous", "ambiguous_state"), ("Error", "pipeline_error")]:
            result = interpret_trace(trace(suffix), True)
            self.assertFalse(result["passed"])
            self.assertEqual(result["reason"], reason)

    def test_positive_guards_and_already_complete(self):
        for suffix, state in [("Ready", "ready"), ("Complete", "complete")]:
            result = interpret_trace(trace(suffix), True)
            self.assertTrue(result["passed"])
            self.assertEqual(result["actual_state"], state)
            self.assertFalse(interpret_trace(trace(suffix), False)["passed"])

    def test_missing_or_failed_nodes_rejected(self):
        for nodes in ([], trace("Ready")[:1], trace("Ready") + trace("Complete"), trace("Unregistered")):
            self.assertFalse(interpret_trace(nodes, True)["passed"])
        for key, value in [("completed", False), ("recognition_hit", False), ("action_success", False), ("action", "Click")]:
            nodes = trace("Ready")
            nodes[-1][key] = value
            self.assertFalse(interpret_trace(nodes, True)["passed"])
        self.assertFalse(interpret_trace(trace("Ready"), True, "capture_failed")["passed"])

    def test_demo_and_ready_and_disabled_input_required(self):
        status = {"ready": True, "browser_demo": True, "input_enabled": False, "width": 1280, "height": 720}
        self.assertEqual(require_demo(status), status)
        for key, value in [("ready", False), ("browser_demo", False), ("input_enabled", True), ("width", 1279)]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                require_demo({**status, key: value})


if __name__ == "__main__":
    unittest.main()
