"""Counterfactual action comparisons and reader/profile-to-policy integration; synthetic inputs only."""
from __future__ import annotations

import dataclasses
import unittest

from agent.core import produce_config as pc
from agent.core import wing_nav as nav
from agent.core import wing_policy as policy
from agent.core import wing_score as score
from agent.core import wing_state as ws
from tests.test_wing_team_profile import fixture


def week(*, number=2, rich=False, auditions=True):
    options = (
        ws.ScheduleOption("lesson_vo", "lesson", "Vo", available=True, preview={"Vo": 24},
                          stamina_cost=18, supports=0, sp_gain=8, fan_gain=0, trouble_rate=0),
        ws.ScheduleOption("lesson_da", "lesson", "Da", available=True, preview={"Da": 24},
                          stamina_cost=18, supports=3 if rich else 0, sp_gain=60 if rich else 8,
                          fan_gain=0, trouble_rate=0),
        ws.ScheduleOption("lesson_vi", "lesson", "Vi", available=True, preview={"Vi": 24},
                          stamina_cost=18, supports=0, sp_gain=8, fan_gain=0, trouble_rate=0),
        ws.ScheduleOption("rest", "rest", available=True),
    )
    return ws.WingState(phase="week", season=1, week=number,
                        stats={"Vo": 82, "Da": 50, "Vi": 50, "Me": 100}, fans=0,
                        stamina=82, max_stamina=100, condition="普通", sp=0,
                        stat_caps={key: 500 for key in ws.STATS}, options=options,
                        auditions=(ws.Audition("s1_1k", "+1000", 1000, 0, True,
                                               recommended={"Vo": 70}),) if auditions else (),
                        trend={"Vo": 1, "Da": 2, "Vi": 3}, rank="F", confidence=1.0, recognized=True)


def fixed_win(*args):
    return 0.922, ()


class OpportunityCostTests(unittest.TestCase):
    def test_strong_support_room_changes_choice_without_inflating_audition(self):
        weak = score.score_week(week(), "Vo", win_model=fixed_win)
        strong = score.score_week(week(rich=True), "Vo", win_model=fixed_win)
        self.assertEqual(weak.choice.id, "s1_1k")
        self.assertEqual(strong.choice.id, "lesson_da")
        before = next(c for c in weak.ranked if c.kind == "audition")
        after = next(c for c in strong.ranked if c.kind == "audition")
        self.assertEqual(before.score, after.score)
        self.assertGreater(weak.context["audition_margins"]["s1_1k"], 0)
        self.assertLess(strong.context["audition_margins"]["s1_1k"], 0)

    def test_primary_preview_does_not_inflate_progress_urgency_or_surplus_fans(self):
        state = week()
        small = ws.Audition("s1_500", "+500", 500, 0, True, recommended={"Vo": 70})
        state = dataclasses.replace(state, auditions=(*state.auditions, small))
        strong_room = dataclasses.replace(state.options[0], preview={"Vo": 100}, supports=3)
        enhanced = dataclasses.replace(state, options=(strong_room, *state.options[1:]))
        weak = score.score_week(state, "Vo", win_model=fixed_win)
        strong = score.score_week(enhanced, "Vo", win_model=fixed_win)
        self.assertEqual(weak.choice.id, "s1_1k")
        self.assertEqual(strong.choice.id, "lesson_vo")
        a = {c.id: c for c in weak.ranked if c.kind == "audition"}
        b = {c.id: c for c in strong.ranked if c.kind == "audition"}
        for candidate_id in a:
            self.assertEqual(a[candidate_id].score, b[candidate_id].score)
        self.assertGreater(a[small.id].terms["fans"], 0)

    def test_last_necessary_week_keeps_hard_deadline(self):
        selected = policy.decide(week(number=8, rich=True), "vo_score", "true_end",
                                 {"win_model": fixed_win})
        self.assertEqual((selected.kind, selected.rule), ("audition", "score_tight_deadline_audition"))

    def test_failure_probability_changes_choice_with_urgency_disabled(self):
        confident = score.score_week(week(), "Vo", params={"urgency_max": 0}, win_model=fixed_win)
        risky = score.score_week(week(), "Vo", params={"urgency_max": 0},
                                win_model=lambda *args: (0.4, ()))
        self.assertEqual(confident.choice.id, "s1_1k")
        self.assertEqual(risky.choice.id, "lesson_vo")
        unknown = score.score_week(week(), "Vo", win_model=lambda *args: (None, ("unread",)))
        self.assertEqual(next(c for c in unknown.excluded if c.id == "s1_1k").notes, ("p_win_unknown",))

    def test_rest_is_in_opportunity_cost_and_every_candidate_is_explained(self):
        state = dataclasses.replace(week(), stamina=30)
        result = score.score_week(state, "Vo", win_model=fixed_win)
        rest = next(c for c in result.ranked if c.kind == "rest")
        self.assertEqual(result.context["best_non_audition_id"], rest.id)
        decision = policy.decide(state, "vo_score", params={"win_model": fixed_win})
        self.assertEqual({c["id"] for c in decision.plan["scores"]}, {c.id for c in result.ranked})

    def test_future_week_prior_rejects_invalid_values(self):
        for invalid in (-1, 0, True, float("nan"), float("inf"), "30"):
            with self.subTest(value=invalid), self.assertRaises(ValueError):
                score.ScoreParams.from_overrides({"future_week_pep": invalid})


