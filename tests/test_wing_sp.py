"""SP panel planner and event-choice rules (synthetic panel modelled on the unit7 TE run, 2026-10-04)."""
from __future__ import annotations

import unittest

from agent.core import wing_events as ev
from agent.core import wing_sp as sp

N = sp.SPNode


def panel(**learned):
    """Hex panel sketch: centre SP20 ring -> SP30 -> SP40 -> branch-end SP50 support-card Live skill."""
    nodes = [
        N("p_vo_up_25", "cap_up", 20, 25, ("Vo",)),
        N("p_passive_vo", "passive", 20, 5, ("Vo",)),
        N("p_ring30", "passive", 30, 2, ()),
        N("p_ring40", "cap_up", 40, 50, ("Vo",), requires=("p_vo_up_25",)),
        N("s_murmurmaid", "live_skill", 50, 4.0, ("Vo",), requires=("p_vo_up_25", "p_ring40")),
        N("s_dive_in", "live_skill", 50, 3.5, ("Vo",)),
        N("s_dive_in_plus", "live_skill", 50, 5.0, ("Vo",), requires=("s_dive_in",), upgrades="s_dive_in"),
        N("s_da_skill", "live_skill", 30, 3.0, ("Da",)),
        N("p_vo_da_100", "cap_up", 50, 100, ("Vo", "Da")),
        N("p_locked", "live_skill", 10, 9.0, ("Vo",), available=False),
    ]
    return [n if n.id not in learned else N(**{**n.__dict__, "learned": True}) for n in nodes]


P_SKILL = sp.LiveSkill("p_hikari", "Vo", 3.0)


