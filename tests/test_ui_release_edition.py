"""The release UI and host expose screenshot capture, not screen/video recording."""
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class RecordingRemovalTests(unittest.TestCase):
    def test_recording_commands_and_settings_are_removed(self):
        protocol = json.loads((ROOT / "ui/protocol.json").read_text(encoding="utf-8"))
        self.assertNotIn("dev.recorder", protocol["commands"])
        self.assertNotIn("video.record", protocol["commands"])
        panel = (ROOT / "ui/components/settings-panel.js").read_text(encoding="utf-8")
        self.assertNotIn("videoView", panel)
        self.assertNotIn('key: "settings-video"', panel)
        self.assertNotIn("devRecorder", panel)

    def test_host_keeps_single_frame_capture(self):
        capture = (ROOT / "browser_host/BrowserHostForm.cs").read_text(encoding="utf-8")
        game = (ROOT / "browser_host/CompositionDemoForm.cs").read_text(encoding="utf-8")
        self.assertIn("SaveCaptureAsync", capture)
        self.assertIn("CaptureBridge", game)
        self.assertIn("CaptureImage", game)
        self.assertNotIn("VideoRecorder", game)
        self.assertNotIn("SessionRecorder", game)


if __name__ == "__main__":
    unittest.main()
