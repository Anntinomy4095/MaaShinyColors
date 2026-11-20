from __future__ import annotations

import unittest

from agent.core import wing_audition as wa

HP = 75_000          # THE LEGEND (Middle) judge interest


def judges(ranks=None, remaining=None, **per):
    ranks = ranks or {"Vo": 2, "Da": 3, "Vi": 1}
    remaining = remaining or {}
    out = []
    for attr in wa.ATTRIBUTES:
        extra = per.get(attr, {})
        out.append(wa.JudgeView(attr, ranks[attr], remaining.get(attr, 1.0), HP, **extra))
    return tuple(out)


def state(stats=None, options=None, **kw):
    stats = stats or {"Vo": 1200, "Da": 300, "Vi": 250, "Me": 500}
    options = options if options is not None else (wa.AppealOption("c1", "Vo", 2.5), wa.AppealOption("c2", "Vo", 1.5))
    base = dict(turn=1, judges=judges(), options=options, stats=stats, memory_gauge=30, memory_level=3,
                mental=500, mental_max=500, need_first=True)
    base.update(kw)
    return wa.LiveState(**base)


class TrendFocusTest(unittest.TestCase):
    def test_sufficient_stats_attack_trend1_off_attribute(self):
        # Vo 2400 -> off-attribute 2.5x hit on the Vi judge is 0.16 of its HP: the TA race there is won.
        d = wa.decide(state(stats={"Vo": 2400, "Da": 300, "Vi": 250, "Me": 500}))
        self.assertEqual(("skill", "c1", "Vi"), (d.action, d.option_id, d.target))
        self.assertTrue(d.focus["active"])
        self.assertEqual("stats_sufficient", d.focus["reason"])
        self.assertGreater(d.rows[0].terms["focus"], 0)

    def test_insufficient_stats_keep_own_attribute_excellent(self):
        d = wa.decide(state(stats={"Vo": 500, "Da": 300, "Vi": 250, "Me": 500}))
        self.assertFalse(d.focus["active"])
        self.assertEqual("stats_insufficient", d.focus["reason"])
        self.assertEqual("Vo", d.target)

    def test_secured_trend1_ta_releases_focus(self):
        js = judges(remaining={"Vi": 0.4}, Vi={"our_dealt": 0.55, "rival_top_dealt": 0.03})
        d = wa.decide(state(stats={"Vo": 2400, "Da": 300, "Vi": 250, "Me": 500}, judges=js, turn=3))
        self.assertEqual("trend1_ta_secured", d.focus["reason"])
        self.assertFalse(d.focus["active"])

    def test_row_breakdown_has_all_terms(self):
        d = wa.decide(state())
        row = next(r for r in d.rows if r.kind == "skill" and r.unknown != ("damage",))
        for key in ("bonus", "last_appeal", "top_appeal", "overkill", "mental", "focus"):
            self.assertIn(key, row.terms)
        self.assertAlmostEqual(row.ev, round(sum(row.terms.values()), 4), places=3)


class LastAppealTest(unittest.TestCase):
    def test_finish_low_judge_for_last_appeal(self):
        # Da judge (trend 3) at 5 %: a cheap finish takes its LA; the Vo hit would not finish anything.
        js = judges(remaining={"Da": 0.03}, Da={"our_dealt": 0.0, "rival_top_dealt": 0.6})
        d = wa.decide(state(judges=js, options=(wa.AppealOption("small", "Da", 1.0),), stats={
            "Vo": 600, "Da": 600, "Vi": 250, "Me": 500}, turn=4))
        best_da = next(r for r in d.rows if r.target == "Da")
        self.assertGreater(best_da.terms["last_appeal"], 0)
        self.assertEqual("Da", d.target)

    def test_last_appeal_weighted_more_when_behind(self):
        js = judges(remaining={"Da": 0.01})
        opts = (wa.AppealOption("small", "Da", 1.0),)
        ahead = wa.decide(state(judges=js, options=opts, our_stars=30, rival_stars=(10, 12)))
        behind = wa.decide(state(judges=js, options=opts, our_stars=10, rival_stars=(30, 12)))
        la = lambda d: next(r for r in d.rows if r.target == "Da").terms["last_appeal"]
        self.assertGreater(la(behind), la(ahead))
        self.assertTrue(behind.focus["behind"])

    def test_overkill_penalised(self):
        js = judges(remaining={"Vo": 0.02})
        d = wa.decide(state(judges=js, options=(wa.AppealOption("big", "Vo", 5.0),)))
        vo = next(r for r in d.rows if r.target == "Vo")
        self.assertLess(vo.terms["overkill"], 0)

    def test_left_judge_never_targeted(self):
        js = judges(remaining={"Vi": 0.0})
        d = wa.decide(state(judges=js))
        self.assertNotIn("Vi", {r.target for r in d.rows})


