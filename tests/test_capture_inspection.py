from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

from tools import inspect_capture as tool


def write_png(path, width=100, height=60):
    def chunk(kind, payload):
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xffffffff)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress((b"\0" + b"\0" * width * 3) * height)) + chunk(b"IEND", b""))


class FakeImage:
    shape = (60, 100, 3)


class FakeRecognizer:
    version = "test-recognizer"
    model = {"files": {}}
    input_attempts = 0

    def __init__(self, text="Visible", score=0.97, box=None):
        self.text, self.score, self.box = text, score, box or [1, 2, 20, 10]
        self.shapes = []

    def recognize(self, field, image):
        self.shapes.append(image.shape)
        # Fixed fake output tests decisions only; expected never feeds this output.
        return {"hit": True, "results": [{"text": self.text, "score": self.score, "box": self.box}], "raw_detail": {"source": "unit-test-only"}}


class CaptureInspectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.image = self.base / "image.png"
        self.manifest = self.base / "manifest.json"
        self.report = self.base / "output/report.json"
        write_png(self.image)
        self.data = {"version": 1, "image_sha256": hashlib.sha256(self.image.read_bytes()).hexdigest(), "dimensions": [100, 60], "fields": [{"name": "SC.Visible", "roi": [0, 0, 30, 20], "expected": "^Visible$", "threshold": 0.95}]}
        self.manifest.write_text(json.dumps(self.data), encoding="utf-8")
        patcher = patch.object(tool, "PRIVATE", self.base)
        patcher.start()
        self.addCleanup(patcher.stop)

    def inspect(self, recognizer=None, data=None):
        with patch.object(tool, "read_png", return_value=FakeImage()) as decoder:
            report = tool.inspect_capture(data or self.data, self.image, recognizer or FakeRecognizer())
            decoder.assert_called_once_with(self.image, expected_size=None)
            return report

    def test_native_dimensions_and_completion_are_distinct_from_acceptance(self):
        recognizer = FakeRecognizer()
        report = self.inspect(recognizer)
        self.assertTrue(report["inspection_complete"])
        self.assertFalse(report["canonical_ready"])
        self.assertFalse(report["pipeline_eligible"])
        self.assertTrue(report["readonly"])
        self.assertFalse(report["input_enabled"])
        self.assertEqual(report["input_attempts"], 0)
        self.assertEqual(recognizer.shapes, [(60, 100, 3)])
        self.assertTrue(report["fields"][0]["accepted"])
        refused = self.inspect(FakeRecognizer(text="Other"))
        self.assertTrue(refused["inspection_complete"])
        self.assertEqual(refused["fields"][0]["reason"], "unexpected_text")

    def test_threshold_and_source_box_mismatch_refuse_fields(self):
        for recognizer, reason in ((FakeRecognizer(score=0.949), "low_score"), (FakeRecognizer(box=[98, 2, 20, 10]), "box_coordinate_mismatch"), (FakeRecognizer(box=[1, 2, 20.0, 10]), "box_coordinate_mismatch"), (FakeRecognizer(box=[-1, 2, 20, 10]), "box_coordinate_mismatch")):
            report = self.inspect(recognizer)
            self.assertFalse(report["fields"][0]["accepted"])
            self.assertEqual(report["fields"][0]["reason"], reason)
            self.assertEqual(report["fields"][0]["raw_detail"], {"source": "unit-test-only"})
        self.assertTrue(self.inspect(FakeRecognizer(score=0.95))["fields"][0]["accepted"])

    def test_strict_manifest_rejects_actions_and_invalid_parameters(self):
        mutations = [lambda d: d.update(action="Click"), lambda d: d.update(pipeline={}), lambda d: d.update(next=[]), lambda d: d.update(version=True), lambda d: d.update(image_sha256="F" * 64), lambda d: d.update(dimensions=[101, 60]), lambda d: d.update(fields=[]), lambda d: d["fields"].append(copy.deepcopy(d["fields"][0]))]
        cases = [("action", "Click"), ("next", []), ("threshold", 0.94), ("threshold", True), ("threshold", float("nan")), ("threshold", float("inf")), ("threshold", 1.01), ("roi", [0, 0, 101, 20]), ("roi", [True, 0, 20, 20]), ("name", "Visible"), ("expected", "Visible"), ("expected", "^[$")]
        mutations.extend(lambda d, k=k, v=v: d["fields"][0].update({k: v}) for k, v in cases)
        for mutate in mutations:
            data = copy.deepcopy(self.data)
            mutate(data)
            with self.assertRaises(ValueError):
                tool.validate_manifest(data, self.image)

    def test_paths_protect_input_manifest_session_existing_files_and_public_output(self):
        for target in (self.image, self.manifest, self.base / "session.json", self.base / "SESSION.JSON", self.base.parent / "outside.json"):
            with self.assertRaises(ValueError):
                tool.validate_paths(self.image, self.manifest, target)
        self.report.parent.mkdir()
        self.report.write_text("keep", encoding="utf-8")
        with self.assertRaises(ValueError):
            tool.validate_paths(self.image, self.manifest, self.report)
        self.assertEqual(self.report.read_text(encoding="utf-8"), "keep")
        with self.assertRaises(ValueError):
            tool.validate_paths(self.base.parent / "outside.png", self.manifest, self.report)

    def test_runtime_input_attempts_are_required_and_strict(self):
        for attempts in (None, True, False, 1):
            recognizer = FakeRecognizer()
            recognizer.input_attempts = attempts
            with self.assertRaises(ValueError):
                self.inspect(recognizer)

    def test_hash_mutation_during_recognition_prevents_completion(self):
        original = self.image
        class MutatingRecognizer(FakeRecognizer):
            def recognize(self, field, image):
                original.write_bytes(b"changed")
                return super().recognize(field, image)
        with self.assertRaisesRegex(ValueError, "截图发生变化"):
            self.inspect(MutatingRecognizer())

    def test_invalid_inputs_and_decode_failures_do_not_construct_maa(self):
        argv = ["--image", str(self.image), "--manifest", str(self.manifest), "--model-dir", str(self.base), "--report", str(self.report)]
        self.manifest.write_text('{"action":"Click"}', encoding="utf-8")
        with patch.object(tool, "MaaOcrRecognizer") as factory, contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(tool.main(argv), 2)
            factory.assert_not_called()
        self.assertFalse(json.loads(self.report.read_text(encoding="utf-8"))["inspection_complete"])
        self.assertEqual(set(json.loads(output.getvalue())), {"inspection_complete", "canonical_ready", "report", "error"})
        self.report.unlink()
        self.manifest.write_text(json.dumps(self.data), encoding="utf-8")
        with patch.object(tool, "read_png", side_effect=ValueError("bad PNG")), patch.object(tool, "MaaOcrRecognizer") as factory, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(tool.main(argv), 2)
            factory.assert_not_called()

    def test_duplicate_json_keys_are_rejected(self):
        self.manifest.write_text('{"version":1,"version":1}', encoding="utf-8")
        with self.assertRaises(ValueError):
            tool.load_manifest(self.manifest)

    def test_decoded_dimensions_cannot_be_resized(self):
        class ResizedImage:
            shape = (720, 1280, 3)
        with self.assertRaises(ValueError):
            tool.inspect_capture(self.data, self.image, FakeRecognizer(), ResizedImage())


if __name__ == "__main__":
    unittest.main()
