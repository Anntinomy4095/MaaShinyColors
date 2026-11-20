"""Verified rarity caps are a level limit, not invented stats, skills, or conflict resolution."""
import copy
import unittest

from agent.core.card_stats import resolve_level_caps
from agent.core.card_data_merge import role_readiness
from agent.core.team_recommend import _assumed_level
from agent.core import team_recommend
from agent.core.card_id_map import CardIdMap
from agent.core.true_end.models import CardData, Ownership, extract_card_features


def support(rarity="S_SSR"):
    return {
        "card_id": "2049990010", "name": "Fixture", "idol": "Fixture", "kind": "S", "status": "PARTIAL",
        "rarity": rarity, "source_url": "https://example.test/card", "max_levels": {},
        "levels": {"60": {"Vo": 100, "Da": 80, "Vi": 70, "Me": 60},
                   "80": {"Vo": 150, "Da": 100, "Vi": 90, "Me": 80}},
        "support_skills": [{"id": "fixture-sp", "kind": "mastery_sp", "lesson": "Vo", "levels": {"1": 4}}],
        "owned_live_skills": [], "panels": [], "events": [], "provenance": {}, "unknown_fields": [],
    }


class VerifiedLevelCapsTests(unittest.TestCase):
    def test_verified_rarities_and_every_limit_break(self):
        expected = {"S_SSR": (60, 65, 70, 75, 80), "SR": (50, 55, 60, 65, 70), "S_UR": (70, 75, 80, 85, 90)}
        for rarity, levels in expected.items():
            raw = support(rarity)
            caps, evidence = resolve_level_caps(raw)
            self.assertEqual(caps, dict(enumerate(levels)))
            self.assertEqual(evidence["source"], "verified_rarity_rule")
            self.assertEqual(raw["max_levels"], {})

    def test_inferred_r_n_and_p_are_not_promoted(self):
        for rarity in ("S_R", "S_N", "unknown"):
            self.assertEqual(resolve_level_caps(support(rarity)), ({}, None))
        p = {**support(), "kind": "P", "rarity": "P_SSR"}
        self.assertEqual(resolve_level_caps(p), ({}, None))

    def test_explicit_card_caps_take_priority_without_filling_unlisted_breaks(self):
        raw = support()
        raw["max_levels"] = {"0": 58, "4": 78}
        self.assertEqual(resolve_level_caps(raw), ({0: 58, 4: 78}, None))
        self.assertEqual(CardData.from_dict(raw).max_levels, {0: 58, 4: 78})
        self.assertNotIn("level_cap_rule", role_readiness(raw))
        self.assertFalse(role_readiness(raw, limit_break=2, level=60)["ready_as_guest"])

    def test_known_stat_row_does_not_replace_an_unknown_rarity_cap(self):
        raw = support("S_R")
        ready = role_readiness(raw, limit_break=4, level=60)
        self.assertFalse(ready["ready_as_guest"])
        self.assertIn("max_levels", ready["critical_gaps"]["guest"])

    def test_general_rule_never_resolves_an_explicit_disagreement(self):
        for raw in (
            {**support(), "unknown_fields": ["unresolved:max_levels"]},
            {**support(), "merge": {"unresolved": [{"field": "max_levels", "critical": True, "status": "unresolved"}]}},
        ):
            self.assertEqual(resolve_level_caps(raw), ({}, None))
            self.assertIn("max_levels", extract_card_features(CardData.from_dict(raw), Ownership(raw["card_id"], 4, 80)).unknowns)
            self.assertFalse(role_readiness(raw)["ready_as_guest"])

    def test_full_level_guest_is_eligible_but_missing_live_stays_an_active_gap(self):
        raw = support()
        before = copy.deepcopy(raw)
        card = CardData.from_dict(raw)
        feature = extract_card_features(card, Ownership(card.card_id, 4, 80))
        self.assertTrue(feature.ready_as_guest)
        self.assertFalse(feature.ready)
        self.assertIn("owned_live_skills", feature.unknowns)
        self.assertEqual(card.provenance["max_levels"]["status"], "DERIVED_RULE")
        readiness = role_readiness(raw)
        self.assertTrue(readiness["ready_as_guest"])
        self.assertFalse(readiness["ready_as_active"])
        self.assertEqual(readiness["assumed"]["level"], 80)
        self.assertEqual(readiness["level_cap_rule"]["rule_id"], "support-level-caps-20261007")
        self.assertEqual(raw, before)

    def test_caps_do_not_add_missing_level_rows(self):
        raw = support()
        card = CardData.from_dict(raw)
        feature = extract_card_features(card, Ownership(card.card_id, 2, 70))
        self.assertFalse(feature.ready_as_guest)
        self.assertIn("levels.70.Vo", feature.unknowns)
        self.assertIn("levels.70", role_readiness(raw, limit_break=2, level=70)["critical_gaps"]["guest"])
        self.assertNotIn(70, card.levels)

    def test_level_cap_and_actual_level_are_kept_separate(self):
        raw = support()
        card = CardData.from_dict(raw)
        feature = extract_card_features(card, Ownership(card.card_id, 0, 60))
        self.assertEqual(feature.stats["Vo"], 100)
        with self.assertRaisesRegex(ValueError, "level exceeds owned limit-break cap"):
            extract_card_features(card, Ownership(card.card_id, 0, 80))
        self.assertIn("level 80 exceeds the LB0 cap 60", role_readiness(raw, limit_break=0, level=80)["critical_gaps"]["guest"])
        self.assertEqual(_assumed_level(raw, 0), 60)
        self.assertEqual(_assumed_level(raw, 2), 70)

    def test_stale_missing_field_label_is_removed_only_when_resolved(self):
        raw = {**support(), "unknown_fields": ["max_levels", "owned_live_skills"]}
        card = CardData.from_dict(raw)
        self.assertNotIn("max_levels", card.unknown_fields)
        self.assertIn("owned_live_skills", card.unknown_fields)
        self.assertIn("max_levels", CardData.from_dict({**raw, "rarity": "S_R"}).unknown_fields)

    def test_assuming_missing_level_keeps_known_zero_break_in_coverage_and_gaps(self):
        raw = support()
        del raw["levels"]["60"]  # Only the max row is known; LB0 must not be assessed at LB4 Lv80.
        key = raw["card_id"]
        owned = {"s": [key]}
        _, inventory, _ = team_recommend._card_coverage(owned, {key: {"limit_break": 0}}, {key: raw}, CardIdMap(), {"lb4_cap": True})
        self.assertEqual((inventory[key]["limit_break"], inventory[key]["level"]), (0, 60))
        row = team_recommend.card_gaps(owned, {key: raw}, inventory, CardIdMap())[0]
        self.assertEqual((row["limit_break"], row["level"]), (0, 60))
        self.assertFalse(row["ready"]["guest"])
        self.assertIn("levels.60", row["unknown"]["guest"])


if __name__ == "__main__":
    unittest.main()