class MemoryTimingTest(unittest.TestCase):
    def test_not_full_gauge_no_memory_row(self):
        d = wa.decide(state(memory_gauge=90))
        self.assertNotIn("memory", {r.kind for r in d.rows})

    def test_hold_full_gauge_when_nothing_finishes(self):
        d = wa.decide(state(memory_gauge=100, memory_level=2))
        self.assertEqual("skill", d.action)
        memory = next(r for r in d.rows if r.kind == "memory")
        self.assertLess(memory.terms["hold_cost"], 0)

    def test_fire_memory_when_it_steals_last_appeals(self):
        js = judges(remaining={"Vo": 0.06, "Da": 0.02, "Vi": 0.02},
                    Vi={"our_dealt": 0.5, "rival_top_dealt": 0.3})
        d = wa.decide(state(judges=js, memory_gauge=100, memory_level=3, turn=5,
                            options=(wa.AppealOption("weak", "Da", 0.1),)))
        self.assertEqual("memory", d.action)
        self.assertEqual("all", d.target)

    def test_mental_danger_fires_memory_without_hold_cost(self):
        d = wa.decide(state(memory_gauge=100, memory_level=2, mental=100, mental_max=500))
        memory = next(r for r in d.rows if r.kind == "memory")
        self.assertEqual(0.0, memory.terms["hold_cost"])
        self.assertTrue(d.focus["mental_danger"])

    def test_level_zero_cannot_fire(self):
        d = wa.decide(state(memory_gauge=100, memory_level=0))
        self.assertNotIn("memory", {r.kind for r in d.rows})


class DeterminismTest(unittest.TestCase):
    def test_same_input_same_output(self):
        s = state(memory_gauge=100)
        self.assertEqual(wa.decide(s).to_dict(), wa.decide(s).to_dict())

    def test_tie_breaks_by_hand_order(self):
        opts = (wa.AppealOption("a", "Vo", 2.0), wa.AppealOption("b", "Vo", 2.0))
        self.assertEqual("a", wa.decide(state(options=opts)).option_id)
        self.assertEqual("b", wa.decide(state(options=tuple(reversed(opts)))).option_id)


class DegradeTest(unittest.TestCase):
    def test_from_dict_ignores_unknown_and_bad_values(self):
        s = wa.LiveState.from_dict({
            "turn": "x", "mystery": 1, "memory_gauge": "abc", "mental": None,
            "judges": [{"attribute": "Vo", "trend_rank": 1, "remaining": "45%"},
                       {"attribute": "Da", "trend_rank": 9, "remaining": 2},
                       {"attribute": "Vi", "remaining": None, "colour": "pink"}, {"attribute": "??"}],
            "options": [{"id": "c1", "attribute": "Vo", "multiplier": 2.5}, {"attribute": "Vo"}],
            "stats": {"Vo": "1200", "Me": -3}})
        self.assertIsNone(s.turn)
        self.assertEqual(3, len(s.judges))
        self.assertIsNone(s.judges[0].remaining)     # "45%" is not a fraction in [0, 1]
        self.assertIsNone(s.judges[1].trend_rank)
        self.assertEqual(1, len(s.options))
        self.assertIsNone(s.stats["Me"])
        d = wa.decide(s)
        self.assertEqual("low", d.confidence)
        self.assertIn("turn", d.unknown)
        self.assertIn("memory_gauge_unknown", d.unknown)
        self.assertIn("judge_remaining", d.unknown)
        self.assertEqual("skill", d.action)

    def test_unknown_damage_uses_proxy(self):
        js = tuple(wa.JudgeView(a, r, 1.0) for a, r in zip(wa.ATTRIBUTES, (2, 3, 1)))   # no max_hp
        d = wa.decide(state(judges=js))
        self.assertEqual("low", d.confidence)
        self.assertTrue(all("proxy" in r.terms for r in d.rows if r.kind == "skill"))

    def test_predicted_preview_overrides_formula(self):
        opts = (wa.AppealOption("c1", None, None, predicted={"Vo": 0.2, "Da": 0.1, "Vi": 0.3}),)
        d = wa.decide(state(options=opts))
        self.assertEqual("ok", d.confidence)
        self.assertEqual("Vi", d.target)

    def test_nothing_to_do(self):
        js = judges(remaining={"Vo": 0.0, "Da": 0.0, "Vi": 0.0})
        self.assertEqual("none", wa.decide(state(judges=js)).action)
        self.assertEqual("none", wa.decide(state(judges=judges()[:2])).action)

    def test_param_override_rejects_unknown(self):
        with self.assertRaises(ValueError):
            wa.decide(state(), {"no_such_param": 1})


