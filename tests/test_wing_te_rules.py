"""Rules learned in the first supervised True End run (unit7 / Middle, 2026-10-04).

Synthetic fixtures modelled on the recorded run: audition tables per season (fan reward, difficulty, rank gate) are
the observed ones; the recommended stats except S4 100k (Vo 450 / Me 450) are synthetic placeholders.
"""
from __future__ import annotations

import dataclasses
import unittest

from agent.core import wing_policy as wp
from agent.core import wing_state as ws
from tests.test_wing_state import make_state


def aud(id_, reward, diff, vo, me=None, rank=None):
    rec = {"Vo": vo} | ({"Me": me} if me is not None else {})
    return ws.Audition(id_, id_, reward, None, True, diff, rec, rank)


SEASON_AUDITIONS = {
    1: (aud("s1_1k", 1_000, 1, 60), aud("s1_2k", 2_000, 2, 80), aud("s1_3k", 3_000, 3, 100),
        aud("s1_5k", 5_000, 4, 130)),
    2: (aud("s2_3k", 3_000, 6, 150), aud("s2_5k", 5_000, 7, 170), aud("s2_10k", 10_000, 8, 190),
        aud("s2_15k", 15_000, 10, 230, rank="D")),
    3: (aud("s3_15k", 15_000, 11, 260), aud("s3_20k", 20_000, 12, 290), aud("s3_40k", 40_000, 13, 330),
        aud("s3_50k", 50_000, 15, 380, rank="C")),
    4: (aud("s4_15k", 15_000, 16, 360), aud("s4_25k", 25_000, 17, 390), aud("s4_50k", 50_000, 18, 420),
        aud("s4_100k", 100_000, 19, 450, me=450, rank="B")),
}

OPTIONS = (
    ws.ScheduleOption("lesson_vo", "lesson", "Vo", "Vocal", True, {"Vo": 30}, 20, 1),
    ws.ScheduleOption("lesson_da", "lesson", "Da", "Dance", True, {"Da": 30}, 20, 0),
    ws.ScheduleOption("lesson_vi", "lesson", "Vi", "Visual", True, {"Vi": 30}, 20, 0),
    ws.ScheduleOption("work_radio", "work", None, "ラジオ", True, {"Me": 20}, 15, 0),
    ws.ScheduleOption("rest", "rest", None, "休息", True, {}, 0),
)


def run_state(season, week, fans, vo, *, wins=(), trend_vo=1, rank="E", me=200, **kw):
    others = [a for a in ws.ATTRIBUTES if a != "Vo"]
    trend = {"Vo": trend_vo, others[0]: 1 if trend_vo != 1 else 2, others[1]: 3 if trend_vo != 3 else 2}
    kw.setdefault("auditions", SEASON_AUDITIONS[season])
    kw.setdefault("options", OPTIONS)
    kw.setdefault("trouble_rate", 0.0)
    return make_state(season=season, week=week, fans=fans, stats={"Vo": vo, "Da": 60, "Vi": 60, "Me": me},
                      trend=trend, rank=rank, wins=tuple(ws.AuditionWin(*w) for w in wins), **kw)


def decide(s, **params):
    # These states stand for caps already read on the SP panel; the provisional 500 has its own tests (test_wing_caps).
    return wp.decide(s, "vo_highest", params={"provisional_cap": None, **params})


