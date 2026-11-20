from __future__ import annotations

import json
import unittest

from agent.core import wing_policy as wp
from agent.core import wing_state as ws
from tests.test_wing_state import make_state

AUDITIONS = (
    ws.Audition("aud_1k", "1k", 1_000, 0, True),
    ws.Audition("aud_10k", "10k", 10_000, 1_000, True),
    ws.Audition("aud_40k", "40k", 40_000, 10_000, True),
    ws.Audition("aud_50k", "50k", 50_000, 10_000, True),
    ws.Audition("aud_100k", "100k", 100_000, 50_000, True),
)


def state(season, week, fans, wins=(), **kw):
    kw.setdefault("auditions", AUDITIONS)
    kw.setdefault("trend", {"Vo": 3, "Da": 1, "Vi": 2})
    return make_state(season=season, week=week, fans=fans, wins=tuple(ws.AuditionWin(*w) for w in wins), **kw)


class VoSingleSeasonTests(unittest.TestCase):
    def test_s1_lesson_until_shortfall_deadline(self) -> None:
        d = wp.decide(state(1, 1, 0))
        self.assertEqual((d.kind, d.option_id, d.rule), ("lesson", "lesson_vo", "strategy_lesson"))
        self.assertEqual(d.plan["pending"], [None])
        d = wp.decide(state(1, 7, 0))
        self.assertEqual((d.kind, d.audition_id, d.rule), ("audition", "aud_1k", "audition_due"))
        self.assertIn("trend_unfavourable", d.reasons)

    def test_s1_no_audition_when_fans_enough(self) -> None:
        d = wp.decide(state(1, 8, 1_500))
        self.assertEqual(d.kind, "lesson")
        self.assertEqual(d.plan["pending"], [])

    def test_s1_opportunistic_when_vo_trend_first(self) -> None:
        d = wp.decide(state(1, 2, 0, trend={"Vo": 1, "Da": 2, "Vi": 3}))
        self.assertEqual((d.kind, d.rule), ("audition", "audition_opportunistic"))

    def test_s2_one_10k_audition(self) -> None:
        self.assertEqual(wp.decide(state(2, 3, 3_000)).kind, "lesson")
        d = wp.decide(state(2, 7, 3_000))
        self.assertEqual((d.kind, d.audition_id), ("audition", "aud_10k"))
        done = wp.decide(state(2, 7, 13_000, wins=[(2, 10_000)]))
        self.assertEqual(done.kind, "lesson")

    def test_s3_40k_then_50k(self) -> None:
        d = wp.decide(state(3, 6, 15_000))
        self.assertEqual(d.plan["pending"], [40_000, 50_000])
        self.assertEqual((d.kind, d.audition_id), ("audition", "aud_40k"))
        d = wp.decide(state(3, 7, 55_000, wins=[(3, 40_000)]))
        self.assertEqual((d.kind, d.audition_id), ("audition", "aud_50k"))

    def test_s4_four_100k_to_true_end(self) -> None:
        d = wp.decide(state(4, 4, 100_000))
        self.assertEqual(d.plan["pending"], [100_000] * 4)
        self.assertEqual((d.kind, d.audition_id), ("audition", "aud_100k"))
        d = wp.decide(state(4, 6, 300_000, wins=[(4, 100_000), (4, 100_000)]))
        self.assertEqual(d.plan["pending"], [100_000, 100_000])

    def test_s4_trimmed_when_fans_ahead_and_free_after(self) -> None:
        d = wp.decide(state(4, 2, 350_000))
        self.assertEqual(d.plan["pending"], [100_000, 100_000])
        self.assertTrue(any(n.startswith("s4_sized") for n in d.plan["notes"]))
        d = wp.decide(state(4, 5, 500_000))
        self.assertEqual((d.kind, d.rule), ("lesson", "free_time_lesson"))

    def test_fans_goal_keeps_planned_s4(self) -> None:
        d = wp.decide(state(4, 2, 350_000), goal="fans")
        self.assertEqual(d.plan["pending"], [100_000] * 4)

    def test_unreachable_plan_stops(self) -> None:
        d = wp.decide(state(4, 7, 100_000))
        self.assertEqual((d.kind, d.rule), ("stop", "fan_plan_unreachable"))

    def test_projection_reaches_true_end(self) -> None:
        p = wp.fan_projection("vo_single")
        self.assertEqual((p[2], p[3], p[4], p["true_end_gap"]), (10_000, 100_000, 500_000, 0))


