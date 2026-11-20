"""Synthetic fixed-team limit-break contracts; never account/game evidence."""
from copy import deepcopy
from dataclasses import replace
import unittest
from unittest.mock import patch

from agent.core.true_end.growth import GrowthConfig
from agent.core.true_end.limit_break import compare_limit_breaks
from agent.core.true_end.models import CardData, LiveSkill, Ownership, Panel, STATS, SupportSkill
from agent.core.true_end.optimizer import SearchConfig, evaluate_team, scenario_from_dict, _battle_supports


def fixture():
    cards = {}
    for key in ("p", "s0", "s1", "s2", "s3", "guest"):
        raw = {"card_id": key, "name": key, "idol": key, "kind": "P" if key == "p" else "S",
               "te_eligible": key == "p", "status": "OK", "source_url": "synthetic://limit-break",
               "max_levels": {str(lb): 60 + 5 * lb for lb in range(5)},
               "levels": {str(lv): dict.fromkeys(STATS, lv * 2) for lv in range(60, 81, 5)},
               "owned_live_skills": [{"id": key + "-own", "components": {"Da": 2}, "unlock_level": 1}],
               "panels": []}
        if key == "s0":
            raw["panels"] = [{"id": "four", "kind": "live", "required_lb": 4, "sp_cost": 10,
                "prerequisites_known": True, "effects": {"id": "four-live", "components": {"Da": 4}}}]
        cards[key] = CardData.from_dict(raw)
    inventory = {key: Ownership(key, 0, 60) for key in cards}
    growth = GrowthConfig.from_dict({"starting_stats": dict.fromkeys(STATS, 100),
        "starting_caps": dict.fromkeys(STATS, 500), "starting_sp": 100,
        "starting_fans": 500000, "starting_live_skills": [
            {"id": f"start{i}", "components": {a: 1}} for i, a in enumerate(("Da", "Vo", "Vi", "Da"))],
        "actions": ["rest"] * 32})
    cfg = SearchConfig(strategies=("DA",), confirmation_trials=1, sensitivity=False)
    return cards, inventory, growth, cfg