class HighestFansAuditionTests(unittest.TestCase):
    def test_takes_highest_unlocked_with_met_stat(self) -> None:
        d = decide(run_state(2, 3, 3_000, 220, trend_vo=2))
        self.assertEqual((d.kind, d.audition_id, d.rule), ("audition", "s2_10k", "audition_favourable"))
        self.assertEqual(d.pre_actions, ("spend_sp",))
        self.assertEqual((d.plan["live_mode"], d.plan["live_mode_requested"]), ("policy", "policy"))
        unlocked = decide(run_state(2, 3, 3_000, 240, trend_vo=2, rank="D"))
        self.assertEqual(unlocked.audition_id, "s2_15k")
        stat_short = decide(run_state(2, 3, 3_000, 220, trend_vo=2, rank="D"))     # 15k needs Vo 230
        self.assertEqual(stat_short.audition_id, "s2_10k")

    def test_unread_rank_never_unlocks_a_gated_audition(self) -> None:
        d = decide(run_state(2, 3, 3_000, 240, trend_vo=1, rank=None))
        self.assertEqual(d.audition_id, "s2_10k")
        self.assertIn("rank_unread_gate_skipped", d.plan["notes"])

    def test_trend3_needs_stat_margin(self) -> None:
        weak = decide(run_state(2, 3, 3_000, 220, trend_vo=3))
        self.assertEqual(weak.kind, "lesson")
        self.assertIn("waiting_for_trend_s2_10k", weak.plan["notes"])
        # recorded run: S3 40k won on Vo trend 3 with Vo ~700 (> 1.6 × recommended)
        strong = decide(run_state(3, 2, 28_000, 700, trend_vo=3, rank="D"))
        self.assertEqual((strong.kind, strong.audition_id), ("audition", "s3_40k"))
        self.assertTrue(any("stat_700_ge_1.6x_330" in r for r in strong.reasons))
        strict = decide(run_state(3, 2, 28_000, 700, trend_vo=3, rank="D"), trend3_stat_factor=2.5)
        self.assertEqual(strict.kind, "lesson")

    def test_deadline_without_favourable_trend(self) -> None:
        d = decide(run_state(2, 7, 3_000, 220, trend_vo=3))
        self.assertEqual((d.kind, d.rule, d.audition_id), ("audition", "audition_due", "s2_10k"))
        self.assertIn("trend_unfavourable", d.reasons)

    def test_tight_without_qualifying_audition_stops(self) -> None:
        # last chance: with no week to spare the highest open audition is taken below the
        # recommendation; only with te_last_chance off does the run stop
        d = decide(run_state(2, 8, 3_000, 100, trend_vo=1))
        self.assertEqual((d.kind, d.audition_id), ("audition", "s2_10k"))
        self.assertIn("last_chance_below_recommendation", d.plan["notes"])
        d = decide(run_state(2, 8, 3_000, 100, trend_vo=1), te_last_chance=False)
        self.assertEqual((d.kind, d.rule), ("stop", "no_audition_meets_recommendation"))
        loose = decide(run_state(2, 3, 3_000, 100, trend_vo=1))
        self.assertEqual(loose.kind, "lesson")
        self.assertIn("recommended_primary_not_met", loose.plan["notes"])

    def test_recommended_missing_is_not_met(self) -> None:
        bare = (ws.Audition("s2_10k", "x", 10_000, None, True),)
        d = decide(run_state(2, 8, 3_000, 900, auditions=bare), te_last_chance=False)
        self.assertEqual(d.rule, "no_audition_meets_recommendation")
        d = decide(run_state(2, 8, 3_000, 900, auditions=bare))
        self.assertIn("recommended_primary_not_met", d.plan["notes"])

    def test_bank_one_extra_audition_after_target(self) -> None:
        d = decide(run_state(2, 5, 13_000, 240, wins=[(2, 10_000)], trend_vo=1, rank="D"))
        self.assertEqual((d.kind, d.rule, d.audition_id), ("audition", "audition_bank_fans", "s2_15k"))
        capped = decide(run_state(2, 6, 28_000, 260, wins=[(2, 10_000), (2, 15_000)], trend_vo=1, rank="D"))
        self.assertEqual(capped.kind, "lesson")
        unfavourable = decide(run_state(2, 5, 13_000, 240, wins=[(2, 10_000)], trend_vo=3, rank="D"))
        self.assertEqual(unfavourable.kind, "lesson")

    def test_s4_true_end_route(self) -> None:
        d = decide(run_state(4, 1, 120_000, 900, trend_vo=3, rank="B", me=310))
        self.assertEqual((d.kind, d.audition_id), ("audition", "s4_100k"))
        self.assertEqual(d.plan["needed_auditions"], 4)
        self.assertIn("mental_below_recommended", d.reasons)
        rank_c = decide(run_state(4, 1, 120_000, 900, trend_vo=1, rank="C"))
        self.assertEqual(rank_c.audition_id, "s4_50k")
        done = decide(run_state(4, 6, 519_861, 1_100, rank="B"))
        self.assertEqual((done.kind, done.rule), ("lesson", "free_time_lesson"))
        late = decide(run_state(4, 6, 120_000, 900, rank="B"))
        self.assertEqual((late.kind, late.rule), ("stop", "fan_plan_unreachable"))

    def test_s1_target_by_work_fans(self) -> None:
        works = OPTIONS + (ws.ScheduleOption("work_event", "work", None, "イベント", True, {"Me": 10}, 15, 0,
                                             False, 400),)
        early = decide(run_state(1, 4, 0, 120, options=works, trend_vo=1))
        self.assertEqual((early.kind, early.plan["fan_route"]), ("lesson", "work"))
        late = decide(run_state(1, 5, 0, 120, options=works, trend_vo=1))
        self.assertEqual((late.kind, late.option_id, late.rule), ("work", "work_event", "work_for_season_fans"))
        no_work = decide(run_state(1, 4, 0, 120, trend_vo=1))
        self.assertEqual((no_work.kind, no_work.audition_id), ("audition", "s1_3k"))

    def test_legacy_strategy_untouched(self) -> None:
        d = wp.decide(run_state(2, 7, 3_000, 220, trend_vo=3, auditions=(
            ws.Audition("aud_10k", "10k", 10_000, 1_000, True),)), "vo_single")
        self.assertEqual((d.kind, d.rule), ("audition", "audition_due"))
        self.assertEqual(d.pre_actions, ("spend_sp",))


