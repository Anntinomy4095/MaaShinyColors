"""Meaningful one-week settlement and shared-Q contracts; synthetic offline data."""
from dataclasses import replace
import json
import unittest

from agent.core.wing_state import Audition, ScheduleOption, WingState
from agent.core.true_end.models import SupportSkill
from agent.core.true_end.terminal_value import TerminalEvaluator
from agent.core.true_end.week_actions import (AuditionModel, EventOutcome, FailureOutcome, RestOutcome,
    Reward, TrainingModel, WeekActionModel, build_week_successors)
from test_true_end_run_state import assumptions, panel, profile, project, state
from test_true_end_terminal_value import model as terminal_model


def observed(before, *, options=(), auditions=(), **extra):
    return WingState("week", before.next_week // 8 + 1, before.next_week % 8 + 1,
                     dict(before.panels.stats), before.fans, before.stamina, 100,
                     sp=before.sp, memory_level=before.memory_level, stat_caps=dict(before.panels.caps),
                     options=options, auditions=auditions, confidence=1, recognized=True, **extra)


def lesson(**extra):
    fields = dict(id="lesson_vo", kind="lesson", attribute="Vo", preview={"Vo": 28},
                  sp_gain=4, fan_gain=30, stamina_cost=15, trouble_rate=0, supports=0)
    return ScheduleOption(**{**fields, **extra})


def transitions(*, training=None, rest=None, auditions=None, events=None):
    return WeekActionModel(("Synthetic explicit probabilities, normal total uncapped preview.",),
                           training or {}, rest or {}, auditions or {}, events or {})


def train(**extra):
    return TrainingModel(**{**dict(perfect_given_success=.1, preview_is_total=True, preview_is_uncapped=True), **extra})


def support(card, kind, amount, *, attribute=None, room=None, unit=None, unsupported=()):
    skill = SupportSkill(kind, kind, {80: amount}, attribute, room, unsupported=unsupported)
    return replace(card, card=replace(card.card, unit=unit), support_effects=((skill, amount),))


