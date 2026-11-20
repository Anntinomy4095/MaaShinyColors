from __future__ import annotations

from copy import deepcopy
import unittest

from agent.core.produce_input import decide_from_params, explicit_scenario
from agent.core.produce_observation import build_observed_scenario
from agent.core.scenarios import DEFAULT_SCENARIO


def observation() -> tuple[dict, dict]:
    values = {
        "Turn": "8", "TurnsToExam": "3", "Stamina": "42", "MaxStamina": "100",
        "Vocal": "260", "Dance": "310", "Visual": "220",
    }
    sample = {
        "id": "offline-frame", "image": "private/frame.png", "image_sha256": "a" * 64,
        "fields": [
            {"name": f"SC.Produce.{name}", "accepted": True, "text": text,
             "score": 0.99, "box": [20, 20, 80, 20], "reason": "recognized", "raw_detail": {}}
            for name, text in values.items()
        ],
    }
    candidates = deepcopy(DEFAULT_SCENARIO["candidates"])
    for candidate in candidates:
        candidate.update(available=True, confidence=0.99)
    annotation = {
        "image_sha256": sample["image_sha256"], "scene_confirmed": True,
        "targets": deepcopy(DEFAULT_SCENARIO["state"]["targets"]), "candidates": candidates,
    }
    return sample, annotation


