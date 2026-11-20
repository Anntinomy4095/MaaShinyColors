"""W.I.N.G. auto-team selection v1 (agent/core/wing_team.py). Synthetic cards, no wiki data."""
from __future__ import annotations

import unittest

from agent.core import wing_team as wt


def s_card(id_, attr="Vo", mult=4.0, *, idol=None, stamina=0, sp=0, level=80, uncap=4, **kw):
    base = dict(id=id_, kind="S", idol=idol or id_, rarity="SSR", level=level, uncap=uncap, live_skill_attr=attr,
                live_skill_mult=mult, live_skill_name=f"skill_{id_}", live_skill_extra=False, passive_attr=attr,
                passive_pct=50, passive_rate=20, passive_cond=1.0, mastery_stamina=stamina, mastery_sp=sp,
                mastery_cap=0, mastery_stats={attr: 10}, audition_mastery=0, events_value=20,
                stats={"Vo": 120, "Da": 120, "Vi": 120, attr: 250})
    base.update(kw)
    return base


P_OK = dict(id="p_natsuha", kind="P", idol="natsuha", level=80, te_flag=True, live_skill_attr="Vo",
            live_skill_mult=3.0, p_cap_total=300)


def pool():
    return [P_OK,
            s_card("vo_a", mult=5.0, stamina=6), s_card("vo_b", mult=4.0), s_card("vo_c", mult=3.5, sp=8),
            s_card("vo_d", mult=3.0), s_card("da_a", attr="Da", mult=5.0), s_card("vi_a", attr="Vi", mult=5.0)]


