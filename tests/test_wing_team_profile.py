"""纯合成卡库，验证真实 Lv/凸与精确数字映射；无账号、网络或输入。"""
from dataclasses import asdict
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from agent.core.card_id_map import CardIdMap
from agent.core.wing_team_profile import build_team_profile
from true_end_fixtures import card

P = "1040010010"
SUPPORTS = [f"20500{i}0010" for i in range(2, 7)]


def fixture():
    ids = [P, *SUPPORTS]
    cards = {f"te-{w}": asdict(card(f"te-{w}", kind="P" if w == P else "S")) for w in ids}
    for w in SUPPORTS:
        raw = cards[f"te-{w}"]
        raw["levels"][60] = {"Vo": 120, "Da": 100, "Vi": 90, "Me": 140}
        raw["max_levels"] = {0: 60, 1: 65, 2: 70, 3: 75, 4: 80}
        raw["support_skills"] = [
            {"id": "growth", "kind": "mastery_stat", "attribute": "Vo", "room": "Vo",
             "levels": {"1": 3, "70": 9}, "required_lb": 2},
            {"id": "bond", "kind": "bond", "levels": {"1": 2, "70": 5}},
            {"id": "sp", "kind": "mastery_sp", "attribute": "SP", "room": "Da", "levels": {"1": 4}},
            {"id": "stamina", "kind": "mastery_stamina", "room": "Vi", "levels": {"1": 6}},
            {"id": "radio", "kind": "mastery_sp", "room": "radio", "levels": {"1": 7}},
            {"id": "talk", "kind": "mastery_sp", "room": "talk", "levels": {"1": 8}},
            {"id": "magazine", "kind": "mastery_stamina", "room": "magazine", "levels": {"1": 9}},
        ]
    id_map = CardIdMap({"version": 1, "mapped": {w: {"true_end_id": f"te-{w}",
                         "kind": "P" if w == P else "S", "match": "exact_title_idol_kind"} for w in ids}})
    team = {"p_card": P, "s_cards": SUPPORTS[:4], "guest": SUPPORTS[4]}
    owned = {"p": [P], "s": SUPPORTS[:], "details": {w: {"level": 80, "limit_break": 4} for w in ids}}
    return team, owned, cards, id_map