class GuardTests(unittest.TestCase):
    def test_bad_condition_rests(self) -> None:
        d = wp.decide(state(2, 3, 3_000, condition="不調"))
        self.assertEqual((d.kind, d.option_id, d.rule), ("rest", "rest", "fatigue_guard"))

    def test_low_stamina_and_trouble_rate_rest(self) -> None:
        self.assertEqual(wp.decide(state(2, 3, 3_000, stamina=20)).kind, "rest")
        self.assertEqual(wp.decide(state(2, 3, 3_000, trouble_rate=6.0)).kind, "rest")
        self.assertEqual(wp.decide(state(2, 3, 3_000, stamina=40), params={"rest_stamina": 30}).kind, "lesson")

    def test_tight_deadline_overrides_fatigue(self) -> None:
        d = wp.decide(state(2, 8, 3_000, condition="絶不調"))
        self.assertEqual(d.kind, "audition")
        self.assertIn("deadline_overrides_fatigue", d.reasons)
        loose = wp.decide(state(2, 7, 3_000, condition="絶不調"))
        self.assertEqual(loose.kind, "rest")

    def test_rest_unavailable_stops(self) -> None:
        opts = tuple(o for o in make_state().options if o.kind != "rest")
        d = wp.decide(state(2, 3, 3_000, stamina=10, options=opts))
        self.assertEqual((d.kind, d.rule), ("stop", "rest_needed_but_unavailable"))

    def test_unknowns_and_confidence_stop(self) -> None:
        cases = {
            "stamina_unread": state(2, 3, 3_000, stamina=None),
            "low_confidence": state(2, 3, 3_000, confidence=0.9),
            "state_invalid": state(2, 3, None),
        }
        for rule, s in cases.items():
            with self.subTest(rule):
                self.assertEqual(wp.decide(s).rule, rule)
        self.assertEqual(wp.decide(state(2, 3, 3_000), strategy="nope").rule, "unknown_strategy")
        self.assertEqual(wp.decide(state(2, 3, 3_000), goal="weekly_count").rule, "unsupported_goal")

    def test_missing_or_ambiguous_lesson_stops(self) -> None:
        opts = tuple(o for o in make_state().options if o.id != "lesson_vo")
        self.assertEqual(wp.decide(state(2, 3, 3_000, options=opts)).rule, "lesson_unavailable")
        twice = make_state().options + (ws.ScheduleOption("lesson_vo2", "lesson", "Vo", "", True, {"Vo": 30}, 20),)
        d = wp.decide(state(2, 3, 3_000, options=twice))
        self.assertEqual((d.rule, d.reasons), ("lesson_unavailable", ("Vo", "ambiguous")))

    def test_planned_audition_not_offered(self) -> None:
        only_1k = (AUDITIONS[0],)
        loose = wp.decide(state(2, 7, 3_000, auditions=only_1k))
        self.assertEqual(loose.kind, "lesson")
        self.assertIn("tier_10000_not_offered_this_week", loose.plan["notes"])
        tight = wp.decide(state(2, 8, 3_000, auditions=only_1k))
        self.assertEqual((tight.kind, tight.rule), ("stop", "planned_audition_unavailable"))

    def test_unknown_entry_requirement_is_not_eligible(self) -> None:
        unknown = (ws.Audition("aud_10k", "10k", 10_000, None, True),)
        self.assertEqual(wp.decide(state(2, 8, 3_000, auditions=unknown)).rule, "planned_audition_unavailable")

    def test_higher_tier_substitutes(self) -> None:
        d = wp.decide(state(2, 8, 12_000, auditions=(AUDITIONS[3],)))
        self.assertEqual(d.audition_id, "aud_50k")

    def test_semifinal_handoff_reports_readiness(self) -> None:
        d = wp.decide(state(4, 8, 520_000, phase="semifinal",
                            stats={"Vo": 480, "Da": 90, "Vi": 80, "Me": 250}))
        self.assertEqual(d.kind, "handoff")
        self.assertEqual(d.plan["checks"]["fans_true_end"], True)
        self.assertIn("not_ready:mental_min", d.reasons)


class OtherStrategyTests(unittest.TestCase):
    def test_da_single_mental_floor_prefers_work(self) -> None:
        d = wp.decide(state(1, 2, 0, stats={"Vo": 50, "Da": 120, "Vi": 50, "Me": 90}, trend={"Vo": 1, "Da": 2, "Vi": 3}),
                      strategy="da_single")
        self.assertEqual((d.kind, d.option_id, d.rule), ("work", "work_radio", "mental_floor"))

    def test_vi_single_lessons_vi(self) -> None:
        d = wp.decide(state(2, 2, 3_000, stats={"Vo": 50, "Da": 50, "Vi": 220, "Me": 300}), strategy="vi_single")
        self.assertEqual((d.kind, d.option_id), ("lesson", "lesson_vi"))

    def test_balanced_lessons_lowest(self) -> None:
        d = wp.decide(state(2, 2, 3_000, stats={"Vo": 150, "Da": 140, "Vi": 160, "Me": 300}), strategy="balanced")
        self.assertEqual((d.kind, d.attribute), ("lesson", "Da"))
        late = wp.decide(state(2, 7, 3_000), strategy="balanced")
        self.assertEqual((late.kind, late.attribute), ("audition", None))

    def test_strategy_table_is_complete(self) -> None:
        for name, strategy in wp.STRATEGIES.items():
            with self.subTest(name):
                self.assertEqual(sorted(strategy.seasons), [1, 2, 3, 4])
                self.assertTrue(strategy.source and strategy.note)

    def test_decision_is_deterministic_and_serialisable(self) -> None:
        a, b = wp.decide(state(3, 6, 15_000)), wp.decide(state(3, 6, 15_000))
        self.assertEqual(a, b)
        json.dumps(a.to_dict(), ensure_ascii=False)
        self.assertIn("audition", a.explain())


if __name__ == "__main__":
    unittest.main()
