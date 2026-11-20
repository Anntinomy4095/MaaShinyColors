"""Per-level support-card stats (agent/core/card_stats.py) and the merge hook card_data_merge.with_owned_level."""
import unittest

from agent.core import card_data_merge as cdm
from agent.core import card_stats as cs

# Wiki rows of SSR【ROOKIE!】市川雛菜 2040230010 (public Wiki table).
HINANA = {"1": {"Vo": 100, "Da": 48, "Vi": 48, "Me": 44}, "60": {"Vo": 250, "Da": 120, "Vi": 120, "Me": 110},
          "65": {"Vo": 265, "Da": 128, "Vi": 128, "Me": 116}, "70": {"Vo": 281, "Da": 136, "Vi": 136, "Me": 122},
          "75": {"Vo": 296, "Da": 144, "Vi": 144, "Me": 128}, "80": {"Vo": 312, "Da": 152, "Vi": 152, "Me": 134}}
CAPS = {"0": 60, "1": 65, "2": 70, "3": 75, "4": 80}
# UR 2050170010: in-game Lv1-4 (training popups) and Lv70/90 taken from the identical-profile UR 2050070010.
UR_ROWS = {"1": {"Vo": 65, "Da": 65, "Vi": 130, "Me": 60}, "70": {"Vo": 163, "Da": 163, "Vi": 325, "Me": 150},
           "90": {"Vo": 212, "Da": 212, "Vi": 423, "Me": 195}}
UR_SEEN = {2: (66, 66, 132, 61), 3: (67, 67, 135, 62), 4: (69, 69, 138, 63)}


class FormulaTests(unittest.TestCase):
    def test_segment2_reproduces_the_wiki_rows(self):
        anchors = {k: HINANA[k] for k in ("1", "60", "80")}
        for lv in (65, 70, 75):
            value = cs.stats_at_level(anchors, lv, rarity="SSR", max_levels=CAPS)
            self.assertEqual(value.status, "derived")
            self.assertEqual(value.stats, HINANA[str(lv)], lv)
            self.assertIn("VERIFIED_WIKI_ROWS", value.evidence)

    def test_segment1_matches_in_game_training_with_floor(self):
        for lv, seen in UR_SEEN.items():
            value = cs.stats_at_level(UR_ROWS, lv, rarity="S_UR")
            self.assertEqual(tuple(value.stats[s] for s in cs.STATS), seen, lv)
            self.assertEqual(value.method, "linear_floor_segment1")

    def test_known_row_and_unknowns(self):
        self.assertEqual(cs.stats_at_level(HINANA, 60, rarity="SSR").status, "known")
        self.assertEqual(cs.stats_at_level({"80": HINANA["80"]}, 70, rarity="SSR").status, "unknown")
        self.assertEqual(cs.stats_at_level({"80": HINANA["80"]}, 30, rarity="SSR").method, "segment1_anchor_missing")
        self.assertEqual(cs.stats_at_level(HINANA, 81, rarity="SSR").status, "unknown")
        self.assertEqual(cs.stats_at_level(HINANA, 30, rarity="N").status, "unknown")
        partial = {**HINANA, "60": {"Vo": 250, "Da": None, "Vi": 120, "Me": 110}}
        self.assertEqual(cs.stats_at_level(partial, 30, rarity="SSR").status, "unknown")

    def test_formula_mismatch_flags_a_typo_without_changing_it(self):
        typo = {**HINANA, "70": {**HINANA["70"], "Me": 125}}
        self.assertEqual(cs.formula_mismatches(typo, rarity="SSR"),
                         [{"field": "levels.70.Me", "stored": 125.0, "formula": 122}])
        self.assertEqual(cs.formula_mismatches(HINANA, rarity="SSR"), [])

    def test_estimate_is_labelled_and_bracketed(self):
        value = cs.estimate_from_max(80, HINANA["80"], 60, rarity="SSR")
        self.assertEqual(value.status, "estimated")
        self.assertLessEqual(value.stats["Vo"], 250)
        self.assertGreaterEqual(value.high["Vo"], 249)


class MergeHookTests(unittest.TestCase):
    def card(self, levels):
        return {"card_id": "2040230010", "kind": "S", "rarity": "SSR", "levels": levels, "max_levels": CAPS}

    def test_derived_row_added_with_provenance(self):
        out = cdm.with_owned_level(self.card({k: HINANA[k] for k in ("1", "60", "80")}), 57, limit_break=1)
        self.assertEqual(out["levels"]["57"]["Vo"], 242.0)   # floor(100 + 150 * 56/59)
        self.assertEqual(out["merge"]["derived_levels"]["57"]["status"], "derived")
        self.assertEqual(cdm.role_readiness(out, limit_break=1, level=57)["derived_levels"], {"57": "derived"})

    def test_level_above_cap_refused(self):
        out = cdm.with_owned_level(self.card(HINANA), 70, limit_break=1)
        self.assertIn("exceeds", out["merge"]["owned_level_error"])
        self.assertEqual(out["levels"], HINANA)

    def test_missing_anchor_stays_unknown(self):
        out = cdm.with_owned_level(self.card({"80": HINANA["80"]}), 57, limit_break=4)
        self.assertNotIn("57", out["levels"])
        self.assertEqual(out["merge"]["derived_levels"]["57"]["status"], "unknown")

    def test_in_game_reading_wins(self):
        card = {"card_id": "2050170010", "kind": "S", "rarity": "S_UR", "levels": {"90": UR_ROWS["90"]}}
        out = cdm.with_owned_level(card, 3, limit_break=0)
        self.assertEqual(out["levels"]["3"], {"Vo": 67.0, "Da": 67.0, "Vi": 135.0, "Me": 62.0})
        self.assertEqual(out["merge"]["derived_levels"]["3"]["status"], "in_game")

    def test_p_card_untouched(self):
        card = {"card_id": "1040010010", "kind": "P", "levels": {}}
        self.assertEqual(cdm.with_owned_level(card, 10), card)


if __name__ == "__main__":
    unittest.main()
