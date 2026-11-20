from __future__ import annotations

import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent.core.confirmation import ConfirmationLedger
from tools.rehearse_confirmation import (
    FIXTURES, MANIFEST, GuardRejected, confirm_frame, load_configuration,
    main, observe_frame, rehearse,
)


class FakeRecognizer:
    version = "fake"
    input_attempts = 0

    def __init__(self):
        self.calls = []

    def recognize(self, scene, path, base):
        self.calls.append((scene["name"], path.name))
        is_a = scene["name"].endswith(".A")
        hit = path.stem in ({"positive_a", "ambiguous", "multiple_targets"} if is_a else {"positive_b", "ambiguous"})
        results = [{"box": [260 if is_a else 760, 240, 80, 64], "score": 1.0}] if hit else []
        if path.stem == "multiple_targets" and is_a:
            results.append({"box": [380, 300, 80, 64], "score": 1.0})
        return {"scene": scene["name"], "hit": hit, "results": results, "roi": scene["roi"], "raw_detail": {}}


class ConfirmationRehearsalTests(unittest.TestCase):
    def setUp(self):
        decoder = patch("tools.rehearse_confirmation.read_png", side_effect=lambda path: path)
        decoder.start()
        self.addCleanup(decoder.stop)

    def test_report_confirms_after_issue_and_exercises_terminal_refusals(self):
        recognizer = FakeRecognizer()
        report = rehearse(lambda: recognizer)
        self.assertTrue(report["passed"], report["cases"])
        self.assertEqual(report["input_attempts"], 0)
        cases = {case["id"]: case for case in report["cases"]}
        self.assertTrue(cases["same_frame_confirm"]["receipt"]["confirmed"])
        self.assertEqual(cases["duplicate_confirmation"]["error"], "proposal_consumed")
        self.assertEqual(cases["multiple_targets"]["error"], "ambiguous_target")
        self.assertEqual(cases["unknown_retry"]["error"], "proposal_cancelled")
        self.assertEqual(cases["same_frame_confirm"]["proposal"]["evidence"]["box"], [260, 240, 80, 64])
        self.assertFalse(report["input_authorized"])
        self.assertFalse(report["input_enabled"])
        self.assertEqual(report["approval_source"], "scripted_test_not_user_consent")
        self.assertEqual(sum(path == "positive_a.png" for _, path in recognizer.calls), 4)

    def test_unknown_current_frame_cancels_existing_proposal(self):
        recognizer = FakeRecognizer()
        evidence, _ = observe_frame(FIXTURES / "positive_a.png", MANIFEST, recognizer)
        ledger = ConfirmationLedger()
        proposal = ledger.issue(evidence)
        with self.assertRaisesRegex(GuardRejected, "unknown_state"):
            confirm_frame(ledger, proposal["proposal_id"], FIXTURES / "unknown.png", MANIFEST, recognizer, approved=True)
        with self.assertRaisesRegex(ValueError, "proposal_cancelled"):
            ledger.confirm(proposal["proposal_id"], evidence, approved=True)

    def test_missing_current_image_cancels_before_retry(self):
        recognizer = FakeRecognizer()
        evidence, _ = observe_frame(FIXTURES / "positive_a.png", MANIFEST, recognizer)
        ledger = ConfirmationLedger()
        proposal = ledger.issue(evidence)
        with self.assertRaises(OSError):
            confirm_frame(ledger, proposal["proposal_id"], FIXTURES / "missing.png", MANIFEST, recognizer, approved=True)
        with self.assertRaisesRegex(ValueError, "proposal_cancelled"):
            ledger.confirm(proposal["proposal_id"], evidence, approved=True)

    def test_configuration_identity_includes_template_bytes(self):
        data, digest, identity = load_configuration(MANIFEST)
        self.assertEqual(identity["manifest_sha256"], hashlib.sha256(MANIFEST.read_bytes()).hexdigest())
        with patch("tools.rehearse_confirmation.template_path") as template:
            template.return_value.read_bytes.return_value = b"different template"
            # Validate the actual original manifest; change only fingerprint inputs.
            with patch("tools.rehearse_confirmation.validate_manifest", return_value=data):
                self.assertNotEqual(load_configuration(MANIFEST)[1], digest)

    def test_image_or_configuration_changes_during_recognition_fail(self):
        data, digest, identity = load_configuration(MANIFEST)
        with patch("tools.rehearse_confirmation.load_configuration", side_effect=[(data, digest, identity), (data, "0" * 64, identity)]):
            with self.assertRaisesRegex(ValueError, "配置或模板发生变化"):
                observe_frame(FIXTURES / "positive_a.png", MANIFEST, FakeRecognizer())
        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp) / "positive_a.png"
            image.write_bytes((FIXTURES / "positive_a.png").read_bytes())
            class MutatingRecognizer(FakeRecognizer):
                def recognize(self, scene, path, base):
                    path.write_bytes(b"changed")
                    return super().recognize(scene, path, base)
            with self.assertRaisesRegex(ValueError, "图片发生变化"):
                observe_frame(image, MANIFEST, MutatingRecognizer())

    def test_denied_input_attempt_blocks_overall_pass(self):
        recognizer = FakeRecognizer()
        recognizer.input_attempts = 1
        self.assertFalse(rehearse(lambda: recognizer)["passed"])

    def test_invalid_report_path_does_not_construct_maa(self):
        with tempfile.TemporaryDirectory() as temp, patch("tools.rehearse_confirmation.MaaRecognizer") as recognizer:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["--report", str(Path(temp) / "report.json")]), 2)
            recognizer.assert_not_called()


if __name__ == "__main__":
    unittest.main()
