"""強化週間 limited auditions in the W.I.N.G. plan (agent/core/wing_special.py).

A limited audition is a bonus only: no target counts on it, the final week is reserved only when the
ordinary route has a fallback, the final-week entry needs the win estimate and a margin, and each season can be
switched off. Also covers how its Live is played (own policy / the game's Auto)."""
from __future__ import annotations

import dataclasses
import json
import shutil
import subprocess
import unittest
from pathlib import Path

from agent.core import produce_config as pc
from agent.core import wing_live_policy as wlp
from agent.core import wing_nav as nav
from agent.core import wing_policy as wp
from agent.core import wing_score as sc
from agent.core import wing_screen as wscr
from agent.core import wing_special as wsp
from tests.test_wing_te_rules import SEASON_AUDITIONS, aud, run_state

ROOT = Path(__file__).resolve().parents[1]
LIMITED = {1: aud("s1_40k_machikado", 40_000, None, 120, me=80),
           2: aud("s2_100k_ereebest", 100_000, None, 170, me=150),
           3: aud("s3_50k_mezase", 50_000, None, 400, me=300),
           4: aud("s4_250k_kaeritakunai", 250_000, None, 450, me=500)}


def final_week(season, fans, *, vo=480, me=200, rank="E", page=True, locked=False, **kw):
    limited = (dataclasses.replace(LIMITED[season], available=not locked),) if page else ()
    return run_state(season, 8, fans, vo, me=me, rank=rank, sp=0, auditions=SEASON_AUDITIONS[season] + limited, **kw)


def score(state, window=True, **params):
    return wp.decide(state, "vo_score", params={"provisional_cap": None, "special_window": window, **params})


def rules(state, window=True, **params):
    return wp.decide(state, "vo_highest", params={"provisional_cap": None, "special_window": window, **params})


def plan(state, **kw):
    return wsp.season_plan(state, window=True, primary="Vo", **kw)