class SPPlannerTests(unittest.TestCase):
    def test_duplicate_live_bridge_costs_zero_without_forgetting(self):
        key = "complete-live-identity"
        held = [sp.LiveSkill("existing", "Vo", 2.5, key), *[sp.LiveSkill(f"held{i}", "Vo", 3) for i in range(3)]]
        nodes = [N("bridge", "live_skill", 30, 2.5, ("Vo",), skill_identity=key),
                 N("cap", "cap_up", 50, 100, ("Vo",), requires=("bridge",))]
        plan = sp.plan_sp(nodes, sp=50, primary="Vo", stat=400, cap=400, projected_stat=450, skills=held)
        self.assertEqual([(s.node_id, s.cost, s.forget) for s in plan.steps], [("bridge", 0, None), ("cap", 50, None)])
        self.assertEqual(plan.skills_after, tuple(held))

    def test_same_run_acquisition_shares_cost_and_survives_forgetting(self):
        key = "complete-live-identity"
        nodes = [N("a", "live_skill", 20, 2.5, ("Vo",), skill_identity=key),
                 N("b", "live_skill", 20, 2.5, ("Vo",), skill_identity=key),
                 N("cap_a", "cap_up", 50, 100, ("Vo",), requires=("a",)),
                 N("cap_b", "cap_up", 50, 100, ("Vo",), requires=("b",))]
        plan = sp.plan_sp(nodes, sp=120, primary="Vo", stat=400, cap=400, projected_stat=600)
        self.assertEqual(sum(s.cost for s in plan.steps if s.node_id in ("a", "b")), 20)
        self.assertEqual(len(plan.skills_after), 1)
        self.assertEqual(plan.sp_left, 0)
        forgotten = sp.plan_sp(nodes[:2] + nodes[3:], sp=50, primary="Vo", stat=400, cap=400,
                               projected_stat=450, mastered_skills=(key,))
        self.assertEqual(forgotten.skills_after, ())
        self.assertEqual(forgotten.spent, 50)

    def test_strongest_skill_first_upgrade_through_base(self) -> None:
        # 2026-10-05 tier planner: within a tier value per SP decides, so the cheaper base DIVE IN! (3.5/50 SP) is
        # bought as its own step and then upgraded (marginal 1.5) with an explicit forget of the base. The old greedy
        # planner bought the base as a "path" step of the upgrade; the learned skills and SP left are unchanged.
        plan = sp.plan_sp(panel(), sp=110, primary="Vo", stat=300, cap=500, skills=[P_SKILL])
        # 2026-10-15: base and 「+」 are equipped together; a free slot takes the 「+」.
        self.assertEqual([(s.node_id, s.category, s.forget) for s in plan.steps],
                         [("s_dive_in", "live_skill", None), ("s_dive_in_plus", "live_skill", None)])
        self.assertEqual(sorted(s.id for s in plan.skills_after), ["p_hikari", "s_dive_in", "s_dive_in_plus"])
        self.assertEqual(plan.sp_left, 10)

    def test_skills_first_with_path_cost(self) -> None:
        plan = sp.plan_sp(panel(s_dive_in=1, s_dive_in_plus=1), sp=110, primary="Vo", stat=300, cap=500,
                          skills=[P_SKILL])
        ids = [s.node_id for s in plan.steps]
        # murmurmaid (4×) via its path costs 110 -> exactly affordable; locked 9× node ignored
        self.assertEqual(ids, ["p_vo_up_25", "p_ring40", "s_murmurmaid"])
        self.assertEqual([s.category for s in plan.steps], ["path", "path", "live_skill"])
        self.assertEqual(plan.sp_left, 0)

    def test_greedy_falls_back_to_affordable_skill_then_passive(self) -> None:
        plan = sp.plan_sp(panel(), sp=75, primary="Vo", stat=300, cap=500, skills=[P_SKILL])
        self.assertEqual([s.node_id for s in plan.steps], ["s_dive_in", "p_passive_vo"])
        self.assertEqual(plan.sp_left, 5)

    def test_caps_only_near_the_cap(self) -> None:
        learned = dict(s_murmurmaid=1, s_dive_in=1, s_dive_in_plus=1, p_vo_up_25=1, p_ring40=1)
        far = sp.plan_sp(panel(**learned), sp=50, primary="Vo", stat=300, cap=575)
        self.assertNotIn("cap_up", [s.category for s in far.steps])
        near = sp.plan_sp(panel(**learned), sp=50, primary="Vo", stat=700, cap=778)
        self.assertEqual(near.steps[0].node_id, "p_vo_da_100")
        unread = sp.plan_sp(panel(**learned), sp=50, primary="Vo", stat=None, cap=778)
        self.assertIn("cap_rule_disabled_stat_or_cap_unread", unread.reasons)
        self.assertNotIn("cap_up", [s.category for s in unread.steps])

    def test_plus_skill_forgets_the_weakest_not_its_base(self) -> None:
        skills = [P_SKILL, sp.LiveSkill("s_dive_in", "Vo", 3.5), sp.LiveSkill("x1", "Vo", 2.5),
                  sp.LiveSkill("x2", "Da", 1.0)]
        nodes = [N("s_dive_in", "live_skill", 50, 3.5, ("Vo",), learned=True),
                 N("s_dive_in_plus", "live_skill", 50, 5.0, ("Vo",), requires=("s_dive_in",), upgrades="s_dive_in")]
        plan = sp.plan_sp(nodes, sp=50, primary="Vo", stat=300, cap=500, skills=skills)
        # 2026-10-15: a full set forgets the weakest skill (the off-attribute 1x), the same rule as the forget page
        self.assertEqual((plan.steps[0].node_id, plan.steps[0].forget), ("s_dive_in_plus", "x2"))
        self.assertEqual(sorted(s.id for s in plan.skills_after), ["p_hikari", "s_dive_in", "s_dive_in_plus", "x1"])

    def test_full_slots_forget_weakest_offattr(self) -> None:
        skills = [P_SKILL, sp.LiveSkill("vo_2", "Vo", 2.0), sp.LiveSkill("da_1", "Da", 1.0),
                  sp.LiveSkill("vi_1", "Vi", 1.0)]
        plan = sp.plan_sp([N("s_dive_in", "live_skill", 50, 3.5, ("Vo",))], sp=50, primary="Vo", stat=300,
                          cap=500, skills=skills)
        self.assertEqual(plan.steps[0].forget, "da_1")
        strong = [sp.LiveSkill(f"vo_{i}", "Vo", 4.0) for i in range(4)]
        none = sp.plan_sp([N("s_dive_in", "live_skill", 50, 3.5, ("Vo",))], sp=50, primary="Vo", stat=300,
                          cap=500, skills=strong)
        self.assertEqual(none.steps, ())

    def test_offattr_skills_skipped_by_default(self) -> None:
        nodes = [N("s_da_skill", "live_skill", 30, 3.0, ("Da",))]
        self.assertEqual(sp.plan_sp(nodes, sp=30, primary="Vo", stat=1, cap=500).steps, ())
        allowed = sp.plan_sp(nodes, sp=30, primary="Vo", stat=1, cap=500, params={"allow_offattr_skills": True})
        self.assertEqual(allowed.steps[0].node_id, "s_da_skill")

    def test_no_spend_after_wing_and_unknown_sp(self) -> None:
        won = sp.plan_sp(panel(), sp=428, primary="Vo", stat=1273, cap=1300, wing_won=True)
        self.assertEqual((won.steps, won.sp_left, won.reasons), ((), 428, ("wing_won_no_spend",)))
        self.assertEqual(sp.plan_sp(panel(), sp=None, primary="Vo", stat=1, cap=1).reasons, ("sp_unread",))

    def test_reserve_and_validation(self) -> None:
        plan = sp.plan_sp(panel(), sp=110, primary="Vo", stat=300, cap=500, params={"reserve_sp": 40})
        self.assertLessEqual(plan.spent, 70)
        with self.assertRaises(ValueError):
            N("x", "gold", 10)
        with self.assertRaises(ValueError):
            sp.plan_sp([N("a", "passive", 1), N("a", "passive", 2)], sp=5, primary="Vo", stat=1, cap=1)

    def test_deterministic(self) -> None:
        a = sp.plan_sp(panel(), sp=200, primary="Vo", stat=480, cap=500, skills=[P_SKILL])
        b = sp.plan_sp(list(reversed(panel())), sp=200, primary="Vo", stat=480, cap=500, skills=[P_SKILL])
        self.assertEqual(a.to_dict(), b.to_dict())

    def test_recursive_path_uses_actual_discounted_cost_once(self) -> None:
        nodes = [N("inner", "passive", 10, 0),
                 N("bridge", "passive", 10, 0, requires=("inner", "inner")),
                 N("live", "live_skill", 30, 5, ("Vo",), requires=("bridge", "inner"))]
        plan = sp.plan_sp(nodes, sp=50, primary="Vo", stat=300, cap=500)
        self.assertEqual([(s.node_id, s.cost) for s in plan.steps],
                         [("inner", 10), ("bridge", 10), ("live", 30)])
        self.assertEqual(plan.sp_left, 0)
        short = sp.plan_sp(nodes, sp=49, primary="Vo", stat=300, cap=500)
        self.assertEqual(short.steps, ())

    def test_unknown_or_cyclic_ancestor_is_not_a_route(self) -> None:
        for requires in (("missing",), ("live",)):
            nodes = [N("bridge", "passive", 10, 0, requires=requires),
                     N("live", "live_skill", 30, 5, ("Vo",), requires=("bridge",))]
            plan = sp.plan_sp(nodes, sp=100, primary="Vo", stat=300, cap=500)
            self.assertEqual(plan.steps, ())

    def test_path_live_skill_must_fit_the_four_slots(self) -> None:
        held = [sp.LiveSkill(f"held{i}", "Vo", 4) for i in range(4)]
        nodes = [N("base", "live_skill", 10, 1, ("Vo",)),
                 N("live", "live_skill", 30, 5, ("Vo",), requires=("base",), upgrades="base"),
                 N("cap", "cap_up", 30, 50, ("Vo",), requires=("base",))]
        plan = sp.plan_sp(nodes, sp=100, primary="Vo", stat=450, cap=500, skills=held)
        self.assertEqual(plan.steps, ())
        self.assertEqual(plan.skills_after, tuple(held))