class WeeklyActionTests(unittest.TestCase):
    def test_room_with_most_supports_and_recovery(self) -> None:
        rooms = (ws.ScheduleOption("lesson_vo_a", "lesson", "Vo", "", True, {"Vo": 30}, 20, 1),
                 ws.ScheduleOption("lesson_vo_b", "lesson", "Vo", "", True, {"Vo": 28}, 20, 2),
                 ws.ScheduleOption("rest", "rest"))
        d = decide(run_state(2, 3, 3_000, 100, options=rooms))
        self.assertEqual((d.kind, d.option_id), ("lesson", "lesson_vo_b"))
        self.assertIn("room_supports_2", d.reasons)
        rooms = (dataclasses.replace(rooms[0], stamina_recovery=True),) + rooms[1:]
        self.assertEqual(decide(run_state(2, 3, 3_000, 100, options=rooms)).option_id, "lesson_vo_a")

    def test_rest_only_at_trouble_threshold(self) -> None:
        # 2026-10-08: the highest_fans rest threshold is 10 % (was 15 %)
        self.assertEqual(decide(run_state(2, 3, 3_000, 100, trouble_rate=8.0)).kind, "lesson")
        self.assertEqual(decide(run_state(2, 3, 3_000, 100, trouble_rate=10.0)).kind, "rest")
        d = decide(run_state(2, 3, 3_000, 100, trouble_rate=18.0))
        self.assertEqual((d.kind, d.rule), ("rest", "fatigue_guard"))
        self.assertEqual(decide(run_state(2, 3, 3_000, 100, trouble_rate=18.0), trouble_rest_rate=20.0).kind,
                         "lesson")
        # stamina is only a fallback when the trouble rate is unread
        self.assertEqual(decide(run_state(2, 3, 3_000, 100, trouble_rate=0.0, stamina=10)).kind, "lesson")
        self.assertEqual(decide(run_state(2, 3, 3_000, 100, trouble_rate=None, stamina=10)).kind, "rest")

    def test_stamina_recovery_room_instead_of_rest(self) -> None:
        opts = (dataclasses.replace(OPTIONS[0], stamina_recovery=True),) + OPTIONS[1:]
        d = decide(run_state(2, 3, 3_000, 100, trouble_rate=18.0, options=opts))
        self.assertEqual((d.kind, d.option_id, d.rule), ("lesson", "lesson_vo", "stamina_recovery_room"))
        bad = decide(run_state(2, 3, 3_000, 100, trouble_rate=18.0, options=opts, condition="不調"))
        self.assertEqual(bad.kind, "rest")

    def test_favourable_audition_deferred_by_fatigue_unless_tight(self) -> None:
        d = decide(run_state(2, 3, 3_000, 220, trend_vo=1, trouble_rate=20.0))
        self.assertEqual(d.kind, "rest")
        self.assertIn("audition_deferred_for_fatigue", d.plan["notes"])
        tight = decide(run_state(2, 8, 3_000, 220, trend_vo=1, trouble_rate=20.0))
        self.assertEqual(tight.kind, "audition")
        self.assertIn("deadline_overrides_fatigue", tight.reasons)

    def test_primary_at_cap_spends_sp_first(self) -> None:
        capped = dict(stat_caps={"Vo": 778}, wins=[(3, 40_000), (3, 50_000)])
        d = decide(run_state(3, 6, 117_000, 760, sp=80, **capped))
        self.assertEqual((d.kind, d.rule, d.pre_actions), ("spend_sp", "primary_at_cap_spend_sp_first", ("spend_sp",)))
        poor = decide(run_state(3, 6, 117_000, 760, sp=10, **capped))
        self.assertEqual(poor.kind, "lesson")
        self.assertIn("capped_lesson_earns_sp", poor.reasons)
        self.assertEqual(decide(run_state(3, 6, 117_000, 760, sp=None, **capped)).rule, "sp_unread_at_cap")
        room = decide(run_state(3, 6, 117_000, 700, sp=80, **capped))
        self.assertEqual(room.kind, "lesson")

    def test_promise_lesson_priority(self) -> None:
        promise = {"season": 2, "week": 3, "attribute": "Da"}
        d = decide(run_state(2, 3, 3_000, 220, trend_vo=1, promise=promise))
        self.assertEqual((d.kind, d.option_id, d.rule), ("lesson", "lesson_da", "promise_lesson"))
        tight = decide(run_state(2, 8, 3_000, 220, trend_vo=1, promise=dict(promise, week=8)))
        self.assertEqual(tight.kind, "audition")
        risky = decide(run_state(2, 3, 3_000, 100, promise=promise, trouble_rate=35.0))
        self.assertEqual(risky.kind, "rest")
        self.assertIn("promise_skipped_trouble_35.0", risky.plan["notes"])
        other_week = decide(run_state(2, 4, 3_000, 100, promise=promise))
        self.assertEqual(other_week.option_id, "lesson_vo")

    def test_promise_applies_to_legacy_strategy(self) -> None:
        s = run_state(2, 3, 3_000, 100, promise={"season": 2, "week": 3, "attribute": "Vi"},
                      auditions=(ws.Audition("aud_10k", "10k", 10_000, 1_000, True),))
        self.assertEqual(wp.decide(s, "vo_single").option_id, "lesson_vi")