class PlannerTests(unittest.TestCase):
    def test_ids_and_seasons_match_the_page_reader(self):
        self.assertEqual({s.id: s.season for s in wsp.SPECIALS.values()},
                         {k: season for k, (season, _) in wscr.SPECIAL_AUDITION_NAMES.items()})
        self.assertEqual(wsp.special_of("s2_100k_ereebest").season, 2)
        self.assertIsNone(wsp.special_of("s2_10k"))
        self.assertTrue(all(s.live in wsp.LIVE_MODES for s in wsp.SPECIALS.values()))

    def test_route_counts_ordinary_wins_on_the_static_table(self):
        # S2 limited (+100k): from 11k the TE route needs S3 40k+50k and S4 100k x4; from 111k only S4 100k x4.
        self.assertEqual(wsp.ordinary_wins(11_000, 3, "true_end"), 6)
        self.assertEqual(wsp.ordinary_wins(111_000, 3, "true_end"), 4)
        self.assertEqual(wsp.season_wins(161_000, 4, 500_000), (4, 561_000))
        self.assertEqual(plan(run_state(2, 4, 3_000, 220))["wins_saved"], 2.0)

    def test_no_window_no_plan_and_setting_off(self):
        state = run_state(2, 3, 3_000, 220)
        self.assertIsNone(wsp.season_plan(state, window=False))
        self.assertIsNone(plan(dataclasses.replace(state, phase="semifinal")))
        off = plan(state, seasons=(1, 3, 4))
        self.assertEqual((off["planned"], off["reason"]), (False, "special_skipped:setting_off"))

    def test_final_week_is_reserved_only_with_a_fallback(self):
        # S2 10k still needed: 1 ordinary win + 1 spare week must fit before the final week.
        ok = plan(run_state(2, 5, 3_000, 220))                    # 4 weeks left
        self.assertEqual((ok["planned"], ok["reserve"], ok["free_weeks"]), (True, True, 1))
        self.assertEqual(ok["prep"], {"kind": "affection"})
        short = plan(run_state(2, 7, 3_000, 220))                 # 2 weeks left: no spare week once reserved
        self.assertEqual((short["planned"], short["reserve"], short["reason"]),
                         (False, False, "special_skipped:no_fallback"))
        s4 = plan(run_state(4, 5, 161_000, 480, me=520, rank="B"))   # 100k x4 in 3 weeks: impossible
        self.assertEqual(s4["reason"], "special_skipped:no_fallback")
        early = plan(run_state(4, 2, 161_000, 480, me=520, rank="B"))  # 100k x4 + 1 spare in 6 weeks
        self.assertEqual((early["planned"], early["reserve"], early["prep"]), (True, True, None))
        self.assertNotIn("hard_credit", early)
        self.assertNotIn("ordinary_target", early)
        tight_prep = plan(run_state(3, 4, 60_000, 400, rank="C"))    # 100k: 1 win + 1 spare of 4 weeks -> 2 free
        self.assertEqual((tight_prep["planned"], tight_prep["free_weeks"], tight_prep["prep"]), (True, 2, {"kind": "bonds"}))
        busy = plan(run_state(3, 6, 60_000, 400, rank="C"))          # 1 win + spare in 2 weeks: no week for prep
        self.assertEqual((busy["planned"], busy["prep"]), (True, None))

    def test_entry_conditions_need_no_extra_auditions(self):
        far = plan(run_state(1, 3, 500, 120))                     # S1 route ends at 3.5k: 10k only with extra wins
        self.assertEqual(far["reason"], "special_skipped:condition_out_of_reach")
        met = plan(run_state(1, 3, 12_000, 120, rank="D"))
        self.assertEqual((met["status"], met["planned"]), ("met", True))
        prep = plan(run_state(4, 2, 161_000, 480, me=470, rank="B"))  # radio Me +20: 2 weeks, 1 free week
        self.assertEqual((prep["planned"], prep["reason"]), (False, "special_skipped:me_deficit_30_not_cheap"))
        cheap = plan(run_state(4, 2, 161_000, 480, me=485, rank="B"))
        self.assertEqual(cheap["prep"], {"kind": "Me", "deficit": 15, "gain_per_week": 20, "value_weeks": 1.0})
        capped = plan(run_state(4, 2, 161_000, 480, me=485, rank="B", stat_caps={"Me": 490}))
        self.assertEqual(capped["reason"], "special_skipped:me_cap_490_below_500")

    def test_final_week_page_and_win_margin(self):
        self.assertEqual(plan(final_week(2, 11_000, vo=230))["reason"], "bonus_target_met")
        locked = plan(final_week(2, 11_000, vo=230, locked=True))
        self.assertEqual((locked["status"], locked["planned"]), ("unmet", False))
        self.assertEqual(plan(final_week(2, 11_000, vo=230, page=False))["status"], "unseen")
        # The game's Auto plays S2: primary >= 1.3 x 推奨 (170 -> 221) and Me >= 推奨 Me.
        low = plan(final_week(2, 11_000, vo=215))
        self.assertEqual((low["planned"], low["reason"], low["win"]["min_ratio"]),
                         (False, "special_skipped:win_chance_low", 1.3))
        self.assertEqual(plan(final_week(2, 11_000, vo=230, me=140))["reason"], "special_skipped:win_chance_low")
        no_me = dataclasses.replace(LIMITED[2], recommended={"Vo": 170})
        state = run_state(2, 8, 11_000, 230, sp=0, auditions=SEASON_AUDITIONS[2] + (no_me,))
        self.assertEqual(plan(state)["reason"], "special_skipped:win_unknown")
        # Own play (S3): the ordinary threshold, primary >= 推奨.
        self.assertTrue(plan(final_week(3, 110_000, vo=400, rank="B"))["planned"])
        self.assertEqual(plan(final_week(3, 110_000, vo=390, rank="B"))["reason"], "special_skipped:win_chance_low")
        self.assertEqual(wsp.season_plan(final_week(3, 110_000, vo=400, rank="B"), window=True)["reason"],
                         "special_skipped:primary_unknown")
        below = plan(final_week(2, 3_000, vo=230))
        self.assertEqual((below["planned"], below["bonus"], below["reason"]), (True, False, "last_chance_only"))


