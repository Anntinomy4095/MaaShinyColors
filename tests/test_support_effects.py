"""The same proven effect must survive the DB card-type prefix and retain source conflicts."""
import unittest

from agent.core.support_effects import support_skill_expectation
from agent.core.true_end.models import SupportSkill, Ownership
from agent.core.card_data_merge import role_readiness, merge_card


def skill(kind, amount, *, probability=15, levels=None, extra=()):
    text = (f"レッスンかお仕事を選択する時に{probability}%の確率で体力消費量-{amount}%"
            if kind == "stamina_support" else f"「休む」を選択時に{probability}%の確率で体力回復量+{amount}")
    return SupportSkill.from_dict({"id": "fixture", "kind": kind, "levels": levels or {"1": 1, "70": 5},
                                   "unsupported": [text, *extra]})


class SupportEffectSourceTests(unittest.TestCase):
    def test_db_prefix_uses_the_same_effect_at_the_actual_unlocked_skill_level(self):
        for rarity, reduction, recovery in (("SSR", 50, 15), ("SR", 30, 10), ("R", 15, 5)):
            for kind, amount in (("stamina_support", reduction), ("rest_support", recovery)):
                entry = skill(kind, amount)
                for unlocked in (0, 1, 5):
                    with self.subTest(rarity=rarity, kind=kind, unlocked=unlocked):
                        base = support_skill_expectation(entry, unlocked, rarity)
                        result = support_skill_expectation(entry, unlocked, "S_" + rarity)
                        self.assertEqual(result, base)
                        self.assertAlmostEqual(result["activation_probability"], unlocked * .03)

    def test_prefix_does_not_hide_conflicting_probability_or_effect_amount(self):
        for entry in (skill("stamina_support", 30), skill("stamina_support", 50, probability=12),
                      skill("stamina_support", 50, extra=("unresolved DB/Wiki ladder",))):
            self.assertIsNone(support_skill_expectation(entry, 5, "S_SSR"))

    def test_explicit_wiki_formula_is_the_same_probability_without_evaluating_other_expressions(self):
        for kind, amount in (("stamina_support", 50), ("rest_support", 15)):
            literal, formula = skill(kind, amount), skill(kind, amount, probability="<Lv*3>")
            self.assertEqual(support_skill_expectation(formula, 1, "SSR"), support_skill_expectation(literal, 1, "S_SSR"))
        for expression in ("<Lv*4>", "<Lv+3>", "<Lv*3> extra"):
            self.assertIsNone(support_skill_expectation(skill("stamina_support", 50, probability=expression), 5, "S_SSR"))

    def test_cached_ur_stamina_uses_the_actual_unlock_ladder(self):
        entry = skill("stamina_support", 50, levels={"10": 3, "70": 5})
        for card_level, probability in ((1, 0), (10, .09), (69, .09), (70, .15), (90, .15)):
            rank = entry.amount(Ownership("fixture", 4, card_level))
            result = support_skill_expectation(entry, rank, "S_UR")
            self.assertAlmostEqual(result["activation_probability"], probability)
            self.assertAlmostEqual(result["expected_stamina_reduction"], probability * .5)
        self.assertIsNone(support_skill_expectation(skill("rest_support", 15), 5, "S_UR"))
        self.assertIsNone(support_skill_expectation(skill("stamina_support", 50, levels={"1": 4}), 4, "S_UR"))

    def test_data_gap_count_uses_the_same_known_mechanics_as_the_scorers(self):
        card = {"kind": "S", "rarity": "S_SSR", "support_skills": [
            {"id": "known", "kind": "stamina_support", "levels": {"1": 5},
             "unsupported": ["レッスンかお仕事を選択する時に15%の確率で体力消費量-50%"]},
            {"id": "conflict", "kind": "stamina_support", "levels": {"1": 5},
             "unsupported": ["unresolved DB/Wiki ladder"]},
            {"id": "unknown", "kind": "unknown", "levels": {"1": 1}, "unsupported": ["not modeled"]},
            # おやすみブースト is valued at zero: never a gap, whatever its text.
            {"id": "rest", "kind": "rest_support", "levels": {"1": 5}, "unsupported": ["unresolved DB/Wiki ladder"]},
        ]}
        self.assertEqual(role_readiness(card)["unmodeled_mechanics"], 2)

    def test_modeled_support_conflict_is_not_downgraded_because_its_source_description_is_preserved(self):
        source = {"card_id": "fixture", "name": "fixture", "kind": "S", "rarity": "S_SSR", "source_url": "synthetic://fixture",
                  "support_skills": [{"id": "rest", "kind": "stamina_support", "levels": {"5": 1},
                                      "unsupported": ["レッスンかお仕事を選択する時に3%の確率で体力消費量-50%"]}]}
        wiki = {**source, "support_skills": [{**source["support_skills"][0], "levels": {"60": 1}}]}
        result = merge_card(source, wiki)
        conflict = next(r for r in result["merge"]["unresolved"] if r["field"] == "support_skills.rest")
        self.assertTrue(conflict["critical"])
        self.assertEqual(result["readiness"]["unmodeled_mechanics"], 1)

    def test_other_card_types_remain_unknown(self):
        entry = skill("stamina_support", 50)
        for rarity in (None, "P_SSR", "S_N", "S_S_SSR", "unknown"):
            self.assertIsNone(support_skill_expectation(entry, 5, rarity))


if __name__ == "__main__":
    unittest.main()
