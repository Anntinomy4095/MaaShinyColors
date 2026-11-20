from dataclasses import asdict, replace
import unittest

from agent.core.true_end.features import effective_cap_contribution, mechanical_vector, score_card_prior, sp_readiness
from agent.core.true_end.models import CardData, LiveSkill, Ownership, extract_card_features, load_inventory, validate_team
from true_end_fixtures import card, feature


class TrueEndModelsTests(unittest.TestCase):
    def test_optional_v2_source_fields_preserve_legacy_card_contract(self):
        raw = asdict(card("legacy"))
        raw.pop("te_condition", None)
        for panel in raw["panels"]:
            panel.pop("source_layout", None)
        parsed = CardData.from_dict(raw)
        self.assertIsNone(parsed.te_condition)
        self.assertTrue(all(p.source_layout is None for p in parsed.panels))
        self.assertEqual(CardData.from_dict(asdict(parsed)), parsed)
        for condition in (False, 500000, ""):
            with self.subTest(condition=condition), self.assertRaises(ValueError):
                CardData.from_dict({**raw, "te_condition": condition})
        bad = asdict(parsed)
        bad["panels"][0]["source_layout"] = dict(table=1, row=1, column=1, rowspan=0, colspan=1)
        with self.assertRaises(ValueError):
            CardData.from_dict(bad)

    def test_healing_only_equipped_skill_does_not_invent_appeal(self):
        skill = LiveSkill.from_dict({"id": "メンタルキュアI", "components": {}, "heal_fraction": .05})
        self.assertEqual(skill.components, {})
        self.assertEqual(skill.heal_fraction, .05)
        for raw in ({"id": "empty", "components": {}},
                    {"id": "bad", "components": {}, "heal_fraction": 1.1}):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                LiveSkill.from_dict(raw)

    def test_excellent_live_skill_is_a_live_skill(self):
        skill = LiveSkill.from_dict({"id": "エクセレントアピールⅤ", "components": {}, "excellent": 3.0})
        self.assertEqual((dict(skill.components), skill.excellent), ({}, 3.0))
        with self.assertRaises(ValueError):
            LiveSkill.from_dict({"id": "neg", "components": {}, "excellent": -1})
        data = replace(card("exc"), owned_live_skills=(skill,))
        f = extract_card_features(data, Ownership("exc", 4, 80))
        self.assertTrue(f.ready)  # usable as Active S
        for attr in ("Vo", "Da", "Vi"):
            self.assertEqual(mechanical_vector(f, attr)["own_appeal"], 3.0)  # Excellent on every primary
        self.assertEqual(mechanical_vector(f, "Vo", slot="guest")["own_appeal"], 0)

    def test_guest_readiness_does_not_need_owned_live(self):
        no_live = replace(card("g"), owned_live_skills=())
        f = extract_card_features(no_live, Ownership("g", 4, 80))
        self.assertFalse(f.ready)
        self.assertTrue(f.ready_as_guest)
        self.assertEqual(f.guest_unknowns, ())
        self.assertIn("owned_live_skills", f.unknowns)  # still disclosed for the Active role
        missing_stats = extract_card_features(no_live, Ownership("g", 4, 65))
        self.assertFalse(missing_stats.ready_as_guest)  # growth's lesson correction reads guest stats
        self.assertFalse(feature("p", kind="P").ready_as_guest)
        p = feature("p", kind="P")
        active = tuple(feature("s" + str(i)) for i in range(4))
        validate_team(p, active, f)  # guest-only card is a legal Guest
        with self.assertRaises(ValueError):
            validate_team(p, (f, *active[1:]), feature("s3x", idol="other"))

    def test_ownership_and_data_are_separate(self):
        c = card("s")
        low = extract_card_features(c, Ownership("s", 0, 80))
        high = extract_card_features(c, Ownership("s", 4, 80))
        self.assertNotIn("cap", [p.id for p in low.panels])
        self.assertIn("cap", [p.id for p in high.panels])

    def test_missing_level_is_unknown_not_interpolation(self):
        f = extract_card_features(card("s"), Ownership("s", 4, 65))
        self.assertFalse(f.ready)
        self.assertIn("levels.65.Vo", f.unknowns)
        self.assertNotIn("Vo", f.stats)

    def test_guest_has_no_owned_appeal_or_combat_stats(self):
        f = feature("s")
        active = mechanical_vector(f, "Vo")
        guest = mechanical_vector(f, "Vo", slot="guest")
        self.assertGreater(active["own_appeal"], 0)
        self.assertEqual(guest["own_appeal"], 0)
        self.assertEqual(guest["combat_stat"], 0)
        for k in ("mastery", "sp", "stamina", "cap", "growth_stat", "panel_live"):
            self.assertEqual(guest[k], active[k])

    def test_metadata_never_changes_prior(self):
        a, b = feature("s", rarity="SR"), feature("s", rarity="Twilight")
        for role in ("MAIN_HITTER", "HYBRID", "SP_ENGINE"):
            self.assertEqual(score_card_prior(a, "VO_FORCE", role), score_card_prior(b, "VO_FORCE", role))

    def test_cap_and_sp_saturate(self):
        self.assertEqual(effective_cap_contribution(720, 1000, 200), 0)
        self.assertEqual(effective_cap_contribution(1180, 1000, 200), 180)
        before = sp_readiness(350, 420)
        self.assertGreater(sp_readiness(420, 420), before)
        self.assertEqual(sp_readiness(650, 420), sp_readiness(720, 420))

    def test_duplicate_idol_including_guest_and_p_rejected(self):
        p = feature("p", kind="P", idol="person")
        active = tuple(feature(f"s{i}") for i in range(4))
        validate_team(p, active, feature("g"))
        with self.assertRaisesRegex(ValueError, "idol"):
            validate_team(p, active, feature("g", idol="person"))

    def test_true_end_card_eligibility_required(self):
        c = replace(card("p", kind="P"), te_eligible=None)
        self.assertFalse(extract_card_features(c, Ownership("p", 4, 80)).ready)

    def test_invalid_numbers_and_duplicate_inventory_rejected(self):
        for invalid in (float("nan"), float("inf"), True, -1):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                LiveSkill.from_dict({"id": "bad", "components": {"Vo": invalid}})
        with self.assertRaises(ValueError):
            load_inventory([{"card_id": "s", "limit_break": 0, "level": 60}] * 2)

    def test_level_cannot_exceed_actual_lb_max(self):
        c = replace(card("s"), max_levels={0: 60, 4: 80})
        with self.assertRaisesRegex(ValueError, "cap"):
            extract_card_features(c, Ownership("s", 0, 80))


if __name__ == "__main__":
    unittest.main()


class SupportSkillBelowUnlockTest(unittest.TestCase):
    def test_level_below_first_unlock_is_known_zero(self):
        from agent.core.true_end.models import SupportSkill, Ownership
        skill = SupportSkill.from_dict({"id": "s1", "kind": "sp_up", "levels": {"40": 2, "60": 3}})
        own = Ownership.from_dict({"card_id": "2040000010", "limit_break": 4, "level": 20})
        self.assertEqual(skill.amount(own), 0.0)
        own60 = Ownership.from_dict({"card_id": "2040000010", "limit_break": 4, "level": 60})
        self.assertEqual(skill.amount(own60), 3)

    def test_empty_ladder_stays_unknown(self):
        from agent.core.true_end.models import SupportSkill, Ownership
        skill = SupportSkill.from_dict({"id": "s1", "kind": "sp_up", "levels": {}})
        own = Ownership.from_dict({"card_id": "2040000010", "limit_break": 4, "level": 20})
        self.assertIsNone(skill.amount(own))
