from __future__ import annotations

import unittest

from agent.core import audition_policy as ap
from agent.core import fes_live_policy as fes


def judges(vo=100.0, da=100.0, vi=100.0, ranks=(3, 1, 2), contested=()):
    out = []
    for attr, remaining, rank in zip(fes.ATTRIBUTES, (vo, da, vi), ranks):
        out.append(ap.AuditionJudge(attr, None if remaining == 0 else remaining, rank,
                                    left=remaining == 0, contested=attr in contested))
    return tuple(out)


def cards(*appeals):
    return tuple(fes.Card(i + 1, a) for i, a in enumerate(appeals))


def make(turn=1, js=None, cs=None, gauge=0, level=2, primary="Vo", max_turns=None):
    return ap.AuditionState(turn, js or judges(), cs or cards("Vo", "Da", "Vi"), gauge, level, primary, max_turns)


class AuditionAdapterTests(unittest.TestCase):
    def test_remaining_converts_to_progress(self) -> None:
        ts = ap.to_turn_state(make(js=judges(vo=40.0, da=0, vi=100.0)))
        self.assertEqual([j.interest for j in ts.judges], [60.0, None, 0.0])
        self.assertEqual([j.alive for j in ts.judges], [True, False, True])
        self.assertEqual(ts.max_turns, ap.NO_TURN_LIMIT)

    def test_validate(self) -> None:
        self.assertEqual(ap.validate(make()), [])
        bad = make(js=(ap.AuditionJudge("Vo", None, 1),) + judges()[1:])
        self.assertIn("alive_judge_remaining_unreadable", ap.validate(bad))
        self.assertIn("trend_ranks_not_a_permutation", ap.validate(make(js=judges(ranks=(1, 1, 2)))))
        self.assertIn("primary_unknown", ap.validate(make(primary="Me")))
        self.assertEqual(ap.decide(make(primary="Me")).kind, "none")

    def test_primary_focus_beats_equal_trend_value(self) -> None:
        # Vo card on Vo judge (trend 3, ×1, Excellent ×2, focus ×1.25 = 2.5) vs Vo card on Da (trend 1, ×2 = 2.0)
        d = ap.decide(make(cs=cards("Vo", "Vo", "Vo")))
        self.assertEqual((d.kind, d.target), ("card", "Vo"))
        self.assertEqual(d.scores["card1"]["Vo"], 2.5)

    def test_matching_trend_one_card_wins(self) -> None:
        d = ap.decide(make(cs=cards(None, "Da", None), primary="Da"))
        self.assertEqual((d.slot, d.target), (2, "Da"))

    def test_contested_judge_penalised(self) -> None:
        plain = ap.decide(make(cs=cards("Da", None, None), primary=None))
        self.assertEqual(plain.target, "Da")
        contested = ap.decide(make(cs=cards("Da", None, None), primary=None, js=judges(contested=("Da",))))
        self.assertEqual(contested.scores["card1"]["Da"], round(plain.scores["card1"]["Da"] * 0.7, 4))

    def test_memory_needs_level_two_and_turn_four(self) -> None:
        near = judges(vo=100.0, da=40.0, vi=100.0)
        self.assertEqual(ap.memory_rule(make(turn=4, js=near, gauge=100, level=1)), (False, "memory_level_too_low"))
        self.assertEqual(ap.memory_rule(make(turn=3, js=near, gauge=100))[1], "too_early")
        d = ap.decide(make(turn=4, js=near, gauge=100))
        self.assertEqual((d.kind, d.target), ("memory", "Da"))
        self.assertEqual(ap.memory_rule(make(turn=5, js=near, gauge=100, max_turns=5)), (True, "last_turn"))
        self.assertEqual(ap.memory_rule(make(turn=4, gauge=None)), (False, "gauge_not_full"))

    def test_left_judge_never_targeted(self) -> None:
        d = ap.decide(make(js=judges(da=0), cs=cards("Da", "Da", "Da"), primary="Da"))
        self.assertNotEqual(d.target, "Da")

    def test_choose_target_with_remaining_predictions(self) -> None:
        state = make(js=judges(vo=50.0, da=30.0, vi=100.0))
        d = ap.decide(state)
        target, info = ap.choose_target(state, d, {"Vo": 20.0, "Da": 0.0, "Vi": None})
        self.assertEqual((target, info["rule"]), ("Da", "predicted_finish"))

    def test_state_from_texts(self) -> None:
        texts = {"turn": "TURN 2", "judges": {"Vo": "80%", "Da": "0%", "Vi": "55.5%"},
                 "multipliers": {"Vo": "★2倍", "Da": None, "Vi": "★1.5倍"}, "memory": "40%"}
        state, reasons = ap.state_from_texts(texts, cards("Vo", "Vi", None), memory_level=2, primary="Vo")
        self.assertEqual(reasons, [])
        self.assertEqual(state.turn, 2)
        self.assertTrue(state.judges[1].left)
        self.assertEqual([j.rank for j in state.judges], [1, 3, 2])
        self.assertEqual(state.memory_gauge, 40)
        self.assertIn("W.I.N.G.", ap.explain(state, ap.decide(state)))

    def test_state_from_texts_rejects(self) -> None:
        texts = {"turn": "?", "judges": {"Vo": "80%", "Da": "x", "Vi": "55%"}, "multipliers": {}}
        state, reasons = ap.state_from_texts(texts, cards("Vo", "Vi", None), memory_level=2, primary="Vo")
        self.assertIsNone(state)
        self.assertIn("turn_unreadable", reasons)
        self.assertIn("trend_multipliers_unreadable", reasons)
        self.assertIn("judge_Da_unreadable", reasons)
        self.assertEqual(ap.parse_turn("3/5"), 3)
        self.assertIsNone(ap.parse_turn("10"))

    def test_fes_params_untouched(self) -> None:
        self.assertEqual(fes.PARAMS["last_appeal_stars"], {1: 12, 2: 9, 3: 6})
        self.assertEqual(fes.PARAMS["memory_min_turn"], 3)


if __name__ == "__main__":
    unittest.main()