class ScoreModeTests(unittest.TestCase):
    def test_bonus_is_entered_only_once_the_target_is_met(self):
        state = final_week(2, 11_000, vo=230, rank="D")
        self.assertEqual(score(state, window=False).kind, "lesson")
        d = score(state)
        self.assertEqual((d.kind, d.audition_id, d.rule), ("audition", "s2_100k_ereebest", "special_audition_bonus"))
        self.assertEqual(d.plan["live_special"]["live"], "game_auto")
        self.assertEqual(score(state, special_seasons=(1, 3, 4)).kind, "lesson")
        te = score(final_week(4, 511_000, vo=600, me=520, rank="A"))
        self.assertEqual((te.audition_id, te.rule), ("s4_250k_kaeritakunai", "special_audition_bonus"))

    def test_below_the_target_the_final_week_belongs_to_the_ordinary_audition(self):
        d = score(final_week(2, 3_000, vo=230))
        self.assertEqual((d.audition_id, d.rule), ("s2_10k", "score_tight_deadline_audition"))
        excluded = {c["id"]: c["notes"] for c in d.plan["excluded"]}
        self.assertIn("limited_bonus_only", excluded["s2_100k_ereebest"])
        s3 = score(final_week(3, 60_000, vo=420, rank="C"))         # gate met, 100k route target not
        self.assertNotEqual(s3.audition_id, "s3_50k_mezase")

    def test_targets_never_count_on_the_limited_audition(self):
        for state in (run_state(4, 2, 161_000, 480, me=520, rank="B", sp=0),
                      run_state(3, 3, 60_000, 420, rank="C", sp=0)):
            on, off = score(state).plan["context"], score(state, window=False).plan["context"]
            self.assertEqual((on["soft_target"], on["hard_target"], on["n_need"]),
                             (off["soft_target"], off["hard_target"], off["n_need"]))

    def test_reserved_week_brings_the_ordinary_deadline_forward(self):
        state = run_state(2, 5, 3_000, 230, sp=0, trend_vo=3)
        on, off = score(state).plan["context"], score(state, window=False).plan["context"]
        self.assertTrue(on["special_reserved_week"])
        self.assertGreater(on["urgency"], off["urgency"])
        self.assertNotIn("special_reserved_week", score(run_state(2, 7, 3_000, 230, sp=0)).plan["context"])

    def test_last_chance_only_when_no_ordinary_route_is_left(self):
        d = score(final_week(4, 261_000, vo=600, me=520, rank="B"))
        self.assertEqual((d.audition_id, d.rule), ("s4_250k_kaeritakunai", "score_tight_deadline_audition"))
        self.assertEqual(d.plan["context"]["limited_last_chance"], "s4_250k_kaeritakunai")
        stop = score(final_week(4, 261_000, vo=500, me=520, rank="B"))      # 1.11x < 1.3x: no entry
        self.assertEqual((stop.kind, stop.rule), ("stop", "fan_plan_unreachable"))

    def test_entry_condition_prep(self):
        me = run_state(4, 2, 161_000, 480, me=485, rank="B", sp=0)
        radio = next(c for c in score(me).plan["scores"] if c["id"] == "work_radio")
        self.assertIn("special_me_prep_15", radio["notes"])
        self.assertEqual(score(me).plan["context"]["special_me_pep"], 2.0)
        promise = run_state(2, 3, 11_000, 230, rank="D", sp=0, promise={"season": 2, "week": 3, "attribute": "Da"})
        off = next(c for c in score(promise, window=False).plan["scores"] if c["id"] == "lesson_da")
        on = next(c for c in score(promise).plan["scores"] if c["id"] == "lesson_da")
        self.assertAlmostEqual(on["terms"]["promise"], 2 * off["terms"]["promise"], places=3)
        bonds = run_state(3, 3, 111_000, 420, rank="B", sp=0)
        lesson = lambda d: next(c for c in d.plan["scores"] if c["id"] == "lesson_vo")  # noqa: E731
        self.assertEqual(lesson(score(bonds, window=False))["terms"]["bond"], 0)
        self.assertGreater(lesson(score(bonds))["terms"]["bond"], 0)


