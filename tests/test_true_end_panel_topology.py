from dataclasses import asdict
import unittest

from agent.core.true_end.growth import panel_closure, estimate_team_growth
from agent.core.true_end.models import CardData, Ownership, extract_card_features
from test_true_end_growth import card, panel, live, config, project


def graph_card(*, lb=4, event=None, partial=False):
    source = card("graph", panels=[
        panel("root", "cap", {"Vo": 25}, cost=10, prerequisites_known=False),
        panel("left", "cap", {"Vo": 25}, cost=20, required_lb=1,
              event=event, prerequisites_known=False),
        panel("right", "cap", {"Vo": 25}, cost=30, prerequisites_known=False),
        panel("goal", "live", live("strong", 4), cost=40, prerequisites_known=False)])
    raw = asdict(source.card)
    raw["panel_topology"] = {"roots": ["root"],
        "edges": [] if partial else [["root", "left"], ["root", "right"], ["left", "goal"], ["right", "goal"]],
        "complete": not partial, "source_url": "synthetic://explicit-edge-test", "locator": "reviewed four-node diagram"}
    return extract_card_features(CardData.from_dict(raw), Ownership("graph", lb, 80))


class PanelTopologyTests(unittest.TestCase):
    def test_adjacent_alternative_does_not_charge_both_branches(self):
        path = panel_closure(graph_card(), "goal")
        self.assertEqual([p.id for p in path], ["root", "left", "goal"])
        self.assertEqual(sum(p.sp_cost for p in path), 70)

    def test_locked_route_uses_the_other_verified_neighbor(self):
        self.assertEqual([p.id for p in panel_closure(graph_card(lb=0), "goal")],
                         ["root", "right", "goal"])

    def test_existing_purchase_changes_incremental_path_without_repaying(self):
        path = panel_closure(graph_card(), "goal", purchased=("root", "right"))
        self.assertEqual([p.id for p in path], ["right", "goal"])
        self.assertEqual(sum(p.sp_cost for p in path if p.id not in ("root", "right")), 40)

    def test_event_gate_keeps_alternative_route_and_qualifies_event_id(self):
        feature = graph_card(event="E4")
        self.assertEqual([p.id for p in panel_closure(feature, "goal", ("other:E4",))],
                         ["root", "right", "goal"])
        self.assertEqual([p.id for p in panel_closure(feature, "goal", ("graph:E4",))],
                         ["root", "left", "goal"])

    def test_partial_edges_never_infer_missing_links_from_layout(self):
        feature = graph_card(partial=True)
        self.assertEqual([p.id for p in panel_closure(feature, "root")], ["root"])
        self.assertIsNone(panel_closure(feature, "goal"))

    def test_invalid_complete_graph_or_duplicate_edge_rejected(self):
        raw = asdict(graph_card().card)
        for edges in ([], [["root", "left"], ["left", "root"]], [["root", "unobserved"]]):
            with self.subTest(edges=edges), self.assertRaises(ValueError):
                CardData.from_dict({**raw, "panel_topology": {**raw["panel_topology"], "edges": edges}})

    def test_unaffordable_bridge_cannot_be_skipped_during_purchase(self):
        raw = asdict(graph_card().card)
        raw["panels"] = [
            panel("root", "cap", {"Vo": 25}, cost=10, prerequisites_known=False),
            panel("bridge", "cap", {"Vo": 25}, cost=50, prerequisites_known=False),
            panel("goal", "live", live("strong", 4), cost=10, prerequisites_known=False)]
        raw["panel_topology"]["edges"] = [["root", "bridge"], ["bridge", "goal"]]
        feature = extract_card_features(CardData.from_dict(raw), Ownership("graph", 4, 80))
        projected = project(config(actions=["rest"] * 32, starting_sp=20), guest=feature)
        self.assertEqual(projected.skill_plan.unlocked_panel_ids, ("graph:root",))
        self.assertEqual(projected.skill_plan.cost, 10)
        self.assertNotIn("strong", [s.id for s in projected.skill_plan.learned_skills])

    def test_purchase_uses_one_certified_neighbor_instead_of_legacy_and(self):
        raw = asdict(graph_card().card)
        raw["panels"][-1]["prerequisites"] = ["left", "right"]
        feature = extract_card_features(CardData.from_dict(raw), Ownership("graph", 4, 80))
        projected = project(config(actions=["rest"] * 32, starting_sp=70), guest=feature)
        self.assertEqual(projected.skill_plan.unlocked_panel_ids,
                         ("graph:root", "graph:left", "graph:goal"))
        self.assertEqual(projected.skill_plan.cost, 70)

    def test_replay_rechecks_complete_purchase_and_sp_budget(self):
        p = card("p", kind="P")
        supports = tuple(card("s" + str(i)) for i in range(4))
        witness = ("graph:root", "graph:left", "graph:goal")
        good = estimate_team_growth(p, supports, graph_card(), "VO",
            config(actions=["rest"] * 32, starting_sp=70), replay_panel_ids=witness)
        self.assertEqual(good.skill_plan.unlocked_panel_ids, witness)
        with self.assertRaisesRegex(ValueError, "could not fund"):
            estimate_team_growth(p, supports, graph_card(), "VO",
                config(actions=["rest"] * 32, starting_sp=20), replay_panel_ids=witness)
        with self.assertRaisesRegex(ValueError, "unavailable"):
            estimate_team_growth(p, supports, graph_card(lb=0), "VO",
                config(actions=["rest"] * 32, starting_sp=70), replay_panel_ids=witness)


if __name__ == "__main__":
    unittest.main()
