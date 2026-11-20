"""Shared PEP objective and budget/paired-battle contracts, synthetic only."""
from dataclasses import asdict, replace
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agent.core.true_end.battle import Scenario
from agent.core.true_end.sp_value import marginal_sp_value
from agent.core.true_end.terminal_value import TerminalEvaluator, TerminalModel, TerminalScenario
from agent.core.true_end.fan_route import FanRoutePolicy
from agent.core.true_end.run_state import fingerprint
from test_true_end_run_state import assumptions, panel, profile, state


def model(*, wing=0, seeds=(20261006,)):
    scenario = Scenario("explicit-test-priors", (("Perfect", 1),), (("Good", 1),), 1.25,
                        "uniform", 2, "discard_shuffle", "player_loses", False,
                        ("Synthetic scenario, not calibrated",))
    return TerminalModel({"Vo": 1, "Da": 0, "Vi": 0, "Me": 0}, wing,
                         (TerminalScenario(scenario, ("Vo", "Da", "Vi"), ("Da", "Vo", "Vi")),), seeds)


def result(won, *, complete=True, rank=None):
    return SimpleNamespace(won=won, complete=complete, rank=rank or (1 if won else 3),
                           failure_reasons=() if won else ("synthetic-loss",))


class TerminalValueTests(unittest.TestCase):
    def test_no_route_policy_keeps_legacy_identity_and_report_shape(self):
        team, cfg, objective = profile(), assumptions(), model()
        evaluator = TerminalEvaluator(team, cfg, objective)
        expected = fingerprint({"version": "wing-terminal-pep-v1", "profile": team.id,
                                "growth": asdict(cfg), "model": asdict(objective)})
        self.assertEqual(evaluator.id, expected)
        value = evaluator.evaluate(state(team))
        self.assertIsNone(value.fan_route)
        self.assertNotIn("fan_route", value.summary())

    def test_each_actual_successor_replans_fans_without_changing_model_identity(self):
        team = profile()
        before = replace(state(team), next_week=27, fans=200000)
        actions = [{"action": "rest"} for _ in range(32)]
        for week in (28, 29, 30):
            actions[week] = {"action": "audition", "fans": 100000, "required_fans": 100000,
                             "stats": {"Vo": 8}, "source": "synthetic://terminal-route"}
        cfg = assumptions(actions=actions, placement_probability=0, rest_threshold=0,
                          event_completion_probability=0)
        route = FanRoutePolicy({w: {"action": "Vo"} for w in (28, 29, 30)}, ("Explicit future pass conditions",))
        evaluator = TerminalEvaluator(team, cfg, model(), fan_route=route)
        win = replace(before, next_week=28, fans=300000,
                      panels=replace(before.panels, stats={**before.panels.stats, "Vo": 108}))
        loss = replace(before, next_week=28)
        report = evaluator.score_successors(before, {"audition": ((.5, win), (.5, loss))})
        branches = report["ranked"][0]["branches"]
        self.assertEqual(report["ranked"][0]["expected_value_pep"], 140)
        self.assertEqual([len(b["evaluation"]["fan_route"]["remaining_audition_weeks"]) for b in branches], [2, 3])
        self.assertTrue(all(b["evaluation"]["model_id"] == evaluator.id for b in branches))
        self.assertIsNone(report["true_end_probability"])

    def test_pregame_and_resume_use_same_objective_without_extra_growth(self):
        team = profile()
        evaluator = TerminalEvaluator(team, assumptions(), model())
        before = evaluator.evaluate(replace(state(team), next_week=0), learn_panels=False)
        resumed = evaluator.evaluate(state(team), learn_panels=False)
        self.assertEqual(before.value_pep, resumed.value_pep)
        self.assertEqual((len(before.projection.timeline), len(resumed.projection.timeline)), (32, 8))
        self.assertEqual(before.model_id, resumed.model_id)
        self.assertIsNone(resumed.true_end_probability)
        json.dumps(resumed.summary(), allow_nan=False)

    def test_sp_callback_never_spends_same_budget_again(self):
        team = profile(panels=[panel("a", "stat", {"Vo": 50}), panel("b", "stat", {"Vo": 50})])
        snap = replace(state(team), next_week=31, sp=20)
        evaluator = TerminalEvaluator(team, assumptions(), model())
        marginal = marginal_sp_value(evaluator.sp_context(snap), 0, 20)
        self.assertEqual(marginal["marginal_pep"], 50)
        self.assertEqual(len(marginal["after"]["purchases"]), 1)
        self.assertEqual(marginal["after"]["value_pep"], 150)
        self.assertEqual(marginal_sp_value(evaluator.sp_context(snap), 40, 100)["marginal_pep"], 0)

    def test_cap_value_uses_remaining_training_not_fixed_future_bonus(self):
        team = profile(panels=[panel("cap", "cap", {"Vo": 100})])
        snap = state(team)
        snap = replace(snap, next_week=31, panels=replace(snap.panels, stats={**snap.panels.stats, "Vo": 500}))
        actions = ["rest"] * 32
        actions[31] = "Vo"
        evaluator = TerminalEvaluator(team, assumptions(actions=actions, placement_probability=0), model())
        self.assertEqual(marginal_sp_value(evaluator.sp_context(snap), 0, 20)["marginal_pep"], 32)
        end = replace(snap, next_week=32, phase="semifinal")
        self.assertEqual(marginal_sp_value(evaluator.sp_context_after_action(snap, end), 0, 20)["marginal_pep"], 0)

    def test_gate_failure_does_not_earn_terminal_te_utility(self):
        team = profile()
        evaluator = TerminalEvaluator(team, assumptions(), model(wing=30))
        for snap in (replace(state(team), fans=400000), replace(state(team), route_feasible=False)):
            with patch("agent.core.true_end.terminal_value.simulate_audition") as battle:
                evaluated = evaluator.evaluate(snap)
            self.assertFalse(evaluated.goal_eligible)
            self.assertEqual(evaluated.value_pep, 0)
            battle.assert_not_called()

    def test_semifinal_failure_never_simulates_final(self):
        team = profile()
        evaluator = TerminalEvaluator(team, assumptions(), model(wing=30))
        with patch("agent.core.true_end.terminal_value.simulate_audition", return_value=result(False)) as battle:
            evaluated = evaluator.evaluate(state(team))
        self.assertEqual(battle.call_count, 1)
        self.assertEqual(evaluated.wing_pep, 0)
        self.assertIsNone(evaluated.conditional_battles[0]["runs"][0]["final_rank"])

    def test_paired_victory_transfers_capped_reward_once(self):
        team = profile()
        snap = state(team)
        snap = replace(snap, panels=replace(snap.panels, stats={**snap.panels.stats, "Vo": 495}))
        evaluator = TerminalEvaluator(team, assumptions(), model(wing=30))
        calls = []
        def battle(idol, supports, audition, policy, rng, scenario):
            calls.append(idol)
            return result(True)
        with patch("agent.core.true_end.terminal_value.simulate_audition", side_effect=battle):
            evaluated = evaluator.evaluate(snap)
        self.assertEqual((calls[0].week, calls[1].week), (32, 33))
        self.assertEqual((calls[0].stats["Vo"], calls[1].stats["Vo"]), (495, 500))
        self.assertEqual((calls[0].mental, calls[1].mental), (100, 120))
        self.assertEqual(evaluated.wing_pep, 30)
        self.assertIsNone(evaluated.true_end_probability)

    def test_final_snapshot_does_not_replay_semifinal_or_reward(self):
        team = profile()
        snap = replace(state(team), next_week=32, phase="final")
        evaluator = TerminalEvaluator(team, assumptions(), model(wing=30))
        with patch("agent.core.true_end.terminal_value.simulate_audition", return_value=result(True)) as battle:
            evaluated = evaluator.evaluate(snap)
        self.assertEqual(battle.call_count, 1)
        self.assertEqual(battle.call_args.args[0].stats["Vo"], 100)
        self.assertEqual(evaluated.conditional_battles[0]["phase"], "final_only")

    def test_turn_limit_keeps_probability_unknown_and_value_lower_bound(self):
        team = profile()
        evaluator = TerminalEvaluator(team, assumptions(), model(wing=30, seeds=(1, 2)))
        with patch("agent.core.true_end.terminal_value.simulate_audition",
                   side_effect=[result(False, complete=False), result(True), result(True)]):
            evaluated = evaluator.evaluate(state(team))
        self.assertIsNone(evaluated.conditional_battles[0]["win_rate"])
        self.assertEqual(evaluated.conditional_battles[0]["value_lower_bound"], .5)
        self.assertEqual(evaluated.wing_pep, 15)
        self.assertTrue(any("turn_limit" in u for u in evaluated.unknowns))

    def test_real_battles_repeat_with_same_seeds_and_state(self):
        team = profile()
        snap = state(team)
        snap = replace(snap, panels=replace(snap.panels, stats={"Vo": 450, "Da": 100, "Vi": 100, "Me": 450}))
        evaluator = TerminalEvaluator(team, assumptions(), model(wing=30, seeds=(1, 2)))
        first = evaluator.evaluate(snap)
        second = evaluator.evaluate(snap)
        self.assertEqual(first.summary(), second.summary())
        self.assertEqual(first.conditional_battles[0]["trials"], 2)
        self.assertTrue(all(r["complete"] for r in first.conditional_battles[0]["runs"]))

    def test_missing_support_stat_keeps_unknown_without_fabricating_strength(self):
        team = profile()
        bad = replace(team.active[0], stats={"Vo": 100, "Vi": 100, "Me": 100}, unknowns=("levels.80.Da",))
        team = replace(team, active=(bad, *team.active[1:]))
        evaluated = TerminalEvaluator(team, assumptions(), model(wing=30)).evaluate(state(team))
        self.assertEqual(evaluated.conditional_battles, ())
        self.assertIn("s0:battle_stats_or_owned_live_unavailable", evaluated.unknowns)
        self.assertEqual(evaluated.wing_pep, 0)

    def test_unknown_guest_own_battle_data_does_not_block_active_deck(self):
        team = profile()
        team = replace(team, guest=replace(team.guest, stats={}, owned_live=None,
                                          unknowns=("levels.80.Vo", "owned_live_skills")))
        evaluator = TerminalEvaluator(team, assumptions(), model(wing=30))
        with patch("agent.core.true_end.terminal_value.simulate_audition", return_value=result(True)):
            evaluated = evaluator.evaluate(state(team))
        self.assertEqual(evaluated.wing_pep, 30)
        # 2026-10-07: the Guest never plays its own Live, so growth no longer reports that gap for it.
        self.assertNotIn("guest:owned_live_skills", evaluated.unknowns)
        self.assertIn("guest:levels.80.Vo", evaluated.unknowns)

    def test_after_action_stat_panel_cannot_restore_current_week_overflow(self):
        team = profile(panels=[panel("stat", "stat", {"Vo": 10})])
        before = state(team)
        before = replace(before, next_week=31, panels=replace(before.panels, stats={**before.panels.stats, "Vo": 499}))
        after = replace(before, next_week=32, phase="semifinal", sp=20,
                        panels=replace(before.panels, stats={**before.panels.stats, "Vo": 500}))
        evaluator = TerminalEvaluator(team, assumptions(), model())
        ctx = evaluator.sp_context_after_action(before, after)
        self.assertEqual(marginal_sp_value(ctx, 0, 20)["marginal_pep"], 0)

    def test_successor_q_uses_all_branch_rewards_once_and_failure_uses_week(self):
        team = profile()
        before = replace(state(team), next_week=31, fans=400000)
        success = replace(before, next_week=32, phase="semifinal", fans=500000,
                          panels=replace(before.panels, stats={**before.panels.stats, "Vo": 108}))
        failure = replace(before, next_week=32, phase="semifinal", route_feasible=False)
        evaluator = TerminalEvaluator(team, assumptions(), model())
        scored = evaluator.score_successors(before, {"audition": ((.8, success), (.2, failure)),
                                                     "rest": ((1, failure),)})
        self.assertEqual(scored["choice"], "audition")
        self.assertAlmostEqual(scored["ranked"][0]["expected_value_pep"], 86.4)
        self.assertEqual(len(scored["ranked"][0]["branches"][1]["evaluation"]["projection"]["timeline"]), 0)
        self.assertIsNone(scored["true_end_probability"])

    def test_sp_after_action_accepts_newly_crossed_gate_at_all_boundaries(self):
        team = profile(panels=[panel("stat", "stat", {"Vo": 10})])
        evaluator = TerminalEvaluator(team, assumptions(), model())
        for week, old_fans, new_fans in ((7, 900, 1000), (23, 40000, 50000), (31, 400000, 500000)):
            before = replace(state(team), next_week=week, fans=old_fans)
            after = replace(before, next_week=week + 1, fans=new_fans, sp=20,
                            phase="semifinal" if week == 31 else "week")
            ctx = evaluator.sp_context_after_action(before, after)
            self.assertEqual(ctx.state, after.panels)
            if week == 31:
                self.assertEqual(marginal_sp_value(ctx, 0, 20)["marginal_pep"], 10)

    def test_successor_contract_rejects_extra_weeks_bad_probabilities_and_resurrection(self):
        team = profile()
        before = state(team)
        evaluator = TerminalEvaluator(team, assumptions(), model())
        for branches in (((1, before),), ((1, replace(before, next_week=26)),),
                         ((.5, replace(before, next_week=25)),)):
            with self.subTest(branches=branches), self.assertRaises(ValueError):
                evaluator.score_successors(before, {"a": branches})
        with self.assertRaisesRegex(ValueError, "eliminated"):
            evaluator.sp_context_after_action(replace(before, route_feasible=False), replace(before, next_week=25))

    def test_fingerprint_changes_with_priors_and_explicit_objective(self):
        team = profile()
        evaluator = TerminalEvaluator(team, assumptions(), model())
        self.assertNotEqual(evaluator.id, TerminalEvaluator(team, assumptions(perfect_probability=.5), model()).id)
        self.assertNotEqual(evaluator.id, TerminalEvaluator(team, assumptions(), model(wing=30)).id)
        with self.assertRaisesRegex(ValueError, "1 PEP"):
            TerminalEvaluator(team, assumptions(), replace(model(), stat_weights=dict.fromkeys(("Vo", "Da", "Vi", "Me"), 2)))

    def test_model_limits_and_missing_te_eligibility(self):
        for changes in ({"seeds": ()}, {"seeds": (1, 1)}, {"seeds": tuple(range(65))},
                        {"wing_win_pep": float("nan")}, {"scenarios": ()}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(model(), **changes)
        team = profile()
        team = replace(team, p=replace(team.p, card=replace(team.p.card, te_eligible=None)))
        evaluated = TerminalEvaluator(team, assumptions(), model()).evaluate(state(team))
        self.assertEqual(evaluated.value_pep, 0)
        self.assertIn("P:TrueEnd_eligibility_unverified_or_false", evaluated.unknowns)


if __name__ == "__main__":
    unittest.main()
