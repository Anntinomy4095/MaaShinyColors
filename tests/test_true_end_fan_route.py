"""Conditional future-route replanning, actual snapshots, synthetic offline only."""
from dataclasses import replace
import json
import unittest
from unittest.mock import patch

from agent.core.true_end.fan_route import FanRoutePolicy, FanRoutePlans
from agent.core.true_end.growth import estimate_team_growth
from agent.core.true_end.models import SupportSkill
from agent.core.true_end.sp_value import marginal_sp_value
from agent.core.true_end.terminal_value import TerminalEvaluator
from test_true_end_run_state import assumptions, panel, profile, state
from test_true_end_terminal_value import model


def future_audition(fans=100000, gate=100000, **extra):
    return {"action": "audition", "fans": fans, "required_fans": gate, "stats": {"Vo": 8},
            "source": "synthetic://fan-route", **extra}


def config(auditions, **extra):
    actions = [{"action": "rest"} for _ in range(32)]
    for week, action in auditions.items():
        actions[week] = action
    return assumptions(actions=actions, placement_probability=0, rest_threshold=0,
                       event_completion_probability=0, **extra)


def policy(weeks, *, budget=64, fallback="Vo"):
    return FanRoutePolicy({w: {"action": fallback} for w in weeks},
                          ("Synthetic future auditions assume pass; fallback uses growth priors.",), budget)