class WeekActionTests(unittest.TestCase):
    def build(self, *, team=None, before=None, option=None, dynamics=None, auditions=(), **extra):
        team = team or profile()
        before = before or state(team)
        option = option or lesson()
        result = build_week_successors(team, before, observed(before, options=(option,), auditions=auditions, **extra),
                                       dynamics or transitions(training={option.id: train()}))
        return team, before, result

    def test_normal_perfect_consume_one_week_without_mutating_input(self):
        team, before, result = self.build()
        normal, perfect = result.actions["lesson_vo"]
        self.assertEqual((normal.probability, perfect.probability), (.9, .1))
        self.assertEqual((normal.state.panels.stats["Vo"], perfect.state.panels.stats["Vo"]), (128, 156))
        self.assertEqual((normal.state.sp, perfect.state.sp), (4, 8))
        self.assertEqual((normal.state.fans, perfect.state.fans), (500030, 500060))
        self.assertTrue(all(b.state.next_week == 25 and b.state.stamina == 85 for b in (normal, perfect)))
        self.assertEqual((before.next_week, before.sp, before.panels.stats["Vo"]), (24, 0, 100))
        self.assertIn("lesson_vo:perfect_integer_preview_doubling_approximation", result.unknowns)

    def test_exact_perfect_preserves_rounding_28_55_not_56(self):
        _, _, result = self.build(dynamics=transitions(training={"lesson_vo": train(perfect_reward=Reward({"Vo": 55}, 7, 59))}))
        self.assertEqual(result.actions["lesson_vo"][1].state.panels.stats["Vo"], 155)
        self.assertFalse(any("doubling" in u for u in result.unknowns))

    def test_conditional_probabilities_failure_and_catastrophe(self):
        failures = (FailureOutcome(.75, {"Vo": -5}), FailureOutcome(.25, {"Vo": -1000}, True))
        _, _, result = self.build(option=lesson(trouble_rate=20, supports=1, support_ids=("guest",)),
                                 dynamics=transitions(training={"lesson_vo": train(failures=failures)}))
        branches = result.actions["lesson_vo"]
        for actual, expected in zip((b.probability for b in branches), (.72, .08, .15, .05)):
            self.assertAlmostEqual(actual, expected)
        self.assertEqual([b.state.panels.stats["Vo"] for b in branches], [128, 156, 95, 0])
        self.assertEqual([b.state.stamina for b in branches], [85, 85, 85, 0])
        self.assertEqual([b.state.bonds["guest"] for b in branches], [57, 57, 49, 49])
        self.assertEqual([b.state.sp for b in branches], [4, 8, 0, 0])

    def test_trouble_without_loss_model_excludes_only_that_action(self):
        team = profile()
        before = state(team)
        obs = observed(before, options=(lesson(trouble_rate=1), ScheduleOption("rest", "rest")))
        result = build_week_successors(team, before, obs, transitions(
            training={"lesson_vo": train()}, rest={"rest": (RestOutcome(1, 50),)}))
        self.assertEqual(set(result.actions), {"rest"})
        self.assertEqual(result.excluded["lesson_vo"], "conditional_failure_losses_unread")

    def test_failure_cost_clamps_at_zero_and_no_support_effects(self):
        team = profile()
        guest = support(team.guest, "mastery_stamina", 6, room="Vo")
        team = replace(team, guest=guest)
        before = replace(state(team), stamina=10)
        _, _, result = self.build(team=team, before=before,
            option=lesson(trouble_rate=100, supports=1, support_ids=("guest",)),
            dynamics=transitions(training={"lesson_vo": train(failures=(FailureOutcome(1, {}),))}))
        branch = result.actions["lesson_vo"][0]
        self.assertEqual((branch.state.stamina, branch.state.bonds["guest"], branch.state.sp), (0, 49, 0))

    def test_success_caps_and_recovery_not_double_counted_or_doubled(self):
        team = profile()
        guest = support(team.guest, "mastery_cap", 5, room="Vo", attribute="Vo")
        recovery = SupportSkill("recovery", "mastery_stamina", {80: 6}, room="Vo")
        guest = replace(guest, support_effects=(*guest.support_effects, (recovery, 6)))
        team = replace(team, guest=guest)
        before = state(team)
        before = replace(before, panels=replace(before.panels, stats={**before.panels.stats, "Vo": 499}),
                         bonds={**before.bonds, "guest": 97})
        _, _, result = self.build(team=team, before=before,
            option=lesson(supports=1, support_ids=("guest",), stamina_gain=18))
        normal, perfect = result.actions["lesson_vo"]
        self.assertEqual((normal.state.panels.caps["Vo"], perfect.state.panels.caps["Vo"]), (505, 510))
        self.assertEqual((normal.state.panels.stats["Vo"], perfect.state.panels.stats["Vo"]), (505, 510))
        self.assertEqual((normal.state.stamina, perfect.state.stamina), (100, 100))
        self.assertEqual((normal.state.bonds["guest"], perfect.state.bonds["guest"]), (100, 100))
        before = replace(before, stamina=50)
        _, _, result = self.build(team=team, before=before, option=lesson(supports=1, support_ids=("guest",)))
        self.assertEqual([b.state.stamina for b in result.actions["lesson_vo"]], [53, 53])

    def test_unit_mastery_counts_same_room_others_excluding_self(self):
        team = profile()
        active = list(team.active)
        active[0] = support(active[0], "unit_mastery_cap", 5, attribute="Vo", unit="same")
        active[1] = replace(active[1], card=replace(active[1].card, unit="same"))
        active[2] = replace(active[2], card=replace(active[2].card, unit="other"))
        team = replace(team, active=tuple(active))
        _, _, result = self.build(team=team, option=lesson(supports=3, support_ids=("s0", "s1", "s2")))
        self.assertEqual([b.state.panels.caps["Vo"] for b in result.actions["lesson_vo"]], [505, 510])

    def test_room_identity_and_missing_totals_do_not_invent_zero(self):
        cases = ((lesson(supports=1), "room_support_identity_unread"),
                 (lesson(supports=1, support_ids=("foreign",)), "room_support_identity_invalid"),
                 (lesson(supports=2, support_ids=("guest", "guest")), "room_support_identity_invalid"),
                 (lesson(sp_gain=None), "training_reward_cost_or_trouble_unread"),
                 (lesson(preview={}), "training_reward_cost_or_trouble_unread"),
                 (lesson(stamina_recovery=True), "room_stamina_recovery_unread"))
        for option, reason in cases:
            with self.subTest(reason=reason):
                _, _, result = self.build(option=option)
                self.assertEqual(result.excluded[option.id], reason)
                self.assertFalse(result.actions)

    def test_uncertain_preview_interpretation_excluded(self):
        for fields in ({"preview_is_total": False}, {"preview_is_uncapped": False}):
            _, _, result = self.build(dynamics=transitions(training={"lesson_vo": train(**fields)}))
            self.assertEqual(result.excluded["lesson_vo"], "normal_preview_must_be_total_uncapped")

    def test_work_id_uses_existing_room_mapping_and_mastery_is_in_total(self):
        team = profile()
        guest = support(team.guest, "mastery_sp", 20, room="talk")
        team = replace(team, guest=guest)
        option = lesson(id="work_talk", kind="work", attribute=None, preview={"Da": 4, "Me": 4},
                        sp_gain=35, supports=1, support_ids=("guest",))
        _, _, result = self.build(team=team, option=option)
        self.assertEqual([b.state.sp for b in result.actions[option.id]], [35, 70])  # no second +20 mastery

    def test_rest_distribution_clamps_and_has_no_training_rewards(self):
        team = profile()
        before = replace(state(team), stamina=40)
        _, _, result = self.build(team=team, before=before, option=ScheduleOption("rest", "rest"),
            dynamics=transitions(rest={"rest": (RestOutcome(.2, 30), RestOutcome(.5, 50), RestOutcome(.3, 70))}))
        branches = result.actions["rest"]
        self.assertEqual([b.state.stamina for b in branches], [70, 90, 100])
        self.assertTrue(all(b.state.sp == before.sp and b.state.panels == before.panels and b.state.bonds == before.bonds for b in branches))

    def test_audition_first_only_mastery_includes_guest_no_stamina_or_base_me_sp(self):
        team = profile()
        guest = support(team.guest, "audition_mastery_sp", 20)
        mental = SupportSkill("aud_me", "audition_mastery_stat", {80: 6}, attribute="Me")
        team = replace(team, guest=replace(guest, support_effects=(*guest.support_effects, (mental, 6))))
        before = replace(state(team), stamina=2)
        audition = Audition("legend", "THE LEGEND", 100000, min_fans=100000, required_rank="B")
        _, _, result = self.build(team=team, before=before, auditions=(audition,), rank="B",
            dynamics=transitions(training={"lesson_vo": train()}, auditions={"legend": AuditionModel(.8, .25, {"Vo": 8}, {})}))
        branches = result.actions["legend"]
        self.assertEqual([b.label for b in branches], ["first", "second", "loss"])
        for b, expected in zip(branches, (.2, .6, .2)):
            self.assertAlmostEqual(b.probability, expected)
        self.assertEqual([b.state.sp for b in branches], [20, 0, 0])
        self.assertEqual([b.state.panels.stats["Me"] for b in branches], [106, 100, 100])
        self.assertEqual([b.state.fans for b in branches], [600000, 600000, 500000])
        self.assertTrue(all(b.state.stamina == 2 and b.state.bonds == before.bonds for b in branches))

    def test_audition_entry_gates_and_unavailable_are_excluded(self):
        audition = Audition("a", "a", 1000, min_fans=600000)
        _, _, result = self.build(auditions=(audition,))
        self.assertEqual(result.excluded["a"], "audition_fan_gate")
        for rank, reason in ((None, "audition_rank_unread"), ("D", "audition_rank_gate")):
            _, _, result = self.build(auditions=(replace(audition, min_fans=0, required_rank="B"),), rank=rank)
            self.assertEqual(result.excluded["a"], reason)
        _, _, result = self.build(option=lesson(available=False))
        self.assertEqual(result.excluded["lesson_vo"], "unavailable")

    def test_unknown_audition_gate_cannot_win_even_with_huge_reward(self):
        audition = Audition("unknown", "unknown", 1000000)
        team, before, result = self.build(auditions=(audition,), dynamics=transitions(
            training={"lesson_vo": train()}, auditions={"unknown": AuditionModel(1, 1, {"Vo": 1000}, {})}))
        self.assertEqual(result.excluded["unknown"], "audition_fan_gate_unread")
        report = result.score(TerminalEvaluator(team, assumptions(), terminal_model()), before)
        self.assertEqual(report["choice"], "lesson_vo")

    def test_end_of_each_season_uses_settled_fans_and_consumes_week(self):
        team = profile()
        for week, gate in ((7, 1000), (15, 10000), (23, 50000), (31, 100000)):
            before = replace(state(team), next_week=week, fans=gate - 30)
            _, _, result = self.build(team=team, before=before, option=lesson(trouble_rate=20),
                dynamics=transitions(training={"lesson_vo": train(perfect_given_success=0, failures=(FailureOutcome(1, {}),))}))
            win, loss = result.actions["lesson_vo"]
            self.assertEqual((win.state.next_week, loss.state.next_week), (week + 1, week + 1))
            self.assertEqual((win.state.route_feasible, loss.state.route_feasible), (True, False))
            self.assertEqual(win.state.phase, "semifinal" if week == 31 else "week")

    def test_s4_100k_enters_semifinal_but_has_zero_true_end_value(self):
        team = profile()
        before = replace(state(team), next_week=31, fans=100000)
        _, _, result = self.build(team=team, before=before)
        report = result.score(TerminalEvaluator(team, assumptions(), terminal_model()), before)
        self.assertTrue(result.actions["lesson_vo"][0].state.route_feasible)
        self.assertEqual(report["ranked"][0]["expected_value_pep"], 0)

    def test_post_action_sp_cannot_recover_already_clipped_training(self):
        team = profile(panels=[panel("cap", "cap", {"Vo": 100}, cost=4)])
        before = state(team)
        before = replace(before, next_week=31, panels=replace(before.panels, stats={**before.panels.stats, "Vo": 500}))
        _, _, result = self.build(team=team, before=before)
        evaluator = TerminalEvaluator(team, assumptions(), terminal_model())
        report = result.score(evaluator, before)
        self.assertEqual(report["ranked"][0]["expected_value_pep"], 500)
        self.assertEqual(result.actions["lesson_vo"][0].state.panels.caps["Vo"], 500)
        purchased = project(team, result.actions["lesson_vo"][0].state, purchases=("p:cap",))
        self.assertEqual(purchased.caps["Vo"], 600)
        self.assertEqual(purchased.effective_stats["Vo"], 500)

    def test_week_ranking_holds_panels_fixed_preweek_purchase_needs_new_snapshot(self):
        team = profile(panels=[panel("cap", "cap", {"Vo": 100}, cost=20)])
        before = state(team)
        before = replace(before, next_week=31, sp=20,
                         panels=replace(before.panels, stats={**before.panels.stats, "Vo": 499}))
        dynamics = transitions(training={"lesson_vo": train(perfect_given_success=0)})
        _, _, result = self.build(team=team, before=before, dynamics=dynamics)
        report = result.score(TerminalEvaluator(team, assumptions(), terminal_model()), before)
        self.assertEqual(report["comparison_scope"], "WEEK_ACTIONS_WITH_CURRENT_PANELS")
        self.assertEqual(result.actions["lesson_vo"][0].state.panels.stats["Vo"], 500)
        learned = replace(before, sp=0, panels=replace(before.panels,
            caps={**before.panels.caps, "Vo": 600}, learned_panel_ids=("p:cap",)))
        _, _, after_purchase = self.build(team=team, before=learned, dynamics=dynamics)
        self.assertEqual(after_purchase.actions["lesson_vo"][0].state.panels.stats["Vo"], 527)

    def test_event_branch_settles_after_training_not_doubled_and_does_not_repeat(self):
        team = profile(events=[{"id": "e", "week": 25, "gains": {"Vo": 10, "SP": 10}}])
        before = state(team)
        before = replace(before, panels=replace(before.panels, stats={**before.panels.stats, "Vo": 499}))
        event = EventOutcome(.5, "p:e", Reward({"Vo": 10}, 10, 0), cap_gain={"Vo": 100}, memory_level_after=1)
        no_event = EventOutcome(.5, None, Reward({}, 0, 0))
        dynamics = transitions(training={"lesson_vo": train()}, events={"lesson_vo": {"normal": (event, no_event)}})
        _, _, result = self.build(team=team, before=before, dynamics=dynamics)
        gained, omitted, perfect = result.actions["lesson_vo"]
        self.assertEqual((gained.state.panels.stats["Vo"], gained.state.sp), (510, 14))
        self.assertEqual((omitted.state.panels.stats["Vo"], perfect.state.panels.stats["Vo"]), (500, 500))
        self.assertEqual(gained.state.events_completed, ("p:e",))
        self.assertEqual(omitted.state.events_completed, ())
        evaluator = TerminalEvaluator(team, assumptions(), terminal_model())
        self.assertEqual(evaluator.evaluate(gained.state, learn_panels=False).projection.sp, 14)
        before = replace(before, events_completed=("p:e",))
        _, _, result = self.build(team=team, before=before, dynamics=dynamics)
        self.assertEqual(result.excluded["lesson_vo"], "event_already_completed")

    def test_event_cannot_use_unknown_id_or_wrong_action_branch(self):
        for event_map, reason in (({"normal": (EventOutcome(1, "p:unknown", Reward({}, 0, 0)),)}, "event_not_in_profile"),
                                  ({"loss": (EventOutcome(1, None, Reward({}, 0, 0)),)}, "event_outcome_not_in_action")):
            _, _, result = self.build(dynamics=transitions(training={"lesson_vo": train()}, events={"lesson_vo": event_map}))
            self.assertEqual(result.excluded["lesson_vo"], reason)

    def test_q_equals_terminal_evaluation_without_extra_immediate_terms(self):
        team = profile()
        before = replace(state(team), next_week=31)
        _, _, result = self.build(team=team, before=before)
        evaluator = TerminalEvaluator(team, assumptions(), terminal_model())
        report = result.score(evaluator, before)
        manual = sum(b.probability * evaluator.evaluate(b.state).value_pep for b in result.actions["lesson_vo"])
        self.assertEqual(report["ranked"][0]["expected_value_pep"], manual)
        self.assertEqual(manual, 130.8)
        self.assertAlmostEqual(report["ranked"][0]["delta_pep"], 30.8)
        self.assertEqual([b["outcome"] for b in report["ranked"][0]["branches"]], ["normal", "perfect"])
        self.assertIsNone(report["true_end_probability"])
        json.dumps(report, allow_nan=False)
        with self.assertRaisesRegex(ValueError, "different"):
            result.score(evaluator, replace(before, sp=1))

    def test_actual_observation_profile_binding_and_eliminated_run(self):
        team = profile()
        before = state(team)
        obs = observed(before, options=(lesson(),))
        for altered in (replace(obs, sp=1), replace(obs, stats={**obs.stats, "Vo": 200}),
                        replace(obs, week=2), replace(obs, memory_level=1), replace(obs, stamina=None)):
            with self.subTest(altered=altered):
                with self.assertRaises(ValueError):
                    build_week_successors(team, before, altered, transitions(training={"lesson_vo": train()}))
        with self.assertRaisesRegex(ValueError, "active"):
            build_week_successors(team, replace(before, route_feasible=False), obs, transitions())
        with self.assertRaisesRegex(ValueError, "different"):
            build_week_successors(replace(team, active=tuple(reversed(team.active))), before, obs, transitions())

    def test_model_probability_and_data_validation(self):
        for build in (lambda: train(perfect_given_success=1.1), lambda: RestOutcome(-.1, 50),
                      lambda: FailureOutcome(1, {"Vo": 1}), lambda: Reward({"Vo": float("nan")}, 0, 0),
                      lambda: AuditionModel(1, 1, {"Me": 1}, {}), lambda: WeekActionModel(()),
                      lambda: EventOutcome(1, None, Reward({"Vo": 1}, 0, 0))):
            with self.assertRaises(ValueError):
                build()
        _, _, result = self.build(option=ScheduleOption("rest", "rest"), dynamics=transitions(rest={"rest": (RestOutcome(.5, 50),)}))
        self.assertEqual(result.excluded["rest"], "outcome_probabilities_must_sum_to_one")

    def test_events_never_silently_drop_probability_mass_at_branch_budget(self):
        events = tuple(EventOutcome(1 / 5, None, Reward({}, 0, 0)) for _ in range(5))
        _, _, result = self.build(dynamics=transitions(training={"lesson_vo": train()},
            events={"lesson_vo": {"normal": events, "perfect": events}}))
        self.assertEqual(result.excluded["lesson_vo"], "action_exceeds_eight_branch_budget")


if __name__ == "__main__":
    unittest.main()
