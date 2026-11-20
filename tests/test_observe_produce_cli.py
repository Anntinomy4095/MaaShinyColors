from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from tools.observe_produce import main, observe

FIXTURES = Path(__file__).parent / "fixtures/ocr_observation"


def evidence():
    annotation = json.loads((FIXTURES / "annotation.json").read_text(encoding="utf-8"))
    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    expected = next(s["expected"] for s in manifest["samples"] if s["id"] == "positive")
    sample = {"id": "positive", "image": "positive.png", "image_sha256": annotation["image_sha256"], "passed": True,
              "fields": [{"name": f["name"], "accepted": True, "text": expected[f["name"]], "score": 0.99, "box": f["roi"], "reason": "accepted", "raw_detail": {}} for f in manifest["fields"]]}
    # Deliberately simulated recognition; this test verifies evidence assembly, not OCR.
    return {"framework_version": "v5.12.3", "samples": [sample]}, annotation


class ObserveProduceCliTests(unittest.TestCase):
    def test_matching_frame_and_annotation_generate_readonly_rest_advice(self):
        report, annotation = evidence()
        result = observe(report, "positive", annotation, FIXTURES / "positive.png")
        self.assertTrue(result["passed"])
        self.assertFalse(result["input_enabled"])
        self.assertEqual(result["decision"]["chosen"]["id"], "rest")
        self.assertEqual(result["availability_source"], "manual_annotation")

    def test_wrong_frame_failed_sample_or_duplicate_id_refused(self):
        report, annotation = evidence()
        with self.assertRaises(ValueError):
            observe(report, "positive", annotation, FIXTURES / "blank.png")
        changed = copy.deepcopy(report)
        changed["samples"][0]["passed"] = False
        with self.assertRaises(ValueError):
            observe(changed, "positive", annotation, FIXTURES / "positive.png")
        report["samples"].append(copy.deepcopy(report["samples"][0]))
        with self.assertRaises(ValueError):
            observe(report, "positive", annotation, FIXTURES / "positive.png")

    def test_cli_failure_persists_refusal_without_decision(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            report, annotation = evidence()
            report["samples"][0]["fields"][0]["text"] = "8O"
            (directory / "ocr.json").write_text(json.dumps(report), encoding="utf-8")
            (directory / "annotation.json").write_text(json.dumps(annotation), encoding="utf-8")
            target = directory / "result.json"
            self.assertEqual(main(["--ocr-report", str(directory / "ocr.json"), "--sample-id", "positive",
                                   "--annotation", str(directory / "annotation.json"), "--image", str(FIXTURES / "positive.png"), "--report", str(target)]), 2)
            result = json.loads(target.read_text(encoding="utf-8"))
            self.assertFalse(result["passed"])
            self.assertNotIn("decision", result)


if __name__ == "__main__":
    unittest.main()
