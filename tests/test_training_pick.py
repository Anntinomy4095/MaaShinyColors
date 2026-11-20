import unittest

from agent.core import training_pick as tp
from tools import ui_engine

CAPS = {"UR": {"0": 70, "4": 90}, "SSR": {"0": 60, "4": 80}, "SR": {"0": 50, "4": 70}, "R": {"0": 40}, "N": {"0": 20}}
CARDS = {f"20{i:08d}": {"kind": "S", "rarity": "S_" + r, "max_levels": CAPS[r]}
         for i, r in enumerate(("UR", "SSR", "SR", "R", "N", "SSR", "UR"), 1)}


def owned(levels):
    return {"p": [], "s": list(levels), "details": {c: {"level": lv, "limit_break": lb} for c, (lv, lb) in levels.items()}}


class TrainingPickTest(unittest.TestCase):
    def test_team_card_first_lowest_level(self):
        o = owned({"2000000001": (10, 0), "2000000002": (30, 0), "2000000006": (20, 0)})
        self.assertEqual(tp.pick(o, CARDS, ["2000000002", "2000000006"])["id"], "2000000006")

    def test_maxed_team_cards_fall_back_to_highest_rarity(self):
        o = owned({"2000000002": (80, 4), "2000000003": (5, 0), "2000000001": (69, 0), "2000000007": (40, 0)})
        chosen = tp.pick(o, CARDS, ["2000000002"])
        self.assertEqual((chosen["id"], chosen["reason"]), ("2000000001", "rarity"))   # UR, highest non-max level

    def test_newer_reading_overrides_the_scan(self):
        o = owned({"2000000001": (66, 0), "2000000006": (20, 0)})
        chosen = tp.pick(o, CARDS, ["2000000001"], {"2000000001": {"level": 70, "limit_break": 0}})
        self.assertEqual((chosen["id"], chosen["reason"]), ("2000000006", "rarity"))

    def test_skipped_ids_are_never_picked(self):
        o = owned({"2000000001": (10, 0), "2000000007": (20, 0)})
        self.assertEqual(tp.pick(o, CARDS, (), {}, skip={"2000000007"})["id"], "2000000001")

    def test_n_r_maxed_and_unread_levels_are_never_picked(self):
        o = owned({"2000000004": (1, 0), "2000000005": (1, 0), "2000000001": (90, 4)})
        o["s"].append("2000000002")   # no level read
        self.assertIsNone(tp.pick(o, CARDS, ["2000000004", "2000000005", "2000000002"]))


class TrainingDefaultCardTest(unittest.TestCase):
    def profile(self, card=""):
        return {"tasks": [{"name": "Training", "enabled": True, "option": {"训练卡": card}}]}

    def test_empty_option_gets_the_pick(self):
        profile = self.profile()
        snap = {"version": 1, "unit": 1, "p_card": "1000000001", "s_cards": ["2000000006", "2000000011",
                "2000000012", "2000000013"], "guest": "2000000014"}
        o = owned({"2000000001": (10, 0), "2000000006": (20, 0)})
        chosen = ui_engine.training_default_card({}, profile, o, snapshot=snap, cards=CARDS, readings={})
        self.assertEqual(chosen["id"], "2000000006")
        self.assertEqual(profile["tasks"][0]["option"]["训练卡"], "2000000006")

    def test_user_choice_is_kept(self):
        profile = self.profile("2000000001")
        self.assertIsNone(ui_engine.training_default_card({}, profile, owned({"2000000006": (1, 0)}), cards=CARDS,
                                                          snapshot={}, readings={}))
        self.assertEqual(profile["tasks"][0]["option"]["训练卡"], "2000000001")


if __name__ == "__main__":
    unittest.main()