class TeamTests(unittest.TestCase):
    def test_picks_primary_team(self) -> None:
        t = wt.select_team(pool(), "Vo")
        self.assertEqual(t.p_card, "p_natsuha")
        self.assertEqual(len(t.starters), 4)
        self.assertGreaterEqual(sum(1 for c in t.starters if c.startswith("vo_")), 3)
        self.assertNotIn(t.guest, t.starters)
        self.assertEqual(t.status, "ok")
        self.assertAlmostEqual(t.confidence, 1.0)

    def test_hard_reject_p_card_and_primary_count(self) -> None:
        cards = pool()
        cards[0] = dict(P_OK, te_flag=None)
        t = wt.select_team(cards, "Vo")
        self.assertEqual((t.status, t.reasons), ("rejected", ("no_eligible_p_card",)))
        self.assertEqual(t.rejected_cards["p_natsuha"], "p_te_flag_unknown")
        two_vo = [P_OK, s_card("vo_a"), s_card("vo_b"), s_card("da_a", "Da"), s_card("da_b", "Da"), s_card("vi_a", "Vi")]
        t = wt.select_team(two_vo, "Vo")
        self.assertEqual(t.status, "rejected")
        self.assertIn("no_team_satisfies_hard_rules", t.reasons)

    def test_hard_reject_level_and_duplicate_idol(self) -> None:
        cards = pool() + [s_card("vo_low", mult=5.0, level=30), s_card("vo_unread", mult=5.0, level=None)]
        t = wt.select_team(cards, "Vo")
        self.assertEqual(t.rejected_cards["vo_low"], "level_below_min")
        self.assertEqual(t.rejected_cards["vo_unread"], "level_unknown")
        self.assertNotIn("vo_low", t.starters + (t.guest,))
        same_idol = [P_OK, s_card("vo_a", idol="mamimi"), s_card("vo_b", idol="mamimi"), s_card("vo_c"),
                     s_card("vo_d"), s_card("da_a", "Da")]
        t = wt.select_team(same_idol, "Vo")
        self.assertFalse({"vo_a", "vo_b"} <= set(t.starters))

    def test_no_idol_twice_across_p_starters_and_guest(self) -> None:
        # Hard reject, P vs S and the (owned) guest included
        cards = [P_OK, s_card("vo_a", idol="natsuha", mult=5.0, stamina=6), s_card("vo_b"), s_card("vo_c"),
                 s_card("vo_d"), s_card("vo_e", mult=2.0), s_card("da_a", "Da")]
        t = wt.select_team(cards, "Vo")
        self.assertNotIn("vo_a", t.starters + (t.guest,))          # strongest card, but the P card's idol
        only_five = [P_OK, s_card("vo_a", idol="natsuha"), s_card("vo_b"), s_card("vo_c"), s_card("vo_d"),
                     s_card("da_a", "Da")]
        t = wt.select_team(only_five, "Vo")
        self.assertEqual(t.status, "rejected")
        # a second eligible P card is used instead when that gives the better team
        other_p = dict(P_OK, id="p_other", idol="other", live_skill_mult=2.5)
        t = wt.select_team(only_five + [other_p], "Vo")
        self.assertEqual(t.p_card, "p_other")
        self.assertIn("vo_a", t.starters + (t.guest,))

    def test_idol_from_wiki_card_id(self) -> None:
        self.assertEqual(wt.idol_key(wt.CardRecord.from_mapping({"id": "2040170010"})), "idol#017")
        self.assertEqual(wt.idol_key(wt.CardRecord.from_mapping({"id": "x1"})), "?x1")
        p = dict(P_OK, id="1040170020", idol=None)
        cards = [p, dict(s_card("2040170010", mult=5.0), idol=None), s_card("vo_b"), s_card("vo_c"), s_card("vo_d"),
                 s_card("vo_e"), s_card("da_a", "Da")]
        t = wt.select_team(cards, "Vo")
        self.assertNotIn("2040170010", t.starters + (t.guest,))   # same idol number 017 as the P card

    def test_stamina_recovery_soft_rule(self) -> None:
        cards = [P_OK] + [s_card(f"vo_{i}", mult=4.0) for i in range(5)]
        none = wt.select_team(cards, "Vo")
        self.assertIn("soft:no_stamina_recovery", none.reasons)
        with_rec = wt.select_team(cards[:-1] + [s_card("vo_rec", mult=3.0, stamina=6)], "Vo")
        self.assertNotIn("soft:no_stamina_recovery", with_rec.reasons)
        self.assertIn("vo_rec", with_rec.starters + (with_rec.guest,))

    def test_unknown_data_lowers_confidence(self) -> None:
        partial = pool()
        partial[1] = {"id": "vo_a", "kind": "S", "idol": "vo_a", "level": 80, "live_skill_attr": "Vo",
                      "live_skill_mult": 5.0}
        t = wt.select_team(partial, "Vo")
        self.assertLess(t.confidence, 1.0)
        self.assertTrue(any(r.startswith("partial_data:vo_a") for r in t.reasons) or "vo_a" not in t.starters)
        unknown = [P_OK] + [{"id": f"x{i}", "kind": "S", "level": 80} for i in range(6)]
        t = wt.select_team(unknown, "Vo")
        self.assertEqual(t.status, "rejected")                         # cannot prove 3 Vo starters
        self.assertEqual(t.rejected_cards["x0"], "no_data_not_starter")
        sparse = [P_OK] + [{"id": f"vo_{i}", "kind": "S", "level": 80, "live_skill_attr": "Vo",
                            "live_skill_mult": 4.0} for i in range(5)]
        t = wt.select_team(sparse, "Vo")
        self.assertEqual(t.status, "needs_confirmation")
        self.assertIn("low_confidence", t.reasons)
        self.assertIn("soft:stamina_recovery_unknown", t.reasons)

    def test_locked_cards_and_extra_keys(self) -> None:
        cards = pool() + [dict(s_card("vo_fav", mult=2.0), locked=True, owned_since="2025", level=None)]
        t = wt.select_team(cards, "Vo")
        self.assertIn("vo_fav", t.starters + (t.guest,))

    def test_deterministic(self) -> None:
        a = wt.select_team(pool(), "Vo").to_dict()
        b = wt.select_team(list(reversed(pool())), "Vo").to_dict()
        self.assertEqual(a, b)

    def test_level_factor_table(self) -> None:
        self.assertEqual([wt.level_factor(x) for x in (80, 65, 55, 45, 39, None)], [1.0, 0.9, 0.75, 0.5, 0.0, None])


if __name__ == "__main__":
    unittest.main()
