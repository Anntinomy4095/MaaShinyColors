"""W.I.N.G. SP lookahead: panel topology, deep-node knowledge and the run-plan planner.

User feedback after the live run of 2026-10-10 (風野灯織, Vo, Middle): the greedy planner bought only caps and a passive
whose condition can never be met, because only open nodes were ever read. Offline, zero input."""
from __future__ import annotations

import unittest

from agent.core import produce_config as pc
from agent.core import wing_nav as nav
from agent.core import wing_skill_panel as wsp
from agent.core import wing_sp
from tests.test_wing_sp_learning import label_box

S = wing_sp.SPNode
INITIAL = [wing_sp.LiveSkill("ボーカルアピールI", "Vo", 1.0), wing_sp.LiveSkill("ダンスアピールI", "Da", 1.0),
           wing_sp.LiveSkill("ビジュアルアピールI", "Vi", 1.0), wing_sp.LiveSkill("メンタルキュアI", None, 0.0)]


def slots_of(states: dict) -> dict:
    """read_panel-like slots: {intent: {"slot", "cost", "state", "petal"}} with cost = 10 x hex distance."""
    out = {}
    for slot, state in states.items():
        out[wsp.slot_intent(slot)] = {"slot": list(slot), "state": state, "petal": wsp.petal_of(slot),
                                      "cost": 0 if state == "learned" else wsp.ring_cost(slot)}
    return out


class TopologyTests(unittest.TestCase):
    def test_ring_cost_matches_the_recorded_labels(self):
        # third demo #491: inner (1,-2) SP:20, (1,-4) SP:30, (2,-5)/(3,-4) SP:40, (3,-6)/(4,-5) SP:50; (5,-2) SP:50
        for slot, cost in (((1, -2), 20), ((-2, 1), 20), ((1, 4), 20), ((1, -4), 30), ((2, -3), 30),
                           ((2, -5), 40), ((3, -4), 40), ((3, -6), 50), ((4, -5), 50), ((5, -2), 50), ((-5, 4), 50)):
            self.assertEqual(wsp.ring_cost(slot), cost, slot)
        self.assertEqual(sorted(wsp.neighbours((1, -2))), [(0, -3), (0, -1), (1, -4), (1, 0), (2, -3), (2, -1)])
        self.assertTrue(all(wsp.hex_distance(s) == 2 for s in wsp.INNER_RING))

    def test_paths_run_outward_inside_one_petal(self):
        states = {(1, -2): "open", (1, -4): "unreachable", (2, -3): "unreachable", (2, -5): "unreachable",
                  (3, -4): "unreachable", (3, -6): "unreachable", (4, -5): "event", (-1, -2): "open",
                  (-1, -4): "unreachable"}
        g = wsp.panel_paths(slots_of(states))
        c = wsp.slot_intent
        self.assertEqual(g[c((1, -2))]["requires"], ())
        self.assertEqual(g[c((1, -4))]["requires"], (c((1, -2)),))
        self.assertEqual(g[c((2, -5))]["cost"], 20 + 30 + 40)
        self.assertEqual(len(g[c((3, -6))]["requires"]), 3)
        # deep gold (「event」) is a gold node not yet reachable (live 2026-10-14: c11_r03 opened after c10_r04)
        self.assertEqual(g[c((4, -5))]["requires"], (c((1, -2)), c((2, -3)), c((3, -4))))
        self.assertEqual(g[c((-1, -4))]["requires"], (c((-1, -2)),))   # top-left petal reaches only its own nodes
        self.assertTrue(all(r in g for e in g.values() for r in (e["requires"] or ())))

    def test_deep_gold_next_to_learned_stays_a_padlock_and_unknown_is_never_planned(self):
        states = {(1, -2): "learned", (1, -4): "event", (2, -3): "open", (3, -4): "unknown", (4, -5): "event"}
        g = wsp.panel_paths(slots_of(states))
        c = wsp.slot_intent
        self.assertTrue(g[c((1, -4))]["blocked"])               # gold next to a learned node yet still dark: padlock
        self.assertNotIn(c((3, -4)), g)
        self.assertNotIn(c((4, -5)), g)                         # only reachable through the unknown node

    def test_learned_nodes_open_their_neighbours_and_padlocks_block(self):
        states = {(1, -2): "learned", (1, -4): "open", (2, -3): "unreachable", (2, -5): "unreachable",
                  (3, -4): "unreachable"}
        g = wsp.panel_paths(slots_of(states))
        c = wsp.slot_intent
        # (2,-3) is next to the learned (1,-2) but still dark: a padlock (☆) node -> blocked, never a path step
        self.assertTrue(g[c((2, -3))]["blocked"])
        self.assertEqual(g[c((2, -5))]["requires"], (c((1, -4)),))
        self.assertEqual(g[c((3, -4))]["requires"], (c((1, -4)), c((2, -5))))   # around the padlock
        g2 = wsp.panel_paths(slots_of(states), blocked=[c((1, -4))])
        self.assertNotIn(c((2, -5)), g2)

    def test_passive_conditions(self):
        self.assertFalse(wsp.condition_met("最大メンタル3400以上メンタル49%以下"))
        self.assertFalse(wsp.condition_met("4ターン以降アンティーカ全員がライブに参加"))
        self.assertFalse(wsp.condition_met("黛冬優子がライブに参加している場合", "風野灯織"))
        self.assertTrue(wsp.condition_met("黛冬優子がライブに参加している場合", "黛冬優子"))
        # 育成の指針: an Active S idol performs too (the guest is not passed in)
        self.assertTrue(wsp.condition_met("月岡恋鐘がライブに参加している場合", "風野灯織", ["月岡恋鐘", "三峰結華"]))
        self.assertFalse(wsp.condition_met("月岡恋鐘がライブに参加している場合", "風野灯織", ["三峰結華"]))
        self.assertTrue(wsp.condition_met("2ターン以前"))
        self.assertIsNone(wsp.condition_met(None))
        info = wsp.classify_skill("Vocal&Visual75%UP", "[条件:最大メンタル3400以上メンタル49%以下][確率:30%][最大:2回]")
        self.assertEqual(info["condition"], "最大メンタル3400以上メンタル49%以下")