class SPTierTests(unittest.TestCase):
    """User tier order: P card skills/caps > main S-card Live skills > caps by overflow > other > passive."""

    NODES = [
        N("p_cap_50", "cap_up", 30, 50, ("Vo",), source="p_card"),
        N("p_skill", "live_skill", 60, 3.0, ("Vo",), source="p_card"),
        N("s1_skill", "live_skill", 50, 4.0, ("Vo",), source="s_main"),
        N("s2_cap_100", "cap_up", 20, 100, ("Vo",), source="s_main"),
        N("s3_skill", "live_skill", 20, 3.5, ("Vo",), source="s_other"),
        N("pass_vo", "passive", 10, 5, ("Vo",), source="s_main"),
    ]

    def plan(self, budget, **kw):
        kw.setdefault("stat", 450)
        kw.setdefault("cap", 500)
        return sp.plan_sp(self.NODES, sp=budget, primary="Vo", **kw)

    def test_tier_order_beats_value_per_sp(self) -> None:
        plan = self.plan(300, projected_stat=700, params={"caps_first_near_cap": False})
        self.assertEqual([(s.node_id, s.tier) for s in plan.steps],
                         [("p_cap_50", "p_card"), ("p_skill", "p_card"), ("s1_skill", "main_skill"),
                          ("s2_cap_100", "cap"), ("s3_skill", "other_skill"), ("pass_vo", "passive")])

    def test_value_per_sp_inside_a_tier(self) -> None:
        # P cap: 50 useful / 30 SP = 1.67 per SP > P skill 3.0 / 60 = 0.05 -> cap first inside tier 1
        self.assertEqual(self.plan(90, projected_stat=700, params={"caps_first_near_cap": False}).steps[0].node_id,
                         "p_cap_50")
        # projection below the cap -> the P cap is worthless, the P skill goes first
        first = self.plan(90, projected_stat=480).steps
        self.assertEqual(first[0].node_id, "p_skill")
        self.assertNotIn("p_cap_50", [s.node_id for s in first])

    def test_cap_valued_by_projected_overflow(self) -> None:
        nodes = [N("cap_a", "cap_up", 20, 100, ("Vo",)), N("cap_b", "cap_up", 20, 50, ("Vo",))]
        partial = sp.plan_sp(nodes, sp=40, primary="Vo", stat=450, cap=500, projected_stat=540)
        # only 40 points would overflow: cap_b (40/20) ties cap_a (40/20) on value per SP; id breaks the tie,
        # after which the projection is covered and the second cap is worth nothing
        self.assertEqual([s.node_id for s in partial.steps], ["cap_a"])
        none = sp.plan_sp(nodes, sp=40, primary="Vo", stat=300, cap=500, projected_stat=480)
        self.assertEqual(none.steps, ())
        legacy = sp.plan_sp(nodes, sp=40, primary="Vo", stat=450, cap=500)        # no projection: cap_margin gate
        # legacy gate (no projection): cap_a lifts the cap to 600, 150 above the stat > cap_margin 100 -> stop
        self.assertEqual([s.node_id for s in legacy.steps], ["cap_a"])

    def test_never_after_wing_and_source_validation(self) -> None:
        won = self.plan(300, projected_stat=700, wing_won=True)
        self.assertEqual((won.steps, won.sp_left), ((), 300))
        with self.assertRaises(ValueError):
            N("x", "passive", 1, source="guest")


