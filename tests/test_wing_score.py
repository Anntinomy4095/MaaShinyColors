"""W.I.N.G. scorer v1 (agent/core/wing_score.py) and its policy routing (decision="score")."""
from __future__ import annotations

import dataclasses
import json
import unittest
from unittest import mock

from agent.core import wing_policy as wp
from agent.core import wing_score as sc
from agent.core import wing_state as ws
from tests.test_wing_te_rules import SEASON_AUDITIONS, run_state

Opt = ws.ScheduleOption


def rooms(*extra, vo=(30, 0), stamina_cost=15):
    """Primary room (preview, supports), the off rooms, radio/talk/magazine and rest."""
    return (Opt("lesson_vo", "lesson", "Vo", "Vocal", True, {"Vo": vo[0]}, stamina_cost, vo[1]),
            Opt("lesson_da", "lesson", "Da", "Dance", True, {"Da": 30}, stamina_cost, 0),
            Opt("work_radio", "work", None, "ラジオ", True, {"Me": 20, "Vo": 5}, 20, 0),
            Opt("work_talk", "work", None, "トーク", True, {"Me": 5, "Da": 5}, 20, 0, sp_gain=20),
            Opt("work_magazine", "work", None, "雑誌撮影", True, {"Vi": 5, "Me": 5}, 5, 0, fan_gain=900),
            Opt("rest", "rest", None, "休息", True, {}, 0)) + extra


def score(state, **params):
    return sc.score_week(state, "Vo", params=params or None)


class FormulaTests(unittest.TestCase):
    def test_worked_example_s2w5(self) -> None:
        # S2 week 5 (W=12), Vo Lv3 base 26, 緑 tension (k=2)
        self.assertEqual(sc.elapsed_weeks(2, 5), 12)
        self.assertEqual(sc.elapsed_weeks(1, 1), 0)
        self.assertEqual(sc.elapsed_weeks(4, 8), 31)
        empty = sc.lesson_gain(26, week_index=12, tension_k=2)
        self.assertEqual(empty, 28)                                    # 26 × 1.05 = 27.3 -> 28
        three = sc.lesson_gain(26, week_index=12, support_stats=(250, 200, 150), bonds=(50, 50, 50), tension_k=2,
                               n_excellent=1, mastery=10)
        self.assertEqual(three, 57)
        self.assertAlmostEqual(empty * 1.1, 30.8, places=6)            # expected incl. 10 % Perfect
        self.assertAlmostEqual(three * 1.1, 62.7, places=6)
        self.assertEqual(sc.lesson_gain(26, week_index=12, tension_k=2, perfect=True), 55)

    def test_hidden_perfect_expectation(self) -> None:
        # the preview excludes Perfect; E[gain] = preview × P(ok) × (1 + p_P|ok),
        # Perfect counted exactly once (the formula path never applies ×2 itself), G.R.A.D. factor = 1 for W.I.N.G.
        self.assertEqual(sc.lesson_gain(26, week_index=12, tension_k=2, grad=1.0), sc.lesson_gain(26, week_index=12,
                                                                                                    tension_k=2))
        self.assertGreater(sc.lesson_gain(26, week_index=12, grad=1.5), sc.lesson_gain(26, week_index=12))
        self.assertAlmostEqual(sc.perfect_given_success(sc.ScoreParams()), 0.10)
        self.assertAlmostEqual(sc.perfect_given_success(sc.ScoreParams(perfect_up=0.02)), 0.12)
        room = Opt("lesson_vo", "lesson", "Vo", "", True, {"Vo": 40}, 15, 1)
        opts = (room, Opt("rest", "rest", None, "", True))
        for params, factor in (({}, 1.10), ({"perfect_up": 0.02}, 1.12)):
            r = sc.score_week(run_state(2, 5, 3_000, 220, options=opts, stamina=60, trouble_rate=None), "Vo",
                              params=params)
            vo = next(c for c in r.ranked if c.id == "lesson_vo")
            self.assertGreater(vo.trouble, 0)                    # low stamina: P(success) < 1 lowers the Perfect odds too
            self.assertAlmostEqual(vo.terms["stat_gain"], 40 * (1 - vo.trouble / 100) * factor, places=6)
            self.assertNotIn("gain_preview_lower_bound", vo.notes)
        r = sc.score_week(run_state(2, 5, 3_000, 220, options=opts, stamina=60, trouble_rate=None), "Vo",
                          params={"preview_includes_mastery_tension": False})
        self.assertIn("gain_preview_lower_bound", next(c for c in r.ranked if c.id == "lesson_vo").notes)

    def test_base_tables(self) -> None:
        self.assertEqual([sc.LESSON_BASE[lv][0] for lv in range(1, 6)], [19, 22, 26, 30, 34])
        self.assertEqual(sc.WORK_BASE["talk"][5][1], 27)
        self.assertEqual(sc.WORK_BASE["magazine"][5][2], 2000)

    def test_trouble_rate_table(self) -> None:
        table = {50: 8, 45: 16, 40: 27, 35: 43, 30: 64, 25: 92, 20: 99, 10: 99, 70: 0, 100: 0, 65: 1, 60: 1, 55: 4,
                 44: 18}
        for stamina, rate in table.items():
            with self.subTest(stamina=stamina):
                self.assertEqual(sc.trouble_rate(stamina), rate)
        self.assertEqual(sc.trouble_rate(20, base=20), 0)       # magazine base 20
        self.assertEqual(sc.trouble_rate(10, base=20), 1)
        self.assertEqual(sc.stamina_from_trouble(18.0), (44, 44))
        self.assertEqual(sc.stamina_from_trouble(0.0)[0], 70)
        self.assertEqual(sc.stamina_from_trouble(1.0), (60, 69))
        self.assertIsNone(sc.stamina_from_trouble(9.0))      # 8 -> 10 jumps over 9

    def test_work_type(self) -> None:
        kinds = {o.id: sc.work_type(o) for o in rooms() if o.kind == "work"}
        self.assertEqual(kinds, {"work_radio": "radio", "work_talk": "talk", "work_magazine": "magazine"})