def line_panel():
    """One petal, two lines: a cap line (inner cap -> cap) and an appeal line (cheap 2.5x / passive bridge -> 5x)."""
    return [S("cap1", "cap_up", 20, 25.0, ("Vo",)), S("cap2", "cap_up", 30, 50.0, ("Vo",), requires=("cap1",)),
            S("live25", "live_skill", 30, 2.5, ("Vo",)), S("bridge", "passive", 30, 0.0),
            S("live5", "live_skill", 50, 5.0, ("Vo",), requires=("bridge",)),
            S("pas", "passive", 20, 0.1, ("Vo",))]


class LookaheadPlannerTests(unittest.TestCase):
    def plan(self, sp, future, nodes=None, stat=400, cap=600, skills=INITIAL, **kw):
        return wing_sp.plan_sp(nodes or line_panel(), sp=sp, primary="Vo", stat=stat, cap=cap, skills=skills,
                               future_sp=future, **kw)

    def test_whole_path_to_the_high_multiplier_skill(self):
        legacy = wing_sp.plan_sp(line_panel(), sp=80, primary="Vo", stat=400, cap=600, skills=INITIAL)
        self.assertEqual(legacy.steps[0].node_id, "live25")          # greedy per SP: the cheap 2.5x first
        plan = self.plan(80, 0)
        self.assertEqual([s.node_id for s in plan.steps][:2], ["bridge", "live5"])
        self.assertEqual(plan.steps[1].forget, "メンタルキュアI")     # recovery skill forgotten first
        self.assertEqual(plan.steps[0].category, "path")

    def test_recursive_shared_route_keeps_budget_for_deep_live(self):
        nodes = [S("inner", "passive", 10, 0),
                 S("bridge", "passive", 10, 0, requires=("inner",)),
                 S("deep", "live_skill", 40, 5, ("Vo",), requires=("bridge",)),
                 S("pas", "passive", 10, 0.1, ("Vo",))]
        waiting = self.plan(50, 20, nodes=nodes)
        self.assertEqual(waiting.steps, ())
        self.assertEqual((waiting.target, waiting.reserve), (("deep",), 46))
        ready = self.plan(60, 0, nodes=nodes)
        self.assertEqual([(s.node_id, s.cost) for s in ready.steps],
                         [("inner", 10), ("bridge", 10), ("deep", 40)])
        self.assertEqual(ready.sp_left, 0)

    def test_two_live_targets_pay_shared_ancestors_once(self):
        nodes = [S("inner", "passive", 10, 0),
                 S("bridge", "passive", 10, 0, requires=("inner",)),
                 S("one", "live_skill", 30, 5, ("Vo",), requires=("bridge",)),
                 S("two", "live_skill", 30, 4, ("Vo",), requires=("bridge", "inner"))]
        plan = self.plan(80, 0, nodes=nodes)
        self.assertEqual({s.node_id for s in plan.steps}, {"inner", "bridge", "one", "two"})
        self.assertEqual(len(plan.steps), 4)
        self.assertEqual(plan.sp_left, 0)

    def test_candidate_limit_does_not_drop_the_p_card_tier(self):
        nodes = [S("p", "live_skill", 40, 1.1, ("Vo",), source="p_card")]
        nodes += [S(f"s{i}", "live_skill", 40, 5, ("Vo",), source="s_main") for i in range(11)]
        plan = self.plan(40, 0, nodes=nodes)
        self.assertEqual([s.node_id for s in plan.steps], ["p"])

    def test_reserve_for_a_reachable_target_instead_of_low_value_nodes(self):
        plan = self.plan(60, 40, nodes=[n for n in line_panel() if n.id != "live25"])
        self.assertEqual(plan.steps, ())
        self.assertEqual((plan.target, plan.reserve), (("live5",), 80 - 28))
        self.assertIn("reserve_for_live_target", plan.reasons)
        legacy = wing_sp.plan_sp([n for n in line_panel() if n.id != "live25"], sp=60, primary="Vo", stat=400,
                                 cap=600, skills=INITIAL)
        self.assertEqual([s.node_id for s in legacy.steps], ["pas"])
        # surplus above the reserve still buys the passive
        rich = self.plan(110, 40, nodes=[n for n in line_panel() if n.id != "live25"])
        self.assertEqual([s.node_id for s in rich.steps], ["bridge", "live5", "pas"])

    def test_last_chance_spends_everything_useful(self):
        plan = self.plan(60, 0, nodes=[n for n in line_panel() if n.id != "live25"])
        self.assertEqual([s.node_id for s in plan.steps], ["pas"])
        self.assertEqual(plan.reserve, 0)

    def test_unreachable_target_does_not_block_spending(self):
        plan = self.plan(60, 5, nodes=[n for n in line_panel() if n.id != "live25"])   # 60 + 3 < 80
        self.assertEqual([s.node_id for s in plan.steps], ["pas"])

    def test_cap_line_near_the_cap_uses_the_reserve(self):
        plan = self.plan(60, 100, nodes=[n for n in line_panel() if n.id != "live25"], stat=590, cap=600)
        self.assertEqual([s.node_id for s in plan.steps], ["cap1", "cap2"])
        self.assertEqual(plan.target, ("live5",))
        far = self.plan(60, 40, nodes=[n for n in line_panel() if n.id != "live25"], stat=300, cap=600)
        self.assertNotIn("cap1", [s.node_id for s in far.steps])    # not near the cap: caps are worth 0

    def test_two_targets_fill_the_slots_and_respect_the_forget_rule(self):
        plan = self.plan(110, 0)
        self.assertEqual(sorted(s.node_id for s in plan.steps if s.category == "live_skill"), ["live25", "live5"])
        strong = [wing_sp.LiveSkill(f"s{i}", "Vo", 3.5) for i in range(4)]
        plan = self.plan(110, 0, skills=strong)
        self.assertEqual([s.node_id for s in plan.steps if s.category == "live_skill"], ["live5"])
        self.assertEqual(plan.steps[-1].forget if plan.steps[-1].node_id == "live5" else
                         next(s.forget for s in plan.steps if s.node_id == "live5"), "s0")

    def test_upgrade_line(self):
        nodes = [S("base", "live_skill", 30, 3.5, ("Vo",)),
                 S("base+", "live_skill", 50, 5.0, ("Vo",), requires=("base",), upgrades="base")]
        plan = self.plan(80, 0, nodes=nodes)
        # 2026-10-15: base and 「+」 both stay equipped; the 「+」 forgets the weakest other skill
        self.assertEqual([(s.node_id, s.forget) for s in plan.steps], [("base", "メンタルキュアI"), ("base+", "ダンスアピールI")])
        self.assertEqual(sum(1 for k in plan.skills_after if k.id.startswith("base")), 2)

    def test_p_card_skill_first(self):
        nodes = [S("p", "live_skill", 40, 3.0, ("Vi",), source="p_card"), S("s", "live_skill", 40, 3.5, ("Vo",))]
        three = INITIAL[:3] + [wing_sp.LiveSkill("x", "Vo", 3.0)]
        plan = self.plan(40, 0, nodes=nodes, skills=three)
        self.assertEqual(plan.steps[0].node_id, "p")


