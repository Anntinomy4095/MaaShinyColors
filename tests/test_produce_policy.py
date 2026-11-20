from __future__ import annotations

import json
import unittest
from pathlib import Path

from agent.core.models import ProduceState, Scenario
from agent.core.produce_policy import ProducePolicy


FIXTURE = (
    Path(__file__).parent / "fixtures" / "produce" / "basic_turn.json"
)


class ProducePolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.scenario = Scenario.from_dict(payload)

    def test_balanced_profile_prefers_visual_gap(self) -> None:
        decision = ProducePolicy("balanced").decide(
            self.scenario.state,
            self.scenario.candidates,
        )
        self.assertEqual(decision.chosen.candidate.candidate_id, "visual_lesson")
        self.assertIn("主要收益", decision.rationale)

    def test_safe_profile_prefers_recovery(self) -> None:
        decision = ProducePolicy("safe").decide(
            self.scenario.state,
            self.scenario.candidates,
        )
        self.assertEqual(decision.chosen.candidate.candidate_id, "rest")

    def test_decision_is_deterministic(self) -> None:
        policy = ProducePolicy("balanced")
        first = policy.decide(
            self.scenario.state,
            self.scenario.candidates,
        ).to_dict()
        second = policy.decide(
            self.scenario.state,
            self.scenario.candidates,
        ).to_dict()
        self.assertEqual(first, second)

    def test_unknown_profile_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown profile"):
            ProducePolicy("unknown")

    def test_invalid_stamina_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "stamina"):
            ProduceState.from_dict(
                {
                    "turn": 1,
                    "turns_to_exam": 1,
                    "stamina": 101,
                    "max_stamina": 100,
                    "stats": {
                        "vocal": 0,
                        "dance": 0,
                        "visual": 0,
                    },
                    "targets": {
                        "vocal": 1,
                        "dance": 1,
                        "visual": 1,
                    },
                }
            )

    def test_optional_absolute_threshold_changes_recovery_score(self) -> None:
        legacy = ProducePolicy().decide(self.scenario.state, self.scenario.candidates)
        threshold = ProducePolicy(low_stamina_threshold=50).decide(self.scenario.state, self.scenario.candidates)
        before = {item.candidate.candidate_id: item for item in legacy.ranking}
        after = {item.candidate.candidate_id: item for item in threshold.ranking}
        self.assertGreater(after["rest"].contributions["recovery"], before["rest"].contributions["recovery"])
        self.assertLess(after["visual_lesson"].contributions["stamina_cost"], before["visual_lesson"].contributions["stamina_cost"])
        self.assertIn("50（绝对体力值）", threshold.rationale)
        self.assertAlmostEqual(threshold.chosen.score, sum(threshold.chosen.contributions.values()))

    def test_invalid_threshold_and_confidence_configuration(self) -> None:
        for value in (-1, float("nan"), float("inf"), True, "50"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                ProducePolicy(low_stamina_threshold=value)
        for value in (-0.1, 1.1, None, float("nan")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                ProducePolicy(min_confidence=value)


if __name__ == "__main__":
    unittest.main()
