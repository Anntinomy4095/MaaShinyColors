from __future__ import annotations

import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tools.evaluate_capture_dataset import evaluate, main

FIXTURES = Path(__file__).parent / "fixtures" / "replay"
TEMPLATES = Path(__file__).parent / "fixtures" / "replay_template"


class FakeRecognizer:
    version = "fake-offline"

    def __init__(self, hits):
        self.hits = hits
        self.calls = []

    def recognize(self, scene, image, base):
        self.calls.append((scene, image, base))
        return {"scene": scene["name"], "algorithm": scene["recognition"],
                "hit": scene["name"] in self.hits.get(image.name, []),
                "confidence_kind": "test_score_not_probability", "raw_detail": {"fixture": image.name}}


class CaptureEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.private = Path(self.temp.name).resolve() / "private"
        self.root = self.private / "dataset"
        guard = patch("tools.manage_capture_session.PRIVATE_ROOT", self.private)
        guard.start()
        self.addCleanup(guard.stop)
        decoder = patch("tools.evaluate_capture_dataset.read_png", side_effect=lambda path: path)
        self.read_png = decoder.start()
        self.addCleanup(decoder.stop)

    def session(self, identifier, split, filenames, fixtures=FIXTURES):
        manifest = json.loads((fixtures / "manifest.json").read_text(encoding="utf-8"))
        labels = {sample["image"]: sample["expected"] for sample in manifest["samples"]}
        directory = self.root / identifier
        directory.mkdir(parents=True)
        samples = []
        for filename in filenames:
            blob = (fixtures / filename).read_bytes()
            (directory / filename).write_bytes(blob)
            samples.append({"file": filename, "sha256": hashlib.sha256(blob).hexdigest(),
                            "label": "ready", "expected": labels[filename]})
        data = {"version": 1, "id": identifier, "split": split, "scene": "SC.Capture",
                "source": {"capture": "user_supplied_png", "dimensions": [1280, 720]}, "samples": samples}
        self.save(identifier, data)
        return data

    def save(self, identifier, data):
        (self.root / identifier / "session.json").write_text(json.dumps(data), encoding="utf-8")

    def run_evaluation(self, hits, fixtures=FIXTURES):
        recognizer = FakeRecognizer(hits)
        factory = Mock(return_value=recognizer)
        report = evaluate(self.root, fixtures / "manifest.json", factory)
        return report, recognizer, factory

    def test_real_fixtures_use_only_dataset_samples_and_keep_details(self):
        self.session("train-1", "train", ["ready.png"])
        self.session("holdout-1", "holdout", ["similar_wrong_color.png", "ambiguous.png"])
        report, recognizer, factory = self.run_evaluation({"ready.png": ["SC.Replay.Ready"],
            "ambiguous.png": ["SC.Replay.Ready", "SC.Replay.Complete"]})
        self.assertTrue(report["passed"], report["errors"])
        factory.assert_called_once_with()
        self.assertEqual(len(report["samples"]), 3)
        self.assertEqual(self.read_png.call_count, 3)
        self.assertEqual(len(recognizer.calls), 6)
        self.assertTrue(all(call[2] == FIXTURES.resolve() for call in recognizer.calls))
        self.assertEqual(report["configuration_sha256"], hashlib.sha256((FIXTURES / "manifest.json").read_bytes()).hexdigest())
        self.assertEqual(report["framework_version"], "fake-offline")
        self.assertEqual(report["splits"]["train"]["samples"], 1)
        self.assertEqual(report["splits"]["holdout"]["samples"], 2)
        by_image = {sample["image"]: sample for sample in report["samples"]}
        self.assertEqual(by_image["ambiguous.png"]["actual"]["reason"], "ambiguous_state")
        self.assertEqual(by_image["similar_wrong_color.png"]["actual"]["reason"], "unknown_state")
        self.assertIn("raw_detail", report["samples"][0]["recognitions"][0])
        self.assertFalse(report["input_authorized"])

    def test_template_branch_passes_configuration_base_and_parameters(self):
        self.session("train-1", "train", ["positive_a.png"], TEMPLATES)
        self.session("holdout-1", "holdout", ["similar_negative.png"], TEMPLATES)
        report, recognizer, _ = self.run_evaluation({"positive_a.png": ["SC.Replay.Badge.A"]}, TEMPLATES)
        self.assertTrue(report["passed"], report["errors"])
        scenes = json.loads((TEMPLATES / "manifest.json").read_text())["scenes"]
        self.assertEqual([call[0] for call in recognizer.calls], scenes * 2)
        self.assertTrue(all(call[2] == TEMPLATES.resolve() for call in recognizer.calls))
        for scene in scenes:
            self.assertEqual(report["template_sha256"][scene["template"]],
                             hashlib.sha256((TEMPLATES / scene["template"]).read_bytes()).hexdigest())

    def test_changed_sample_after_preflight_is_rejected_before_recognition(self):
        self.session("train-1", "train", ["ready.png"])
        self.session("holdout-1", "holdout", ["unknown.png"])
        recognizer = FakeRecognizer({})
        def mutate():
            (self.root / "holdout-1" / "unknown.png").write_bytes(b"changed")
            return recognizer
        report = evaluate(self.root, FIXTURES / "manifest.json", mutate)
        self.assertFalse(report["passed"])
        self.assertIn("样本发生变化", report["errors"][0])
        self.assertFalse(recognizer.calls)
        self.read_png.assert_not_called()

    def test_false_accept_refusal_and_wrong_scene_count_by_split(self):
        self.session("train-1", "train", ["ready.png", "complete.png"])
        self.session("holdout-1", "holdout", ["unknown.png", "partial.png"])
        report, _, _ = self.run_evaluation({"complete.png": ["SC.Replay.Ready"], "unknown.png": ["SC.Replay.Ready"]})
        self.assertFalse(report["passed"])
        self.assertEqual(report["splits"]["train"], {"samples": 2, "false_accepts": 0, "false_refusals": 1, "wrong_scene": 1, "faileds": 2})
        self.assertEqual(report["splits"]["holdout"], {"samples": 2, "false_accepts": 1, "false_refusals": 0, "wrong_scene": 0, "faileds": 1})

    def test_pending_error_missing_holdout_and_unknown_scene_reject_before_factory(self):
        train = self.session("train-1", "train", ["ready.png"])
        factory = Mock()
        report = evaluate(self.root, FIXTURES / "manifest.json", factory)
        self.assertIn("holdout", report["errors"][0])
        holdout = self.session("holdout-1", "holdout", ["unknown.png"])
        train["samples"][0]["label"] = "pending"
        del train["samples"][0]["expected"]
        self.save("train-1", train)
        self.assertFalse(evaluate(self.root, FIXTURES / "manifest.json", factory)["passed"])
        train["samples"][0].update(label="ready", expected={"scene": "SC.Unknown", "allowed": True, "reason": "positive_guard"})
        self.save("train-1", train)
        self.assertIn("未知场景", evaluate(self.root, FIXTURES / "manifest.json", factory)["errors"][0])
        holdout["samples"][0]["sha256"] = "0" * 64
        self.save("holdout-1", holdout)
        self.assertFalse(evaluate(self.root, FIXTURES / "manifest.json", factory)["passed"])
        factory.assert_not_called()
        self.read_png.assert_not_called()

    def test_full_manifest_validation_blocks_action_before_factory(self):
        self.session("train-1", "train", ["ready.png"])
        self.session("holdout-1", "holdout", ["unknown.png"])
        path = self.private / "malformed.json"
        path.write_text('{"action":"Click"}', encoding="utf-8")
        factory = Mock()
        report = evaluate(self.root, path, factory)
        self.assertFalse(report["passed"])
        factory.assert_not_called()

    def test_cli_writes_preflight_and_runtime_errors_to_private_report(self):
        output = self.private / "report.json"
        arguments = ["--dataset", str(self.root), "--recognition-manifest", str(FIXTURES / "manifest.json"), "--report", str(output)]
        with patch("tools.evaluate_capture_dataset.MaaRecognizer") as factory, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(arguments), 2)
            factory.assert_not_called()
        self.assertTrue(json.loads(output.read_text(encoding="utf-8"))["errors"])
        self.session("train-1", "train", ["ready.png"])
        self.session("holdout-1", "holdout", ["unknown.png"])
        with patch("tools.evaluate_capture_dataset.MaaRecognizer", side_effect=RuntimeError("native unavailable")), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(arguments), 2)
        self.assertIn("native unavailable", output.read_text(encoding="utf-8"))

    def test_cli_private_report_guard_and_comparison_failure_exit(self):
        self.session("train-1", "train", ["ready.png"])
        self.session("holdout-1", "holdout", ["unknown.png"])
        arguments = ["--dataset", str(self.root), "--recognition-manifest", str(FIXTURES / "manifest.json"), "--report"]
        with patch("tools.evaluate_capture_dataset.MaaRecognizer", return_value=FakeRecognizer({})), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(arguments + [str(self.private / "report.json")]), 1)
            self.assertEqual(main(arguments + [str(self.private.parent / "public.json")]), 2)
            self.assertEqual(main(arguments + [str(self.root / "train-1" / "session.json")]), 2)
        self.assertFalse((self.private.parent / "public.json").exists())


if __name__ == "__main__":
    unittest.main()