class LiveModeTests(unittest.TestCase):
    def test_handoff_reports_live_mode_and_spends_sp(self) -> None:
        s = run_state(4, 8, 520_000, 1_273, me=490, phase="semifinal", rank="B")
        d = decide(s)
        # 2026-10-05: default is the own live policy; the game's Auto is never used when 1st place is required
        self.assertEqual((d.kind, d.plan["live_mode"], d.pre_actions), ("handoff", "policy", ("spend_sp",)))
        opted = decide(s, live_mode="game_auto")
        self.assertEqual((opted.plan["live_mode"], opted.plan["live_mode_reason"]), ("policy", "first_place_required"))
        self.assertEqual(decide(s, live_mode="agent").plan["live_mode"], "policy")
        self.assertEqual(decide(s, live_mode="auto_then_agent").rule, "unsupported_live_mode")
        self.assertEqual(decide(run_state(4, 8, 520_000, 1_273, phase="final", sp=0)).pre_actions, ())

    def test_game_auto_only_with_opt_in_and_margin(self) -> None:
        s = run_state(4, 1, 120_000, 900, trend_vo=3, rank="B", me=310)
        default = decide(s)
        self.assertEqual((default.kind, default.plan["live_mode"], default.plan["live_mode_reason"]),
                         ("audition", "policy", "auto_not_opted_in"))
        opted = decide(s, live_mode="game_auto")
        self.assertEqual(opted.plan["live_mode_requested"], "game_auto")
        p_pass = opted.plan["audition_estimate"]["p_pass"]
        self.assertEqual(opted.plan["live_mode"], "game_auto" if p_pass >= 0.97 else "policy")
        strong = decide(run_state(4, 1, 120_000, 2_000, trend_vo=1, rank="B", me=600), live_mode="game_auto")
        self.assertEqual((strong.plan["live_mode"], strong.plan["live_mode_reason"]),
                         ("game_auto", "safe_margin_opted_in"))

    def test_new_strategies_registered(self) -> None:
        for name in ("vo_highest", "da_highest", "vi_highest"):
            with self.subTest(name):
                self.assertEqual(wp.STRATEGIES[name].audition_rule, "highest_fans")

    def test_state_round_trip_with_new_fields(self) -> None:
        s = run_state(3, 2, 28_000, 700, stat_caps={"Vo": 778}, promise={"season": 3, "week": 4, "attribute": "Vo"},
                      rank="C")
        again = ws.WingState.from_json(s.to_json())
        self.assertEqual(again.to_dict(), s.to_dict())
        self.assertEqual(again.auditions[3].required_rank, "C")
        self.assertEqual(decide(again), decide(s))


if __name__ == "__main__":
    unittest.main()