class FanRouteTests(unittest.TestCase):
    def test_enumeration_keeps_history_and_replaces_whole_dictionary(self):
        cfg = config({7: future_audition(), 28: future_audition(), 30: future_audition()})
        cfg = replace(cfg, start_week=27)
        result = policy((7, 28, 30)).candidates(cfg)
        self.assertEqual((len(result.plans), result.total_possible, result.search_complete), (4, 4, True))
        self.assertEqual(result.plans[0].replaced_weeks, ())
        self.assertEqual(result.plans[1].replaced_weeks, (28, 30))
        for candidate in result.plans:
            self.assertEqual(candidate.actions[:27], cfg.actions[:27])
            for week in candidate.replaced_weeks:
                self.assertEqual(candidate.actions[week], {"action": "Vo"})
        self.assertEqual(cfg.actions[28]["action"], "audition")

    def test_budget_counts_unique_plans_and_always_includes_original_and_all_fallbacks(self):
        cfg = config({w: future_audition() for w in range(24, 32)})
        cfg = replace(cfg, start_week=24)
        result = policy(range(24, 32), budget=3).candidates(cfg)
        self.assertEqual((len(result.plans), result.total_possible, result.search_complete), (3, 256, False))
        self.assertEqual(result.plans[0].replaced_weeks, ())
        self.assertEqual(result.plans[1].replaced_weeks, tuple(range(24, 32)))
        self.assertEqual(len({p.id for p in result.plans}), 3)

    def test_single_and_no_editable_slots_are_complete(self):
        cfg = replace(config({31: future_audition()}), start_week=31)
        self.assertEqual(len(policy((31,)).candidates(cfg).plans), 2)
        result = policy((30,)).candidates(cfg)
        self.assertEqual((len(result.plans), result.search_complete), (1, True))
        terminal = replace(cfg, start_week=32)
        self.assertEqual(len(policy((31,)).candidates(terminal).plans), 1)

    def test_unknown_reward_and_gate_block_original_route_too(self):
        team = profile()
        before = replace(state(team), next_week=31)
        for field, value in (("required_fans", None), ("required_fans", float("nan")),
                             ("fans", None), ("fans", 0)):
            row = future_audition(**{field: value})
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                TerminalEvaluator(team, config({31: row}), model(), fan_route=policy((31,))).evaluate(before)
        row = future_audition()
        row.pop("required_fans")
        with self.assertRaises(ValueError):
            policy((31,)).candidates(config({31: row}))

    def test_policy_rejects_invalid_budget_week_or_ignored_fallback_rewards(self):
        for fallback, budget in (({32: {"action": "Vo"}}, 64), ({True: {"action": "Vo"}}, 64),
            ({31: {"action": "audition"}}, 64), ({31: {"action": "Vo", "fans": 100000}}, 64),
            ({31: {"action": "Vo", "facility_level": 6}}, 64), ({}, 1), ({}, 65)):
            with self.assertRaises(ValueError):
                FanRoutePolicy(fallback, ("declared",), budget)
        with self.assertRaises(ValueError):
            FanRoutePolicy({}, ())
        with self.assertRaisesRegex(ValueError, "32-week"):
            policy(()).candidates(replace(assumptions(), actions=()))

    def test_more_actual_fans_release_more_auditions_under_same_objective(self):
        team = profile()
        cfg = config({w: future_audition() for w in (28, 29, 30)})
        evaluator = TerminalEvaluator(team, cfg, model(), fan_route=policy((28, 29, 30)))
        releases = []
        for fans in (200000, 300000, 400000, 500000):
            before = replace(state(team), next_week=27, fans=fans)
            value = evaluator.evaluate(before)
            releases.append(len(value.fan_route["replaced_weeks"]))
            self.assertTrue(value.goal_eligible)
            self.assertEqual(value.model_id, evaluator.id)
            self.assertEqual(value.fan_route["evaluated_plans"], 8)
            self.assertTrue(value.fan_route["search_complete"])
        self.assertEqual(releases, [0, 1, 2, 3])

    def test_deleted_unlock_audition_cannot_reuse_original_fan_chain(self):
        team = profile()
        before = replace(state(team), next_week=8, fans=1000)
        cfg = config({8: future_audition(49000, 1000), 9: future_audition(500000, 50000)})
        value = TerminalEvaluator(team, cfg, model(), fan_route=policy((8, 9), fallback="rest")).evaluate(before)
        rows = {tuple(row["replaced_weeks"]): row for row in value.fan_route["plans"]}
        self.assertTrue(rows[()]["conditional_te_fan_route_feasible"])
        self.assertFalse(rows[(8,)]["conditional_te_fan_route_feasible"])
        self.assertEqual(rows[(8,)]["final_fans"], 1000)
        self.assertEqual(value.fan_route["replaced_weeks"], ())

    def test_later_season_fans_cannot_rescue_a_missed_gate(self):
        team = profile()
        for week, gate in ((7, 1000), (15, 10000), (23, 50000)):
            before = replace(state(team), next_week=week, fans=gate - 100)
            cfg = config({week: future_audition(100, 0), week+1: future_audition(500000, 0)})
            value = TerminalEvaluator(team, cfg, model(), fan_route=policy((week,), fallback="rest")).evaluate(before)
            removed = next(row for row in value.fan_route["plans"] if row["replaced_weeks"])
            self.assertFalse(removed["conditional_te_fan_route_feasible"])
            self.assertEqual(removed["final_fans"], gate - 100)
            self.assertEqual(value.fan_route["replaced_weeks"], ())

    def test_100k_wing_fan_requirement_is_separate_from_500k_te_route(self):
        team = profile()
        before = replace(state(team), next_week=31, fans=100000)
        cfg = config({31: future_audition(100000, 100000)})
        value = TerminalEvaluator(team, cfg, model(), fan_route=policy((31,))).evaluate(before)
        self.assertEqual(value.fan_route["status"], "TE_FAN_ROUTE_INFEASIBLE")
        self.assertEqual(value.value_pep, 0)
        self.assertTrue(all(r["wing_final_fan_requirement_met"] for r in value.fan_route["plans"]))
        self.assertFalse(any(r["conditional_te_fan_route_feasible"] for r in value.fan_route["plans"]))

    def test_extra_auditions_can_remain_useful_after_fan_goal_is_met(self):
        team = profile()
        mastery = SupportSkill("aud_vo", "audition_mastery_stat", {80: 100}, attribute="Vo")
        team = replace(team, guest=replace(team.guest, support_effects=((mastery, 100),)))
        before = replace(state(team), next_week=27)
        cfg = config({w: future_audition() for w in (28, 29, 30)}, first_place_probability=1)
        value = TerminalEvaluator(team, cfg, model(), fan_route=policy((28, 29, 30))).evaluate(before)
        self.assertEqual(value.fan_route["replaced_weeks"], ())
        self.assertEqual(value.value_pep, 424)

    def test_fatigued_training_fallback_is_evaluated_as_actual_rest(self):
        team = profile()
        before = replace(state(team), next_week=31, stamina=0)
        cfg = config({31: future_audition()})
        value = TerminalEvaluator(team, cfg, model(), fan_route=policy((31,))).evaluate(before)
        self.assertEqual(value.fan_route["replaced_weeks"], ())
        self.assertEqual(value.value_pep, 108)
        replaced = next(row for row in value.fan_route["plans"] if row["replaced_weeks"])
        self.assertEqual(replaced["value_pep"], 100)  # no fictional lesson at zero stamina

    def test_candidates_do_not_share_sp_purchases_or_completed_events(self):
        team = profile(panels=[panel("gain", "stat", {"Vo": 50}, cost=20)])
        before = replace(state(team), next_week=31, sp=20)
        evaluator = TerminalEvaluator(team, config({31: future_audition()}), model(), fan_route=policy((31,)))
        first = evaluator.evaluate(before)
        candidates = evaluator.fan_route.candidates(before.growth_config(evaluator.growth))
        with patch.object(FanRoutePolicy, "candidates", return_value=FanRoutePlans(
                tuple(reversed(candidates.plans)), candidates.total_possible, candidates.search_complete)):
            reversed_result = evaluator.evaluate(before)
        values = lambda result: {r["plan_id"]: r["value_pep"] for r in result.fan_route["plans"]}
        self.assertEqual(values(first), values(reversed_result))
        self.assertEqual(first.fan_route["selected_plan_id"], reversed_result.fan_route["selected_plan_id"])
        self.assertEqual((before.sp, before.panels.stats["Vo"], before.panels.learned_panel_ids), (20, 100, ()))

    def test_learning_disabled_for_every_route_and_sp_callback(self):
        team = profile(panels=[panel("a", "stat", {"Vo": 50}), panel("b", "stat", {"Vo": 50})])
        before = replace(state(team), next_week=31, sp=20,
                         panels=replace(state(team).panels, stats={**state(team).panels.stats, "Vo": 150},
                                        learned_panel_ids=("p:a",)))
        evaluator = TerminalEvaluator(team, config({31: future_audition()}), model(), fan_route=policy((31,)))
        with patch("agent.core.true_end.terminal_value.estimate_team_growth", wraps=estimate_team_growth) as growth:
            value = evaluator.evaluate(before, learn_panels=False)
        self.assertEqual(len(growth.call_args_list), 2)
        self.assertTrue(all(call.kwargs["replay_panel_ids"] == () for call in growth.call_args_list))
        self.assertEqual(value.projection.skill_plan.cost, 0)
        self.assertEqual(value.projection.skill_plan.unlocked_panel_ids, ("p:a",))
        self.assertEqual(marginal_sp_value(evaluator.sp_context(before), 0, 20)["marginal_pep"], 50)

    def test_budget_limit_and_model_policy_identity_are_reported(self):
        team = profile()
        before = state(team)
        cfg = config({w: future_audition() for w in range(24, 32)})
        evaluator = TerminalEvaluator(team, cfg, model(), fan_route=policy(range(24, 32), budget=2))
        value = evaluator.evaluate(before)
        self.assertFalse(value.fan_route["search_complete"])
        self.assertIn("fan_route_search_budget_limited", value.unknowns)
        self.assertNotEqual(evaluator.id, TerminalEvaluator(team, cfg, model(), fan_route=policy(range(24, 32), budget=3)).id)
        self.assertNotEqual(evaluator.id, TerminalEvaluator(team, cfg, model(), fan_route=policy(range(24, 32), budget=2, fallback="radio")).id)
        self.assertIsNone(value.true_end_probability)
        json.dumps(value.summary(), allow_nan=False)

    def test_eliminated_state_cannot_be_restored_by_replanning(self):
        team = profile()
        before = replace(state(team), route_feasible=False)
        value = TerminalEvaluator(team, config({31: future_audition()}), model(), fan_route=policy((31,))).evaluate(before)
        self.assertEqual(value.fan_route["evaluated_plans"], 1)
        self.assertFalse(value.goal_eligible)
        self.assertEqual(value.value_pep, 0)
        self.assertEqual(value.fan_route["status"], "TE_FAN_ROUTE_INFEASIBLE")

    def test_budget_miss_does_not_claim_entire_route_space_is_infeasible(self):
        team = profile()
        before = replace(state(team), next_week=30, fans=100000)
        cfg = config({30: future_audition(1000, 200000), 31: future_audition(400000, 100000)})
        limited = TerminalEvaluator(team, cfg, model(), fan_route=policy((30, 31), budget=2)).evaluate(before)
        self.assertEqual(limited.fan_route["status"], "NO_FEASIBLE_ROUTE_FOUND_WITHIN_BUDGET")
        self.assertFalse(limited.fan_route["search_complete"])
        complete = TerminalEvaluator(team, cfg, model(), fan_route=policy((30, 31), budget=4)).evaluate(before)
        self.assertTrue(complete.goal_eligible)
        self.assertEqual(complete.fan_route["replaced_weeks"], (30,))
        self.assertGreater(complete.value_pep, limited.value_pep)


if __name__ == "__main__":
    unittest.main()