class ProduceObservationTests(unittest.TestCase):
    def test_ocr_state_and_manual_candidates_use_existing_policy(self) -> None:
        sample, annotation = observation()
        original = deepcopy((sample, annotation))
        scenario = build_observed_scenario(sample, annotation)
        self.assertEqual(scenario["state"]["stats"], {"vocal": 260, "dance": 310, "visual": 220})
        self.assertIs(scenario["state"]["recognized"], True)
        self.assertEqual(explicit_scenario(scenario).state.mental, 0)
        self.assertNotIn("mental", scenario["state"])
        self.assertEqual(decide_from_params({"scenario": scenario}).chosen.candidate.candidate_id, "visual_lesson")
        sample["fields"][2]["text"] = "1"
        scenario = build_observed_scenario(sample, annotation)
        self.assertEqual(decide_from_params({"scenario": scenario}).chosen.candidate.candidate_id, "rest")
        self.assertEqual(annotation, original[1])
        scenario["candidates"][0]["gains"]["vocal"] = 999
        self.assertEqual(annotation["candidates"][0]["gains"]["vocal"], 42)

    def test_optional_mental_affects_minimum_score_and_passed_is_only_metadata(self) -> None:
        sample, annotation = observation()
        sample["passed"] = False
        mental = deepcopy(sample["fields"][0])
        mental.update(name="SC.Produce.Mental", text="55", score=0.97)
        sample["fields"].append(mental)
        scenario = build_observed_scenario(sample, annotation)
        self.assertEqual(scenario["state"]["mental"], 55)
        self.assertEqual(scenario["state"]["confidence"], 0.97)
        sample["passed"] = True
        sample["fields"][0]["accepted"] = False
        with self.assertRaises(ValueError):
            build_observed_scenario(sample, annotation)

    def test_text_is_never_corrected_or_coerced(self) -> None:
        for text in ("8O", "-1", "+1", "1e2", "NaN", "1,000", "8.0", " 8", "8 ", "8\n", "８", "٨", "", 8, True, None):
            sample, annotation = observation()
            sample["fields"][0]["text"] = text
            with self.subTest(text=text), self.assertRaises(ValueError):
                build_observed_scenario(sample, annotation)

    def test_scores_and_thresholds_are_strict(self) -> None:
        for score in (0.9499, -1, 1.1, float("nan"), float("inf"), True, "0.99", None, 10 ** 1000):
            sample, annotation = observation()
            sample["fields"][0]["score"] = score
            with self.subTest(score=score), self.assertRaises(ValueError):
                build_observed_scenario(sample, annotation)
        sample, annotation = observation()
        for threshold in (0, 0.94, -1, 1.1, True, "0.95", float("nan"), float("inf")):
            with self.subTest(threshold=threshold), self.assertRaises(ValueError):
                build_observed_scenario(sample, annotation, threshold)
        with self.assertRaises(ValueError):
            build_observed_scenario(sample, annotation, 1.0)
        for field in sample["fields"]:
            field["score"] = 0.95
        self.assertEqual(build_observed_scenario(sample, annotation)["state"]["confidence"], 0.95)

    def test_acceptance_requires_boolean_true(self) -> None:
        for accepted in (False, 1, "true", None):
            sample, annotation = observation()
            sample["fields"][0]["accepted"] = accepted
            with self.subTest(accepted=accepted), self.assertRaises(ValueError):
                build_observed_scenario(sample, annotation)

    def test_missing_duplicate_and_unrelated_fields_are_rejected(self) -> None:
        for kind in ("missing", "duplicate", "unknown", "extra_key"):
            sample, annotation = observation()
            if kind == "missing":
                sample["fields"].pop()
            elif kind == "duplicate":
                sample["fields"].append(deepcopy(sample["fields"][0]))
            elif kind == "unknown":
                extra = deepcopy(sample["fields"][0])
                extra["name"] = "SC.Other.Counter"
                sample["fields"].append(extra)
            else:
                sample["fields"][0]["live_click"] = True
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                build_observed_scenario(sample, annotation)

    def test_hash_requires_exact_digest_and_same_frame(self) -> None:
        for digest in ("a" * 63, "a" * 65, "z" * 64, " " + "a" * 64, None, 123):
            sample, annotation = observation()
            sample["image_sha256"] = digest
            annotation["image_sha256"] = digest
            with self.subTest(digest=digest), self.assertRaises(ValueError):
                build_observed_scenario(sample, annotation)
        sample, annotation = observation()
        annotation["image_sha256"] = "b" * 64
        with self.assertRaisesRegex(ValueError, "does not match"):
            build_observed_scenario(sample, annotation)
        annotation["image_sha256"] = "A" * 64
        build_observed_scenario(sample, annotation)

    def test_scene_and_annotation_require_explicit_evidence(self) -> None:
        for scene in (False, 1, "true", None):
            sample, annotation = observation()
            annotation["scene_confirmed"] = scene
            with self.subTest(scene=scene), self.assertRaises(ValueError):
                build_observed_scenario(sample, annotation)
        for key in ("available", "confidence"):
            sample, annotation = observation()
            del annotation["candidates"][0][key]
            with self.subTest(key=key), self.assertRaises(ValueError):
                build_observed_scenario(sample, annotation)
        for key in ("image_sha256", "scene_confirmed", "targets", "candidates"):
            sample, annotation = observation()
            del annotation[key]
            with self.subTest(key=key), self.assertRaises(ValueError):
                build_observed_scenario(sample, annotation)

    def test_unknown_configuration_and_nested_fields_are_rejected(self) -> None:
        for location in ("sample", "annotation", "targets", "candidate", "gains"):
            sample, annotation = observation()
            container = {
                "sample": sample, "annotation": annotation, "targets": annotation["targets"],
                "candidate": annotation["candidates"][0], "gains": annotation["candidates"][0]["gains"],
            }[location]
            container["unknown"] = 1
            with self.subTest(location=location), self.assertRaises(ValueError):
                build_observed_scenario(sample, annotation)
        sample, annotation = observation()
        sample["passed"] = 1
        with self.assertRaises(ValueError):
            build_observed_scenario(sample, annotation)

    def test_boxes_use_canonical_canvas_and_integer_coordinates(self) -> None:
        for box in ([-1, 0, 10, 10], [0, -1, 10, 10], [0, 0, 0, 10], [0, 0, 10, -1],
                    [1279, 0, 2, 10], [0, 719, 10, 2], [0, 0, 10.0, 10], [False, 0, 10, 10],
                    [0, 0, 10], "0,0,10,10", None):
            sample, annotation = observation()
            sample["fields"][0]["box"] = box
            with self.subTest(box=box), self.assertRaises(ValueError):
                build_observed_scenario(sample, annotation)
        sample, annotation = observation()
        sample["fields"][0]["box"] = [0, 0, 1280, 720]
        build_observed_scenario(sample, annotation)

    def test_state_targets_and_candidate_validation_is_reused(self) -> None:
        for kind in ("zero_max_stamina", "excess_stamina", "negative_target", "missing_target", "invalid_availability", "duplicate_candidate", "empty_candidates", "bad_risk"):
            sample, annotation = observation()
            if kind == "zero_max_stamina":
                sample["fields"][3]["text"] = "0"
            elif kind == "excess_stamina":
                sample["fields"][2]["text"] = "101"
            elif kind == "negative_target":
                annotation["targets"]["vocal"] = -1
            elif kind == "missing_target":
                del annotation["targets"]["vocal"]
            elif kind == "invalid_availability":
                annotation["candidates"][0]["available"] = 1
            elif kind == "duplicate_candidate":
                annotation["candidates"][1]["id"] = annotation["candidates"][0]["id"]
            elif kind == "empty_candidates":
                annotation["candidates"] = []
            else:
                annotation["candidates"][0]["risk"] = 2
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                build_observed_scenario(sample, annotation)


if __name__ == "__main__":
    unittest.main()