class WinEstimateTest(unittest.TestCase):
    def ctx(self, **kw):
        base = dict(primary="Vo", stats={"Vo": 1200, "Me": 500}, recommended={"Vo": 450, "Me": 450},
                    trend={"Vo": 1, "Da": 2, "Vi": 3}, memory_level=3)
        base.update(kw)
        return wa.AuditionContext(**base)

    def test_strong_trend1_is_likely_first(self):
        est = wa.audition_win_estimate(self.ctx())
        self.assertGreater(est["p_first"], 0.9)
        self.assertGreaterEqual(est["p_pass"], est["p_first"])

    def test_need_first_pass_equals_first(self):
        est = wa.audition_win_estimate(self.ctx(need_first=True))
        self.assertEqual(est["p_first"], est["p_pass"])

    def test_weaker_and_trend3_lower(self):
        strong = wa.audition_win_estimate(self.ctx())["p_first"]
        weak = wa.audition_win_estimate(self.ctx(stats={"Vo": 400, "Me": 500}, trend={"Vo": 3, "Da": 1, "Vi": 2}))
        self.assertLess(weak["p_first"], strong)

    def test_low_mental_penalised(self):
        ok = wa.audition_win_estimate(self.ctx())["p_first"]
        low = wa.audition_win_estimate(self.ctx(stats={"Vo": 1200, "Me": 100}))["p_first"]
        self.assertLess(low, ok)

    def test_unknown_inputs(self):
        est = wa.audition_win_estimate(self.ctx(recommended={}, trend=None, memory_level=None))
        self.assertIsNone(est["p_first"])
        self.assertIn("recommended_primary", est["unknown"])
        est = wa.audition_win_estimate(self.ctx(stats={}))
        self.assertIsNone(est["p_first"])

    def test_firepower_path(self):
        est = wa.audition_win_estimate(self.ctx(rival_base_attack=4250, best_multiplier=3.0))
        self.assertEqual("firepower", est["source"])

    def test_context_from_wing_duck_typed(self):
        class S:
            stats = {"Vo": 900, "Me": 400}
            trend = {"Vo": 2, "Da": 1, "Vi": 3}
            memory_level = 2

        class A:
            recommended = {"Vo": 450, "Me": 450}

        est = wa.audition_win_estimate(wa.context_from_wing(S(), A(), "Vo"))
        self.assertIsNotNone(est["p_first"])


class StarRulesTest(unittest.TestCase):
    """wiki オーディションの基本."""

    def test_bonus_follows_own_contribution_not_total_depletion(self):
        # Judge already 50 % depleted by rivals, we have dealt nothing: a 25 % hit takes OUR share to 25 % -> no bonus,
        # although total depletion crosses 60 %.
        j = wa.JudgeView("Vo", 1, 0.5, HP, our_dealt=0.0)
        terms = wa._hit_terms(state(), j, 0.25, 0.0, wa.LiveParams(), False)
        self.assertEqual(terms["bonus"], 0.0)
        # We have dealt 20 %; a 15 % hit crosses OUR 30 % line -> one full trend-1 bonus (4), no first-mover discount.
        j2 = wa.JudgeView("Vo", 1, 0.5, HP, our_dealt=0.2)
        terms2 = wa._hit_terms(state(), j2, 0.15, 0.0, wa.LiveParams(), False)
        self.assertEqual(terms2["bonus"], 4.0)

    def test_bonus_ignores_overkill(self):
        j = wa.JudgeView("Vo", 2, 0.05, HP, our_dealt=0.27)
        terms = wa._hit_terms(state(), j, 0.5, 0.0, wa.LiveParams(), False)  # only 5 % lands -> own 32 %: one line
        self.assertEqual(terms["bonus"], 3.0)

    def test_percentage_buffs_add(self):
        option = wa.AppealOption.from_dict({"id": "c", "attribute": "Vo", "multiplier": 2.5, "buffs": [0.2, 0.3]})
        self.assertAlmostEqual(option.buff, 1.5)
        self.assertAlmostEqual(wa.additive_buff([0.2, 0.3]), 1.5)
        self.assertNotAlmostEqual(option.buff, 1.2 * 1.3)
        unreadable = wa.AppealOption.from_dict({"id": "c", "attribute": "Vo", "multiplier": 2.5, "buffs": [0.2, "x"]})
        self.assertEqual(unreadable.buff, 1.0)

    def test_equal_stars_tie_is_first(self):
        self.assertEqual(wa.star_placement(30, (30, 12)), 1)
        self.assertEqual(wa.star_placement(29, (30, 12)), 2)
        self.assertFalse(wa._behind(state(our_stars=30, rival_stars=(30, 10))))
        self.assertTrue(wa._behind(state(our_stars=29, rival_stars=(30, 10))))