# Scorer mechanics are tested with the scorer's own logistic so they do not move when the audition-play
# model (wing_audition) is re-tuned; the default wiring is tested in WinModelWiringTests.
PIN = mock.patch.object(sc, "wing_audition_win_probability", sc.default_win_probability)


@PIN
class ScoreTests(unittest.TestCase):
    def test_breakdown_terms_and_serialisable(self) -> None:
        r = score(run_state(2, 3, 3_000, 220, options=rooms(), stamina=90, trouble_rate=None))
        for c in r.ranked:
            self.assertEqual(set(c.terms), set(sc.TERMS))
            self.assertAlmostEqual(c.score, sum(c.terms.values()), places=9)
        json.dumps(r.to_dict(), ensure_ascii=False)

    def test_urgency_grows_with_season_week(self) -> None:
        values = []
        for week in range(1, 9):
            r = score(run_state(2, week, 3_000, 220, trend_vo=2, options=rooms(), stamina=90, trouble_rate=None))
            values.append(r.context["urgency"])
        self.assertEqual(values, sorted(values))
        self.assertLess(values[0], 0.05)
        self.assertGreater(values[-1], 2.0)
        met = score(run_state(2, 8, 13_000, 220, trend_vo=2, options=rooms(), stamina=90, trouble_rate=None))
        self.assertEqual(met.context["urgency"], 0.0)

    def test_audition_preferred_when_behind_late(self) -> None:
        # unfavourable trend early (P ≈ 0.49): the needed audition waits for a better week
        early = score(run_state(2, 1, 3_000, 220, trend_vo=3, options=rooms(), stamina=90, trouble_rate=None))
        self.assertEqual(early.choice.id, "lesson_vo")
        late = score(run_state(2, 7, 3_000, 220, trend_vo=2, options=rooms(), stamina=90, trouble_rate=None))
        self.assertEqual((late.choice.kind, late.choice.id), ("audition", "s2_10k"))
        # the highest-reward unlocked audition wins over the small ones
        self.assertLess(late.ranked.index(next(c for c in late.ranked if c.id == "s2_3k")),
                        len(late.ranked))
        self.assertGreater(next(c for c in late.ranked if c.id == "s2_10k").score,
                           next(c for c in late.ranked if c.id == "s2_3k").score)

    def test_confident_needed_audition_is_front_loaded(self) -> None:
        # a needed win costs one week now or later; later lesson weeks are worth more (W1), so a confident
        # (P ≈ 0.85) needed audition goes first. Same state with a low P waits.
        s = run_state(2, 1, 3_000, 220, trend_vo=2, options=rooms(), stamina=90, trouble_rate=None)
        r = score(s)
        self.assertEqual(r.choice.id, "s2_10k")
        self.assertGreater(r.choice.terms["audition_progress"], 0)
        self.assertEqual(r.context["plan_chain"], ["s2_10k"])
        # without the later-week growth the same P only ties the best room (the growth argument is load-bearing)
        self.assertEqual(score(s, lesson_growth_per_week=0.0).choice.id, "lesson_vo")
        self.assertEqual(score(s, a0=-1.0).choice.id, "lesson_vo")                      # lower P -> wait

    def test_s1_room_fans_covering_the_target_defer_the_audition(self) -> None:
        # S1 1,000 fans usually come from Vo lessons/work alone; ~140 fans/week × 7 weeks covers the
        # 967 deficit, so the week-1 audition no longer beats the Vo lesson. Without room fans it is still front-loaded.
        def opts(fans):
            return (Opt("lesson_vo", "lesson", "Vo", "Vocal", True, {"Vo": 30}, 15, 0, fan_gain=fans),
                    Opt("lesson_da", "lesson", "Da", "Dance", True, {"Da": 30}, 15, 0),
                    Opt("rest", "rest", None, "休息", True, {}, 0))
        covered = score(run_state(1, 1, 33, 133, trend_vo=2, options=opts(140), stamina=100, trouble_rate=None))
        self.assertEqual(covered.choice.id, "lesson_vo")
        self.assertGreater(covered.context["passive_cover"], 0.9)
        bare = score(run_state(1, 1, 33, 133, trend_vo=2, options=opts(0), stamina=100, trouble_rate=None))
        self.assertEqual(bare.choice.kind, "audition")
        self.assertNotIn("passive_cover", bare.context)

    def test_unlock_chain_values_prerequisite(self) -> None:
        # S3 at 13,852 fans, rank D: 50k needs C -> only the 40k win (to 53,852 -> C) unlocks it
        s = run_state(3, 1, 13_852, 785, trend_vo=1, rank="D", options=rooms(), stamina=90, trouble_rate=None)
        r = score(s)
        self.assertIn("rank_locked", next(c for c in r.excluded if c.id == "s3_50k").notes)
        self.assertEqual(r.context["plan_chain"], ["s3_40k", "s3_50k"])
        forty = next(c for c in r.ranked if c.id == "s3_40k")
        self.assertIn("unlocks:s3_50k", forty.notes)
        self.assertIn("wins_saved_1", forty.notes)
        small = next(c for c in r.ranked if c.id == "s3_15k")
        self.assertIn("wins_saved_0", small.notes)          # 15k does not shorten the chain: only banked fans
        self.assertGreater(forty.score, small.score)
        self.assertEqual(r.choice.id, "s3_40k")
        self.assertEqual([sc.rank_from_fans(f) for f in (0, 1_000, 13_852, 53_852, 103_852)],
                         ["F", "E", "D", "C", "B"])

    def test_team_profile_formula_and_recovery(self) -> None:
        # S2 week 5 (W=12), Lv3 Vo room with three profiled supports -> the formula without tension
        team = sc.TeamProfile({
            "s1": sc.SupportProfile("s1", {"Vo": 250}, 50, {"lesson_Vo": {"stat": 6, "stamina": 6, "sp": 3}}),
            "s2": sc.SupportProfile("s2", {"Vo": 200}, 50, {"lesson_Vo": {"stat": 4}}),
            "s3": sc.SupportProfile("s3", {"Vo": 150}, 50)})
        room = Opt("lesson_vo", "lesson", "Vo", "", True, {}, 15, 3, level=3, excellent=1,
                   support_ids=("s1", "s2", "s3"))
        opts = (room, Opt("rest", "rest", None, "", True))
        r = sc.score_week(run_state(2, 5, 3_000, 220, trend_vo=3, options=opts, stamina=90, trouble_rate=None),
                          "Vo", team=team)
        vo = next(c for c in r.ranked if c.id == "lesson_vo")
        expected = sc.lesson_gain(26, week_index=12, support_stats=(250, 200, 150), bonds=(50, 50, 50),
                                  n_excellent=1, mastery=10)
        self.assertAlmostEqual(vo.terms["stat_gain"], expected * 1.1 * (1 - vo.trouble / 100), places=6)
        self.assertIn("gain_formula_team_profile", vo.notes)
        self.assertIn("recovery_from_profile", vo.notes)
        self.assertEqual(vo.terms["bond"], 0.0)             # profiled bonds already at 50
        # an unprofiled support id falls back to the lower bound
        stranger = dataclasses.replace(room, support_ids=("s1", "zz"))
        r2 = sc.score_week(run_state(2, 5, 3_000, 220, options=(stranger,), stamina=90), "Vo", team=team)
        self.assertIn("gain_formula_lower_bound", next(c for c in r2.ranked if c.id == "lesson_vo").notes)

    def test_pluggable_win_model(self) -> None:
        calls = []

        def model(state, audition, primary, params):
            calls.append(audition.id)
            return (0.99, ()) if audition.id == "s2_5k" else (None, ("judge_model_has_no_data",))

        s = run_state(2, 7, 3_000, 220, trend_vo=3, options=rooms(), stamina=90, trouble_rate=None)
        r = sc.score_week(s, "Vo", win_model=model)
        self.assertEqual(sorted(set(calls)), sorted(a.id for a in s.auditions))
        self.assertEqual([c.id for c in r.ranked if c.kind == "audition"], ["s2_5k"])
        self.assertIn("p_win_unknown", next(c for c in r.excluded if c.id == "s2_10k").notes)

    def test_support_room_beats_empty_room_mid_game(self) -> None:
        support = Opt("lesson_vo_b", "lesson", "Vo", "Vocal", True, {"Vo": 57}, 15, 3, excellent=1)
        opts = rooms(support, vo=(28, 0))
        r = score(run_state(2, 5, 3_000, 220, trend_vo=3, options=opts, stamina=90, trouble_rate=None))
        self.assertEqual(r.choice.id, "lesson_vo_b")
        ids = [c.id for c in r.ranked]
        self.assertLess(ids.index("lesson_vo_b"), ids.index("lesson_vo"))
        # previews unread: formula lower bound from the level; the empty room is still scorable
        bare = (Opt("lesson_vo", "lesson", "Vo", "", True, {}, 15, 0, level=3),
                Opt("rest", "rest", None, "", True, {}, 0))
        lb = score(run_state(2, 5, 3_000, 220, trend_vo=3, options=bare, stamina=90, trouble_rate=None))
        vo = next(c for c in lb.ranked if c.id == "lesson_vo")
        self.assertIn("gain_formula_lower_bound", vo.notes)

    def test_cap_overflow_penalty(self) -> None:
        base = dict(options=rooms(), stamina=90, trouble_rate=None, trend_vo=3)
        free = score(run_state(2, 3, 3_000, 400, stat_caps={"Vo": 500}, **base))
        vo = next(c for c in free.ranked if c.id == "lesson_vo")
        self.assertEqual(vo.terms["cap_overflow"], 0.0)
        near = score(run_state(2, 3, 3_000, 490, stat_caps={"Vo": 500}, **base))
        vo = next(c for c in near.ranked if c.id == "lesson_vo")
        # Both normal (+30) and Perfect (+60) are capped to +10: expected gain is 10, not 11.
        self.assertAlmostEqual(vo.terms["cap_overflow"], -23, places=6)
        self.assertAlmostEqual(vo.terms["stat_gain"] + vo.terms["cap_overflow"], 10, places=6)
        capped = score(run_state(2, 3, 3_000, 500, stat_caps={"Vo": 500}, **base))
        self.assertNotEqual(capped.choice.id, "lesson_vo")
        # A +50 cap fits normal growth but still loses 10 on the 10% Perfect branch.
        bought = sc.score_week(run_state(2, 3, 3_000, 500, stat_caps={"Vo": 500}, **base), "Vo", extra_cap=50)
        self.assertAlmostEqual(next(c for c in bought.ranked if c.id == "lesson_vo").terms["cap_overflow"], -1.0)
        for stat, expected in ((470, 30), (450, 32), (430, 33)):
            with self.subTest(stat=stat):
                scored = score(run_state(2, 3, 3_000, stat, stat_caps={"Vo": 500}, **base))
                lesson = next(c for c in scored.ranked if c.id == "lesson_vo")
                self.assertAlmostEqual(lesson.terms["stat_gain"] + lesson.terms["cap_overflow"], expected)
        unread = score(run_state(2, 3, 3_000, 500, **base))
        self.assertIn("cap", next(c for c in unread.ranked if c.id == "lesson_vo").unknown)

    def test_low_stamina_prefers_rest_high_stamina_lessons(self) -> None:
        def pick(stamina):                           # rooms vs rest only (the 10k audition outranks both)
            return score(run_state(2, 3, 3_000, 220, trend_vo=3, options=rooms(), stamina=stamina,
                                   trouble_rate=None, auditions=())).choice.id
        self.assertEqual(pick(90), "lesson_vo")
        self.assertEqual(pick(55), "lesson_vo")
        self.assertEqual(pick(50), "rest")          # 8 %: failure costs already favour rest
        self.assertEqual(pick(44), "rest")          # 18 %: the user rested here in the first TE run
        # the trouble rate alone is enough: stamina is taken as the lowest value consistent with it
        r = score(run_state(2, 3, 3_000, 220, trend_vo=3, options=rooms(), stamina=None, trouble_rate=18.0))
        self.assertEqual((r.context["stamina"], r.context["stamina_source"]), (44, "from_trouble_rate_lower_bound"))

    def test_recovery_room_keeps_training(self) -> None:
        recover = Opt("lesson_vo_r", "lesson", "Vo", "", True, {"Vo": 30}, 15, 1, stamina_gain=18)
        opts = rooms(recover)
        week = run_state(2, 3, 3_000, 220, trend_vo=3, options=opts, stamina=47, trouble_rate=None)
        self.assertEqual(score(week, trouble_rest_rate=20.0).choice.id, "lesson_vo_r")
        # 13 % trouble: the default 10 % threshold does not risk a failure for the recovery
        self.assertIn("lesson_vo_r", {c.id for c in score(week).excluded if "trouble_hard_cap" in c.notes})
        flagged = Opt("lesson_vo_r", "lesson", "Vo", "", True, {"Vo": 30}, 15, 1, stamina_recovery=True)
        c = next(c for c in score(run_state(2, 3, 3_000, 220, options=rooms(flagged), stamina=60,
                                            trouble_rate=None)).ranked if c.id == "lesson_vo_r")
        self.assertIn("recovery_amount_assumed", c.notes)

    def test_promise_is_ordinary_terms(self) -> None:
        # W1: kept -> tension +25 and affection +10; broken -> tension −10. No hard rule: the room decides.
        promise = {"season": 2, "week": 3, "attribute": "Da"}
        busy_da = Opt("lesson_da", "lesson", "Da", "Dance", True, {"Da": 30}, 15, 3, sp_gain=15)
        opts = tuple(o for o in rooms(vo=(26, 0)) if o.id != "lesson_da") + (busy_da,)      # empty Lv3 room = 26
        r = score(run_state(2, 3, 3_000, 220, trend_vo=3, options=opts, stamina=90, trouble_rate=None,
                            promise=promise))
        terms = {c.id: c.terms["promise"] for c in r.ranked}
        g = r.context["g_ref"]
        # 中立 default (2026-10-14 user): the promised room gets a small bonus, every other room scores as usual
        self.assertAlmostEqual(terms["lesson_da"], 25 * g * 0.025 / 20 * 4 + 10 * 0.3, places=6)
        self.assertEqual(terms["lesson_vo"], 0.0)
        kept = {c.id: c.terms["promise"] for c in score(run_state(2, 3, 3_000, 220, trend_vo=3, options=opts, stamina=90,
                                                                  trouble_rate=None, promise=promise),
                                                        promise_weight=4.0).ranked}
        self.assertAlmostEqual(kept["lesson_vo"], -10 * g * 0.025 / 20 * 4 * 4.0, places=6)   # 优先守约 charges it
        self.assertEqual(r.choice.id, "lesson_da")          # 3 supports + promise beat the empty Vo room
        strong_vo = Opt("lesson_vo", "lesson", "Vo", "Vocal", True, {"Vo": 70}, 15, 3, excellent=1)
        opts2 = tuple(o for o in opts if o.id != "lesson_vo") + (strong_vo,)
        week8 = run_state(2, 8, 13_000, 220, trend_vo=3, options=opts2, stamina=90, trouble_rate=None,
                          promise=dict(promise, week=8), wins=[(2, 10_000)])
        self.assertEqual(score(week8).choice.id, "lesson_vo")   # 中立 (default): demo S2 week 8 broke it
        self.assertEqual(score(week8, promise_weight=4.0).choice.id, "lesson_da")   # 优先守约: keep it

    def test_supported_off_stat_room_beats_empty_primary_room_early(self) -> None:
        # A room's value comes from its supports/events/level, not its stat label
        busy_da = Opt("lesson_da", "lesson", "Da", "Dance", True, {"Da": 35}, 15, 3, sp_gain=10)
        opts = tuple(o for o in rooms(vo=(22, 0)) if o.id != "lesson_da") + (busy_da,)
        early = score(run_state(1, 3, 0, 120, trend_vo=3, options=opts, stamina=90, trouble_rate=None, auditions=()))
        da = next(c for c in early.ranked if c.id == "lesson_da")
        self.assertGreater(da.terms["bond"], 0)
        self.assertGreater(da.terms["events"], 0)
        self.assertEqual(early.choice.id, "lesson_da")
        late = score(run_state(4, 2, 300_000, 900, trend_vo=3, rank="B", options=rooms(vo=(90, 0)) , stamina=90,
                               trouble_rate=None, auditions=()))
        self.assertEqual(next(c for c in late.ranked if c.id == "lesson_da").terms["bond"], 0.0)   # β fades by W16
        self.assertEqual(late.choice.id, "lesson_vo")

    def test_unknown_inputs_degrade(self) -> None:
        aud = (ws.Audition("a_unknown", "x", 10_000, None, True),)              # no recommended stat
        with mock.patch.dict(sc.RECOMMENDED_FALLBACK, clear=True),                 mock.patch.dict(sc.RECOMMENDED_FALLBACK_BY_REWARD, clear=True):  # no table row either
            r = score(run_state(2, 7, 3_000, 220, options=rooms(), auditions=aud, stamina=90, trouble_rate=None))
        self.assertEqual([c.id for c in r.excluded], ["a_unknown"])
        self.assertIn("p_win_unknown", r.excluded[0].notes)
        self.assertFalse(r.context["hard_plan_unreachable"])                   # unknown p is not "unreachable"
        gated = score(run_state(2, 7, 3_000, 240, rank=None, options=rooms(), stamina=90, trouble_rate=None))
        self.assertIn("rank_unread", next(c for c in gated.excluded if c.id == "s2_15k").notes)
        no_gain = (Opt("lesson_vo", "lesson", "Vo", "", True, {}, 15, 1), Opt("rest", "rest", None, "", True))
        self.assertEqual(score(run_state(2, 3, 3_000, 220, options=no_gain, stamina=90)).rule, "no_reference_gain")
        no_stamina = score(run_state(2, 3, 3_000, 220, options=rooms(), stamina=None, trouble_rate=None))
        self.assertIn("rest", [c.id for c in no_stamina.excluded])
        # neither stamina nor any trouble rate: rooms cannot be risk-assessed and are excluded, not guessed
        self.assertIn("trouble_unknown", next(c for c in no_stamina.excluded if c.id == "lesson_vo").notes)

    def test_fallback_to_empty_room_or_rest(self) -> None:
        # every option is weak: a huge floor forces the fallback
        s = run_state(2, 3, 3_000, 220, trend_vo=3, options=rooms(), stamina=90, trouble_rate=None)
        r = score(s, floor_ratio=10.0)
        self.assertEqual((r.rule, r.choice.id), ("fallback_empty_room", "lesson_vo"))
        # 27 %: every room is over the hard cap, rest is already the best -> no fallback needed
        tired = run_state(2, 3, 3_000, 220, trend_vo=3, options=rooms(), stamina=40, trouble_rate=None)
        r = score(tired, floor_ratio=10.0)
        self.assertEqual((r.rule, r.choice.id), ("best", "rest"))

    def test_fallback_never_overrides_a_better_rest_or_a_non_room_best(self) -> None:
        # stamina 47 (13 %), rest 12.6 > empty Vo 8.1 under the old fallback -> empty room was forced
        week = run_state(2, 5, 12_000, 220, trend_vo=3, options=rooms(), stamina=47, trouble_rate=None, auditions=())
        r = score(week, trouble_rest_rate=15.0, floor_ratio=10.0)
        by = {c.id: c.score for c in r.ranked}
        self.assertGreater(by["rest"], by["lesson_vo"])
        self.assertEqual((r.rule, r.choice.id), ("best", "rest"))            # best is rest: nothing to replace
        # a weak lesson best whose empty room is below rest -> rest, even when its trouble is under the threshold
        strong = Opt("lesson_vo_sup", "lesson", "Vo", "", True, {"Vo": 30}, 15, 3, sp_gain=20)
        week = run_state(2, 5, 12_000, 220, trend_vo=3, options=rooms(strong), stamina=47, trouble_rate=None,
                         auditions=())
        r = score(week, trouble_rest_rate=15.0, floor_ratio=10.0)
        self.assertEqual(r.ranked[0].kind, "lesson")
        self.assertEqual((r.rule, r.choice.id), ("fallback_rest", "rest"))
        self.assertIn("below_floor", r.context["fallback_reason"])
        # an audition best is never replaced
        aud = run_state(2, 3, 3_000, 220, trend_vo=3, options=rooms(), stamina=55, trouble_rate=None)
        r = score(aud, floor_ratio=10.0)
        self.assertEqual((r.rule, r.choice.kind), ("best", "audition"))

    def test_trouble_hard_cap_excludes_stamina_actions(self) -> None:
        rich = Opt("lesson_vo_rich", "lesson", "Vo", "", True, {"Vo": 30}, 15, 3, sp_gain=40)
        for stamina, excluded in ((44, False), (43, True), (41, True)):      # 18 % / 20 % / 25 %
            week = run_state(2, 5, 12_000, 220, trend_vo=3, options=rooms(rich), stamina=stamina, trouble_rate=None,
                             auditions=())
            r = score(week, trouble_rest_rate=20.0)
            with self.subTest(stamina=stamina):
                capped = {c.id for c in r.excluded if "trouble_hard_cap" in c.notes}
                self.assertEqual("lesson_vo_rich" in capped, excluded)
                if excluded:
                    self.assertEqual(capped, {"lesson_vo", "lesson_da", "lesson_vo_rich", "work_radio", "work_talk"})
                    self.assertEqual({c.kind for c in r.ranked} - {"work"}, {"rest"})
                    self.assertEqual([c.id for c in r.ranked if c.kind == "work"], ["work_magazine"])  # B = 20
                    self.assertEqual(r.choice.id, "rest")
        # the rest threshold caps them too (default 10 %): 11 % at stamina 48 is excluded, 8 % at 50 is not
        for stamina, excluded in ((48, True), (50, False)):
            week = run_state(2, 5, 12_000, 220, trend_vo=3, options=rooms(rich), stamina=stamina, trouble_rate=None,
                             auditions=())
            with self.subTest(default_stamina=stamina):
                self.assertEqual("lesson_vo_rich" in {c.id for c in score(week).excluded
                                                      if "trouble_hard_cap" in c.notes}, excluded)
        # the cap is never configurable above 20 %
        for bad in (25.0, 0, True):
            with self.assertRaises(ValueError):
                sc.ScoreParams.from_overrides({"trouble_hard_cap": bad})
        self.assertEqual(sc.ScoreParams.from_overrides({"trouble_hard_cap": 15.0}).trouble_hard_cap, 15.0)

    def test_s4_equal_wins_prefer_the_fan_buffer(self) -> None:
        """Live wing13 (S4, 8 weeks left, 161,667 fans): both 50k and 100k leave 3 wins to 500k and the safer 50k won.
        The 100k leaves 61,667 fans above the target after those wins (50k: 11,667): a buffer against a later loss."""
        odds = {"s4_100k": 0.93, "s4_50k": 0.96, "s4_25k": 0.96, "s4_15k": 0.96}
        win = lambda state, a, primary, p: (odds[a.id], [])  # noqa: E731
        week = run_state(4, 1, 161_667, 960, rank="B", me=344, options=rooms(), stamina=90, trouble_rate=None)
        r = sc.score_week(week, "Vo", win_model=win)
        by = {c.id: c for c in r.ranked}
        self.assertIn("fan_buffer_61667", by["s4_100k"].notes)
        self.assertIn("fan_buffer_11667", by["s4_50k"].notes)
        self.assertEqual(r.choice.id, "s4_100k")
        # a clearly lower win rate still picks the safer audition
        odds["s4_100k"] = 0.8
        self.assertEqual(sc.score_week(week, "Vo", win_model=win).choice.id, "s4_50k")
        # the win that reaches the target needs no buffer
        last = sc.score_week(run_state(4, 6, 461_667, 960, rank="B", me=344, options=rooms(), stamina=90,
                                       trouble_rate=None), "Vo", win_model=win)
        self.assertFalse([n for c in last.ranked for n in c.notes if n.startswith("fan_buffer")])

    def test_failure_costs_and_catastrophe_floor(self) -> None:
        p = sc.ScoreParams()
        self.assertEqual((p.l_fail, p.l_cat_base, p.cat_share_floor, p.trouble_rest_rate), (30.0, 120.0, 0.10, 10.0))
        week = run_state(2, 5, 12_000, 220, trend_vo=3, options=rooms(), stamina=55, trouble_rate=None, auditions=())
        r = score(week)
        vo = next(c for c in r.ranked if c.id == "lesson_vo")
        self.assertEqual(vo.trouble, 4.0)
        # 4 %: the 10 % 大失敗 floor applies (the old curve gave p_cat = 0 below 10 %), so the risk exceeds the
        # failure part alone by at least p × 0.10 × l_cat_base
        self.assertLessEqual(vo.terms["trouble_risk"], -(0.04 * 30) - 0.04 * 0.10 * 120 + 1e-9)
        old = score(week, l_fail=10.0, l_cat_base=60.0, cat_share_floor=0.0)
        self.assertAlmostEqual(next(c for c in old.ranked if c.id == "lesson_vo").terms["trouble_risk"], -0.4, places=6)

    def test_rank_a_s_thresholds(self) -> None:
        self.assertEqual([sc.rank_from_fans(f) for f in (499_999, 500_000, 999_999, 1_000_000)], ["B", "A", "A", "S"])
        self.assertEqual(set(sc.RANK_THRESHOLD_SOURCES), {r for r, _ in sc.RANK_FAN_THRESHOLDS})
        self.assertIn("R1", sc.RANK_THRESHOLD_SOURCES["S"])

    def test_params_validation(self) -> None:
        with self.assertRaises(ValueError):
            sc.ScoreParams.from_overrides({"nope": 1})
        with self.assertRaises(ValueError):
            sc.ScoreParams.from_overrides({"w_sp": 5.0})
        with self.assertRaises(ValueError):
            sc.score_week(run_state(2, 3, 3_000, 220), "Me")

    def test_deterministic_and_order_independent(self) -> None:
        s = run_state(3, 5, 30_000, 500, trend_vo=2, rank="C", options=rooms(), stamina=60, trouble_rate=None)
        rev = dataclasses.replace(s, options=tuple(reversed(s.options)), auditions=tuple(reversed(s.auditions)))
        a, b, c = score(s), score(s), score(rev)
        self.assertEqual(a.to_dict(), b.to_dict())
        self.assertEqual([x.id for x in a.ranked], [x.id for x in c.ranked])