# ---- session integration -------------------------------------------------------------------------------------------
C = wsp.slot_intent
P_LINE = {(1, -2): ("Vocal上限UP", "cap_up", ("Vo",), 50.0),
          (1, -4): ("Vocal上限UP", "cap_up", ("Vo",), 50.0),
          (2, -3): ("Vocal&Dance&Visual100%UP", "passive", ("Vo", "Da", "Vi"), 0.3),
          (2, -5): ("あなたと、月の満ちる頃+", "live_skill", ("Vo",), 4.5),
          (3, -4): ("Vocal上限UP", "cap_up", ("Vo",), 25.0),
          (3, -6): ("Visual上限UP", "cap_up", ("Vi",), 50.0),
          (4, -5): ("Dance上限UP", "cap_up", ("Da",), 50.0)}
OTHER_INNER = {s: ("Dance上限UP", "cap_up", ("Da",), 25.0) for s in wsp.INNER_RING if s != (1, -2)}
INFO = {**P_LINE, **OTHER_INNER}


def frame(sp, learned=(), selected=None, state=None):
    nodes = []
    for slot in wsp.SLOTS:
        if slot in learned:
            cost, st = 0, "learned"
        elif slot in wsp.INNER_RING or any(n in learned for n in wsp.neighbours(slot)) and slot in INFO:
            cost, st = wsp.ring_cost(slot), "open"
        elif slot in INFO:
            cost, st = wsp.ring_cost(slot), "unreachable"
        else:
            cost, st = wsp.ring_cost(slot), "unknown"   # filler: colour not read, never planned
        nodes.append({"cost": cost, "state": st, "label": label_box(slot)})
    r = {"sp": sp, "panel": wsp.read_panel(nodes), "skill_info": None, "skill_text": None}
    if selected is not None:
        name, kind, attrs, value = INFO[selected]
        desc = "[条件:最大メンタル3400以上メンタル49%以下][確率:30%]" if kind == "passive" else ""
        r["skill_info"] = {"cost": 0 if selected in learned else wsp.ring_cost(selected),
                           "state": state or ("learned" if selected in learned else "locked")}
        r["skill_text"] = {"name": name, "kind": kind, "attributes": attrs, "value": value,
                           "multiplier": value if kind == "live_skill" else None,
                           "condition": wsp.classify_skill(name, desc).get("condition")}
    return r


