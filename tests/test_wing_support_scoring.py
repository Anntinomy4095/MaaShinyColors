"""Support skills change weekly action value using the actual formed team."""
import dataclasses
import unittest

from agent.core import wing_score as sc, wing_state as ws
from tests.test_wing_te_rules import run_state
from tests.test_wing_team_profile import fixture, P, SUPPORTS
from agent.core.wing_team_profile import build_team_profile


class SupportScoringTests(unittest.TestCase):
    def test_db_rarity_reaches_the_real_weekly_stamina_and_rest_terms(self):
        team, owned, cards, ids = fixture()
        raw = cards[f"te-{SUPPORTS[0]}"]
        raw["rarity"] = "S_SSR"
        raw["support_skills"] += [
            {"id": "cost", "kind": "stamina_support", "levels": {"1": 1, "70": 5},
             "unsupported": ["レッスンかお仕事を選択する時に15%の確率で体力消費量-50%"]},
            {"id": "rest", "kind": "rest_support", "levels": {"1": 1, "70": 5},
             "unsupported": ["「休む」を選択時に15%の確率で体力回復量+15"]},
        ]
        owned["details"][SUPPORTS[0]] = {"level": 60, "limit_break": 0}
        built = build_team_profile(team, owned, cards=cards, id_map=ids)
        profile = built.profile.supports[SUPPORTS[0]]
        self.assertTrue(built.complete, built.unknowns)
        self.assertAlmostEqual(profile.stamina_support[0]["activation_probability"], .03)
        self.assertAlmostEqual(profile.rest_support[0]["expected_recovery"], .45)
        owned["details"][SUPPORTS[0]] = {"level": 80, "limit_break": 4}
        built = build_team_profile(team, owned, cards=cards, id_map=ids)
        option = ws.ScheduleOption("lesson_vo", "lesson", "Vo", preview={"Vo": 20}, level=1, supports=0)
        # Keep the lesson above zero stamina and the rest below its cap; neither benefit is clipped away.
        for kind, stamina in (("lesson", 60), ("rest", 0)):
            plain = self.candidate(sc.TeamProfile(), option=option, stamina=stamina)
            scored = self.candidate(built.profile, option=option, stamina=stamina)
            a = next(c for c in plain.ranked if c.kind == kind)
            b = next(c for c in scored.ranked if c.kind == kind)
            self.assertGreater(b.terms["stamina"], a.terms["stamina"])

    def candidate(self, team, *, option=None, first=.25, auditions=False, stamina=60):
        option = option or ws.ScheduleOption("lesson_vo", "lesson", "Vo", level=1, supports=2,
                                            support_ids=("a", "b"))
        state = run_state(2, 3, 3000, 220, options=(option, ws.ScheduleOption("rest", "rest")),
                          stamina=stamina, trouble_rate=0)
        if not auditions:
            state = dataclasses.replace(state, auditions=())
        result = sc.score_week(state, "Vo", team=team, win_model=lambda *a: (1., ()),
                               first_model=lambda *a: (first, ()))
        return result

    def test_unit_counts_only_same_unit_and_p(self):
        holder = sc.SupportProfile("a", masteries={"unit": {"sp": 10, "stamina": 3}}, unit="A")
        other = sc.SupportProfile("b", unit="B")
        no_p = self.candidate(sc.TeamProfile({"a": holder, "b": other}))
        with_p = self.candidate(sc.TeamProfile({"a": holder, "b": other}, "A"))
        a = next(c for c in no_p.ranked if c.kind == "lesson")
        b = next(c for c in with_p.ranked if c.kind == "lesson")
        self.assertAlmostEqual(b.terms["sp"] - a.terms["sp"], 11.)
        self.assertNotIn("unit_mastery_sp_10", a.notes)
        self.assertIn("unit_mastery_sp_10", b.notes)
        self.assertGreater(b.terms["stamina"], a.terms["stamina"])
        self.assertEqual(sc.unit_partners(holder, [holder, dataclasses.replace(other, unit="A")],
                                         sc.TeamProfile(produce_unit="A")), 2)

    def test_read_preview_and_sp_are_not_augmented_again(self):
        team = sc.TeamProfile({"a": sc.SupportProfile("a", masteries={"unit": {"sp": 100, "stat_Vo": 100}}, unit="A"),
                               "b": sc.SupportProfile("b", unit="A")}, "A")
        option = ws.ScheduleOption("lesson_vo", "lesson", "Vo", preview={"Vo": 20}, sp_gain=5,
                                    level=1, supports=2, support_ids=("a", "b"))
        candidate = next(c for c in self.candidate(team, option=option).ranked if c.kind == "lesson")
        self.assertAlmostEqual(candidate.terms["stat_gain"], 22.)
        self.assertAlmostEqual(candidate.terms["sp"], 5.5)

    def test_audition_bonus_requires_first_not_just_pass(self):
        # Guest participates in growth mastery, even though it has no audition Live skill.
        team = sc.TeamProfile({"guest": sc.SupportProfile("guest", masteries={"audition": {"sp": 20, "stat_Me": 10}})})
        for probability in (0., .25, 1.):
            result = self.candidate(team, first=probability, auditions=True)
            audition = next(c for c in result.ranked if c.kind == "audition")
            self.assertAlmostEqual(audition.terms["sp"], probability * 20)
            self.assertGreaterEqual(audition.terms["mental"], 0)
            self.assertIn(f"audition_mastery_p_first_{probability:g}", audition.notes)

    def test_global_stamina_support_does_not_lower_current_trouble(self):
        effect = {"activation_probability": 1., "stamina_reduction": .5}
        team = sc.TeamProfile({"elsewhere": sc.SupportProfile("elsewhere", stamina_support=(effect,))})
        option = ws.ScheduleOption("lesson_vo", "lesson", "Vo", preview={"Vo": 20}, level=1, supports=0)
        a = next(c for c in self.candidate(sc.TeamProfile(), option=option).ranked if c.kind == "lesson")
        b = next(c for c in self.candidate(team, option=option).ranked if c.kind == "lesson")
        self.assertEqual(a.trouble, b.trouble)
        self.assertEqual(a.terms["stat_gain"], b.terms["stat_gain"])
        self.assertGreater(b.terms["stamina"], a.terms["stamina"])
        explicit = dataclasses.replace(option, stamina_cost=15)
        a = next(c for c in self.candidate(sc.TeamProfile(), option=explicit).ranked if c.kind == "lesson")
        b = next(c for c in self.candidate(team, option=explicit).ranked if c.kind == "lesson")
        self.assertEqual(a.terms["stamina"], b.terms["stamina"])

    def test_rest_support_and_read_recovery_take_precedence(self):
        team = sc.TeamProfile({"a": sc.SupportProfile("a", rest_support=({"expected_recovery": 15.},))})
        plain = self.candidate(sc.TeamProfile(), stamina=25)
        boosted = self.candidate(team, stamina=25)
        a = next(c for c in plain.ranked if c.kind == "rest")
        b = next(c for c in boosted.ranked if c.kind == "rest")
        self.assertGreater(b.terms["stamina"], a.terms["stamina"])
        self.assertIn("rest_support_expectation", b.notes)
        option = ws.ScheduleOption("lesson_vo", "lesson", "Vo", preview={"Vo": 20}, supports=0)
        state = run_state(2, 3, 3000, 220, options=(option, ws.ScheduleOption("rest", "rest", stamina_gain=10)),
                          stamina=60, trouble_rate=0)
        a = sc.score_week(state, "Vo")
        b = sc.score_week(state, "Vo", team=team)
        self.assertEqual(next(c for c in a.ranked if c.kind == "rest").score,
                         next(c for c in b.ranked if c.kind == "rest").score)

    def test_audition_sp_uses_reviewed_path_and_no_perfect_multiplier(self):
        from tests.test_true_end_sp_value import context, snapshot
        state = run_state(2, 3, 3000, 100,
                          options=(ws.ScheduleOption("lesson_vo", "lesson", "Vo", preview={"Vo": 20}, sp_gain=0),),
                          stamina=100, trouble_rate=0)
        snap = snapshot(stats=dict(state.stats), caps=dict.fromkeys(ws.STATS, 500))
        state = dataclasses.replace(state, sp=35, stat_caps=snap.caps)
        team = sc.TeamProfile({"guest": sc.SupportProfile("guest", masteries={"audition": {"sp": 15}})})
        result = sc.score_week(state, "Vo", team=team, win_model=lambda *a: (1., ()),
                               first_model=lambda *a: (.25, ()), sp_context=context(state=snap))
        candidate = next(c for c in result.ranked if c.kind == "audition")
        self.assertAlmostEqual(candidate.terms["sp"], .25 * 60)
        self.assertEqual(result.context["sp_valuation"]["auditions"][candidate.id]["probability"], .25)

    def test_profile_binds_unlocked_mastery_unit_and_rarity(self):
        team, owned, cards, ids = fixture()
        owned["details"][P] = {"limit_break": 0}
        cards[f"te-{P}"]["unit"] = "A"
        raw = cards[f"te-{SUPPORTS[0]}"]
        raw["unit"] = "A"
        raw["rarity"] = "SSR"
        raw["support_skills"] += [
            {"id": "ad-sp", "kind": "audition_mastery_sp", "levels": {"1": 7, "70": 15}, "required_lb": 2},
            {"id": "ad-me", "kind": "audition_mastery_stat", "attribute": "Me", "levels": {"1": 5}},
            {"id": "cost", "kind": "stamina_support", "levels": {"1": 3}},
            {"id": "rest", "kind": "rest_support", "levels": {"1": 4}}]
        owned["details"][SUPPORTS[0]] = {"level": 60, "limit_break": 0}
        built = build_team_profile(team, owned, cards=cards, id_map=ids)
        profile = built.profile.supports[SUPPORTS[0]]
        self.assertTrue(built.complete, built.unknowns)
        self.assertEqual(built.profile.produce_unit, "A")
        self.assertEqual(profile.masteries["audition"], {"stat_Me": 5})
        self.assertAlmostEqual(profile.stamina_support[0]["activation_probability"], .09)
        self.assertAlmostEqual(profile.rest_support[0]["expected_recovery"], 1.8)
        owned["details"][SUPPORTS[0]] = {"level": 80, "limit_break": 4}
        built = build_team_profile(team, owned, cards=cards, id_map=ids)
        self.assertEqual(built.profile.supports[SUPPORTS[0]].masteries["audition"]["sp"], 15)


if __name__ == "__main__":
    unittest.main()