@PIN
class ScorePolicyTests(unittest.TestCase):
    def test_score_strategies_registered(self) -> None:
        for name in ("vo_score", "da_score", "vi_score"):
            self.assertEqual(wp.STRATEGIES[name].decision, "score")
        self.assertEqual(wp.STRATEGIES["vo_highest"].decision, "rules")

    def test_routes_through_scorer(self) -> None:
        d = wp.decide(run_state(2, 7, 3_000, 220, trend_vo=2, options=rooms(), stamina=90), "vo_score")
        self.assertEqual((d.kind, d.audition_id, d.pre_actions), ("audition", "s2_10k", ("spend_sp",)))
        self.assertEqual(d.plan["mode"], "score")
        early = wp.decide(run_state(2, 1, 3_000, 220, trend_vo=3, options=rooms(), stamina=90), "vo_score")
        self.assertEqual((early.kind, early.option_id, early.rule), ("lesson", "lesson_vo", "score_best"))
        # decision_mode overrides any strategy, both ways
        self.assertEqual(wp.decide(run_state(2, 1, 3_000, 220, options=rooms(), stamina=90), "vo_highest",
                                   params={"decision_mode": "score"}).plan["mode"], "score")
        self.assertNotIn("mode", wp.decide(run_state(2, 1, 3_000, 220, options=rooms(), stamina=90), "vo_score",
                                           params={"decision_mode": "rules"}).plan)
        self.assertEqual(wp.decide(run_state(2, 1, 3_000, 220), "vo_score",
                                   params={"decision_mode": "x"}).rule, "unsupported_decision_mode")

    def test_hard_guards_stay(self) -> None:
        unreachable = wp.decide(run_state(4, 6, 120_000, 900, rank="B", options=rooms(), stamina=90), "vo_score")
        self.assertEqual((unreachable.kind, unreachable.rule), ("stop", "fan_plan_unreachable"))
        tight = wp.decide(run_state(2, 8, 3_000, 220, trend_vo=3, options=rooms(), stamina=30), "vo_score")
        self.assertEqual((tight.kind, tight.rule), ("audition", "score_tight_deadline_audition"))
        bad = wp.decide(run_state(2, 3, 3_000, 220, options=rooms(), stamina=90, condition="不調"), "vo_score")
        self.assertEqual((bad.kind, bad.rule), ("rest", "score_condition_guard"))
        unread = wp.decide(run_state(2, 3, 3_000, 220, options=rooms(), stamina=None, trouble_rate=None), "vo_score")
        self.assertEqual(unread.rule, "stamina_unread")
        capped = wp.decide(run_state(3, 6, 117_000, 760, sp=80, stat_caps={"Vo": 778}, wins=[(3, 40_000), (3, 50_000)],
                                     options=rooms(vo=(30, 3)), stamina=90), "vo_score")
        self.assertEqual((capped.kind, capped.rule), ("spend_sp", "primary_at_cap_spend_sp_first"))
        # no SP left to raise the cap: the capped Vo room loses to SP-earning work (user S4 w2 pattern)
        poor = wp.decide(run_state(3, 6, 117_000, 770, sp=10, stat_caps={"Vo": 778}, options=rooms(vo=(60, 3)),
                                   stamina=90, auditions=()), "vo_score")
        self.assertEqual((poor.kind, poor.option_id), ("work", "work_talk"))

    def test_any_stat_pushed_past_its_cap_spends_sp_first(self) -> None:
        """2026-10-14 user: the chosen action's preview overflowing any read cap (not only the primary) -> 振り返り."""
        from types import SimpleNamespace as NS
        state = NS(stats={"Vo": 500, "Da": 505, "Vi": 100, "Me": 90}, stat_caps={"Vo": 800, "Da": 520, "Vi": 500})
        self.assertEqual(wp._capped_attributes(state, NS(preview={"Da": 20, "Vo": 10})), ("Da",))
        self.assertEqual(wp._capped_attributes(state, NS(preview={"Vi": 30, "Me": 40})), ())   # Me cap unread

    def test_unread_audition_list_is_not_unreachable(self) -> None:
        # found by the demo comparison: an unread list must not prove the fan gate unreachable
        d = wp.decide(run_state(1, 3, 159, 177, options=rooms(), auditions=(), stamina=90), "vo_score")
        self.assertNotEqual(d.kind, "stop")
        self.assertTrue(d.plan["context"]["audition_list_unread"])
        self.assertTrue(d.plan["context"]["urgency_unknown"])
        late = wp.decide(run_state(4, 6, 120_000, 900, rank="B", options=rooms(), stamina=90), "vo_score")
        self.assertEqual(late.rule, "fan_plan_unreachable")          # observed list: still a hard stop

    def test_outing_invalidates_wing_state(self) -> None:
        opts = rooms(Opt("outing", "outing", None, "お出かけ", True))
        d = wp.decide(run_state(2, 3, 3_000, 220, options=opts, stamina=90), "vo_score")
        self.assertEqual(d.rule, "state_invalid")
        self.assertIn("outing_not_in_wing", d.reasons)

    def test_unscorable_falls_back_to_rules(self) -> None:
        no_gain = (Opt("lesson_vo", "lesson", "Vo", "", True, {}, 15, 1), Opt("rest", "rest", None, "", True))
        d = wp.decide(run_state(2, 3, 3_000, 220, options=no_gain, stamina=90), "vo_score")
        self.assertEqual(d.plan["score_fallback"], "no_reference_gain")
        self.assertFalse(d.rule.startswith("score_"))
        self.assertEqual(d, dataclasses.replace(wp.decide(run_state(2, 3, 3_000, 220, options=no_gain, stamina=90),
                                                          "vo_highest"), plan=d.plan))

    def test_new_option_fields_round_trip(self) -> None:
        o = Opt("lesson_vo", "lesson", "Vo", "", True, {"Vo": 30}, 15, 2, level=4, excellent=1, sp_gain=6,
                trouble_rate=8.0, stamina_gain=18)
        again = ws.ScheduleOption.from_dict(json.loads(json.dumps(o.to_dict())))
        self.assertEqual(again, o)
        self.assertNotIn("level", Opt("x", "rest").to_dict())
        with self.assertRaises(ValueError):
            ws.ScheduleOption.from_dict({**o.to_dict(), "level": 6})