class EventChoiceTests(unittest.TestCase):
    OPTS = (ev.EventOption("A"), ev.EventOption("B"), ev.EventOption("C"))

    def test_promise_accepted(self) -> None:
        opts = (ev.EventOption("断る"), ev.EventOption("二週間後ボーカルレッスン", promise=True))
        self.assertEqual(ev.choose_event_option(opts).index, 1)
        self.assertEqual(ev.choose_event_option(opts, accept_promises=False).rule, "config_default")
        two = (ev.EventOption("a", True), ev.EventOption("b", True))
        self.assertIsNone(ev.choose_event_option(two).index)

    def test_table_then_config_default(self) -> None:
        self.assertEqual(ev.choose_event_option(self.OPTS, event_key="pass_audition",
                                                choice_table={"pass_audition": 2}).index, 2)
        d = ev.choose_event_option(self.OPTS, event_key="unknown", choice_table={"pass_audition": 2}, default_index=1)
        self.assertEqual((d.index, d.rule), (1, "config_default"))
        self.assertEqual(ev.choose_event_option(self.OPTS).index, 0)

    def test_out_of_range_stops(self) -> None:
        self.assertIsNone(ev.choose_event_option(self.OPTS[:2], default_index=2).index)
        self.assertIsNone(ev.choose_event_option((), default_index=0).index)


if __name__ == "__main__":
    unittest.main()


