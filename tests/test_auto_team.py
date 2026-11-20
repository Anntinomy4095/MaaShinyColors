"""Auto Produce 自动编队 chooser (agent/core/auto_team.py): the official Auto's rules, not the True End scorer."""
from __future__ import annotations

import unittest

from agent.core import auto_team as at

LEVELS = {"0": 60, "1": 65, "2": 70, "3": 75, "4": 80}
MASTERY = {"id": at.MASTERY, "levels": {"65": 1.0, "80": 3.0}}


def p(idol, rarity="P_SSR"):
    return {"kind": "P", "idol": idol, "rarity": rarity, "max_levels": LEVELS}


def s(idol, rarity="S_SSR", skills=()):
    return {"kind": "S", "idol": idol, "rarity": rarity, "max_levels": LEVELS, "support_skills": list(skills)}


CARDS = {
    "1001": p("灯織"), "1002": p("真乃"), "1003": p("恋鐘", "P_SR"),
    "2001": s("めぐる", skills=[MASTERY]), "2002": s("霧子", skills=[MASTERY]), "2003": s("霧子", skills=[MASTERY]),
    "2004": s("智代子"), "2005": s("摩美々", "S_SR"), "2006": s("真乃"), "2007": s("灯織"), "2008": s("咲耶"),
}


def owned(**lb):
    return {"p": ["1001", "1002", "1003"], "s": [f"200{i}" for i in range(1, 9)],
            "details": {k: {"limit_break": v} for k, v in lb.items()}}


class AutoTeamTests(unittest.TestCase):
    def test_mastery_by_level(self):
        self.assertEqual(at.mastery(CARDS["2001"], 80), 3.0)
        self.assertEqual(at.mastery(CARDS["2001"], 70), 1.0)
        self.assertEqual(at.mastery(CARDS["2001"], 60), 0.0)
        self.assertEqual(at.mastery(CARDS["2004"], 80), 0.0)
        self.assertEqual(at.card_level(CARDS["2001"], 4), 80)
        self.assertEqual(at.card_level(CARDS["2001"], None), 60)

    def test_mastery_first_no_idol_twice_guest_owned(self):
        team = at.choose(owned(**{"2001": 4, "2002": 1, "2003": 4, "1002": 4}), CARDS, avoid_idols={"灯織"})
        self.assertEqual(team["p_card"], "1002")                 # 真乃: usual idol 灯織 avoided, most raised
        self.assertEqual(team["s_cards"][:2], ["2001", "2003"])  # mastery 3.0 first; the second 霧子 card is skipped
        self.assertNotIn("2002", team["s_cards"] + [team["guest"]])
        self.assertNotIn("2006", team["s_cards"] + [team["guest"]])   # 真乃 is the P
        idols = team["idols"]
        self.assertEqual(len(idols), len(set(idols)))
        self.assertIn(team["guest"], owned()["s"])
        self.assertEqual(team["mastery"], {"2001": 3.0, "2003": 3.0})

    def test_scanned_level_wins(self):
        # a scanned Lv70 card (mastery 1.0) ranks below an unscanned LB4 one (Lv80, 3.0)
        owned_cards = owned(**{"2001": 4, "2003": 4})
        owned_cards["details"]["2001"]["level"] = 70
        team = at.choose(owned_cards, CARDS)
        self.assertEqual(team["s_cards"][0], "2003")
        self.assertEqual(team["mastery"]["2001"], 1.0)

    def test_book_idols_and_fallbacks(self):
        team = at.choose(owned(), CARDS, book_idols={"めぐる", "霧子"})
        self.assertFalse({"2001", "2002", "2003"} & set(team["s_cards"] + [team["guest"]]))
        team = at.choose(owned(), CARDS, avoid_idols={"灯織", "真乃", "恋鐘"})
        self.assertIsNotNone(team["p_card"])
        self.assertIn("p_only_usual_idols", team["notes"])
        team = at.choose({"p": ["1001"], "s": ["2001", "2004"]}, CARDS)
        self.assertEqual((team["p_card"], team["notes"]), (None, ["not_enough_s_cards"]))
        team = at.choose({"p": ["9999"], "s": []}, CARDS)
        self.assertEqual(team["notes"], ["no_p_card"])

    def test_grad_colour_quota_comes_first(self):
        # 2026-11-01 (按目标词条): ひらめき colour is fixed per idol; the quota is filled in the usual order first
        cards = {"1001": p("風野灯織"), "2001": s("八宮めぐる", skills=[MASTERY]), "2002": s("幽谷霧子", skills=[MASTERY]),
                 "2003": s("田中摩美々"), "2004": s("白瀬咲耶"), "2005": s("櫻木真乃"), "2006": s("園田智代子", "S_SR"),
                 "2007": s("月岡恋鐘", "S_SR")}
        mine = {"p": ["1001"], "s": [f"200{i}" for i in range(1, 8)], "details": {k: {"limit_break": 4} for k in cards}}
        plain = at.choose(mine, cards)
        self.assertNotIn("2006", plain["s_cards"] + [plain["guest"]])          # SR cards rank last
        team = at.choose(mine, cards, colors={"Me": 2, "Vo": 1})
        five = team["s_cards"] + [team["guest"]]
        self.assertTrue({"2006", "2007", "2002"} <= set(five))                 # two Me idols and the first Vo one
        self.assertEqual(team["s_cards"][:2], ["2001", "2002"])                 # S1–S4 keep the usual order
        self.assertEqual(len(set(team["idols"])), 6)

    def test_scenario_skills_and_ur_rank(self):
        # 2026-10-31: after the mastery, the scenario's skills (unlocked at the card's level) lead.
        stamina = {"id": "体力サポート", "kind": "stamina_support", "levels": {"10": 3.0}}
        facility = {"id": "施設マスタリー", "kind": "facility_mastery", "levels": {"75": 5.0}}
        audition = {"id": "オーディションマスタリーSP", "kind": "audition_mastery_sp", "levels": {"1": 2.0}}
        cards = {"1001": p("灯織"), "2001": s("A"), "2002": s("B", skills=[stamina]), "2003": s("C", skills=[facility]),
                 "2004": s("D", skills=[audition]), "2005": s("E"), "2006": s("F", "S_UR"), "2007": s("G")}
        own = {"p": ["1001"], "s": [f"200{i}" for i in range(1, 8)],
               "details": {f"200{i}": {"limit_break": 4} for i in range(1, 8)}}
        self.assertEqual(at.scenario_value(cards["2003"], 80, "STEP"), 3.0)
        self.assertEqual(at.scenario_value(cards["2003"], 70, "STEP"), 0.0)       # 施設マスタリー from Lv75
        self.assertEqual(at.scenario_value(cards["2004"], 80, "GRAD"), 0.0)       # no audition outside W.I.N.G.
        step = at.choose(own, cards, scenario="STEP")
        self.assertEqual(step["s_cards"][:2], ["2002", "2003"])
        wing = at.choose(own, cards, scenario="WING")
        self.assertEqual(wing["s_cards"][:2], ["2002", "2004"])
        self.assertEqual(at.rarity(cards["2006"]), 4)
        self.assertEqual(wing["s_cards"][2], "2006")                               # UR before SSR on equal terms


if __name__ == "__main__":
    unittest.main()
