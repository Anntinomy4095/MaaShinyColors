"""Duplicate Live panels are free bridges, never a second skill acquisition."""
from dataclasses import asdict, replace
import unittest
from unittest import mock

from agent.core.true_end.skill_identity import strict_live_identity, mastered_identities, with_live_skill_names, panel_skill_identity
from agent.core.true_end.sp_value import _apply_path
from agent.core.true_end.growth import GrowthConfig, _plan, _simulate, panel_closure
from agent.core.true_end.models import STATS
from agent.core.wing_sp_catalog import load_sp_catalog, bind_sp_catalog
from tests.test_true_end_sp_value import feature, panel, live, snapshot
from tests.test_wing_sp_catalog import card, raw, SLOTS, KNOWN


class SkillIdentityTests(unittest.TestCase):
    def test_only_complete_same_name_and_effects_merge(self):
        key = strict_live_identity("同技能", live("panel-a", 2.5))
        self.assertEqual(key, strict_live_identity("同技能", {**live("panel-b", 2.5), "unlock_level": 80}))
        for name, effect in (("同技能+", live("panel-a", 2.5)),
                             ("同技能", {"id": "x", "components": {"Da": 2.5}}),
                             ("同技能", {**live("x", 2.5), "heal_fraction": .05})):
            self.assertNotEqual(key, strict_live_identity(name, effect))
        self.assertIsNone(strict_live_identity("同技能", {**live("x", 2.5), "unsupported": ["LINK"]}))
        self.assertIsNone(strict_live_identity(None, live("scdb-31", 2.5)))
        self.assertIsNone(strict_live_identity(None, live("scdb-31-dup", 2.5)))
        self.assertIsNone(strict_live_identity("同技能", {**live("x", 2.5), "unparsed": 1}))

    def test_old_anonymous_snapshot_needs_same_card_raw_name(self):
        from agent.core.true_end.models import CardData
        old = CardData.from_dict(card())
        self.assertIsNone(panel_skill_identity(old.panels[-1]))
        hydrated = with_live_skill_names(old, raw())
        self.assertIsNotNone(panel_skill_identity(hydrated.panels[-1]))
        self.assertIsNone(panel_skill_identity(with_live_skill_names(old, raw("2049990020")).panels[-1]))

    def fixtures(self):
        a = feature("a", panels=[panel("learn", "live", live("same", 4), 20)])
        b = feature("b", kind="S", panels=[panel("duplicate", "live", live("same", 4), 20),
            panel("cap", "cap", {"Vo": 100}, 50, prerequisites=("duplicate",))])
        return a, b

    def test_same_round_free_bridge_and_no_relearning_after_forget(self):
        a, b = self.fixtures()
        first, cost, _ = _apply_path(snapshot(), a, a.panels, "Vo", (a, b))
        self.assertEqual(cost, 20)
        # The acquired skill has subsequently left the four equipped slots.
        forgotten = replace(first, live_skills=snapshot().live_skills)
        after, cost, purchases = _apply_path(forgotten, b, b.panels, "Vo", (a, b))
        self.assertEqual(cost, 50)
        self.assertEqual(purchases, ("b:duplicate", "b:cap"))
        self.assertEqual(after.live_skills, forgotten.live_skills)
        self.assertEqual(after.caps["Vo"], 600)

    def test_historical_panel_ids_infer_mastered_skill_without_equipment(self):
        a, b = self.fixtures()
        state = snapshot(learned=("a:learn",))
        after, cost, _ = _apply_path(state, b, b.panels, "Vo", (a, b))
        self.assertEqual(cost, 50)
        self.assertEqual(after.live_skills, state.live_skills)
        self.assertTrue(mastered_identities((a, b), state.learned_panel_ids))

    def test_growth_plan_and_replay_share_same_free_bridge_cost(self):
        a, b = self.fixtures()
        cfg = GrowthConfig(starting_stats=dict.fromkeys(STATS, 100),
            starting_live_skills=snapshot().live_skills, starting_sp=70,
            start_week=32, starting_fans=500000, actions=tuple({"action": "rest"} for _ in range(32)))
        purchases, required = _plan((a, b), {**dict.fromkeys(STATS, 100), "Vo": 1000}, cfg,
                                    ("Vo",), dict.fromkeys(STATS, 500))
        self.assertEqual(required, 70)
        result = _simulate(a, (b,), None, cfg, cfg.actions, ("Vo",), purchases)
        self.assertEqual(result["plan"].cost, required)
        self.assertIn("b:duplicate", result["plan"].unlocked_panel_ids)
        self.assertEqual(sum(s.id == "same" for s in result["plan"].learned_skills), 1)

    def test_free_bridge_changes_cheapest_reviewed_route(self):
        nodes = [panel("root", "stat", {"Vo": 0}, 0),
                 panel("paid", "stat", {"Vo": 0}, 10),
                 panel("duplicate", "live", live("same", 4), 50),
                 panel("cap", "cap", {"Vo": 100}, 20)]
        b = feature("b", panels=nodes, topology={"roots": ["root"],
            "edges": [["root", "paid"], ["paid", "cap"], ["root", "duplicate"], ["duplicate", "cap"]],
            "complete": True, "source_url": "synthetic://topology", "locator": "verified"})
        a, _ = self.fixtures()
        mastered = mastered_identities((a, b), ("a:learn",))
        self.assertEqual([p.id for p in panel_closure(b, "cap", mastered_skills=mastered)],
                         ["root", "duplicate", "cap"])

    def test_catalog_duplicate_unlearned_bridge_is_zero_not_done(self):
        cards = {cid: card(cid) for cid in ("2049990010", "2049990020")}
        raws = {cid: raw(cid) for cid in cards}
        catalog = load_sp_catalog({"S": list(cards)}, {cid: {"limit_break": 2} for cid in cards},
                                  cards=cards, raw_cards=raws)
        bound = bind_sp_catalog(catalog, SLOTS, KNOWN, petal_cards={"left": "2049990020"},
                                learned=("2049990010:scdb-31",))
        key = "catalog:2049990020:scdb-31"
        self.assertEqual(bound["details"][key]["cost"], 0)
        self.assertTrue(bound["details"][key]["duplicate_skill"])
        self.assertEqual(bound["details"][key]["state"], "unobserved")
        self.assertIn(key, bound["graph"])
        self.assertEqual(bound["graph"][key]["path_cost"], 30)
        self.assertFalse(bound["routes"])
        unknown = bind_sp_catalog(catalog, SLOTS, KNOWN, petal_cards={"left": "2049990020"})
        self.assertEqual(unknown["details"][key]["cost"], 30)

    def test_catalog_counts_same_skill_only_once_within_a_route(self):
        data, source = card(), raw()
        data["panels"][1].update(kind="live", effects=live("scdb-21", 4.5))
        source["cardPanels"][1].update(skillTitle="高倍率", skillDesc="Vocal4.5倍アピール")
        c = load_sp_catalog({"S": ["2049990010"]}, {"2049990010": {"limit_break": 2}},
                            cards={"2049990010": data}, raw_cards={"2049990010": source})
        bound = bind_sp_catalog(c, SLOTS, KNOWN)
        self.assertEqual(bound["graph"]["catalog:2049990010:scdb-31"]["path_cost"], 30)

    def test_zero_cost_open_duplicate_is_not_already_learned(self):
        cid = "2049990010"
        c = load_sp_catalog({"S": [cid]}, {cid: {"limit_break": 2}}, cards={cid: card()}, raw_cards={cid: raw()})
        identity = c["cards"][cid]["nodes"]["scdb-31"]["skill_identity"]
        slots = {**SLOTS, "high": {"petal": "left", "state": "open", "cost": 0}}
        known = {**KNOWN, "high": {"name": "高倍率", "kind": "live_skill", "attributes": ["Vo"],
                                     "value": 4.5, "cost": 0, "state": "learnable"}}
        result = bind_sp_catalog(c, slots, known, mastered_skills=(identity,))
        self.assertIn("high", result["graph"])
        self.assertEqual(result["details"]["high"]["state"], "learnable")
        self.assertTrue(result["details"]["high"]["duplicate_skill"])

    def test_normal_adapter_hydrates_only_owned_cards_and_reduces_bridge_cost(self):
        from agent.core import team_recommend as tr
        from agent.core.card_id_map import CardIdMap
        from agent.core.true_end.models import extract_card_features
        p, a, b = "1049990010", "2049990010", "2049990020"
        owned_s = [a, b, "2049990030", "2049990040", "2049990050"]
        data = {cid: asdict(feature(cid, kind="P" if cid == p else "S").card)
                for cid in [p, *owned_s, "2049990090"]}
        data[a]["panels"] = [panel("scdb-31", "live", live("scdb-31", 4), 20)]
        data[b]["panels"] = [panel("scdb-31", "live", live("scdb-31", 4), 20),
                             panel("scdb-41", "cap", {"Vo": 100}, 50, prerequisites=("scdb-31",))]
        own = {"p": [p], "s": owned_s,
               "details": {cid: {"limit_break": 4, "level": 80} for cid in [p, *owned_s]}}
        raws = {cid: {"enzaId": cid, "cardPanels": [{"panelSlot": 31, "skillTitle": "同技能"}]}
                for cid in (a, b)}
        costs = []
        def capture(cards, inventory, selected_p, **kwargs):
            ready = tuple(extract_card_features(c, inventory[k]) for k, c in cards.items())
            fb = next(c for c in ready if c.card.card_id == b)
            _, cost, _ = _apply_path(snapshot(learned=(f"{a}:scdb-31",)), fb, fb.panels, "Vo", ready)
            costs.append(cost)
            return {"status": "blocked", "reason": "captured adapter input"}
        library = {"cards": data, "notes": [], "sources": {}, "counts": {}, "snapshots": []}
        fallback = lambda records, primary: {"status": "rejected"}
        with mock.patch.object(tr, "load_library", return_value=library), \
                mock.patch("agent.core.wing_sp_catalog._local_texts", return_value=raws) as reader, \
                mock.patch("agent.core.true_end.optimizer.find_best_teams", side_effect=capture):
            tr.recommend(own, "Vo", id_map=CardIdMap(), settings={}, fallback=fallback)
            reader.assert_called_once_with({p, *owned_s}, root=None)
            tr.recommend(own, "Vo", cards=data, id_map=CardIdMap(), settings={}, fallback=fallback)
            self.assertEqual(reader.call_count, 1)
        self.assertEqual(costs, [50, 70])


if __name__ == "__main__":
    unittest.main()