class RuleTableTests(unittest.TestCase):
    def test_final_week_bonus_and_last_chance(self):
        d = rules(final_week(2, 11_000, vo=230, rank="D"))
        self.assertEqual((d.audition_id, d.rule), ("s2_100k_ereebest", "special_audition"))
        self.assertNotEqual(rules(final_week(2, 11_000, vo=215, rank="D")).audition_id, "s2_100k_ereebest")
        self.assertEqual(rules(final_week(2, 3_000, vo=230)).audition_id, "s2_10k")
        last = rules(final_week(4, 261_000, vo=600, me=520, rank="B"))
        self.assertEqual((last.audition_id, last.rule), ("s4_250k_kaeritakunai", "special_audition_last_chance"))

    def test_reserved_week(self):
        reserved = rules(run_state(2, 5, 3_000, 230, trend_vo=3))
        self.assertEqual((reserved.plan["weeks_left"], reserved.plan["ordinary_weeks"]), (4, 3))
        self.assertNotIn("ordinary_weeks", rules(run_state(2, 7, 3_000, 230, trend_vo=3)).plan)


class SettingTests(unittest.TestCase):
    def test_default_is_all_seasons_and_each_can_be_switched_off(self):
        params = pc.wing_params(pc.ProduceConfig.from_task_options({}))
        self.assertEqual(params["policy"]["special_seasons"], (1, 2, 3, 4))
        off = pc.ProduceConfig.from_task_options({"special_s2": False, "special_s4": False})
        self.assertEqual(pc.wing_params(off)["policy"]["special_seasons"], (1, 3))
        with self.assertRaises(ValueError):
            pc.ProduceConfig.from_task_options({"special_s1": "yes"})

    def test_ui_shows_the_setting_only_during_the_event(self):
        notes = json.loads((ROOT / "ui" / "annotations.json").read_text(encoding="utf-8"))["options"]
        tasks = json.loads((ROOT / "assets" / "tasks" / "auto_produce.json").read_text(encoding="utf-8"))
        keys = [f"Produce限定试镜S{i}" for i in range(1, 5)]
        self.assertTrue(set(keys) <= set(tasks["task"][0]["option"]))
        for key in keys:
            self.assertEqual(tasks["option"][key]["default_case"], "Yes")
            self.assertEqual(notes[key]["active_dates"], [list(w) for w in sc.SPECIAL_AUDITION_WINDOWS])


    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_js_visible_only_inside_the_event_window(self):
        script = ("import { visibleOptions } from './ui/components/display.js';"
                  "import { readFileSync } from 'node:fs';"
                  "const schema = JSON.parse(readFileSync('ui/fixtures/schema.json', 'utf8'));"
                  "const annotations = JSON.parse(readFileSync('ui/annotations.json', 'utf8'));"
                  "const task = schema.tasks.find((t) => t.name === 'AutoProduce');"
                  "const shown = (now, dev) => visibleOptions({ schema, annotations, prefs: { developer: dev }, now },"
                  " task, { enabled: true, option: {} }).filter((k) => k.startsWith('Produce限定试镜'));"
                  # 05:00 JST rollover: 2026-11-10 20:00 UTC is game day 2026-11-11
                  "console.log(JSON.stringify([shown('2026-10-29T03:00:00Z', false), shown('2026-11-10T19:00:00Z', false),"
                  " shown('2026-11-10T20:00:00Z', false), shown('2026-10-19T12:00:00Z', true)]));")
        result = subprocess.run(["node", "--input-type=module", "-e", script], cwd=ROOT, capture_output=True, text=True,
                                encoding="utf-8", timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        keys = [f"Produce限定试镜S{i}" for i in range(1, 5)]
        self.assertEqual(json.loads(result.stdout), [keys, keys, [], []])


class LiveTests(unittest.TestCase):
    JUDGES = [{"attribute": "Vo", "trend_rank": 2}, {"attribute": "Da", "trend_rank": 1},
              {"attribute": "Vi", "trend_rank": 3}]

    def test_s3_rule_spears_trend2_unless_the_primary_is_trend1(self):
        self.assertEqual(wlp.target_judge(self.JUDGES, primary="Vo", estimate=None)["attribute"], "Da")
        t = wlp.target_judge(self.JUDGES, primary="Vi", estimate=None, special_rule="trend2")
        self.assertEqual((t["attribute"], t["reason"]), ("Vo", "special_spear_trend2"))
        self.assertEqual(wlp.target_judge(self.JUDGES, primary="Da", estimate=None, special_rule="trend2")["attribute"],
                         "Da")
        left = [dict(j, left=j["attribute"] == "Vo", present=j["attribute"] != "Vo") for j in self.JUDGES]
        self.assertEqual(wlp.target_judge(left, primary="Vi", estimate=None, special_rule="trend2")["attribute"], "Da")

    def runtime(self, special):
        rt = nav.WingNavRuntime()
        rt.begin()
        rt.memory.live_special, rt.memory.live_need_first = special, False
        return rt

    def test_unplayable_rule_hands_the_live_to_game_auto_behind_the_toggle(self):
        rt = self.runtime("ereebest")
        d = rt.decide("Live", {"auto": "OFF", "live_phase": "select_card", "live_state": {"turn": 1}})
        self.assertEqual((d.action, d.intent, d.reason), ("click", "live_auto", "live_special_auto"))
        self.assertTrue(rt.memory.live_auto_secured)
        self.assertEqual(rt.decide("Live", {"auto": "ON"}).reason, "live:game_auto_secured")
        rt = self.runtime("kaeritakunai")
        self.assertIsNone(rt._live_special_auto({}))                         # toggle unread: no tap
        self.assertEqual(rt._live_special_auto({"auto": "ON"}).reason, "live:game_auto_special")
        for special, need_first in (("mezase", False), ("machikado", False), ("ereebest", True), (None, False)):
            rt = self.runtime(special)
            rt.memory.live_need_first = need_first
            self.assertIsNone(rt._live_special_auto({"auto": "OFF"}), special)

    def test_limited_entry_marks_the_live_and_results_clear_it(self):
        rt = nav.WingNavRuntime()
        rt.begin()
        m = rt.memory
        m.pending, m.pending_audition, m.pending_special, m.audition_pages = "audition", 100_000, "ereebest", 0
        m.auditions_seen = {"key": [2, 1], "pages": [], "done": True, "special_week": True}
        page = {"season": 2, "weeks_left": 1, "reward": 100_000, "special": "ereebest", "recommended": {"Vo": 200}}
        self.assertEqual(rt.decide("AuditionTab", page).intent, "schedule_decide")
        self.assertEqual(m.live_special, "ereebest")
        self.assertEqual(m.wins, [])                     # no fans assumed before the result screen
        rt.decide("ResultJudge", {})
        self.assertIsNone(m.live_special)

    def test_live_special_survives_a_segment_and_never_reaches_the_wing_stage(self):
        m = nav.RunMemory(live_special="mezase")
        restored = nav.RunMemory()
        restored.restore(m.snapshot())
        self.assertEqual(restored.live_special, "mezase")
        with self.assertRaises(ValueError):
            nav.RunMemory().restore({**m.snapshot(), "live_special": "unknown"})
        rt = nav.WingNavRuntime(nav.RunMemory(live_special="kaeritakunai", final_sp_checked="HubSemiFinal",
                                              last_sp=0))
        rt.begin()
        self.assertEqual(rt.decide("HubSemiFinal", {}).intent, "hub_final_live")
        self.assertIsNone(rt.memory.live_special)


if __name__ == "__main__":
    unittest.main()
