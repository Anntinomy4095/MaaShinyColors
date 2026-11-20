from __future__ import annotations

import copy
import builtins
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.replay_screenshots import CANVAS, decide, main, png_dimensions, validate_manifest, read_png

FIXTURES = Path(__file__).parent / "fixtures" / "replay"
TEMPLATE_FIXTURES = Path(__file__).parent / "fixtures" / "replay_template"


class ScreenshotReplayTests(unittest.TestCase):
    def test_accelerated_decoder_matches_fallback_pixels_and_rejects_bad_crc(self):
        from PIL import Image
        import numpy as np
        expected=np.arange(8*5*3,dtype=np.uint8).reshape(5,8,3)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'pixels.png'
            Image.fromarray(expected).save(path)
            fast=read_png(path,expected_size=None)
            original_import=builtins.__import__
            def no_pillow(name,*args,**kwargs):
                if name=='PIL':raise ImportError('exercise fallback')
                return original_import(name,*args,**kwargs)
            with patch('builtins.__import__',side_effect=no_pillow):slow=read_png(path,expected_size=None)
            np.testing.assert_array_equal(fast,expected[:,:,::-1])
            np.testing.assert_array_equal(fast,slow)
            corrupt=bytearray(path.read_bytes());corrupt[29]^=1;path.write_bytes(corrupt)
            with self.assertRaises(ValueError):read_png(path,expected_size=None)

    def test_accelerated_decoder_rejects_transparent_screenshot(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'transparent.png'
            Image.new('RGBA',(4,4),(10,20,30,254)).save(path)
            with self.assertRaises(ValueError):read_png(path,expected_size=None)

    def setUp(self):
        self.manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))

    def test_builtin_samples_are_valid_and_include_negative_cases(self):
        validate_manifest(self.manifest, FIXTURES)
        reasons = {item["expected"]["reason"] for item in self.manifest["samples"]}
        self.assertEqual(reasons, {"positive_guard", "unknown_state", "ambiguous_state", "invalid_dimensions"})
        self.assertEqual(png_dimensions(FIXTURES / "ready.png"), CANVAS)

    def test_template_manifest_accepts_owned_small_badges(self):
        data = json.loads((TEMPLATE_FIXTURES / "manifest.json").read_text(encoding="utf-8"))
        validate_manifest(data, TEMPLATE_FIXTURES)
        self.assertEqual(png_dimensions(TEMPLATE_FIXTURES / "badge-a.png"), (80, 64))
        self.assertEqual({sample["expected"]["reason"] for sample in data["samples"]}, {"positive_guard", "unknown_state", "invalid_dimensions", "ambiguous_state"})

    def test_template_threshold_must_be_finite_numeric_and_bounded(self):
        for value in (True, "0.95", float("nan"), float("inf"), -0.1, 0, 1.01):
            with self.subTest(value=value):
                data = json.loads((TEMPLATE_FIXTURES / "manifest.json").read_text(encoding="utf-8"))
                data["scenes"][0]["threshold"] = value
                with self.assertRaises(ValueError):
                    validate_manifest(data, TEMPLATE_FIXTURES)

    def test_template_paths_cannot_escape_or_accept_multiple_templates(self):
        for value in ("../replay/ready.png", str((TEMPLATE_FIXTURES / "badge-a.png").resolve()), ["badge-a.png"], "missing.png", "generate_samples.py"):
            with self.subTest(value=value):
                data = json.loads((TEMPLATE_FIXTURES / "manifest.json").read_text(encoding="utf-8"))
                data["scenes"][0]["template"] = value
                with self.assertRaises(ValueError):
                    validate_manifest(data, TEMPLATE_FIXTURES)

    def test_template_larger_than_roi_is_rejected(self):
        data = json.loads((TEMPLATE_FIXTURES / "manifest.json").read_text(encoding="utf-8"))
        data["scenes"][0]["roi"] = [0, 0, 30, 30]
        with self.assertRaises(ValueError):
            validate_manifest(data, TEMPLATE_FIXTURES)

    def test_template_scene_rejects_input_custom_and_native_options(self):
        for key, value in (("action", "Click"), ("custom_action", "SC.DoInput"), ("next", ["SC.Click"]), ("method", 1), ("green_mask", True)):
            with self.subTest(key=key):
                data = json.loads((TEMPLATE_FIXTURES / "manifest.json").read_text(encoding="utf-8"))
                data["scenes"][0][key] = value
                with self.assertRaises(ValueError):
                    validate_manifest(data, TEMPLATE_FIXTURES)

    def test_rejects_actions_custom_and_task_graph_before_maa_is_imported(self):
        for key, value in [("action", "Click"), ("custom_recognition", "arbitrary"), ("next", ["SC.Click"]), ("on_error", ["SC.Recovery"])]:
            with self.subTest(key=key):
                data = copy.deepcopy(self.manifest)
                data["scenes"][0][key] = value
                with self.assertRaises(ValueError):
                    validate_manifest(data, FIXTURES)
        self.manifest["scenes"][0]["recognition"] = "Custom"
        with self.assertRaises(ValueError):
            validate_manifest(self.manifest, FIXTURES)

    def test_rejects_out_of_bounds_roi_and_invalid_pixel_threshold(self):
        for field, value in [("roi", [1200, 0, 420, 160]), ("roi", [0, 0, 0, 1]), ("count", 0), ("count", True), ("lower", [256, 0, 0])]:
            with self.subTest(field=field, value=value):
                data = copy.deepcopy(self.manifest)
                data["scenes"][0][field] = value
                with self.assertRaises(ValueError):
                    validate_manifest(data, FIXTURES)

    def test_rejects_paths_outside_manifest_directory(self):
        self.manifest["samples"][0]["image"] = "../produce/basic_turn.json"
        with self.assertRaises(ValueError):
            validate_manifest(self.manifest, FIXTURES)

    def test_rejects_contradictory_expected_permission(self):
        self.manifest["samples"][0]["expected"]["reason"] = "unknown_state"
        with self.assertRaises(ValueError):
            validate_manifest(self.manifest, FIXTURES)

    def test_unknown_ambiguous_and_wrong_size_fail_closed(self):
        self.assertEqual(decide([], CANVAS)["reason"], "unknown_state")
        matches = [{"scene": "SC.A", "hit": True}, {"scene": "SC.B", "hit": True}]
        self.assertEqual(decide(matches, CANVAS), {"scene": None, "allowed": False, "reason": "ambiguous_state"})
        self.assertEqual(decide(matches[:1], (640, 360))["reason"], "invalid_dimensions")
        self.assertFalse(decide(matches[:1], (640, 360))["allowed"])

    def test_invalid_cli_manifest_writes_error_and_never_constructs_maa(self):
        with tempfile.TemporaryDirectory() as temp:
            manifest = Path(temp) / "manifest.json"
            report = Path(temp) / "report.json"
            manifest.write_text('{"action":"Click"}', encoding="utf-8")
            with patch("tools.replay_screenshots.MaaRecognizer") as recognizer:
                self.assertEqual(main(["--manifest", str(manifest), "--report", str(report)]), 2)
                recognizer.assert_not_called()
            self.assertFalse(json.loads(report.read_text(encoding="utf-8"))["passed"])

    def test_cli_propagates_comparison_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            report = Path(temp) / "report.json"
            with patch("tools.replay_screenshots.MaaRecognizer"), patch("tools.replay_screenshots.replay", return_value={"passed": False, "samples": []}):
                self.assertEqual(main(["--manifest", str(FIXTURES / "manifest.json"), "--report", str(report)]), 1)


if __name__ == "__main__":
    unittest.main()