class ProfileTests(unittest.TestCase):
    def test_owned_intermediate_level_uses_the_same_derived_row_as_recommendations(self):
        team, owned, cards, id_map = fixture()
        owned["details"][SUPPORTS[0]] = {"level": 65, "limit_break": 1}
        built = build_team_profile(team, owned, cards=cards, id_map=id_map)
        self.assertEqual(built.profile.supports[SUPPORTS[0]].stats,
                         {"Vo": 155.0, "Da": 135.0, "Vi": 122.0, "Me": 167.0})
        self.assertEqual(built.sources[SUPPORTS[0]]["owned_level"]["status"], "derived")
        self.assertNotIn(f"{SUPPORTS[0]}:stats.level:65", built.unknowns)

    def build(self, *, change=None):
        team, owned, cards, id_map = fixture()
        if change:
            change(team, owned, cards)
        return build_team_profile(team, owned, cards=cards, id_map=id_map)

    def test_all_five_supports_numeric_keys_and_guest(self):
        built = self.build()
        self.assertTrue(built.complete, built.unknowns)
        self.assertEqual(set(built.profile.supports), set(SUPPORTS))
        self.assertNotIn(P, built.profile.supports)
        self.assertEqual(built.sources[SUPPORTS[4]]["role"], "guest")
        self.assertEqual(built.profile.supports[SUPPORTS[4]].bond0, 25)
        self.assertEqual(built.sources[P]["true_end_id"], f"te-{P}")

    def test_actual_level_and_limit_break_unlocks(self):
        def low(team, owned, cards):
            owned["details"][SUPPORTS[0]] = {"level": 60, "limit_break": 0}
            owned["details"][SUPPORTS[1]] = {"level": 60, "limit_break": 2}
        built = self.build(change=low)
        self.assertTrue(built.complete, built.unknowns)
        locked, unlocked, high = (built.profile.supports[w] for w in SUPPORTS[:3])
        self.assertNotIn("lesson_Vo", locked.masteries)
        self.assertEqual(unlocked.masteries["lesson_Vo"]["stat"], 3)
        self.assertEqual(high.masteries["lesson_Vo"]["stat"], 9)
        self.assertEqual((locked.bond0, high.bond0), (10, 25))
        self.assertEqual((locked.stats["Vo"], high.stats["Vo"]), (120, 260))

    def test_room_mapping_and_quantity_units(self):
        prof = self.build().profile.supports[SUPPORTS[0]]
        self.assertEqual(prof.masteries, {"lesson_Vo": {"stat": 9}, "lesson_Da": {"sp": 4},
                         "lesson_Vi": {"stamina": 6}, "radio": {"sp": 7}, "talk": {"sp": 8},
                         "magazine": {"stamina": 9}})

    def test_work_mastery_preserves_both_attributes_and_lesson_offstat(self):
        def change(team, owned, cards):
            cards[f"te-{SUPPORTS[0]}"]["support_skills"] += [
                {"id": "radio-vo", "kind": "mastery_stat", "attribute": "Vo", "room": "radio", "levels": {"1": 3}},
                {"id": "radio-me", "kind": "mastery_stat", "attribute": "Me", "room": "radio", "levels": {"1": 5}},
                {"id": "lesson-da", "kind": "mastery_stat", "attribute": "Da", "room": "Vo", "levels": {"1": 7}}]
        built = self.build(change=change)
        prof = built.profile.supports[SUPPORTS[0]]
        self.assertEqual(prof.masteries["radio"], {"sp": 7, "stat_Vo": 3, "stat_Me": 5})
        self.assertEqual(prof.masteries["lesson_Vo"], {"stat": 9, "stat_Da": 7})
        self.assertTrue(built.complete, built.unknowns)

    def test_missing_anchor_does_not_estimate_unknown_level(self):
        def change(team, owned, cards):
            owned["details"][SUPPORTS[0]]["level"] = 65
            cards[f"te-{SUPPORTS[0]}"]["levels"].pop(60)
        built = self.build(change=change)
        self.assertFalse(built.complete)
        self.assertIsNone(built.profile.supports[SUPPORTS[0]].stats)
        self.assertIn(f"{SUPPORTS[0]}:levels.65.Vo", built.unknowns)
        self.assertEqual(built.profile.supports[SUPPORTS[0]].masteries["lesson_Vo"]["stat"], 3)

    def test_over_cap_rejected_not_truncated_to_max(self):
        def change(team, owned, cards):
            owned["details"][SUPPORTS[0]] = {"level": 80, "limit_break": 0}
        built = self.build(change=change)
        prof = built.profile.supports[SUPPORTS[0]]
        self.assertIsNone(prof.stats)
        self.assertEqual(prof.masteries, {})
        self.assertTrue(any("level exceeds" in u for u in built.unknowns))

    def test_missing_owned_details_active_and_guest(self):
        def change(team, owned, cards):
            owned["details"].pop(SUPPORTS[0])
            owned["details"].pop(SUPPORTS[4])
        built = self.build(change=change)
        for w in (SUPPORTS[0], SUPPORTS[4]):
            self.assertIsNone(built.profile.supports[w].stats)
            self.assertIsNone(built.profile.supports[w].bond0)
            self.assertIn(f"{w}:ownership.level:missing", built.unknowns)
            self.assertIn(f"{w}:ownership.limit_break:missing", built.unknowns)

    def test_details_for_unowned_card_not_trusted(self):
        def change(team, owned, cards):
            owned["s"].remove(SUPPORTS[4])
        built = self.build(change=change)
        self.assertIn(f"{SUPPORTS[4]}:ownership.level:missing", built.unknowns)

    def test_numeric_mapping_exact_no_titles_or_integer_coercion(self):
        team, owned, cards, id_map = fixture()
        team["guest"] = "te-" + SUPPORTS[4]
        built = build_team_profile(team, owned, cards=cards, id_map=id_map)
        self.assertIn("team.guest:invalid_numeric_id", built.unknowns)
        team["guest"] = int(SUPPORTS[4])
        built = build_team_profile(team, owned, cards=cards, id_map=id_map)
        self.assertIn("team.guest:invalid_numeric_id", built.unknowns)
        team["guest"] = SUPPORTS[4]
        built = build_team_profile(team, owned, cards=cards, id_map=CardIdMap())
        self.assertTrue(any("mapping:not_in_map" in u for u in built.unknowns))
        self.assertIsNone(built.profile.supports[SUPPORTS[4]].stats)

    def test_unknown_skill_level_and_unsupported_not_ordinary(self):
        def change(team, owned, cards):
            raw = cards[f"te-{SUPPORTS[0]}"]
            raw["support_skills"] += [
                {"id": "missing", "kind": "mastery_sp", "room": "Vo", "levels": {}},
                {"id": "unit", "kind": "unit_mastery_stat", "room": "Vo", "attribute": "Vo", "levels": {"1": 99}},
                {"id": "cap", "kind": "mastery_cap", "room": "Vo", "attribute": "Vo", "levels": {"1": 10}},
                {"id": "special", "kind": "mastery_stat", "room": "Vo", "attribute": "Vo", "levels": {"1": 99},
                 "unsupported": ["condition_not_resolved"]}]
        built = self.build(change=change)
        self.assertEqual(built.profile.supports[SUPPORTS[0]].masteries["lesson_Vo"], {"stat": 9})
        self.assertIn(f"{SUPPORTS[0]}:support_skills.missing.level", built.unknowns)
        # 2026-10-12: ユニットマスタリー is consumed (per other unit member in the room, wing_score lower bound).
        self.assertEqual(built.profile.supports[SUPPORTS[0]].masteries["unit"], {"stat_Vo": 99})
        self.assertIn(f"{SUPPORTS[0]}:support_skills.cap:unconsumed:mastery_cap", built.unknowns)
        self.assertTrue(any("condition_not_resolved" in u for u in built.unknowns))

    def test_missing_cap_does_not_assume_attainable_level(self):
        def change(team, owned, cards):
            cards[f"te-{SUPPORTS[0]}"]["max_levels"].pop(4)
        built = self.build(change=change)
        self.assertIsNone(built.profile.supports[SUPPORTS[0]].stats)
        self.assertIn(f"{SUPPORTS[0]}:ownership.level:cap_unverified", built.unknowns)

    def test_carddata_instances_supported_and_partial_stat_preserved(self):
        from agent.core.true_end.models import CardData
        team, owned, cards, id_map = fixture()
        cards[f"te-{SUPPORTS[0]}"]["levels"][80]["Da"] = None
        cards = {key: CardData.from_dict(value) for key, value in cards.items()}
        built = build_team_profile(team, owned, cards=cards, id_map=id_map)
        self.assertEqual(built.profile.supports[SUPPORTS[0]].stats["Vo"], 260)
        self.assertNotIn("Da", built.profile.supports[SUPPORTS[0]].stats)
        self.assertFalse(built.complete)


if __name__ == "__main__":
    unittest.main()