class GameAutoTest(unittest.TestCase):
    def test_false_when_first_matters(self):
        self.assertFalse(wa.should_use_game_auto()[0])
        self.assertFalse(wa.should_use_game_auto(need_first=True, estimate={"p_pass": 1.0}, user_opt_in=True)[0])

    def test_regular_audition_needs_opt_in_and_margin(self):
        self.assertFalse(wa.should_use_game_auto(need_first=False, estimate={"p_pass": 0.99})[0])
        self.assertFalse(wa.should_use_game_auto(need_first=False, estimate={"p_pass": 0.9}, user_opt_in=True)[0])
        self.assertFalse(wa.should_use_game_auto(need_first=False, estimate=None, user_opt_in=True)[0])
        self.assertTrue(wa.should_use_game_auto(need_first=False, estimate={"p_pass": 0.99}, user_opt_in=True)[0])


class NavAdapterTest(unittest.TestCase):
    """wing_nav resolves ``choose_audition`` / ``live_plan`` by name (SCORER_HOOKS); both are pure."""

    @staticmethod
    def memory(**kw):
        import types
        base = dict(pending="audition", pending_audition=100_000, audition_pages=0, live_mode=None)
        base.update(kw)
        return types.SimpleNamespace(**base)

    def test_choose_audition_pages_to_the_planned_reward(self):
        m = self.memory()
        self.assertEqual(wa.choose_audition({"reward": 100_000}, m)["action"], "schedule_decide")
        self.assertEqual(wa.choose_audition({"reward": 50_000}, m)["action"], "audition_next")
        self.assertEqual(wa.choose_audition({"reward": 50_000}, self.memory(audition_pages=wa.AUDITION_MAX_PAGES - 1))["reason"],
                         "planned_audition_not_found")
        self.assertEqual(wa.choose_audition({"reward": None}, m)["reason"], "audition_reward_unread")
        self.assertEqual(wa.choose_audition({"reward": 100_000, "locked": True}, m)["action"], "decline")
        self.assertEqual(wa.choose_audition({"reward": 100_000}, self.memory(pending=None))["action"], "decline")
        before = dict(vars(m))
        wa.choose_audition({"reward": 50_000}, m)
        self.assertEqual(vars(m), before)                       # pure: memory untouched

    def test_live_plan_auto_only_with_opt_in_and_safe_margin(self):
        safe = {"need_first": False, "estimate": {"p_pass": 0.99}, "auto": "OFF"}
        self.assertEqual(wa.live_plan(safe, self.memory(live_mode="game_auto"))["action"], "live_auto")
        self.assertEqual(wa.live_plan(dict(safe, auto="ON"), self.memory(live_mode="game_auto"))["action"], "wait")
        # no opt-in, or 1st place required (default), never turns Auto on
        self.assertNotEqual(wa.live_plan(safe, self.memory())["action"], "live_auto")
        final = wa.live_plan({"estimate": {"p_pass": 0.99}, "auto": "OFF"}, self.memory(live_mode="game_auto"))
        self.assertEqual((final["action"], final["auto_reason"]), ("stop", "first_place_required"))
        self.assertEqual(wa.live_plan({"auto": "ON"}, self.memory())["reason"], "game_auto_on_without_permission")

    def test_live_plan_own_policy(self):
        live = {"turn": 1, "stats": {"Vo": 1200, "Da": 300, "Vi": 250, "Me": 500},
                "judges": [{"attribute": a, "trend_rank": r, "remaining": 1.0, "max_hp": HP}
                           for a, r in (("Vo", 2), ("Da", 3), ("Vi", 1))],
                "options": [{"id": "c1", "attribute": "Vo", "multiplier": 2.5}], "auto": "OFF"}
        out = wa.live_plan({"live_state": live, "auto": "OFF"}, self.memory())
        self.assertEqual(out["action"], "play")
        self.assertEqual(out["decision"]["option_id"], "c1")
        self.assertEqual(wa.live_plan({"auto": "OFF"}, self.memory())["reason"], "live_state_unread")


if __name__ == "__main__":
    unittest.main()
