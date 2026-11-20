"""Joint pre-week SP/weekly action regressions; synthetic, no native input."""
from dataclasses import asdict, replace
import json
import unittest

from agent.core.wing_state import Audition, ScheduleOption
from agent.core.true_end.preweek_actions import score_preweek, to_weekly_decision
from agent.core.true_end.sp_value import best_panel_plan
from agent.core.true_end.terminal_value import TerminalEvaluator
from agent.core.true_end.week_actions import AuditionModel, RestOutcome
from test_true_end_run_state import assumptions, feature, panel, profile, state
from test_true_end_terminal_value import model
from test_true_end_week_actions import lesson, observed, train, transitions


def snapshot(team, *, sp=20):
    before = state(team)
    return replace(before, sp=sp, panels=replace(before.panels,
        stats={**before.panels.stats, "Vo": 499}))


def provider(before):
    option = lesson(sp_gain=0)
    return observed(before, options=(option,)), transitions(training={option.id: train(perfect_given_success=0)})


def evaluate(team, before, inputs=provider, *, objective=None, **kwargs):
    evaluator = TerminalEvaluator(team, assumptions(event_completion_probability=0), objective or model())
    return score_preweek(evaluator, before, inputs, provider_id="synthetic-rebuilt-input-v1",
                         assumptions=("Synthetic independent inputs per paid snapshot, not calibrated",), **kwargs)