@PIN
class CompareToolTests(unittest.TestCase):
    def test_compare_reports_agreement(self) -> None:
        from tools import wing_score_compare as cmp
        early3 = run_state(2, 1, 3_000, 220, trend_vo=3, options=rooms(), stamina=90)
        late = run_state(2, 7, 3_000, 220, trend_vo=2, options=rooms(), stamina=90)
        timeline = {"version": 1, "primary": "Vo", "strategy": "vo_score", "entries": [
            {"id": "a", "state": early3.to_dict(), "user_choice": "lesson_vo", "choice_certain": True},
            {"id": "b", "state": late.to_dict(), "user_choice": "lesson_vo", "choice_certain": True},
            {"id": "c", "state": late.to_dict(), "user_choice": "s2_10k", "choice_certain": False}]}
        report = cmp.compare(timeline)
        self.assertEqual([r["scorer_match"] for r in report["entries"]], [True, False, True])
        self.assertEqual(report["summary"]["certain"], 2)
        self.assertEqual(report["summary"]["scorer_agreement_certain"], 0.5)
        self.assertEqual(report["entries"][1]["user_rank"], 2)


class WinModelWiringTests(unittest.TestCase):
    def test_default_uses_wing_audition_with_fallback(self) -> None:
        s = run_state(2, 1, 3_000, 220, trend_vo=3, options=rooms(), stamina=90)
        a = next(x for x in s.auditions if x.id == "s2_10k")
        pw, unknown = sc.wing_audition_win_probability(s, a, "Vo", sc.ScoreParams())
        from agent.core import wing_audition as wa
        est = wa.audition_win_estimate(wa.context_from_wing(s, a, "Vo", need_first=False))
        self.assertAlmostEqual(pw, est["p_pass"])
        self.assertTrue(all(u.startswith("wa:") for u in unknown))
        r = sc.score_week(s, "Vo")
        self.assertIn(f"p_win_{round(pw, 3)}", next(c for c in r.ranked if c.id == "s2_10k").notes)
        # no recommended stat: the season table estimates it (marked table_fallback); without a table row both
        # models have no estimate -> None, marked
        bare = ws.Audition("x", "x", 10_000, None, True)
        pw_t, unk_t = sc.wing_audition_win_probability(s, bare, "Vo", sc.ScoreParams())
        self.assertIsNotNone(pw_t)
        self.assertIn("table_fallback", unk_t)
        with mock.patch.dict(sc.RECOMMENDED_FALLBACK, clear=True),                 mock.patch.dict(sc.RECOMMENDED_FALLBACK_BY_REWARD, clear=True):
            self.assertEqual(sc.wing_audition_win_probability(s, bare, "Vo", sc.ScoreParams())[0], None)
            self.assertIn("wing_audition_estimate",
                          sc.wing_audition_win_probability(s, bare, "Vo", sc.ScoreParams())[1])
        with mock.patch("agent.core.wing_audition.audition_win_estimate", return_value={"p_pass": None}):
            pw2, unk2 = sc.wing_audition_win_probability(s, a, "Vo", sc.ScoreParams())
        self.assertEqual(pw2, sc.default_win_probability(s, a, "Vo", sc.ScoreParams())[0])
        self.assertEqual(unk2[0], "wing_audition_estimate")