class RoomBridgeTests(unittest.TestCase):
    def readings(self, room):
        return {"season": 1, "weeks_left": 7, "stats": {"Vo": 82, "Me": 100}, "fans": 1000,
                "sp": 0, "stamina_pct": 82, "stat_caps": {"Vo": 500}, "condition": "普通", "rooms": [room]}

    def test_observed_room_values_survive_into_the_scorer(self):
        raw = {"id": "lesson_vo", "kind": "lesson", "attribute": "Vo", "level": 2, "supports": 3,
               "excellent": 2, "preview": {"Vo": 42}, "sp_gain": 10, "fan_gain": 300,
               "trouble_rate": 3.0, "stamina_cost": 15, "stamina_gain": 9,
               "support_ids": ["s1", "s2", "s3"]}
        state, problems = nav.build_week_state(self.readings(raw), nav.RunMemory())
        self.assertFalse(problems)
        option = state.options[0]
        self.assertEqual((option.supports, option.excellent, option.preview, option.sp_gain), (3, 2, {"Vo": 42}, 10))
        self.assertEqual((option.support_ids, option.stamina_gain, option.fan_gain), (("s1", "s2", "s3"), 9, 300))
        self.assertEqual((state.stat_caps, state.condition), ({"Vo": 500}, "普通"))
        self.assertEqual(ws.ScheduleOption.from_dict(option.to_dict()), option)

    def test_badge_is_not_an_exact_excellent_count(self):
        raw = {"id": "lesson_vo", "kind": "lesson", "attribute": "Vo", "level": 2,
               "supports": 3, "excellent": True, "sp_gain": 4, "fan_gain": 0}
        state, problems = nav.build_week_state(self.readings(raw), nav.RunMemory())
        self.assertFalse(problems)
        self.assertIsNone(state.options[0].excellent)
        self.assertTrue(state.options[0].excellent_present)
        self.assertEqual(ws.ScheduleOption.from_dict(state.options[0].to_dict()), state.options[0])
        result = score.score_week(state, "Vo")
        candidate = next(c for c in result.ranked if c.id == "lesson_vo")
        self.assertIn("excellent_count", candidate.unknown)
        self.assertIn("excellent_count_lower_bound", candidate.notes)

    def test_unread_fields_stay_unknown_and_bad_room_values_are_rejected(self):
        raw = {"id": "lesson_vo", "kind": "lesson", "attribute": "Vo", "level": "MAX"}
        state, problems = nav.build_week_state(self.readings(raw), nav.RunMemory())
        self.assertFalse(problems)
        self.assertEqual(state.options[0].level, 5)
        self.assertIsNone(state.options[0].supports)
        self.assertEqual(state.options[0].preview, {})
        state, problems = nav.build_week_state(self.readings({**raw, "supports": 8}), nav.RunMemory())
        self.assertIsNone(state)
        self.assertTrue(problems[0].startswith("room_error:"))