class PreweekActionTests(unittest.TestCase):
    def test_buy_cap_before_training_instead_of_clipping_then_learning(self):
        team = profile(panels=[panel("cap", "cap", {"Vo": 100})])
        before = snapshot(team)
        evaluator = TerminalEvaluator(team, assumptions(event_completion_probability=0), model())
        # The old SP-only value sees no remaining training and buys nothing.
        self.assertFalse(best_panel_plan(evaluator.sp_context(before), 20).purchases)
        result = evaluate(team, before)
        chosen = result["choice"]
        self.assertEqual((chosen["next"], chosen["purchases"], chosen["spent_sp"], chosen["sp_remaining"]),
                         ("learn_panels", ["p:cap"], 20, 0))
        self.assertEqual((result["baseline_no_learning"]["ranked"][0]["expected_value_pep"],
                          chosen["expected_value_pep"], result["delta_vs_no_learning_pep"]), (500, 527, 27))
        paid = chosen["state_after_learning"]
        self.assertEqual((paid["next_week"], paid["panels"]["stats"]["Vo"]), (24, 499))
        branch = result["selected_week"]["ranked"][0]["branches"][0]["evaluation"]["projection"]
        self.assertEqual(branch["effective_stats"]["Vo"], 527)
        self.assertEqual(asdict(before), asdict(snapshot(team)))

    def test_joint_learning_can_change_which_week_action_is_best(self):
        team = profile(panels=[panel("cap", "cap", {"Vo": 100})])
        def inputs(paid):
            options = (lesson(sp_gain=0), ScheduleOption("work_magazine", "work", preview={"Me": 20},
                sp_gain=0, fan_gain=0, stamina_cost=15, trouble_rate=0, supports=0))
            return observed(paid, options=options), transitions(training={o.id: train(perfect_given_success=0) for o in options})
        objective = replace(model(), stat_weights={"Vo": 1, "Da": 0, "Vi": 0, "Me": 1})
        result = evaluate(team, snapshot(team), inputs, objective=objective)
        self.assertEqual((result["baseline_no_learning"]["choice"], result["choice"]["week_action"]),
                         ("work_magazine", "lesson_vo"))
        self.assertEqual(result["delta_vs_no_learning_pep"], 8)

    def test_new_sp_cannot_pay_for_learning_before_this_week(self):
        team = profile(panels=[panel("cap", "cap", {"Vo": 100})])
        def inputs(paid):
            option = lesson(sp_gain=20)
            return observed(paid, options=(option,)), transitions(training={option.id: train(perfect_given_success=0)})
        result = evaluate(team, snapshot(team, sp=0), inputs)
        self.assertEqual((result["choice"]["spent_sp"], result["choice"]["purchases"],
                          result["choice"]["expected_value_pep"]), (0, [], 500))
        self.assertEqual(result["search"]["evaluated_panel_states"], 1)

    def test_full_bridge_path_cost_and_already_paid_root(self):
        team = profile(panels=[panel("root", "stat", {"Vo": 0}, cost=30),
            panel("cap", "cap", {"Vo": 100}, prerequisites=("root",))])
        self.assertEqual(evaluate(team, snapshot(team, sp=49))["choice"]["purchases"], [])
        full = evaluate(team, snapshot(team, sp=50))["choice"]
        self.assertEqual((full["spent_sp"], full["purchases"]), (50, ["p:root", "p:cap"]))
        paid_root = snapshot(team)
        paid_root = replace(paid_root, panels=replace(paid_root.panels, learned_panel_ids=("p:root",)))
        selected = evaluate(team, paid_root)["choice"]
        self.assertEqual((selected["spent_sp"], selected["purchases"]), (20, ["p:cap"]))

    def test_paths_with_same_panel_ids_preserve_acquisition_order_overflow(self):
        team = profile(panels=[panel("stat", "stat", {"Vo": 100}), panel("cap", "cap", {"Vo": 100})])
        result = evaluate(team, snapshot(team, sp=40))
        # Buying the stat after training also reaches 600; keep its SP available.
        self.assertEqual(result["choice"]["purchases"], ["p:cap"])
        self.assertEqual(result["choice"]["expected_value_pep"], 600)
        both = [r["expected_value_pep"] for r in result["ranked_plans"] if len(r["new_panel_ids"]) == 2]
        self.assertIn(528, both)
        self.assertIn(600, both)

    def test_guest_cap_is_available_to_preweek_learning(self):
        team = replace(profile(), guest=feature("guest", panels=[panel("cap", "cap", {"Vo": 100})]))
        result = evaluate(team, snapshot(team))
        self.assertEqual(result["choice"]["purchases"], ["guest:cap"])
        self.assertEqual(result["choice"]["expected_value_pep"], 527)

    def test_zero_benefit_keeps_sp_and_does_not_restore_previous_overflow(self):
        team = profile(panels=[panel("cap", "cap", {"Vo": 100})])
        before = snapshot(team)
        before = replace(before, panels=replace(before.panels, stats={**before.panels.stats, "Vo": 500}))
        def inputs(paid):
            return observed(paid, options=(ScheduleOption("rest", "rest"),)), transitions(rest={"rest": (RestOutcome(1, 50),)})
        result = evaluate(team, before, inputs)
        self.assertEqual((result["choice"]["purchases"], result["choice"]["expected_value_pep"]), ([], 500))

    def test_skill_purchase_rebuilds_current_audition_probability_and_equipment(self):
        team = profile(panels=[panel("power", "live", {"id": "power", "components": {"Vo": 5}})])
        before = replace(state(team), next_week=31, fans=400000, sp=20)
        probabilities = []
        def inputs(paid):
            win = int(any(s.components.get("Vo", 0) >= 4 for s in paid.panels.live_skills))
            probabilities.append(win)
            audition = Audition("trial", "Synthetic trial", 100000, min_fans=100000)
            return observed(paid, auditions=(audition,)), transitions(auditions={"trial": AuditionModel(win, 1, {}, {})})
        result = evaluate(team, before, inputs)
        self.assertEqual(probabilities, [0, 1])
        self.assertEqual((result["baseline_no_learning"]["ranked"][0]["expected_value_pep"],
                          result["choice"]["expected_value_pep"]), (0, 100))
        self.assertEqual(result["choice"]["purchases"], ["p:power"])
        equipped = result["choice"]["state_after_learning"]["panels"]["live_skills"]
        self.assertEqual(len(equipped), 4)
        self.assertTrue(any(s["id"] == "power" for s in equipped))
        ids = [r["transition_model_id"] for r in result["ranked_plans"]]
        self.assertEqual(len(set(ids)), 2)

    def test_missing_week_inputs_cannot_become_a_zero_value_action(self):
        team = profile(panels=[panel("cap", "cap", {"Vo": 100})])
        def inputs(paid):
            return observed(paid, options=(lesson(sp_gain=None),)), transitions(training={"lesson_vo": train()})
        result = evaluate(team, snapshot(team), inputs)
        self.assertEqual(result["status"], "BLOCKED_WEEK_INPUTS")
        self.assertIsNone(result["choice"])
        self.assertTrue(all(r["expected_value_pep"] is None and r["excluded"] for r in result["ranked_plans"]))

    def test_unread_baseline_can_become_modeled_after_learning(self):
        team = profile(panels=[panel("power", "live", {"id": "power", "components": {"Vo": 5}})])
        def inputs(paid):
            gain = 0 if paid.panels.learned_panel_ids else None
            return observed(paid, options=(lesson(sp_gain=gain),)), transitions(training={"lesson_vo": train(perfect_given_success=0)})
        result = evaluate(team, snapshot(team), inputs)
        self.assertIsNone(result["baseline_no_learning"])
        self.assertIsNone(result["delta_vs_no_learning_pep"])
        self.assertEqual((result["status"], result["choice"]["purchases"]), ("READY", ["p:power"]))

    def test_budget_limit_keeps_no_purchase_and_reports_partial_search(self):
        team = profile(panels=[panel("cap", "cap", {"Vo": 100})])
        result = evaluate(team, snapshot(team), max_states=1)
        self.assertEqual(result["choice"]["purchases"], [])
        self.assertFalse(result["search"]["complete"])
        self.assertIn("preweek_panel_search_budget_limited", result["unknowns"])

    def test_stale_observation_cannot_be_reused_after_purchase(self):
        team = profile(panels=[panel("cap", "cap", {"Vo": 100})])
        before = snapshot(team)
        stale = provider(before)
        with self.assertRaisesRegex(ValueError, "snapshot|differ"):
            evaluate(team, before, lambda paid: stale)

    def test_locked_panel_events_and_qualified_identity_do_not_leak_between_plans(self):
        team = profile(panels=[panel("locked", "cap", {"Vo": 100}, event="E4")])
        before = snapshot(team)
        self.assertEqual(evaluate(team, before)["choice"]["purchases"], [])
        unlocked = replace(before, events_completed=("p:E4",))
        result = evaluate(team, unlocked)
        self.assertEqual(result["choice"]["purchases"], ["p:locked"])
        self.assertEqual(result["choice"]["state_after_learning"]["events_completed"], ("p:E4",))

    def test_provider_budget_validation_and_deterministic_serializable_output(self):
        team = profile(panels=[panel("cap", "cap", {"Vo": 100})])
        before = snapshot(team)
        first, second = evaluate(team, before), evaluate(team, before)
        self.assertEqual(first, second)
        self.assertIsNone(first["true_end_probability"])
        json.dumps(first, allow_nan=False)
        evaluator = TerminalEvaluator(team, assumptions(), model())
        for changes in ({"provider_id": ""}, {"assumptions": ()}, {"max_states": 0}):
            with self.assertRaises(ValueError):
                score_preweek(evaluator, before, provider, **{**dict(provider_id="synthetic", assumptions=("explicit",)), **changes})
        with self.assertRaises(ValueError):
            evaluate(team, replace(before, sp=20.5))
        with self.assertRaises(ValueError):
            evaluate(team, replace(before, route_feasible=False))

    def test_policy_proposal_learns_first_and_does_not_queue_a_stale_week_target(self):
        team = profile(panels=[panel("cap", "cap", {"Vo": 100})])
        result = evaluate(team, snapshot(team))
        decision = to_weekly_decision(result)
        self.assertEqual((decision.kind, decision.pre_actions), ("spend_sp", ("spend_sp",)))
        self.assertIsNone(decision.option_id)
        self.assertIsNone(decision.audition_id)
        self.assertEqual(decision.plan["evaluation"]["choice"]["purchases"], ["p:cap"])
        # Learning has completed; the next call uses a new observed snapshot.
        paid = state(team).from_dict(result["choice"]["state_after_learning"])
        resumed = to_weekly_decision(evaluate(team, paid))
        self.assertEqual((resumed.kind, resumed.option_id, resumed.pre_actions), ("lesson", "lesson_vo", ()))

    def test_policy_proposal_selects_audition_without_unnecessary_sp_visit(self):
        team = profile()
        before = state(team)
        def inputs(paid):
            return observed(paid, auditions=(Audition("trial", "Trial", 100000, min_fans=100000),)), transitions(
                auditions={"trial": AuditionModel(1, 1, {}, {})})
        decision = to_weekly_decision(evaluate(team, before, inputs))
        self.assertEqual((decision.kind, decision.audition_id, decision.pre_actions), ("audition", "trial", ()))

    def test_policy_proposal_stops_when_all_weekly_inputs_are_unread(self):
        decision = to_weekly_decision({"status": "BLOCKED_WEEK_INPUTS", "choice": None})
        self.assertEqual((decision.kind, decision.pre_actions), ("stop", ()))


if __name__ == "__main__":
    unittest.main()