def audition_week(state, strategy, goal, params):
    return {"kind": "audition", "audition_id": "s1_10k", "pre_actions": ["spend_sp"], "plan": {}}


def runtime():
    config = pc.ProduceConfig.from_dict(pc.default_config("true_end"))
    rt = nav.WingNavRuntime(nav.RunMemory.from_produce_config(config), hooks={"week": audition_week},
                            options=nav.RunOptions(sp_mode="auto", sp_small_view=False))   # default-view flow
    rt.begin()
    return rt


WEEK = {"season": 1, "weeks_left": 4, "stats": {"Vo": 300, "Da": 100, "Vi": 100, "Me": 100}, "fans": 1000,
        "rooms": [], "selected_room": None}
OWNED = {"owned": {"Vo": (300, 520), "Da": (100, 520), "Vi": (100, 520), "Me": (100, 500)}}


class SessionLookaheadTests(unittest.TestCase):
    def open(self, rt, key, sp):
        rt.memory.auditions_seen = {"key": list(key), "pages": [], "trend": None, "done": True}
        go = rt.decide("Schedule", {**WEEK, "season": key[0], "weeks_left": key[1], "sp": sp})
        self.assertEqual(go.reason, "spend_sp_auto")
        rt.decide("Hub")
        d = rt.decide("Review", frame(sp))
        if d.intent == "skill_owned":
            rt.decide("Owned", OWNED)
            rt.decide("Hub")
            d = rt.decide("Review", frame(sp))
        return d

    def inspect_all(self, rt, d, sp, learned=()):
        tapped = []
        while d.reason == "sp_inspect":
            slot = tuple(d.detail["slot"])
            tapped.append(slot)
            st = "learnable" if slot in wsp.INNER_RING else "locked"
            d = rt.decide("Review", frame(sp, learned, selected=slot, state=st))
        return d, tapped

    def test_deep_nodes_read_once_per_run_and_sp_kept_for_the_target(self):
        rt = runtime()
        d = self.open(rt, (1, 4), 86)
        d, tapped = self.inspect_all(rt, d, 86)
        self.assertEqual(set(tapped), set(INFO))                       # inner ring + the whole P petal
        self.assertEqual(tapped[:6], sorted(tapped[:6], key=wsp.ring_cost))   # cheapest path first
        # 4.5x needs 20 + 30 + 40 = 90 SP: nothing else is worth buying (caps far from the cap, passive condition
        # unmet), so the session ends and the SP is kept for it
        self.assertEqual((d.intent, d.reason), ("page_return", "sp_session_done"))
        plan = rt.memory.sp_run["plan"]
        self.assertEqual([t["intent"] for t in plan["target"]], [C((2, -5))])
        self.assertEqual(rt.memory.sp_run["catalog"][C((2, -3))]["condition"], "最大メンタル3400以上メンタル49%以下")
        # the next week: nothing is re-read; with 106 SP the whole line is bought, inner node first
        d = self.open(rt, (1, 3), 106)
        self.assertEqual((d.intent, d.reason), (C((1, -2)), "sp_select"))
        self.assertEqual(d.detail["step"]["category"], "path")
        self.assertEqual(d.detail["sp_trace"]["plan"]["target"], [])

    def test_learned_cap_raises_the_session_cap(self):
        rt = runtime()
        d = self.open(rt, (1, 3), 106)
        d, _ = self.inspect_all(rt, d, 106)
        self.assertEqual(d.intent, C((1, -2)))
        d = rt.decide("Review", frame(106, selected=(1, -2), state="learnable"))
        self.assertEqual(d.intent, "skill_learn")
        rt.decide("LearnConfirm", {"learn_confirm": {"cost": 20, "sp_before": 106, "sp_after": 86,
                                                      "name": "Vocal上限UP"}})
        d = rt.decide("Review", frame(86, learned=((1, -2),), selected=(1, -2), state="learned"))
        self.assertEqual(rt.memory.sp_session["caps"]["Vo"], 570)
        self.assertEqual(rt.memory.sp_run["spent"], 20)
        self.assertEqual(d.reason, "sp_select")
        self.assertIn(d.intent, (C((1, -4)), C((2, -3))))

    def test_run_knowledge_survives_a_segment(self):
        rt = runtime()
        d = self.open(rt, (1, 4), 86)
        self.inspect_all(rt, d, 86)
        rt.memory.sp_done_keys.append(["HubFinal"])
        snap = rt.memory.snapshot()
        config = pc.ProduceConfig.from_dict(pc.default_config("true_end"))
        fresh = nav.RunMemory.from_produce_config(config)
        fresh.restore(snap)
        self.assertEqual(fresh.sp_run["catalog"], rt.memory.sp_run["catalog"])
        self.assertIn(["HubFinal"], fresh.sp_done_keys)

    def test_future_income(self):
        inc = nav._sp_future_income([1, 4], 86, 0)
        self.assertEqual((inc["week"], inc["weeks_left"]), (5, 28))
        self.assertEqual(inc["future"], int(86 / 4 * 28))
        self.assertEqual(nav._sp_future_income(["HubFinal"], 200, 100)["future"], 0)
        self.assertEqual(nav._sp_future_income([4, 1], 300, 0)["weeks_left"], 1)

    def test_stat_growth_left(self):
        run = {}
        self.assertIsNone(nav._stat_growth_left(["HubFinal"], 900, run))
        self.assertIsNone(nav._stat_growth_left([3, 4], None, run))
        # first session (week 5): the stat over the weeks played, bounded to STAT_RATE_MAX
        self.assertEqual(nav._stat_growth_left([1, 4], 200, run), int(50 * 27))
        self.assertEqual(run["growth_mark"], [5, 200])
        self.assertEqual(nav._stat_growth_left([1, 4], 400, {}), nav.STAT_RATE_MAX * 27)
        # two or more weeks later: the gain since the mark
        self.assertEqual(nav._stat_growth_left([3, 4], 904, run), int((904 - 200) / 16 * 11))
        self.assertEqual(run["growth_mark"], [5, 200])
        bad = {"growth_mark": ["x", 1]}
        nav._stat_growth_left([3, 4], 904, bad)
        self.assertEqual(bad["growth_mark"], [21, 904])


if __name__ == "__main__":
    unittest.main()
