from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.replay_ocr import decide_field, main, model_metadata, replay, validate_manifest

FIXTURES = Path(__file__).parent / "fixtures" / "replay"
FIELD = {"name": "SC.Produce.Stamina", "roi": [0, 0, 80, 40], "expected": "^[0-9]+$", "threshold": 0.95}


def manifest():
    return {"version": 1, "canvas": [1280, 720], "fields": [copy.deepcopy(FIELD)], "samples": [{"id": "positive", "image": "ready.png", "expected": {FIELD["name"]: "42"}}]}


def recognition(text="42", score=0.97, hit=True):
    return {"hit": hit, "results": [{"text": text, "score": score, "box": [1, 2, 20, 10]}], "raw_detail": {"all": [{"text": text, "score": score}]}}


class OcrReplayTests(unittest.TestCase):
    def test_valid_manifest_and_threshold_boundaries(self):
        for value in (0, 0.95, 1):
            data = manifest()
            data["fields"][0]["threshold"] = value
            self.assertIs(validate_manifest(data, FIXTURES), data)

    def test_strict_parameters_reject_actions_and_invalid_values(self):
        cases = [("threshold", True), ("threshold", float("nan")), ("threshold", float("inf")), ("threshold", -0.1), ("threshold", 1.1), ("roi", [True, 0, 40, 40]), ("roi", [0, 0, 1281, 40]), ("roi", [0, 0, 0, 40]), ("name", "Stamina"), ("expected", "[0-9]+"), ("expected", "^[$"), ("expected", "^" + "a" * 255 + "$"), ("action", "Click"), ("model", "arbitrary"), ("next", ["SC.Input"])]
        for key, value in cases:
            with self.subTest(key=key, value=value):
                data = manifest()
                data["fields"][0][key] = value
                with self.assertRaises(ValueError):
                    validate_manifest(data, FIXTURES)

    def test_manifest_rejects_duplicate_fields_ids_and_wrong_expected(self):
        for mutation in (lambda d: d["fields"].append(copy.deepcopy(FIELD)), lambda d: d["samples"].append(copy.deepcopy(d["samples"][0])), lambda d: d["samples"][0].update(expected={}), lambda d: d["samples"][0].update(expected={FIELD["name"]: 42}), lambda d: d.update(version=True), lambda d: d.update(canvas=[1280, 720.0])):
            data = manifest()
            mutation(data)
            with self.assertRaises(ValueError):
                validate_manifest(data, FIXTURES)

    def test_paths_cannot_escape_or_be_absolute(self):
        for name in ("../replay_template/positive_a.png", str((FIXTURES / "ready.png").resolve()), "missing.png", "manifest.json"):
            data = manifest()
            data["samples"][0]["image"] = name
            with self.assertRaises(ValueError):
                validate_manifest(data, FIXTURES)

    def test_field_acceptance_and_rejections_preserve_evidence(self):
        good = decide_field(FIELD, recognition())
        self.assertTrue(good["accepted"])
        self.assertEqual(good["text"], "42")
        for raw, reason in ((recognition(score=0.949), "low_score"), (recognition(text="4?"), "unexpected_text"), (recognition(text="42\n"), "unexpected_text"), (recognition(hit=False), "no_match"), ({"hit": False, "results": [], "raw_detail": {}}, "no_match"), (recognition(score=float("nan")), "low_score"), (recognition(score=True), "low_score")):
            with self.subTest(reason=reason):
                result = decide_field(FIELD, raw)
                self.assertFalse(result["accepted"])
                self.assertIsNone(result["text"])
                self.assertEqual(result["reason"], reason)
                self.assertEqual(result["raw_detail"], raw["raw_detail"])
        multi = recognition()
        multi["results"].append({"text": "43", "score": 0.99, "box": [2, 2, 20, 10]})
        self.assertEqual(decide_field(FIELD, multi)["reason"], "ambiguous_text")
        self.assertTrue(decide_field(FIELD, recognition(score=0.95))["accepted"])
        self.assertFalse(decide_field(FIELD, recognition(score=1.01))["accepted"])
        self.assertFalse(decide_field(FIELD, recognition(score=-0.01))["accepted"])

    def test_replay_compares_exact_strings_and_records_hashes_without_maa(self):
        class FakeRecognizer:
            version = "v5.12.3"
            model = {"files": {"det.onnx": "test"}}
            def recognize(self, field, image): return recognition()
        data = manifest()
        with patch("tools.replay_ocr.read_png", return_value=object()):
            report = replay(data, FIXTURES, FakeRecognizer())
            self.assertTrue(report["passed"])
            self.assertEqual(report["samples"][0]["image_sha256"], hashlib.sha256((FIXTURES / "ready.png").read_bytes()).hexdigest())
            data["samples"][0]["expected"][FIELD["name"]] = "042"
            changed = replay(data, FIXTURES, FakeRecognizer())
            self.assertFalse(changed["passed"])
            self.assertEqual(changed["metrics"]["wrong_values"], 1)
            data["samples"][0]["expected"][FIELD["name"]] = None
            refused = replay(data, FIXTURES, FakeRecognizer())
            self.assertEqual(refused["metrics"]["false_accepts"], 1)

    def test_wrong_dimensions_skip_recognition_and_support_null_expected(self):
        data = manifest()
        data["samples"][0].update(image="invalid_size.png", expected={FIELD["name"]: None})
        class FakeRecognizer:
            version = "v5.12.3"
            model = {"files": {}}
            def recognize(self, field, image): raise AssertionError("invalid dimensions must not invoke OCR")
        with patch("tools.replay_ocr.read_png", side_effect=AssertionError("must not decode")):
            report = replay(data, FIXTURES, FakeRecognizer())
        self.assertTrue(report["passed"])
        self.assertEqual(report["samples"][0]["fields"][0]["reason"], "invalid_dimensions")

    def test_input_attempt_prevents_overall_pass_even_when_values_match(self):
        class FakeRecognizer:
            version = "fake"
            model = {"files": {}}
            input_attempts = 1
            def recognize(self, field, image): return recognition()
        with patch("tools.replay_ocr.read_png", return_value=object()):
            report = replay(manifest(), FIXTURES, FakeRecognizer())
        self.assertTrue(report["samples"][0]["passed"])
        self.assertFalse(report["passed"])
        self.assertEqual(report["input_attempts"], 1)

    def test_model_metadata_requires_exact_files_and_hashes(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            with self.assertRaises(ValueError): model_metadata(directory)
            for name in ("det.onnx", "rec.onnx", "keys.txt"):
                (directory / name).write_bytes(name.encode())
            self.assertEqual(model_metadata(directory)["files"]["keys.txt"], hashlib.sha256(b"keys.txt").hexdigest())
            (directory / "keys.txt").write_bytes(b"")
            with self.assertRaises(ValueError): model_metadata(directory)

    def test_changed_image_cannot_bind_old_ocr_values_to_new_hash(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            path = directory / "ready.png"
            path.write_bytes((FIXTURES / "ready.png").read_bytes())
            class MutatingRecognizer:
                def recognize(self, field, image):
                    path.write_bytes(b"changed after image decode")
                    return recognition()
            with patch("tools.replay_ocr.read_png", return_value=object()):
                with self.assertRaisesRegex(ValueError, "样本发生变化"):
                    replay(manifest(), directory, MutatingRecognizer())

    def test_cli_invalid_manifest_does_not_load_maa(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            source = directory / "manifest.json"
            report = directory / "report.json"
            source.write_text('{"action":"Click"}', encoding="utf-8")
            with patch("tools.replay_ocr.MaaOcrRecognizer") as recognizer:
                self.assertEqual(main(["--manifest", str(source), "--model-dir", str(directory), "--report", str(report)]), 2)
                recognizer.assert_not_called()
            self.assertFalse(json.loads(report.read_text(encoding="utf-8"))["passed"])


if __name__ == "__main__":
    unittest.main()
