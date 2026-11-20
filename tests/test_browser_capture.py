from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from tools.capture_browser_window import capture, select_target
from tools.probe_browser import WindowRecord


def target(hwnd=42, title="Shiny Colors - Google Chrome"):
    return WindowRecord(hwnd, "Chrome_WidgetWin_1", title, 1280, 800, True, False)


class BrowserCaptureTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.private = Path(temp.name) / "private"
        self.output = self.private / "capture"
        guard = patch("tools.manage_capture_session.PRIVATE_ROOT", self.private)
        guard.start()
        self.addCleanup(guard.stop)

    def controller(self):
        controller = Mock()
        controller.set_screenshot_use_raw_size.return_value = True
        controller.post_connection.return_value.wait.return_value.succeeded = True
        job = controller.post_screencap.return_value.wait.return_value
        job.succeeded = True
        image = Mock(shape=(800, 1280, 3))
        image.max.return_value, image.min.return_value = 200, 10
        job.get.return_value = image
        return controller

    def test_only_unique_visible_game_window_can_be_selected(self):
        for windows in ([], [target(title="Other page")], [target(), target(43)]):
            with self.assertRaises(ValueError):
                select_target(windows)
        self.assertEqual(select_target([target()]).hwnd, 42)

    def test_capture_uses_null_input_and_writes_only_private_evidence(self):
        controller = self.controller()
        factory = Mock(return_value=controller)
        discover = Mock(return_value=("v5.12.3", [target()]))
        with patch("tools.capture_browser_window.encode_png", return_value=b"test PNG"):
            report = capture(self.output, discover=discover, controller_factory=factory)
        factory.assert_called_once_with(42, screencap_method=2, mouse_method=0, keyboard_method=0)
        self.assertTrue(report["capture_complete"])
        self.assertFalse(report["pipeline_eligible"])
        self.assertFalse(report["input_enabled"])
        self.assertEqual(report["frames"][0]["dimensions"], [1280, 800])
        self.assertEqual((self.output / "frame-0001.png").read_bytes(), b"test PNG")
        self.assertEqual({call[0] for call in controller.method_calls},
                         {"set_screenshot_use_raw_size", "post_connection", "post_screencap"})
        self.assertNotIn("title", json.loads((self.output / "report.json").read_text(encoding="utf-8")))

    def test_changed_window_before_or_after_capture_is_not_saved(self):
        initial = ("v5.12.3", [target()])
        changed = ("v5.12.3", [target(title="Other page")])
        for calls in ([initial, changed], [initial, initial, changed]):
            output = self.private / str(len(calls))
            with patch("tools.capture_browser_window.encode_png") as encoder:
                report = capture(output, discover=Mock(side_effect=calls), controller_factory=Mock(return_value=self.controller()))
            self.assertFalse(report["capture_complete"])
            encoder.assert_not_called()
            self.assertEqual(list(output.glob("*.png")), [])

    def test_bad_destination_and_capture_settings_fail_before_device_creation(self):
        factory = Mock()
        for output, method, frames in ((self.private.parent / "public", "framepool", 1),
                                     (self.output, "other", 1), (self.output, "framepool", True),
                                     (self.output, "framepool", 4)):
            with self.assertRaises(ValueError):
                capture(output, method, frames, discover=Mock(), controller_factory=factory)
        self.output.mkdir(parents=True)
        with self.assertRaises(ValueError):
            capture(self.output, discover=Mock(), controller_factory=factory)
        factory.assert_not_called()

    def test_ambiguous_target_and_wrong_version_fail_before_device_creation(self):
        factory = Mock()
        for snapshot in (("v5.12.3", [target(), target(43)]), ("v0", [target()])):
            with self.assertRaises(ValueError):
                capture(self.output, discover=Mock(return_value=snapshot), controller_factory=factory)
        factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
