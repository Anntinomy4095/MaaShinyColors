"""Affordable SP effects and threshold-sensitive weekly decisions; synthetic mechanics."""
from __future__ import annotations

from dataclasses import replace
import unittest

from agent.core import wing_policy, wing_score, wing_state
from agent.core.true_end.models import CardData, LiveSkill, Ownership, STATS, extract_card_features
from agent.core.true_end.sp_value import (PanelValueState, SPValueContext, best_panel_plan,
                                          marginal_sp_value)
from tests.test_wing_state import make_state


def live(key, multiplier=1):
    return {"id": key, "components": {"Vo": multiplier}}


def panel(key, kind, effects, cost=20, prerequisites=(), **kwargs):
    return {"id": key, "kind": kind, "effects": effects, "sp_cost": cost,
            "prerequisites_known": True, "prerequisites": list(prerequisites), **kwargs}


def feature(key="p", *, panels=(), lb=4, topology=None, kind="P"):
    card = CardData.from_dict({"card_id": key, "name": key, "idol": key, "kind": kind,
        "te_eligible": kind == "P", "status": "OK", "source_url": "synthetic://sp-value",
        "levels": {"80": dict.fromkeys(STATS, 100)}, "max_levels": {str(i): 80 for i in range(5)},
        "owned_live_skills": [live("support-own", 9)], "panels": list(panels),
        "panel_topology": topology})
    return extract_card_features(card, Ownership(key, lb, 80))


def snapshot(*, stats=None, caps=None, learned=()):
    return PanelValueState(stats or dict.fromkeys(STATS, 100), caps or dict.fromkeys(STATS, 500),
                           tuple(LiveSkill.from_dict(live(f"initial-{i}")) for i in range(4)),
                           learned_panel_ids=learned)


def pep(state):
    # Explicit synthetic utility scale: 20 PEP per equipped Vo multiplier, 100 per passive expected buff.
    return (state.stats["Vo"] + 20 * sum(s.components.get("Vo", 0) for s in state.live_skills)
            + 100 * sum(p["buff"] * p["probability"] for p in state.passives))


def context(*, cards=None, state=None, evaluator=pep, **kwargs):
    return SPValueContext(cards or (feature(panels=[
        panel("bridge", "stat", {"Vo": 0}, cost=30),
        panel("skill", "live", live("strong", 4), cost=20, prerequisites=("bridge",))]),),
        state or snapshot(), "Vo", evaluator, **kwargs)


