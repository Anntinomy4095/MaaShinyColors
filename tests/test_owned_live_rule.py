"""S 卡自带 Live 的规则推算 (agent/core/owned_live_rule.py); synthetic cards, no private data."""
from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.core import owned_live_rule as rule  # noqa: E402
from agent.core.true_end.models import CardData  # noqa: E402


def s_card(rarity="S_SSR", method="GeneralGasha", date="2022-05-31", stats=(312, 144, 144), **extra):
    vo, da, vi = stats
    return {"card_id": "2040000000", "name": "x", "idol": "x", "kind": "S", "rarity": rarity, "status": "PARTIAL",
            "source_url": "synthetic", "implementation_date": date, "scdb": {"get_method": method},
            "levels": {"1": {"Vo": 10, "Da": 10, "Vi": 10, "Me": 10}, "80": {"Vo": vo, "Da": da, "Vi": vi, "Me": 150}},
            "max_levels": {"4": 80}, "owned_live_skills": [], "support_skills": [], "panels": [], "events": [],
            **extra}


def ladder(skills):
    return [(s["unlock_level"], s["components"] or {"EX": s["excellent"]}, s["buff_amount"]) for s in skills]


class OwnedLiveRuleTests(unittest.TestCase):
    def test_tiers_follow_rarity_source_and_date(self):
        self.assertEqual(ladder(rule.derive(s_card()))[0], (1, {"Vo": 2.5}, 0))
        self.assertEqual(ladder(rule.derive(s_card()))[-1], (80, {"Vo": 3.0}, 0))
        self.assertEqual(ladder(rule.derive(s_card(method="LimitedGasha")))[-1], (80, {"Vo": 3.5}, 0))
        self.assertEqual(ladder(rule.derive(s_card(method="LimitedGasha", date="2019-10-21")))[0], (1, {"Vo": 2.5}, 0))
        twilight = ladder(rule.derive(s_card(method="TwilightCollection")))
        self.assertEqual((twilight[0], twilight[-1]), ((1, {"Vo": 3.0}, 0), (80, {"Vo": 3.5}, .25)))
        ur = rule.derive(s_card(rarity="S_UR", stats=(212, 212, 423)))
        self.assertEqual(ladder(ur)[-1], (90, {"Vi": 4.0}, .25))
        self.assertEqual([s["unlock_level"] for s in ur], [1, 70, 75, 80, 85, 90])
        self.assertEqual(ur[0]["id"], "ビジュアルアピールⅦ")
        self.assertEqual(ladder(rule.derive(s_card(rarity="S_SR", method="Events"))), [(1, {"Vo": 2.0}, 0)])
        self.assertEqual(ladder(rule.derive(s_card(rarity="S_SR", method="Present", date="2024-11-16"))),
                         [(1, {"Vo": 2.5}, 0)])
        self.assertEqual(ladder(rule.derive(s_card(rarity="S_R"))), [(1, {"Vo": 1.5}, 0)])
        # an unsampled source takes the lower tier
        self.assertEqual(ladder(rule.derive(s_card(method="PCup")))[0], (1, {"Vo": 2.5}, 0))

    def test_attribute_from_the_highest_stat(self):
        self.assertEqual(ladder(rule.derive(s_card(stats=(150, 225, 150))))[0][1], {"Da": 2.5})
        excellent = rule.derive(s_card(method="LimitedGasha", stats=(218, 218, 218)))
        self.assertEqual((excellent[0]["id"], excellent[0]["excellent"], excellent[-1]["excellent"]),
                         ("エクセレントアピールⅤ", 3.0, 3.5))
        self.assertIsNone(rule.derive(s_card(stats=(215, 215, 138))))       # two-attribute tie: unknown
        self.assertIsNone(rule.derive(s_card(rarity="S_N")))
        self.assertIsNone(rule.derive(s_card(levels={})))
        self.assertIsNone(rule.derive({**s_card(), "kind": "P"}))

    def test_library_labels_derived_skills_and_keeps_wiki_values(self):
        wiki = s_card(owned_live_skills=[{"id": "ボーカルアピールⅤ", "components": {"Vo": 3.0}, "unlock_level": 1}])
        cards, derived = rule.with_derived_owned_live({"a": s_card(unknown_fields=["owned_live_skills"]), "b": wiki})
        self.assertEqual(derived, ["a"])
        self.assertIs(cards["b"], wiki)
        self.assertEqual(cards["a"]["provenance"]["owned_live_skills"]["source"], rule.RULE)
        self.assertEqual(cards["a"]["unknown_fields"], [])
        CardData.from_dict(cards["a"])   # the derived rows parse as LiveSkill


if __name__ == "__main__":
    unittest.main()
