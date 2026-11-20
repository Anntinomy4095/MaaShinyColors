from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from agent.core.models import Scenario
from agent.core.produce_input import decide_from_params, explicit_scenario
from agent.core.scenarios import DEFAULT_SCENARIO


ROOT = Path(__file__).resolve().parents[1]


def observed_payload() -> dict:
    payload = deepcopy(DEFAULT_SCENARIO)
    payload["state"]["confidence"] = 0.99
    for candidate in payload["candidates"]:
        candidate.update(available=True, confidence=0.99)
    return payload


class ProduceInputTests(unittest.TestCase):
    def test_no_implicit_sample_or_invalid_scenario_fallback(self) -> None:
        for params in ({}, {"scenario": None}, {"scenario": {}}, {"sample": "true"}, {"sample": True, "scenario": observed_payload()}):
            with self.subTest(params=params), self.assertRaises(ValueError):
                decide_from_params(params)
        self.assertEqual(decide_from_params({"sample": True}).chosen.candidate.candidate_id, "visual_lesson")

    def test_explicit_state_changes_decision(self) -> None:
        payload = observed_payload()
        original = decide_from_params({"scenario": payload})
        payload["state"]["stamina"] = 1
        changed = decide_from_params(json.dumps({"scenario": payload}))
        self.assertEqual(original.chosen.candidate.candidate_id, "visual_lesson")
        self.assertEqual(changed.chosen.candidate.candidate_id, "rest")

    def test_unknown_and_low_confidence_state_are_rejected(self) -> None:
        for field, value in (("recognized", False), ("confidence", 0.9), ("confidence", None)):
            payload = observed_payload()
            payload["state"][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                decide_from_params({"scenario": payload})
        for field in ("confidence", "stamina", "turn", "targets"):
            payload = observed_payload()
            del payload["state"][field]
            with self.subTest(missing=field), self.assertRaises(ValueError):
                explicit_scenario(payload)

    def test_missing_attribute_or_candidate_evidence_is_rejected(self) -> None:
        payload = observed_payload()
        del payload["state"]["stats"]["visual"]
        with self.assertRaisesRegex(ValueError, "explicit stats"):
            explicit_scenario(payload)
        for field in ("available", "confidence"):
            payload = observed_payload()
            del payload["candidates"][0][field]
            with self.subTest(field=field), self.assertRaises(ValueError):
                explicit_scenario(payload)

    def test_invalid_numeric_and_object_fields_are_rejected(self) -> None:
        for field, value in (("turn", 1.5), ("turn", True), ("turns_to_exam", -1), ("stamina", float("nan")), ("max_stamina", float("inf")), ("mental", -1), ("confidence", 1.1)):
            payload = observed_payload()
            payload["state"][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                Scenario.from_dict(payload)
        for field, value in (("stamina_cost", float("inf")), ("priority_bonus", float("nan")), ("available", "false"), ("confidence", -0.1), ("risk", 2), ("id", None)):
            payload = observed_payload()
            payload["candidates"][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                Scenario.from_dict(payload)
        for payload in (None, [], {"state": {}, "candidates": [None]}, {"state": {}, "candidates": []}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                Scenario.from_dict(payload)

    def test_duplicate_and_unavailable_candidates(self) -> None:
        payload = observed_payload()
        payload["candidates"][1]["id"] = payload["candidates"][0]["id"]
        with self.assertRaisesRegex(ValueError, "duplicate"):
            Scenario.from_dict(payload)
        payload = observed_payload()
        payload["candidates"][1]["available"] = False
        decision = decide_from_params({"scenario": payload})
        self.assertNotIn("visual_lesson", [item.candidate.candidate_id for item in decision.ranking])
        for candidate in payload["candidates"]:
            candidate["available"] = False
        with self.assertRaisesRegex(ValueError, "available"):
            decide_from_params({"scenario": payload})

    def test_low_confidence_available_candidate_stops_decision(self) -> None:
        payload = observed_payload()
        payload["candidates"][0]["confidence"] = 0.2
        with self.assertRaisesRegex(ValueError, "low confidence"):
            decide_from_params({"scenario": payload})
        payload["candidates"][0]["available"] = False
        self.assertEqual(decide_from_params({"scenario": payload}).chosen.candidate.candidate_id, "visual_lesson")

    def test_cost_does_not_infer_availability(self) -> None:
        payload = observed_payload()
        payload["candidates"] = [payload["candidates"][0]]
        payload["candidates"][0]["stamina_cost"] = 200
        self.assertEqual(decide_from_params({"scenario": payload}).chosen.candidate.candidate_id, "vocal_lesson")

    def test_cli_fixture_sample_strict_and_rejections(self) -> None:
        command = [sys.executable, str(ROOT / "tools" / "simulate_produce.py")]
        fixture = ROOT / "tests" / "fixtures" / "produce" / "basic_turn.json"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "observed.json"
            path.write_text(json.dumps(observed_payload()), encoding="utf-8")
            for args in ([str(fixture)], ["--sample", "--low-stamina-threshold", "50"], [str(path), "--require-confidence"]):
                result = subprocess.run(command + args, capture_output=True, text=True, encoding="utf-8", env=self._utf8_env())
                with self.subTest(args=args):
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("chosen", json.loads(result.stdout))
            for args in ([], [str(fixture), "--require-confidence"], ["--sample", "--low-stamina-threshold", "nan"], [str(path), "--sample"]):
                result = subprocess.run(command + args, capture_output=True, text=True, encoding="utf-8", env=self._utf8_env())
                with self.subTest(args=args):
                    self.assertEqual(result.returncode, 2)
                    self.assertNotIn("Traceback", result.stderr)

    @staticmethod
    def _utf8_env() -> dict[str, str]:
        import os
        return dict(os.environ, PYTHONIOENCODING="utf-8")


if __name__ == "__main__":
    unittest.main()
