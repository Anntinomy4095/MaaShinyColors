"""本地 SP 深层路线、真实凸锁及信息栏绑定（0 游戏输入）。"""
import copy
import unittest

from agent.core.wing_sp_catalog import bind_sp_catalog, load_sp_catalog


def card(cid="2049990010", *, event=False):
    nodes = [
        {"id": "scdb-11", "kind": "cap", "sp_cost": 10, "required_lb": 0,
         "effects": {"Vo": 25}},
        {"id": "scdb-21", "kind": "passive", "sp_cost": 20, "required_lb": 0,
         "effects": {"attribute": "Vo", "buff": .2, "probability": .3}},
        {"id": "scdb-31", "kind": "live", "sp_cost": 30, "required_lb": 2,
         "effects": {"id": "scdb-31", "components": {"Vo": 4.5}},
         "event": "event-unspecified" if event else None},
    ]
    return {"card_id": cid, "name": "测试", "idol": "偶像", "kind": "S", "status": "PARTIAL",
            "source_url": "https://example.invalid/card", "panels": nodes,
            "panel_topology": {"roots": ["scdb-11"], "edges": [["scdb-11", "scdb-21"],
                                ["scdb-21", "scdb-31"]], "complete": True,
                                "source_url": "https://example.invalid/topology", "locator": "test",
                                "confidence": "TEMPLATE_SUBSET_DATASITE_GEOMETRY"}}


def raw(cid="2049990010"):
    return {"enzaId": cid, "cardPanels": [
        {"panelSlot": 11, "skillTitle": "Vocal上限UP", "skillDesc": "Vocal上限＋25"},
        {"panelSlot": 21, "skillTitle": "Vocal20％UP", "skillDesc": "[条件:2ターン以降][確率:30％]"},
        {"panelSlot": 31, "skillTitle": "高倍率", "skillDesc": "Vocal4.5倍アピール"},
    ]}


def catalog(*, lb=2, event=False, other=False):
    ids = ["2049990010", "2049990020"] if other else ["2049990010"]
    return load_sp_catalog({"P": None, "S": ids, "guest": None},
                           {cid: {"level": 25, "limit_break": lb} for cid in ids},
                           cards={cid: card(cid, event=event) for cid in ids},
                           raw_cards={cid: raw(cid) for cid in ids})


SLOTS = {"root": {"slot": [-2, 1], "petal": "left", "cost": 10, "state": "open"}}
KNOWN = {"root": {"name": "Vocal上限UP", "kind": "cap_up", "value": 25,
                   "attributes": ["Vo"], "cost": 10, "state": "learnable"}}


class SPCatalogTests(unittest.TestCase):
    def test_complete_route_reserves_invisible_high_skill(self):
        result = bind_sp_catalog(catalog(), SLOTS, KNOWN)
        self.assertEqual(result["bindings"], {"left": "2049990010"})
        route = result["routes"][0]
        self.assertEqual(route["cost"], 60)
        self.assertEqual(route["multiplier"], 4.5)
        self.assertEqual(route["path"][0], "root")
        self.assertTrue(route["planning_only"])
        self.assertEqual(list(result["screen_nodes"]), ["root"])
        self.assertEqual(result["graph"][route["node_id"]]["requires"], tuple(route["path"][:-1]))

    def test_offline_route_has_no_screen_target(self):
        result = catalog()
        self.assertEqual(result["offline_routes"][0]["cost"], 60)
        self.assertTrue(all(k.startswith("catalog:") for k in result["offline_routes"][0]["path"]))

    def test_unknown_or_insufficient_lb_excludes_high_skill(self):
        for lb in (None, 1):
            self.assertFalse(bind_sp_catalog(catalog(lb=lb), SLOTS, KNOWN)["routes"])

    def test_support_order_does_not_bind_identical_root(self):
        result = bind_sp_catalog(catalog(other=True), SLOTS, KNOWN)
        self.assertFalse(result["bindings"])
        self.assertFalse(result["screen_map"])

    def test_known_petal_disambiguates_root_without_new_coordinates(self):
        result = bind_sp_catalog(catalog(other=True), SLOTS, KNOWN,
                                 petal_cards={"left": "2049990020"})
        self.assertEqual(result["screen_map"], {"catalog:2049990020:scdb-11": "root"})
        self.assertEqual(set(result["screen_nodes"]), {"root"})

    def test_learning_removes_consumed_cost(self):
        slots = {"root": {**SLOTS["root"], "state": "learned", "cost": 0}}
        known = {"root": {**KNOWN["root"], "state": "learned"}}
        result = bind_sp_catalog(catalog(), slots, known)
        self.assertEqual(result["routes"][0]["cost"], 50)
        self.assertNotIn("root", result["routes"][0]["path"])

    def test_unknown_event_is_not_planned_until_screen_proves_open(self):
        c = catalog(event=True)
        self.assertFalse(bind_sp_catalog(c, SLOTS, KNOWN)["routes"])
        slots = {**SLOTS, "live": {"petal": "left", "cost": 30, "state": "open"}}
        known = {**KNOWN, "live": {"name": "高倍率", "kind": "live_skill", "multiplier": 4.5,
                                   "value": 4.5, "attributes": ["Vo"], "cost": 30,
                                   "state": "learnable"}}
        result = bind_sp_catalog(c, slots, known)
        self.assertEqual(result["routes"][0]["node_id"], "live")
        self.assertEqual(result["routes"][0]["cost"], 60)

    def test_duplicate_labels_do_not_alias_screen_slots(self):
        slots = {**SLOTS, "root2": dict(SLOTS["root"])}
        known = {**KNOWN, "root2": dict(KNOWN["root"])}
        result = bind_sp_catalog(catalog(), slots, known)
        self.assertFalse(result["screen_map"])

    def test_inconsistent_info_and_label_do_not_bind(self):
        known = {"root": {**KNOWN["root"], "cost": 20}}
        self.assertFalse(bind_sp_catalog(catalog(), SLOTS, known)["bindings"])

    def test_actual_unique_node_cost_overrides_local_cost(self):
        slots = {"root": {**SLOTS["root"], "cost": 20}}
        known = {"root": {**KNOWN["root"], "cost": 20}}
        result = bind_sp_catalog(catalog(), slots, known)
        self.assertEqual(result["screen_nodes"]["root"]["cost"], 20)
        self.assertEqual(result["routes"][0]["remaining_cost"], 70)

    def test_contested_costs_do_not_enter_live_graph(self):
        c = catalog()
        c["cards"]["2049990010"]["costs_known"] = False
        result = bind_sp_catalog(c, SLOTS, KNOWN)
        self.assertFalse(result["graph"])

    def test_library_inputs_are_not_mutated(self):
        c, slots, known = catalog(), copy.deepcopy(SLOTS), copy.deepcopy(KNOWN)
        before = copy.deepcopy((c, slots, known))
        bind_sp_catalog(c, slots, known)
        self.assertEqual((c, slots, known), before)

    def test_compound_appeal_does_not_become_single_attribute_multiplier(self):
        raw_card = card()
        raw_card["panels"][-1]["effects"]["components"] = {"Vo": 4.5, "Vi": 4.5}
        c = load_sp_catalog({"S": ["2049990010"]}, {"2049990010": {"limit_break": 2}},
                            cards={"2049990010": raw_card}, raw_cards={"2049990010": raw()})
        node = c["cards"]["2049990010"]["nodes"]["scdb-31"]
        self.assertIsNone(node["kind"])
        self.assertIsNone(node["multiplier"])
        self.assertFalse(c["offline_routes"])


if __name__ == "__main__":
    unittest.main()