class PanelMarginalTests(unittest.TestCase):
    def test_full_bridge_cost_required_and_excess_sp_has_no_value(self):
        ctx = context()
        self.assertEqual(marginal_sp_value(ctx, 35, 14)["marginal_pep"], 0)
        crossing = marginal_sp_value(ctx, 35, 15)
        self.assertEqual(crossing["marginal_pep"], 60)
        self.assertEqual(crossing["after"]["purchases"], ["p:bridge", "p:skill"])
        self.assertEqual(crossing["after"]["spent"], 50)
        self.assertEqual(marginal_sp_value(ctx, 50, 1000)["marginal_pep"], 0)

    def test_shared_predecessor_paid_once_across_two_targets(self):
        passive = {"attribute": "Vo", "buff": .2, "probability": 1, "max_activations": 1}
        card = feature(panels=[panel("root", "stat", {"Vo": 0}),
            panel("skill", "live", live("strong", 4), prerequisites=("root",)),
            panel("passive", "passive", passive, prerequisites=("root",))])
        plan = best_panel_plan(context(cards=(card,)), 60)
        self.assertEqual(plan.spent, 60)
        self.assertEqual(plan.purchases.count("p:root"), 1)
        self.assertEqual({*plan.purchases}, {"p:root", "p:skill", "p:passive"})
        self.assertEqual(plan.value_pep, 260)

    def test_already_purchased_predecessor_is_not_charged_again(self):
        ctx = context(state=snapshot(learned=("p:bridge",)))
        result = marginal_sp_value(ctx, 0, 20)
        self.assertEqual(result["marginal_pep"], 60)
        self.assertEqual(result["after"]["purchases"], ["p:skill"])
        self.assertEqual(result["after"]["spent"], 20)

    def test_guest_panel_works_but_guest_own_live_is_not_equipped(self):
        guest = feature("guest", kind="S", panels=[panel("skill", "live", live("guest-panel", 3))])
        plan = best_panel_plan(context(cards=(guest,)), 20)
        self.assertEqual(plan.purchases, ("guest:skill",))
        self.assertIn("guest-panel", [s.id for s in plan.state.live_skills])
        self.assertNotIn("support-own", [s.id for s in plan.state.live_skills])
        self.assertEqual(len(plan.state.live_skills), 4)

    def test_duplicate_skill_sources_do_not_create_fifth_or_extra_strength(self):
        cards = tuple(feature(k, panels=[panel("skill", "live", live("same", 4))]) for k in ("a", "b"))
        plan = best_panel_plan(context(cards=cards), 40)
        self.assertEqual(plan.spent, 20)
        self.assertEqual(len(plan.purchases), 1)
        self.assertEqual(sum(s.id == "same" for s in plan.state.live_skills), 1)

    def test_lb_event_unknown_path_and_unsupported_effects_do_not_buy(self):
        nodes = [panel("lb", "live", live("lb", 4), required_lb=4),
                 panel("event", "live", live("event", 4), event="E4"),
                 panel("unknown", "live", live("unknown", 4), prerequisites_known=False),
                 panel("unsupported", "live", {**live("special", 4), "unsupported": ["LINK"]})]
        ctx = context(cards=(feature(panels=nodes[:3], lb=0),))
        result = marginal_sp_value(ctx, 0, 1000)
        self.assertEqual(result["marginal_pep"], 0)
        self.assertFalse(result["after"]["purchases"])
        self.assertTrue(result["unknowns"])
        # 2026-10-14: an unparsed side effect (LINK) keeps the parsed 4x appeal
        self.assertEqual(best_panel_plan(context(cards=(feature(panels=nodes, lb=0),)), 1000).purchases, ("p:unsupported",))
        event = replace(ctx, events_completed=("p:E4",))
        self.assertEqual(best_panel_plan(event, 20).purchases, ("p:event",))

    def test_reviewed_diamond_uses_one_branch_not_both(self):
        card = feature(panels=[panel("root", "stat", {"Vo": 0}, 10),
            panel("left", "stat", {"Vo": 0}, 10), panel("right", "stat", {"Vo": 0}, 40),
            panel("skill", "live", live("strong", 4), 20)],
            topology={"roots": ["root"], "edges": [["root", "left"], ["root", "right"],
                ["left", "skill"], ["right", "skill"]], "complete": True,
                "source_url": "synthetic://topology", "locator": "reviewed edges"})
        plan = best_panel_plan(context(cards=(card,)), 40)
        self.assertEqual(plan.purchases, ("p:root", "p:left", "p:skill"))
        self.assertEqual(plan.spent, 40)

    def test_cap_needs_future_growth_and_never_restores_lost_overflow(self):
        cards = (feature(panels=[panel("cap", "cap", {"Vo": 100})]),)
        state = snapshot(stats=dict.fromkeys(STATS, 500))
        inert = context(cards=cards, state=state)
        self.assertEqual(marginal_sp_value(inert, 0, 20)["marginal_pep"], 0)
        # Exactly 40 future Vo gain is stipulated, after learning; no retroactive recovery of 100.
        growing = replace(inert, value_pep=lambda s: min(s.caps["Vo"], s.stats["Vo"] + 40))
        plan = best_panel_plan(growing, 20)
        self.assertEqual(plan.value_pep, 540)
        self.assertEqual(plan.state.stats["Vo"], 500)
        self.assertEqual(marginal_sp_value(growing, 0, 20)["marginal_pep"], 40)

    def test_stat_before_cap_is_clipped_in_path_order(self):
        nodes = [panel("stat", "stat", {"Vo": 100}),
                 panel("cap", "cap", {"Vo": 100}, prerequisites=("stat",))]
        ctx = context(cards=(feature(panels=nodes),), state=snapshot(stats=dict.fromkeys(STATS, 500)),
                      evaluator=lambda s: s.stats["Vo"] + s.caps["Vo"])
        plan = best_panel_plan(ctx, 40)
        self.assertEqual(plan.state.stats["Vo"], 500)
        self.assertEqual(plan.state.caps["Vo"], 600)

    def test_unaffordable_paths_cannot_generate_value(self):
        ctx = context(evaluator=lambda s: pep(s) + sum(s.caps.values()))
        plan = best_panel_plan(ctx, 19)
        self.assertFalse(plan.purchases)
        self.assertTrue(plan.search_complete)

    def test_state_budget_is_hard_and_truncation_is_explicit(self):
        calls = []
        def evaluate(s):
            calls.append(s)
            return pep(s)
        ctx = context(evaluator=evaluate, max_states=1)
        result = marginal_sp_value(ctx, 35, 100)
        self.assertEqual(len(calls), 2)  # one state at each budget, not an unbounded scan
        self.assertEqual(result["marginal_pep"], 0)
        self.assertFalse(result["search_complete"])
        self.assertEqual(result["after"]["evaluated_states"], 1)

    def test_larger_budget_retains_affordable_incumbent_under_truncation(self):
        nodes = [panel("expensive", "live", live("weak", 2), 50),
                 panel("good", "live", live("strong", 4), 20)]
        ctx = context(cards=(feature(panels=nodes),), max_states=2)
        result = marginal_sp_value(ctx, 20, 30)
        self.assertEqual(result["before"]["purchases"], ["p:good"])
        self.assertEqual(result["after"]["purchases"], ["p:good"])
        self.assertEqual(result["marginal_pep"], 0)
        self.assertFalse(result["search_complete"])

    def test_invalid_budget_value_and_profile_are_rejected(self):
        for value in (True, -1, 1.5, float("inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                best_panel_plan(context(), value)
        for value in (True, float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                best_panel_plan(context(evaluator=lambda s: value), 0)
        with self.assertRaises(ValueError):
            context(state=snapshot(learned=("other:skill",)))
        with self.assertRaises(ValueError):
            context(cards=(feature(), feature()))


class WeeklyMarginalTests(unittest.TestCase):
    def week(self, *, gain=8, sp=35, trouble=0):
        opts = (wing_state.ScheduleOption("talk", "work", label="トーク", preview={"Da": 1},
                    sp_gain=gain, supports=0, fan_gain=0, stamina_cost=0, trouble_rate=trouble),
                wing_state.ScheduleOption("lesson", "lesson", "Vo", preview={"Vo": 10},
                    sp_gain=0, supports=0, fan_gain=0, stamina_cost=0, trouble_rate=0))
        return make_state(season=4, week=8, stats=dict.fromkeys(STATS, 100), fans=500000, sp=sp,
                          stat_caps=dict.fromkeys(STATS, 500), options=opts, auditions=(), stamina=100)

    def score(self, state, ctx, **kwargs):
        return wing_score.score_week(state, "Vo", sp_context=ctx, **kwargs)

    def talk(self, result):
        return next(c for c in result.ranked if c.id == "talk")

    def test_only_perfect_crosses_path_threshold_not_mean_sp(self):
        result = self.score(self.week(), context())
        self.assertAlmostEqual(self.talk(result).terms["sp"], 6)
        reports = result.context["sp_valuation"]["rooms"]["talk"]
        self.assertEqual(reports["normal"]["after"]["purchases"], [])
        self.assertEqual(reports["perfect"]["after"]["spent"], 50)
        # Normal 35+8=43; Perfect 35+16=51; 0.1 * 60 = 6. Mean SP43.8 buys no skill.
        self.assertEqual(result.choice.id, "lesson")

    def test_failure_does_not_earn_sp_and_perfect_is_counted_once(self):
        # 失败率上限 20 %: the default 10 % (9c59d8e) would exclude this 10 % room before its SP is valued
        result = self.score(self.week(gain=15, trouble=10), context(), params={"trouble_rest_rate": 20})
        self.assertAlmostEqual(self.talk(result).terms["sp"], 54)
        failed = self.score(self.week(gain=15, trouble=100), context())
        self.assertNotIn("talk", [candidate.id for candidate in failed.ranked])

    def test_useless_and_finished_sp_targets_stop_biasing_work(self):
        empty = context(cards=(feature(),))
        state = self.week(gain=90)
        legacy = wing_score.score_week(state, "Vo")
        paths = self.score(state, empty)
        self.assertEqual(legacy.choice.id, "talk")
        self.assertEqual(paths.choice.id, "lesson")
        self.assertEqual(self.talk(paths).terms["sp"], 0)
        finished = self.score(self.week(gain=90, sp=50), context())
        self.assertEqual(self.talk(finished).terms["sp"], 0)

    def test_full_path_crossing_changes_choice_and_fixed_sp_weight_is_not_added(self):
        result = self.score(self.week(gain=15), context(), params={"w_sp": 2})
        self.assertEqual(result.choice.id, "talk")
        self.assertEqual(self.talk(result).terms["sp"], 60)
        self.assertEqual(self.score(self.week(gain=15), context(), params={"w_sp": .3}).choice.id, "talk")

    def test_unknown_sp_or_caps_never_fall_back_to_fixed_sp_credit(self):
        for state, reason in ((replace(self.week(), sp=None), "sp_unread"),
                              (replace(self.week(), stat_caps=None), "sp_value_caps_unread")):
            with self.subTest(reason=reason):
                c = self.talk(self.score(state, context()))
                self.assertEqual(c.terms["sp"], 0)
                self.assertIn(reason, c.unknown)

    def test_stale_snapshot_and_primary_mismatch_are_rejected(self):
        with self.assertRaises(ValueError):
            self.score(replace(self.week(), stats={**self.week().stats, "Vo": 101}), context())
        with self.assertRaises(ValueError):
            self.score(self.week(), replace(context(), primary="Da"))

    def test_truncated_search_is_visible_in_candidate_and_json(self):
        result = self.score(self.week(gain=15), context(max_states=1))
        self.assertIn("sp_value_search_truncated", self.talk(result).unknown)
        self.assertFalse(result.to_dict()["context"]["sp_valuation"]["rooms"]["talk"]["normal"]["search_complete"])

    def test_policy_passes_context_and_preserves_complete_explanations(self):
        result = wing_policy.decide(self.week(gain=15), "vo_score", params={"score_sp_context": context()})
        self.assertEqual((result.kind, result.option_id), ("work", "talk"))
        self.assertEqual(result.plan["context"]["sp_valuation"]["mode"], "reviewed_path_marginal")
        stale = replace(self.week(), stats={**self.week().stats, "Vo": 101})
        stopped = wing_policy.decide(stale, "vo_score", params={"score_sp_context": context()})
        self.assertEqual((stopped.kind, stopped.rule), ("stop", "score_params_invalid"))


if __name__ == "__main__":
    unittest.main()