class RecommendedFallbackTests(unittest.TestCase):
    """2026-10-09 live seg2b: 推奨能力値 read {} on every S1 page -> every audition p_win_unknown -> fan_plan_unreachable."""

    def live_state(self, recommended=None):
        auds = tuple(ws.Audition(f"s1_{r // 1000}k", f"+{r}", r, None, r != 5_000, difficulty=d,
                                 recommended=dict(recommended or {}), required_rank=None if r == 5_000 else "F")
                     for r, d in ((1_000, 1), (2_000, 2), (3_000, 3), (5_000, None)))
        s = run_state(1, 5, 385, 245, trend_vo=2, me=176, rank="F", options=rooms(), auditions=auds, stamina=94)
        return s

    def test_table_values(self) -> None:
        self.assertEqual(sc.fallback_recommended(1, 1_000), {"primary": 100, "Me": 70})
        self.assertEqual(sc.fallback_recommended(4, 100_000), {"primary": 500, "Me": 450})
        self.assertEqual(sc.fallback_recommended(3, 15_000), {"primary": 390, "Me": 250})
        self.assertIsNone(sc.fallback_recommended(None, 1_000))
        self.assertIsNone(sc.fallback_recommended("wing", 5_000))

    def test_read_value_never_overwritten(self) -> None:
        s = self.live_state({"Vo": 70})
        a = s.auditions[0]
        same, notes = sc.with_recommended_fallback(s, a, "Vo")
        self.assertIs(same, a)
        self.assertEqual(notes, ())
        filled, notes = sc.with_recommended_fallback(self.live_state(), self.live_state().auditions[0], "Vo")
        self.assertEqual((dict(filled.recommended), notes), ({"Vo": 100, "Me": 70}, ("table_fallback",)))

    def test_s1_unread_recommended_gets_high_p_win_and_no_stop(self) -> None:
        s = self.live_state()
        r = sc.score_week(s, "Vo")
        auds = {c.id: c for c in r.ranked if c.kind == "audition"}
        self.assertEqual(sorted(auds), ["s1_1k", "s1_2k", "s1_3k"])
        for c in auds.values():
            self.assertIn("table_fallback", c.unknown)
            p = float(next(n for n in c.notes if n.startswith("p_win_"))[6:])
            self.assertGreater(p, 0.9)
        self.assertFalse(r.context["hard_plan_unreachable"])
        d = wp.decide(s, "vo_score")
        self.assertNotEqual(d.kind, "stop")

    def test_unknown_p_alone_does_not_stop(self) -> None:
        s = self.live_state()
        r = sc.score_week(s, "Vo", win_model=lambda *a: (None, ("nothing",)))
        self.assertEqual({c.id for c in r.excluded if "p_win_unknown" in c.notes}, {"s1_1k", "s1_2k", "s1_3k"})
        self.assertFalse(r.context["hard_plan_unreachable"])
        self.assertEqual(r.context["n_need"], 1)
        self.assertTrue(r.context["plan_p_win_unknown"])


if __name__ == "__main__":
    unittest.main()