class CapLineTests(unittest.TestCase):
    def test_line_to_a_deep_cap_beats_a_lone_small_cap(self):
        """2026-10-14 user: finish a line to its +100 instead of learning the +20/+25 nodes next to the centre."""
        nodes = [sp.SPNode("lone", "cap_up", 20, 25.0, ("Vo",)),
                 sp.SPNode("step", "cap_up", 20, 25.0, ("Vo",)),
                 sp.SPNode("deep", "cap_up", 50, 100.0, ("Vo",), requires=("step",))]
        plan = sp.plan_sp(nodes, sp=70, primary="Vo", stat=950, cap=1000, params={"lookahead": False})
        self.assertEqual([s.node_id for s in plan.steps][:2], ["step", "deep"])

    def test_affordable_strong_live_skill_beats_near_cap_caps(self):
        """Live a9 (S4, 8 weeks left): 85 SP, the 5x 「にゃんにゃんにゃ～ん♡+」 at 50 SP and Vo 16 below its cap; two caps
        were bought first and the 5x skill was left unaffordable."""
        nodes = [sp.SPNode("nyan_plus", "live_skill", 50, 5.0, ("Vo",), source="s_main"),
                 sp.SPNode("cap_a", "cap_up", 20, 25.0, ("Vo",)), sp.SPNode("cap_b", "cap_up", 40, 25.0, ("Vo",))]
        skills = [sp.LiveSkill("nyan", "Vo", 3.5), sp.LiveSkill("hoppop", "Vo", 4.0), sp.LiveSkill("iv", "Vo", 2.5),
                  sp.LiveSkill("p", "Vo", 3.0)]
        plan = sp.plan_sp(nodes, sp=85, primary="Vo", stat=1185, cap=1201, projected_stat=1195,
                          skills=skills, future_sp=218)
        self.assertEqual(plan.steps[0].node_id, "nyan_plus")
        # A forecasted overflow uses the same chooser: buy the small cap, then the skill, not another cap.
        growing = sp.plan_sp(nodes, sp=85, primary="Vo", stat=1185, cap=1201, projected_stat=1205,
                             skills=skills, future_sp=218)
        self.assertEqual([v.node_id for v in growing.steps], ["cap_a", "nyan_plus"])

    def test_run_growth_values_caps_before_passives(self):
        """Live wing12 (S3, 5 weeks left, before an audition, 55 SP, Vo 904): 「Vocal7%UP」 (40 SP) was bought over a
        40 SP Vo cap because the audition adds no growth. The run's later growth needs the cap."""
        nodes = [sp.SPNode("vo7", "passive", 40, 7.0, ("Vo",)), sp.SPNode("cap", "cap_up", 40, 50.0, ("Vo",))]
        old = sp.plan_sp(nodes, sp=55, primary="Vo", stat=904, cap=920, projected_stat=904, future_sp=120)
        self.assertEqual([v.node_id for v in old.steps], ["vo7"])
        new = sp.plan_sp(nodes, sp=55, primary="Vo", stat=904, cap=920, projected_stat=904, future_sp=120,
                         final_stat=1300)
        self.assertEqual([v.node_id for v in new.steps], ["cap"])
        # an unaffordable needed cap keeps the SP rather than letting the passive take it
        dear = [nodes[0], sp.SPNode("cap", "cap_up", 60, 50.0, ("Vo",))]
        kept = sp.plan_sp(dear, sp=55, primary="Vo", stat=904, cap=920, projected_stat=904, future_sp=120,
                          final_stat=1300)
        self.assertEqual((kept.steps, kept.sp_left), ((), 55))
        # once the growth fits under the cap the passive is bought
        covered = sp.plan_sp(nodes, sp=55, primary="Vo", stat=904, cap=1400, projected_stat=904, future_sp=120,
                             final_stat=1300)
        self.assertEqual([v.node_id for v in covered.steps], ["vo7"])

    def test_run_growth_caps_wait_for_a_pending_live_target(self):
        nodes = [sp.SPNode("path", "cap_up", 30, 25.0, ("Vo",)),
                 sp.SPNode("live", "live_skill", 60, 5.0, ("Vo",), source="s_main", requires=("path",)),
                 sp.SPNode("cap", "cap_up", 40, 50.0, ("Vo",))]
        # 80 SP: the 5x line costs 90, so the side cap (needed only later) does not delay it
        wait = sp.plan_sp(nodes, sp=80, primary="Vo", stat=400, cap=520, projected_stat=440, future_sp=200,
                          final_stat=900)
        self.assertEqual((wait.steps, wait.target), ((), ("live",)))
        # an imminent overflow still buys the cap first
        urgent = sp.plan_sp(nodes, sp=80, primary="Vo", stat=500, cap=520, projected_stat=540, future_sp=200,
                            final_stat=900)
        self.assertEqual(urgent.steps[0].node_id, "cap")
        # with the line affordable the skill comes first, then the cap from what is left
        both = sp.plan_sp(nodes, sp=130, primary="Vo", stat=400, cap=520, projected_stat=440, future_sp=200,
                          final_stat=900)
        self.assertEqual([v.node_id for v in both.steps], ["path", "live", "cap"])

    def test_final_does_not_buy_caps_without_growth(self):
        nodes = [sp.SPNode("live", "live_skill", 50, 5, ("Vo",)),
                 sp.SPNode("cap", "cap_up", 50, 100, ("Vo",))]
        plan = sp.plan_sp(nodes, sp=50, primary="Vo", stat=500, cap=500, future_sp=0, last_chance=True)
        self.assertEqual([v.node_id for v in plan.steps], ["live"])