class TeamToPolicyTests(unittest.TestCase):
    def test_level_break_and_room_identity_reach_runtime_scoring(self):
        team, owned, cards, mapping = fixture()
        data = pc.default_config("true_end")
        data["team"] = {**pc.DEFAULT_TEAM, **team}
        config = pc.ProduceConfig.from_dict(data)
        rt = nav.WingNavRuntime.from_produce_config(config, owned=owned, cards=cards, id_map=mapping)
        self.assertTrue(rt.profile_report["complete"], rt.profile_report)
        rt.memory.auditions_seen = {"key": [1, 7], "pages": [], "done": True}
        support_id = team["s_cards"][0]
        readings = {"season": 1, "weeks_left": 7, "stats": {"Vo": 82, "Da": 50, "Vi": 50, "Me": 100}, "fans": 1000,
                    "sp": 0, "stamina_pct": 82, "stat_caps": {"Vo": 500},
                    "rooms": [{"id": "lesson_vo", "kind": "lesson", "attribute": "Vo", "level": 2,
                               "supports": 1, "support_ids": [support_id], "selected": False}]}
        rt.decide("Schedule", readings)
        self.assertIn("scores", rt.memory.last_week_decision.get("plan", {}), rt.memory.last_week_decision)
        actual = rt.memory.last_week_decision["plan"]
        candidate = next(c for c in actual["scores"] if c["id"] == "lesson_vo")
        self.assertIn("gain_formula_team_profile", candidate["notes"])
        self.assertTrue(actual["team_profile"]["complete"])
        self.assertIn(team["guest"], rt.policy_params["score_team"].supports)

    def test_work_mastery_applies_once_and_observed_preview_is_not_double_counted(self):
        profile = score.TeamProfile({"s1": score.SupportProfile("s1", stats={"Vo": 100}, bond0=50,
                                  masteries={"radio": {"stat_Vo": 10, "stat_Me": 20, "sp": 5}})})
        room = ws.ScheduleOption("work_radio", "work", available=True, level=1, supports=1,
                                 support_ids=("s1",), stamina_cost=14, trouble_rate=0)
        state = dataclasses.replace(week(auditions=False), options=(*week().options, room))
        result = score.score_week(state, "Vo", team=profile)
        candidate = next(c for c in result.ranked if c.id == room.id)
        self.assertAlmostEqual(candidate.terms["stat_gain"], 14 * 1.1)
        self.assertAlmostEqual(candidate.terms["mental"], 35 * 0.8 * 1.1)
        self.assertAlmostEqual(candidate.terms["sp"], 9 * 1.1)
        preview = dataclasses.replace(room, preview={"Vo": 14, "Me": 35}, sp_gain=9)
        second = score.score_week(dataclasses.replace(state, options=(*week().options, preview)), "Vo", team=profile)
        observed = next(c for c in second.ranked if c.id == room.id)
        self.assertAlmostEqual(candidate.terms["stat_gain"], observed.terms["stat_gain"])
        self.assertAlmostEqual(candidate.terms["mental"], observed.terms["mental"])

    def test_missing_support_identity_and_partial_profile_are_reported(self):
        state = week(auditions=False)
        missing = dataclasses.replace(state.options[0], supports=2)
        result = score.score_week(dataclasses.replace(state, options=(missing, *state.options[1:])), "Vo")
        self.assertIn("support_ids", next(c for c in result.ranked if c.id == missing.id).unknown)
        profile = score.TeamProfile({"s1": score.SupportProfile("s1", unknowns=("ownership.level:missing",))})
        known = dataclasses.replace(missing, supports=1, support_ids=("s1",))
        result = score.score_week(dataclasses.replace(state, options=(known, *state.options[1:])), "Vo", team=profile)
        self.assertIn("support.s1.ownership.level:missing", next(c for c in result.ranked if c.id == known.id).unknown)

    def test_known_mastery_is_credited_when_card_stats_are_incomplete(self):
        profile = score.TeamProfile({"s1": score.SupportProfile("s1", bond0=50,
                                    masteries={"lesson_Vo": {"stat": 20}}, unknowns=("stats.Vo:missing",))})
        state = week(auditions=False)
        room = dataclasses.replace(state.options[0], preview={}, level=2, supports=1, support_ids=("s1",))
        result = score.score_week(dataclasses.replace(state, options=(room, *state.options[1:])), "Vo", team=profile)
        candidate = next(c for c in result.ranked if c.id == room.id)
        self.assertAlmostEqual(candidate.terms["stat_gain"], (22 + 20) * 1.1)
        self.assertIn("gain_formula_lower_bound", candidate.notes)
        self.assertIn("support.s1.stats.Vo:missing", candidate.unknown)


if __name__ == "__main__":
    unittest.main()