class LimitBreakTests(unittest.TestCase):
    def run_compare(self, values=None, **kwargs):
        cards, inventory, growth, cfg = values or fixture()
        return compare_limit_breaks(cards, inventory, "p", ("s0", "s1", "s2", "s3"), "guest",
                                    kwargs.pop("target_card", "s0"), growth=growth, config=cfg, **kwargs)

    def test_four_break_panel_score_uses_real_gate(self):
        cases = self.run_compare()["modes"][0]["cases"]
        self.assertEqual(len(cases), 5)
        self.assertTrue(all(c["status"] == "ESTIMATED" for c in cases))
        self.assertEqual(len({c["total_score"] for c in cases[:4]}), 1)
        self.assertGreater(cases[4]["total_score"], cases[0]["total_score"])
        self.assertIn("s0:four", cases[4]["learned_panel_ids"])
        self.assertTrue(all("s0:four" in c["locked_panel_ids"] for c in cases[:4]))

    def test_guest_without_owned_live_is_compared(self):
        cards, inventory, growth, cfg = fixture()
        cards["guest"] = replace(cards["guest"], owned_live_skills=())
        cases = self.run_compare((cards, inventory, growth, cfg))["modes"][0]["cases"]
        self.assertTrue(all(c["status"] == "ESTIMATED" for c in cases))
        self.assertFalse(any("guest:owned_live" in g for c in cases for g in c.get("gaps", ())))
        cards["s1"] = replace(cards["s1"], owned_live_skills=())  # an Active still needs its own Live
        cases = self.run_compare((cards, inventory, growth, cfg))["modes"][0]["cases"]
        self.assertTrue(all(c["status"] == "NEEDS_DATA" for c in cases))

    def test_fixed_level_isolated_from_level_curve(self):
        result = self.run_compare()
        fixed, capped = (m["cases"] for m in result["modes"])
        self.assertEqual([c["level"] for c in fixed], [60] * 5)
        self.assertEqual([c["level"] for c in capped], [60, 65, 70, 75, 80])
        self.assertEqual(len({c["target_support_stats"]["Da"] for c in fixed}), 1)
        self.assertEqual([c["target_support_stats"]["Da"] for c in capped], [120, 130, 140, 150, 160])

    def test_two_break_support_gate_and_level_owned_skill_upgrade(self):
        cards, owned, growth, cfg = fixture()
        skill = SupportSkill("gated-sp", "mastery_sp", {1: 10, 70: 20}, room="Da", required_lb=2)
        upgrade = LiveSkill("s0-upgraded", {"Da": 3}, unlock_level=70)
        cards["s0"] = replace(cards["s0"], support_skills=(skill,),
                              owned_live_skills=(*cards["s0"].owned_live_skills, upgrade))
        modes = self.run_compare((cards, owned, growth, cfg))["modes"]
        fixed, capped = (m["cases"] for m in modes)
        self.assertEqual([c["target_support_effects"][0]["amount"] for c in fixed], [0, 0, 10, 10, 10])
        self.assertTrue(all(c["target_owned_skill"]["id"] == "s0-own" for c in fixed))
        self.assertEqual([c["target_owned_skill"]["id"] for c in capped],
                         ["s0-own", "s0-own", "s0-upgraded", "s0-upgraded", "s0-upgraded"])

    def test_same_seed_repeatable_and_other_ownership_unchanged(self):
        values = fixture()
        before = deepcopy(values[1])
        first = self.run_compare(values)
        self.assertEqual(first, self.run_compare(values))
        self.assertEqual(values[1], before)
        cases = first["modes"][0]["cases"]
        self.assertEqual(len({c["comparison_sha256"] for c in cases}), 1)
        self.assertEqual(len({c["case_sha256"] for c in cases}), 5)
        self.assertEqual(len({c["seed"] for c in cases}), 1)

    def test_missing_level_is_null_not_zero_and_no_interpolation(self):
        cards, owned, growth, cfg = fixture()
        cards["s0"] = replace(cards["s0"], levels={60: cards["s0"].levels[60]})
        capped = self.run_compare((cards, owned, growth, cfg))["modes"][1]["cases"]
        self.assertEqual(capped[1]["status"], "NEEDS_DATA")
        self.assertIsNone(capped[1]["total_score"])
        self.assertIsNone(capped[1]["delta_from_zero"])
        self.assertTrue(capped[1]["missing"])

    def test_fixed_level_cannot_cross_locked_maximum(self):
        cases = self.run_compare(fixed_level=80)["modes"][0]["cases"]
        self.assertTrue(all(c["total_score"] is None for c in cases[:4]))
        self.assertIsNotNone(cases[4]["total_score"])
        self.assertIsNone(cases[4]["delta_from_zero"])

    def test_unknown_path_not_purchased(self):
        cards, owned, growth, cfg = fixture()
        cards["s0"] = replace(cards["s0"], panels=tuple(replace(p, prerequisites_known=False)
                                                       for p in cards["s0"].panels))
        case = self.run_compare((cards, owned, growth, cfg), allow_partial=True)["modes"][0]["cases"][4]
        self.assertIn("s0:four", case["unavailable_panel_paths"])
        self.assertNotIn("s0:four", case["learned_panel_ids"])
        self.assertEqual(case["delta_from_zero"], 0)
        self.assertEqual(case["mechanism_coverage"], "PARTIAL")
        self.assertIsNone(case["full_strength_score"])

    def test_partial_mechanism_is_explicit_not_full_score(self):
        cards, owned, growth, cfg = fixture()
        cards["s0"] = replace(cards["s0"], unsupported=("synthetic-unknown",))
        blocked = self.run_compare((cards, owned, growth, cfg))["modes"][0]["cases"][0]
        self.assertIsNone(blocked["total_score"])
        allowed = self.run_compare((cards, owned, growth, cfg), allow_partial=True)["modes"][0]["cases"][0]
        self.assertEqual(allowed["status"], "ESTIMATED_WITH_GAPS")
        self.assertIn("s0:synthetic-unknown", allowed["gaps"])
        self.assertFalse(allowed["guaranteed_reachable"])

    def test_guest_stays_guest_in_actual_evaluation(self):
        with patch("agent.core.true_end.limit_break.evaluate_team", wraps=evaluate_team) as wrapped:
            self.run_compare(target_card="guest")
        candidate = wrapped.call_args_list[4].args[0]
        supports = _battle_supports(candidate)
        self.assertEqual(len([s for s in supports if not s.guest]), 4)
        self.assertTrue(supports[-1].guest)
        self.assertNotIn("guest-own", [s.id for s in candidate.projection.skill_plan.learned_skills])

    def test_one_small_battle_reuses_actual_evaluator(self):
        scenario = scenario_from_dict({"name": "synthetic", "player_judgments": {"Perfect": 1},
            "npc_judgments": {"Good": 1}, "npc_late_multiplier": 1.25, "reaction_mode": "uniform",
            "fourth_turn_multiplier": 2, "hand_recycle": "discard_shuffle", "ta_tie": "player_loses",
            "overkill_credit": False, "assumptions": ["合成测试情景，未校准"]})
        cards, owned, growth, cfg = fixture()
        # Other levels deliberately missing so this runs one case in each mode only.
        cards["s0"] = replace(cards["s0"], max_levels={0: 60})
        result = self.run_compare((cards, owned, growth, cfg), fixed_level=61, scenarios=(scenario,))
        case = result["modes"][1]["cases"][0]
        self.assertEqual(case["battle"]["status"], "CONDITIONAL_SIMULATION")
        self.assertIsNotNone(case["total_score"])
        self.assertIsNone(case["true_end_probability"])

    def test_equipment_and_settings_change_common_hash(self):
        values = fixture()
        original = self.run_compare(values)["comparison_sha256"]
        cards, owned, growth, cfg = values
        updated = replace(growth, starting_live_skills=tuple(reversed(growth.starting_live_skills)))
        self.assertNotEqual(original, self.run_compare((cards, owned, updated, cfg))["comparison_sha256"])

    def test_nonmonotonic_is_preserved_and_reported(self):
        def declining(candidate, scenarios, config, *, trials):
            result = evaluate_team(candidate, scenarios, config, trials=trials)
            result["rating"] = 90 - candidate.active[0].ownership.limit_break
            return result
        with patch("agent.core.true_end.limit_break.evaluate_team", side_effect=declining):
            mode = self.run_compare()["modes"][0]
        self.assertEqual(mode["cases"][4]["delta_from_zero"], -4)
        self.assertEqual(len(mode["nonmonotonic"]), 4)
        self.assertTrue(mode["review_note"])

    def test_higher_break_replays_affordable_lower_plan_instead_of_clamping_score(self):
        cards, owned, growth, cfg = fixture()
        cards["s0"] = replace(cards["s0"], panels=(
            Panel.from_dict({"id": "good", "kind": "live", "sp_cost": 80,
                "prerequisites_known": True, "effects": {"id": "good", "components": {"Da": 4}}}),
            # 2026-10-27: a passive is valued like the stat it multiplies; at 1 SP it still comes first and leaves 79
            Panel.from_dict({"id": "temptation", "kind": "passive", "sp_cost": 1, "required_lb": 1,
                "prerequisites_known": True, "effects": {"id": "temptation", "attribute": "Da",
                    "buff": .5, "probability": 1, "max_activations": 1}})))
        growth = replace(growth, starting_sp=80)
        cases = self.run_compare((cards, owned, growth, cfg))["modes"][0]["cases"]
        higher = cases[1]
        self.assertLess(higher["independent_greedy_score"], cases[0]["model_score"])
        self.assertEqual(higher["selected_plan_origin_lb"], 0)
        self.assertEqual(higher["learned_panel_ids"], ["s0:good"])
        self.assertEqual(higher["purchased_path_cost"], 80)
        self.assertEqual(higher["purchase_weeks"], {"s0:good": 0})
        self.assertEqual(higher["model_score"], cases[0]["model_score"])
        self.assertEqual(len(higher["evaluated_plans"]), 2)
        self.assertTrue(higher["evaluated_plans"][1]["replayed"])

    def test_all_case_values_never_claim_complete_strength(self):
        result = self.run_compare()
        self.assertEqual(result["modes"][0]["comparability"], "ALL_CASES_EVALUATED")
        self.assertTrue(all(case["full_strength_score"] is None
                            for mode in result["modes"] for case in mode["cases"]))

    def test_replay_keeps_original_sp_goal_instead_of_awarding_free_readiness(self):
        cards, owned, growth, cfg = fixture()
        cards["s0"] = replace(cards["s0"], panels=(
            Panel.from_dict({"id": "goal", "kind": "live", "sp_cost": 80,
                "prerequisites_known": True, "effects": {"id": "goal", "components": {"Da": 4}}}),
            Panel.from_dict({"id": "cheap", "kind": "passive", "sp_cost": 5, "required_lb": 1,
                "prerequisites_known": True, "effects": {"attribute": "Da", "buff": .5,
                    "probability": 1, "max_activations": 1}})))
        cases = self.run_compare((cards, owned, replace(growth, starting_sp=10), cfg))["modes"][0]["cases"]
        # 2026-10-25: the score counts earned SP against a fixed line, so an unfunded goal neither lowers nor raises it:
        # the replayed 0凸 plan scores as 0凸. 2026-10-27: the 1凸 plan's cheap passive now counts, so it scores higher.
        self.assertEqual(cases[0]["sp_path_cost"], 80)
        self.assertEqual(cases[1]["purchased_path_cost"], 5)
        own, replayed = (plan["model_score"] for plan in cases[1]["evaluated_plans"])
        self.assertEqual(replayed, cases[0]["model_score"])
        self.assertGreater(own, cases[0]["model_score"])
        self.assertEqual(cases[1]["model_score"], own)


if __name__ == "__main__":
    unittest.main()
