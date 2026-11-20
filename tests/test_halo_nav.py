"""H2b: say "Halo" in the W.I.N.G. navigator (RunOptions.halo_route; 掘书线 only since 2026-11-07).

Synthetic readings check the week flow (Hub -> schedule tabs -> carousel survey -> 決定), the live and the gating; the
replay part drives the practice carousel with real H1 frames (private, skipped when absent)."""
import json
import re
import unittest
from pathlib import Path

from agent.core import wing_nav as nav
from tools import run_tasks
from tools import halo_samples as hs

NONE = dict.fromkeys(("vo", "da", "vi", "me", "hirameki", "rest", "work", "limit", "best"), 0)


def runtime(route="dig", **kw):
    return nav.WingNavRuntime(options=nav.RunOptions(input_enabled=True, halo_route=route, **kw))


class GoalsOptionTests(unittest.TestCase):
    def test_halo_goals_reach_the_memory(self):
        goals = {"facility": True, "facility_plus": False, "completion": None, "fans_cap": 100000}
        rt = runtime(halo_goals=goals)
        rt.decide("HaloHub", hub(season=1))
        self.assertEqual(rt.memory.halo.goals.as_dict(), goals)
        rt = runtime()
        rt.decide("HaloHub", hub(season=1))
        self.assertEqual(rt.memory.halo.goals, nav.hp.Goals())                 # no halo_goals: the defaults
        with self.assertRaises(ValueError):
            nav.RunOptions(halo_goals=goals)                         # only with halo_route
        with self.assertRaises(ValueError):
            nav.RunOptions(halo_route="dig", halo_goals={"fans_cap": 0})


def hub(**kw):
    read = {"season": 2, "weeks_left": 6, "stamina_pct": 60, "hub_button": "produce", "advice": dict(NONE)}
    read.update(kw)
    return read


def starter(route="dig", book="auto"):
    """A say "Halo" start run (H2c batch 2): mode start, the route and the book source."""
    return nav.WingNavRuntime(options=nav.RunOptions(input_enabled=True, mode="start", halo_route=route, halo_book=book))


EMPTY = {"slot1": "empty", "stats": None, "layout": "halo",
         "buttons": dict.fromkeys(("auto", "history", "decide", "preset"), True)}
FILLED = {**EMPTY, "slot1": "filled", "stats": {"vo": 150, "da": 50, "vi": 50, "me": 40}}


def decide(rt, screen, read):
    """One decision, re-read once when the runtime waits for a settled reading."""
    d = rt.decide(screen, read)
    if d.action == "wait" and d.reason.endswith("settling"):
        d = rt.decide(screen, read)
    return d


class OptionTests(unittest.TestCase):
    def test_route_values_and_exclusive_with_giveup(self):
        self.assertEqual(nav.RunOptions(halo_route="dig").halo_route, "dig")
        for bad in ({"halo_route": "x"}, {"halo_route": "fes"}, {"halo_route": "dig", "halo_giveup": True},
                    {"halo_giveup": 1}):
            with self.assertRaises(ValueError):
                nav.RunOptions(**bad)
        self.assertIn("halo_route", nav.RunOptions.PARAM_KEYS)

    def test_without_route_every_page_stops(self):
        rt = nav.WingNavRuntime(options=nav.RunOptions(input_enabled=True))
        for screen in ("HaloHub", "HaloLesson", "HaloPractice", "HaloAudition", "HaloReport", "HaloRecruit",
                       "HaloLiveSelect", "HaloGrowth"):
            self.assertEqual(rt.decide(screen, hub()).reason, "halo_not_automated", screen)
        self.assertEqual(rt.decide("FesBirthSkillDialog", {}).reason, "fes_birth_skill_dialog")

    def test_halo_taps_are_their_own_family_admitted_only_with_a_route(self):
        from agent.core.pipeline_actions import default_table
        halo = [i for i in nav.TARGETS if i.startswith("halo_")]
        self.assertTrue(halo)
        self.assertEqual({nav.intent_family(i) for i in halo}, {"wing-halo"})
        table = default_table()
        ids = {nav.action_id(i) for i in halo}
        # 2026-11-07 H2d: every say "Halo" tap is enabled (目標ライブ cards 1/2 too) ...
        self.assertEqual({a.id for a in table.actions if a.id in ids and not a.enabled}, set())
        # ... and admitted only for a run with halo_route.
        self.assertFalse(ids & run_tasks.wing_task_actions(halo=False))
        self.assertTrue(ids <= run_tasks.wing_task_actions(halo=True))
        # スキル一括変更: キャンセル back to フェスアイドル誕生 (Maa never changes skills)
        rt = runtime()
        d = rt.decide("SkillBulkChange", {})
        self.assertEqual((d.action, d.intent), ("click", "fes_skill_bulk_cancel"))
        self.assertEqual(nav.intent_family("fes_skill_bulk_cancel"), "wing-end")
        dig = run_tasks.wing_plan({"name": "Wing", "option": {"halo_route": "dig"}})
        self.assertTrue(run_tasks.wing_plan_halo(dig) and run_tasks.wing_plan_giveup(dig))
        self.assertFalse(run_tasks.wing_plan_halo(run_tasks.wing_plan({"name": "Wing", "option": {}})))


class SetupTests(unittest.TestCase):
    """H2c batch 2: Maa starts the say "Halo" run: 特化プロデュース → say "Halo" → the W.I.N.G. team →
    one book (おまかせ編成 / 設定履歴 / プリセット) → no items → 使用確認."""
    def test_book_option(self):
        self.assertEqual(nav.RunOptions(halo_route="dig", halo_book="preset2").halo_book, "preset2")
        for bad in ({"halo_route": "dig", "halo_book": "x"}, {"halo_book": "history"}):
            with self.assertRaises(ValueError):
                nav.RunOptions(**bad)
        self.assertIn("halo_book", nav.RunOptions.PARAM_KEYS)
        plan = run_tasks.wing_plan({"name": "Wing", "option": {"mode": "start", "halo_route": "dig",
                                                               "halo_book": "history"}})
        self.assertEqual(run_tasks._wing_run_param(plan)["halo_book"], "history")

    def test_wing_page_switches_to_the_special_tab_once(self):
        rt = starter()
        page = {"difficulty": "middle", "special_tab": True}
        self.assertEqual(rt.decide("ProduceSelect", page).intent, "halo_tab_special")
        for _ in range(rt.HALO_SETUP_WAITS):
            self.assertEqual(rt.decide("ProduceSelect", page).action, "wait")
        self.assertEqual(rt.decide("ProduceSelect", page).reason, "halo_tab_no_effect")
        self.assertEqual(starter().decide("ProduceSelect", {"special_tab": False}).reason, "produce_tab_unread")

    def test_special_list_moves_down_to_halo_then_next(self):
        rt = starter()
        page = {"special_tab": True, "next_label": True, "train_tab": True}
        self.assertEqual(rt.decide("ProduceSelectOther", {**page, "caption": "grad"}).intent, "halo_tab_special")
        self.assertEqual(rt.decide("ProduceSelectOther", {**page, "caption": "step"}).intent, "halo_scenario_down")
        self.assertEqual(rt.decide("ProduceSelectOther", {**page, "caption": "step"}).action, "wait")
        self.assertEqual(rt.decide("ProduceSelectOther", {**page, "caption": "halo"}).intent, "halo_scenario_next")
        self.assertEqual(starter().decide("ProduceSelectOther", {**page, "caption": "halo", "next_label": False}).reason,
                         "produce_next_unread")
        # Without a route the page keeps the W.I.N.G. rule (育成 tab).
        plain = nav.WingNavRuntime(options=nav.RunOptions(input_enabled=True, mode="start"))
        self.assertEqual(plain.decide("ProduceSelectOther", {**page, "caption": "halo"}).intent, "produce_tab_train")

    def test_unread_caption_waits_then_stops(self):
        rt = starter()
        for _ in range(rt.HALO_SETUP_WAITS):
            self.assertEqual(rt.decide("ProduceSelectOther", {"caption": None}).action, "wait")
        self.assertEqual(rt.decide("ProduceSelectOther", {"caption": None}).reason, "produce_caption_unread")

    def test_recommended_book_then_decide(self):
        rt = starter()
        self.assertEqual(rt.decide("BookSelect", {"book": EMPTY}).intent, "halo_book_auto")
        self.assertEqual(rt.decide("BookSelect", {"book": EMPTY}).action, "wait")
        rt.decide("Loading", {})
        d = rt.decide("BookSelect", {"book": FILLED})
        self.assertEqual((d.intent, d.detail["stats"]["vo"]), ("halo_book_decide", 150))
        self.assertEqual(rt.decide("BookSelect", {"book": FILLED}).action, "wait")

    def test_recommended_over_a_filled_slot_needs_the_loading(self):
        rt = starter()
        self.assertEqual(rt.decide("BookSelect", {"book": FILLED}).intent, "halo_book_auto")
        self.assertEqual(rt.decide("BookSelect", {"book": FILLED}).action, "wait")
        rt.decide("Loading", {})
        self.assertEqual(rt.decide("BookSelect", {"book": FILLED}).intent, "halo_book_decide")

    def test_history_and_presets(self):
        rt = starter(book="history")
        self.assertEqual(rt.decide("BookSelect", {"book": EMPTY}).intent, "halo_book_history")
        history = {"history": {"date": "2026/10/22", "call": True}}
        self.assertEqual(rt.decide("BookHistory", history).intent, "halo_history_call")
        self.assertEqual(rt.decide("BookSelect", {"book": FILLED}).intent, "halo_book_decide")
        rt = starter(book="preset2")
        self.assertEqual(rt.decide("BookSelect", {"book": EMPTY}).intent, "halo_book_preset")
        self.assertEqual(rt.decide("BookPreset", {"presets": [{"label": True, "set": True}] * 2}).intent,
                         "halo_preset_set_2")
        self.assertEqual(rt.decide("BookSelect", {"book": FILLED}).intent, "halo_book_decide")
        # A page the chosen source never opens stops.
        self.assertEqual(starter().decide("BookHistory", history).reason, "book_page_unexpected")

    PLAN = {"title": "夜明けのノウハウ", "vo": 120, "da": 80, "vi": 60, "me": 40, "eval": 650}

    def picker(self, evals=None, selected=(), grey=()):
        evals = evals or {}
        tiles = [{"r": r, "c": c, "eval": evals.get((r, c), 700 - 10 * (5 * r + c)), "grey": (r, c) in grey,
                  "selected": (r, c) in selected} for r in range(3) for c in range(5)]
        return {"picker": {"sort": "ノウハウ評価値", "order": "降順", "tiles": tiles}}

    def detail(self, title, **stats):
        return {"title": title, "stats": {k: stats.get(k, self.PLAN[k]) for k in ("vo", "da", "vi", "me")}}

    def target(self):
        rt = nav.WingNavRuntime(options=nav.RunOptions(input_enabled=True, mode="start", halo_route="dig",
                                                       halo_book="target", halo_book_plan=dict(self.PLAN)))
        self.assertEqual(rt.decide("BookSelect", {"book": EMPTY}).intent, "halo_book_slot1")
        return rt

    def test_target_book_option(self):
        with self.assertRaises(ValueError):
            nav.RunOptions(halo_route="dig", halo_book="target")                  # no plan
        with self.assertRaises(ValueError):
            nav.RunOptions(halo_route="dig", halo_book="auto", halo_book_plan=dict(self.PLAN))
        with self.assertRaises(ValueError):
            nav.RunOptions(halo_route="dig", halo_book="target", halo_book_plan={**self.PLAN, "vo": "120"})
        self.assertIn("halo_book_plan", nav.RunOptions.PARAM_KEYS)

    def test_target_book_found_checked_and_decided(self):
        rt = self.target()
        page = self.picker()                       # tile (1, 0) has 評価値 650
        d = rt.decide("BookPicker", page)
        self.assertEqual(d.intent, "halo_picker_tile_1_0")
        self.assertEqual(rt.decide("BookPicker", page).action, "wait")      # ✓ not yet shown
        shown = {**self.picker(selected=[(1, 0)]), "picker_detail": self.detail("夜明けのノウハウ")}
        self.assertEqual(rt.decide("BookPicker", shown).intent, "halo_picker_decide")
        self.assertEqual(rt.decide("BookPicker", shown).action, "wait")
        filled = {**EMPTY, "slot1": "filled", "stats": {k: self.PLAN[k] for k in ("vo", "da", "vi", "me")}}
        self.assertEqual(rt.decide("BookSelect", {"book": filled}).intent, "halo_book_decide")

    def test_a_same_score_other_book_is_taken_off_again(self):
        rt = self.target()
        evals = {(1, 0): 650, (1, 1): 650}
        rt.decide("BookPicker", self.picker(evals))
        wrong = {**self.picker(evals, selected=[(1, 0)]), "picker_detail": self.detail("別の本", vo=10)}
        for _ in range(rt.HALO_PICKER_WAITS):
            self.assertEqual(rt.decide("BookPicker", wrong).action, "wait")
        self.assertEqual(rt.decide("BookPicker", wrong).intent, "halo_picker_tile_1_0")    # unpick
        self.assertEqual(rt.decide("BookPicker", self.picker(evals)).intent, "halo_picker_tile_1_1")
        right = {**self.picker(evals, selected=[(1, 1)]), "picker_detail": self.detail("夜明けのノウハウ")}
        self.assertEqual(rt.decide("BookPicker", right).intent, "halo_picker_decide")

    def test_picker_stops(self):
        rt = self.target()
        page = self.picker()
        page["picker"]["sort"] = "入手順"
        self.assertEqual(rt.decide("BookPicker", page).reason, "picker_sort")
        rt = self.target()
        self.assertEqual(rt.decide("BookPicker", self.picker({(1, 0): 640})).reason, "target_book_not_visible")
        rt = self.target()
        self.assertEqual(rt.decide("BookPicker", self.picker(grey=[(1, 0)])).reason, "target_book_blocked")
        rt = self.target()
        self.assertEqual(rt.decide("BookPicker", self.picker(selected=[(0, 0)])).reason,
                         "picker_selection_unexpected")
        self.assertEqual(starter().decide("BookPicker", self.picker()).reason, "book_picker_not_automated")
        filled = {**EMPTY, "slot1": "filled", "stats": {"vo": 1, "da": 1, "vi": 1, "me": 1}}
        self.assertEqual(self.target_fresh().decide("BookSelect", {"book": filled}).reason, "book_slot_prefilled")

    def target_fresh(self):
        return nav.WingNavRuntime(options=nav.RunOptions(input_enabled=True, mode="start", halo_route="dig",
                                                         halo_book="target", halo_book_plan=dict(self.PLAN)))

    def test_a_different_book_in_the_slot_stops(self):
        rt = self.target()
        rt.decide("BookPicker", self.picker())
        rt.decide("BookPicker", {**self.picker(selected=[(1, 0)]), "picker_detail": self.detail("夜明けのノウハウ")})
        other = {**EMPTY, "slot1": "filled", "stats": {"vo": 1, "da": 80, "vi": 60, "me": 40}}
        self.assertEqual(rt.decide("BookSelect", {"book": other}).reason, "target_book_mismatch")

    def test_book_page_gates(self):
        rt = starter()
        three = {"book": {**FILLED, "layout": None}}      # S.T.E.P.'s three slots / an unsettled frame
        for _ in range(rt.HALO_SETUP_WAITS):
            self.assertEqual(rt.decide("BookSelect", three).action, "wait")
        self.assertEqual(rt.decide("BookSelect", three).reason, "book_layout_unread")
        self.assertEqual(starter().decide("BookSelect", {"book": {**EMPTY, "buttons": {}}}).reason,
                         "book_button_unread")
        for options in (nav.RunOptions(input_enabled=True, mode="start"),
                        nav.RunOptions(input_enabled=True, halo_route="dig")):
            self.assertEqual(nav.WingNavRuntime(options=options).decide("BookSelect", {"book": EMPTY}).reason,
                             "setup_requires_user")
        rt = starter()
        rt.memory.start_attempted = True
        self.assertEqual(rt.decide("BookSelect", {"book": EMPTY}).reason, "setup_after_start_attempt")


def status(points, bottom, counts=None, costs=None, learn=False):
    """A ステータス reading: points {colour: n}, bottom {colour: [stat, cap]}."""
    rows = {c: {"label": True, "stat_count": 0, "cap_count": 0, "stat_cost": 10, "cap_cost": 10} for c in
            ("vo", "da", "vi", "me")}
    for c, cost in (costs or {}).items():
        rows[c].update(cost)
    pts = {"vo": 0, "da": 0, "vi": 0, "me": 0, "unity": 0}
    pts.update(points)
    full = {c: [100, 500] for c in ("vo", "da", "vi", "me")}
    full.update(bottom)
    return {"growth": {"tab": "status", "points": pts, "rows": rows, "bottom": full, "learn": learn}}


def other(unity, plus=True, learn=False):
    return {"growth": {"tab": "other", "points": {"vo": 0, "da": 0, "vi": 0, "me": 0, "unity": unity},
                       "affection_plus": plus, "learn": learn}}


class GrowthTests(unittest.TestCase):
    """H2c batch 3: one 成長 visit per week from S2 (Vo/Da/Vi stats and caps, 親愛度+10 for the dig route)."""
    def test_step_rule(self):
        from agent.core import halo_growth as hg
        read = status({"vo": 74}, {"vo": [478, 520]})["growth"]
        self.assertEqual(hg.stat_step("vo", read), "cap_plus")          # 10 + 10 * 5 <= 74
        read = status({"vo": 64}, {"vo": [478, 530]})["growth"]
        self.assertEqual(hg.stat_step("vo", read), "stat_max")          # 10 + 10 * 6 > 64: fill instead
        read = status({"vo": 7}, {"vo": [358, 520]})["growth"]
        self.assertIsNone(hg.stat_step("vo", read))
        read = status({"vo": 50}, {"vo": [520, 520]}, costs={"vo": {"cap_cost": 15}})["growth"]
        self.assertEqual(hg.stat_step("vo", read), "cap_plus")
        read = status({"vo": 20}, {"vo": [520, 520]}, costs={"vo": {"cap_cost": 15}})["growth"]
        self.assertIsNone(hg.stat_step("vo", read))                     # a cap the stat could not use

    def test_parsers(self):
        from agent.core import halo_growth as hg
        texts = [("14", (692, 42, 36, 29)), ("4", (889, 52, 13, 15)), ("46", (813, 42, 37, 28)), ("2", (944, 43, 22, 27)),
                 ("1056", (1050, 41, 54, 30)), ("1,092", (1176, 42, 53, 29)), ("Vocal+10", (109, 195, 120, 28)),
                 ("20回", (442, 195, 55, 30)), ("1回", (1032, 197, 38, 26)), ("10", (114, 247, 39, 34)),
                 ("15", (696, 248, 38, 33)), ("548", (216, 633, 48, 26)), ("1,020", (214, 664, 50, 25))]
        read = hg.read(texts)
        self.assertEqual(read["points"], {"vo": 14, "da": 46, "vi": 2, "me": 1056, "unity": 1092})
        self.assertEqual(read["tab"], "status")
        self.assertEqual(read["rows"]["vo"], {"label": True, "stat_count": 20, "cap_count": 1, "stat_cost": 10,
                                              "cap_cost": 15})
        self.assertEqual(read["bottom"]["vo"], [548, 1020])

    def visit(self, route="dig", affection=10):
        rt = runtime(route)
        d = rt.decide("HaloHub", hub(affection_value=affection))
        self.assertEqual((d.intent, d.reason), ("halo_growth_open", "halo_growth"))
        return rt, d

    def test_dig_visit_fills_stats_learns_then_buys_affection(self):
        rt, d = self.visit()
        self.assertEqual(d.detail["plan"]["affection"], 1)
        steps = [
            (status({"vo": 74, "da": 9}, {"vo": [478, 520]}), "halo_growth_cap_vo"),
            (status({"vo": 64, "da": 9}, {"vo": [478, 530]}), "halo_growth_stat_vo"),
            (status({"vo": 4, "da": 9}, {"vo": [528, 530]}, learn=True), "halo_growth_learn"),
        ]
        for read, intent in steps:
            self.assertEqual(rt.decide("HaloGrowth", read).intent, intent)
        self.assertEqual(rt.decide("HaloGrowthConfirm", {}).intent, "halo_growth_confirm")
        self.assertEqual(rt.decide("HaloGrowth", status({"vo": 4, "da": 9}, {"vo": [528, 530]})).intent,
                         "halo_growth_tab_other")
        self.assertEqual(rt.decide("HaloGrowth", other(199)).intent, "halo_growth_affection")
        self.assertEqual(rt.decide("HaloGrowth", other(169, plus=False, learn=True)).intent, "halo_growth_learn")
        self.assertEqual(rt.decide("HaloGrowthConfirm", {}).intent, "halo_growth_confirm")
        d = rt.decide("HaloGrowth", other(169, plus=False))
        self.assertEqual((d.intent, d.reason), ("halo_back", "halo_growth_done"))
        # Back on the Hub: this week's 成長 is done.
        self.assertEqual(rt.decide("HaloHub", hub(affection_value=10)).intent, "halo_team_info")

    def test_a_missed_untapped_colour_keeps_its_earlier_points(self):
        # live 2026-11-15 S3: Vi 「1」 read once, then OCR missed it after the Vo cap tap
        rt, _ = self.visit()
        self.assertEqual(rt.decide("HaloGrowth", status({"vo": 74, "vi": 1}, {"vo": [478, 520]})).intent,
                         "halo_growth_cap_vo")
        later = status({"vo": 64, "vi": 1}, {"vo": [478, 530]})
        later["growth"]["points"]["vi"] = None
        self.assertEqual(rt.decide("HaloGrowth", later).intent, "halo_growth_stat_vo")
        tapped = status({"vo": 4}, {"vo": [528, 530]})
        tapped["growth"]["points"]["vo"] = None                     # the tapped colour itself must read again
        self.assertEqual(rt.decide("HaloGrowth", tapped).action, "wait")

    def test_affection_reached_and_nothing_to_learn_goes_back(self):
        rt, d = self.visit(affection=90)                # 親愛度 90: past 施設効率相談 / + (50 / 80)
        self.assertEqual(d.detail["plan"]["affection"], 0)
        d = rt.decide("HaloGrowth", status({"vo": 5}, {}))
        self.assertEqual((d.intent, d.reason), ("halo_back", "halo_growth_done"))

    def test_a_tap_without_effect_stops(self):
        rt, _ = self.visit()
        read = status({"vo": 74}, {"vo": [478, 520]})
        self.assertEqual(rt.decide("HaloGrowth", read).intent, "halo_growth_cap_vo")
        for _ in range(rt.HALO_SETTLE_WAITS):
            self.assertEqual(rt.decide("HaloGrowth", read).action, "wait")
        self.assertEqual(rt.decide("HaloGrowth", read).reason, "halo_growth_tap_no_effect")

    def test_unread_page_waits_then_stops_and_affection_needs_unity(self):
        rt, _ = self.visit()
        broken = status({"vo": 74}, {"vo": None})
        for _ in range(rt.HALO_SETTLE_WAITS):
            self.assertEqual(rt.decide("HaloGrowth", broken).action, "wait")
        self.assertEqual(rt.decide("HaloGrowth", broken).reason, "halo_growth_status_unread")
        rt, _ = self.visit()
        self.assertEqual(rt.decide("HaloGrowth", status({}, {})).intent, "halo_growth_tab_other")
        self.assertEqual(rt.decide("HaloGrowth", other(20)).intent, "halo_back")     # 団結力 short of 30
        # A visit the user opened (no plan) and a confirm Maa did not ask for stop.
        self.assertEqual(runtime().decide("HaloGrowth", status({}, {})).reason, "halo_growth_not_opened")
        rt, _ = self.visit()
        self.assertEqual(rt.decide("HaloGrowthConfirm", {}).reason, "halo_growth_confirm_unexpected")


class HubTests(unittest.TestCase):
    def test_season_one_produces_and_season_two_reads_team_info_once(self):
        rt = runtime()
        d = rt.decide("HaloHub", hub(season=1))
        self.assertEqual((d.intent, d.reason), ("halo_main", "halo_produce"))
        rt.memory.halo.growth_weeks.add((2, 6))          # this week's 成長 already visited (GrowthTests)
        rt.memory.halo.disbanded = True                  # DisbandTests
        self.assertEqual(rt.decide("HaloHub", hub()).intent, "halo_team_info")
        self.assertEqual(rt.decide("HaloTeamInfo", {"completion": 12}).intent, "halo_back")
        self.assertEqual(rt.memory.halo.completion, 12)
        self.assertEqual(rt.decide("HaloHub", hub()).intent, "halo_main")
        self.assertEqual(rt.decide("HaloHub", hub(stamina_pct=5)).intent, "halo_rest")
        self.assertEqual(rt.decide("HaloTeamInfo", {"completion": None}).reason, "halo_completion_unread")

    def test_dig_gives_up_with_a_long_press_fes_plays_the_final(self):
        rt = runtime()
        rt.memory.halo = nav.hp.HaloMemory(facility_done=True, facility_plus_done=True, completion=100)
        self.assertEqual(rt.decide("HaloHub", hub()).intent, "giveup_open")
        d = rt.decide("AbandonDialog", {"abandon_hold_hint": True, "abandon_button": True})
        self.assertEqual(d.intent, "giveup_hold")
        other = runtime()
        self.assertEqual(other.decide("AbandonDialog", {"abandon_hold_hint": True, "abandon_button": True}).reason,
                         "abandon_dialog_not_automated")       # a dialog this run did not open

    def test_hub_button_must_read(self):
        self.assertEqual(runtime().decide("HaloHub", hub(season=1, hub_button=None)).reason, "halo_hub_button_unread")


def team(*present):
    return {"leave_buttons": [i in present for i in range(1, 5)]}


class DisbandTests(unittest.TestCase):
    """S2 (live 2026-11-15): チーム情報 → チーム編集 → 離脱させる → チーム離脱確認 「離脱させる」 per recruit, then ←."""
    def opened(self):
        rt = runtime()
        rt.decide("HaloHub", hub(season=1))
        rt.memory.halo.growth_weeks.add((2, 6))
        self.assertEqual(rt.decide("HaloHub", hub()).intent, "halo_team_info")
        self.assertEqual(rt.decide("HaloTeamInfo", {"completion": 20}).intent, "halo_team_edit")
        return rt

    def test_every_recruit_leaves_one_at_a_time_then_back(self):
        rt = self.opened()
        self.assertEqual(rt.decide("HaloTeamEdit", team(1, 2, 4)).intent, "halo_leave_1")
        q = {"leave_question": True, "leave_name": "x"}
        self.assertEqual(rt.decide("HaloLeaveConfirm", q).intent, "halo_leave_ok")
        self.assertEqual(rt.decide("HaloLeaveConfirm", q).reason, "halo_leave_confirm_unexpected")
        rt = self.opened()
        rt.decide("HaloTeamEdit", team(1, 2, 4))
        rt.decide("HaloLeaveConfirm", q)
        self.assertEqual(rt.decide("HaloTeamEdit", team(1, 2, 4)).action, "wait")     # the slot still shows it
        self.assertEqual(rt.decide("HaloTeamEdit", team(1, 3)).intent, "halo_leave_1")  # slots shift left
        rt.decide("HaloLeaveConfirm", q)
        self.assertEqual(rt.decide("HaloTeamEdit", team(1)).intent, "halo_leave_1")
        rt.decide("HaloLeaveConfirm", q)
        d = rt.decide("HaloTeamEdit", team())
        self.assertEqual((d.intent, d.reason), ("halo_back", "halo_disbanded"))
        self.assertTrue(rt.memory.halo.disbanded)
        self.assertEqual(rt.decide("HaloHub", hub()).intent, "halo_main")
        self.assertIn("disbanded", nav.hp.SAVED_FACTS)

    def test_a_button_that_stays_stops(self):
        rt = self.opened()
        rt.decide("HaloTeamEdit", team(1))
        rt.decide("HaloLeaveConfirm", {"leave_question": True})
        for _ in range(rt.HALO_LEAVE_WAITS):
            self.assertEqual(rt.decide("HaloTeamEdit", team(1)).action, "wait")
        self.assertEqual(rt.decide("HaloTeamEdit", team(1)).reason, "halo_leave_no_effect")

    def test_unread_or_unopened_pages_stop(self):
        rt = self.opened()
        self.assertEqual(rt.decide("HaloTeamEdit", {"leave_buttons": None}).reason, "halo_team_edit_unread")
        rt = self.opened()
        rt.decide("HaloTeamEdit", team(2))
        self.assertEqual(rt.decide("HaloLeaveConfirm", {"leave_question": False}).reason, "halo_leave_confirm_unread")
        self.assertEqual(runtime().decide("HaloTeamEdit", team(1)).reason, "halo_team_edit_not_opened")
        self.assertEqual(runtime().decide("HaloLeaveConfirm", {"leave_question": True}).reason,
                         "halo_team_edit_not_opened")
        rt = self.opened()
        self.assertEqual(rt.decide("HaloTeamInfo", {"completion": 20}).reason, "halo_team_edit_no_effect")

    def test_season_one_never_disbands(self):
        rt = runtime()
        rt.decide("HaloHub", hub(season=1))
        self.assertEqual(rt.decide("HaloTeamInfo", {"completion": 5}).intent, "halo_back")


def rooms(selected=None):
    kinds = {"lesson_vo": ["vo"], "lesson_da": ["me", "rest"], "lesson_vi": [], "work_radio": ["?"],
             "work_talk": ["work"], "work_magazine": []}
    return [{"id": rid, "advice": kinds[rid], "selected": rid == selected} for rid in nav.ROOM_SLOTS]


class ScheduleTests(unittest.TestCase):
    def test_season_one_lesson_room_then_decide(self):
        rt = runtime()
        rt.decide("HaloHub", hub(season=1))
        d = rt.decide("HaloLesson", {"season": 1, "advice": dict(NONE), "rooms": rooms()})
        self.assertEqual((d.intent, d.detail["room"]), ("halo_room_2", "lesson_da"))
        d = rt.decide("HaloLesson", {"season": 1, "advice": dict(NONE), "rooms": rooms("lesson_da")})
        self.assertEqual(d.intent, "halo_decide")
        # still on the page after 決定: the animation is waited for, then the run stops
        waits = [rt.decide("HaloLesson", {"season": 1, "rooms": rooms("lesson_da")}) for _ in range(nav.WingNavRuntime.HALO_DECIDE_WAITS)]
        self.assertTrue(all(w.action == "wait" for w in waits[:-1]))
        self.assertEqual(rt.decide("HaloLesson", {"season": 1, "rooms": rooms("lesson_da")}).reason,
                         "halo_decide_no_effect")

    def test_room_tap_must_select(self):
        rt = runtime()
        rt.decide("HaloHub", hub(season=1))
        for _ in range(nav.WingNavRuntime.HALO_ROOM_TAPS):
            self.assertEqual(rt.decide("HaloLesson", {"season": 1, "rooms": rooms()}).intent, "halo_room_2")
        self.assertEqual(rt.decide("HaloLesson", {"season": 1, "rooms": rooms()}).reason, "halo_room_not_selected")


class Carousel:
    """A circular carousel of card readings; › / ‹ move it, 決定 records the card."""
    def __init__(self, tab, cards, start=0):
        self.screen = {"practice": "HaloPractice", "audition": "HaloAudition"}[tab]
        self.cards, self.pos, self.decided, self.taps = cards, start, None, 0

    def read(self):
        return {"season": 2, "stamina_pct": 60, "advice": dict(NONE), **self.cards[self.pos]}

    def run(self, rt, limit=120):
        for _ in range(limit):
            d = decide(rt, self.screen, self.read())
            if d.action != "click" or d.intent not in ("halo_card_next", "halo_card_prev"):
                return d
            self.taps += 1
            self.pos = (self.pos + (1 if d.intent == "halo_card_next" else -1)) % len(self.cards)
        raise AssertionError("carousel did not finish")


def practice(name, rate, note=None):
    return {"card_name": name, "card_rate": rate, "card_note": note}


def audition(name, rate, fans):
    return {"card_name": name, "card_rate": rate, "card_fans": fans}


class CarouselTests(unittest.TestCase):
    def start(self, route="dig", tab="practice"):
        rt = runtime(route)
        rt.decide("HaloHub", hub())
        rt.memory.halo.disbanded = True      # DisbandTests
        rt.decide("HaloHub", hub())          # S2: チーム情報 first
        rt.decide("HaloTeamInfo", {"completion": 0})
        self.assertEqual(rt.decide("HaloHub", hub()).intent, "halo_main")
        if tab == "practice":
            self.assertEqual(rt.decide("HaloLesson", {"season": 2, "rooms": rooms()}).intent, "halo_tab_practice")
        return rt

    def test_practice_survey_wraps_then_takes_the_shorter_way_to_the_facility_card(self):
        rt = self.start()
        cards = [practice("歌唱力強化練習", 100), practice("安定感強化練習+", 60, "short"),
                 practice("みんなで休憩", 43, "short"), practice("ビジュアル効率相談", 77, "short"),
                 practice("施設効率相談", 100, "best")]
        c = Carousel("practice", cards)
        d = c.run(rt)
        self.assertEqual((d.intent, d.detail["card"]["name"]), ("halo_decide", "施設効率相談"))
        self.assertEqual((c.pos, c.taps), (4, 6))          # five › to read all and wrap, one ‹ back
        self.assertTrue(rt.memory.halo.facility_done)
        self.assertFalse(rt.memory.halo.facility_plus_done)

    def test_nothing_wanted_moves_to_the_next_tab(self):
        rt = self.start()
        c = Carousel("practice", [practice("歌唱力強化練習", 100), practice("施設効率相談", 70, "short")])
        d = c.run(rt)
        self.assertEqual((d.intent, d.reason), ("halo_tab_audition", "halo_nothing_on_tab"))
        a = Carousel("audition", [audition("ビギナーズ！", 100, 2000), audition("アイドル☆ウォー！", 100, 8000),
                                  audition("出場条件", 100, 145000), audition("フェイバリット", 57, 14000)])
        d = a.run(rt)
        self.assertEqual((d.intent, d.detail["card"]["fans"]), ("halo_decide", 8000))
        self.assertEqual(rt.memory.halo_state["week"]["tried"], ["practice"])

    def test_a_dropped_arrow_is_tapped_once_more(self):
        # H2d live 2026-11-06: › on card 10 of 13 left 「Rhythmic Diva」 and page dot 9 on the next frames.
        names = [("歌唱力強化練習", 100, None), ("安定感強化練習+", 60, "short"), ("みんなで休憩", 43, "short"),
                 ("ビジュアル効率相談", 77, "short"), ("施設効率相談", 100, "best")]
        cards = [{**practice(*c), "dots": {"count": 5, "selected": i}} for i, c in enumerate(names)]
        rt = self.start()
        c, dropped, reasons = Carousel("practice", cards), {2}, []
        for _ in range(40):
            d = decide(rt, "HaloPractice", c.read())
            reasons.append(d.reason)
            if d.intent not in ("halo_card_next", "halo_card_prev"):
                break
            if d.intent == "halo_card_next" and c.pos in dropped:
                dropped.discard(c.pos)          # this tap is lost
                continue
            c.pos = (c.pos + (1 if d.intent == "halo_card_next" else -1)) % len(cards)
        self.assertEqual((d.intent, d.detail["card"]["name"]), ("halo_decide", "施設効率相談"))
        self.assertEqual(reasons.count("halo_carousel_retap"), 1)
        rt = self.start()
        c = Carousel("practice", cards)
        self.assertEqual(decide(rt, "HaloPractice", c.read()).reason, "halo_survey")
        self.assertEqual(decide(rt, "HaloPractice", c.read()).reason, "halo_carousel_retap")
        self.assertEqual(decide(rt, "HaloPractice", c.read()).reason, "halo_carousel_no_effect")

    def test_frame_after_an_arrow_is_read_once_when_it_shows_the_next_card(self):
        # H2d 2026-11-06: two reads a card cost ~10 s; the frame after an arrow is >= 2 s old.
        names = [("歌唱力強化練習", 100, None), ("安定感強化練習+", 60, "short"), ("みんなで休憩", 43, "short"),
                 ("施設効率相談", 100, "best"), ("ビジュアル効率相談", 77, "short")]
        cards = [{**practice(*c), "dots": {"count": 5, "selected": i}} for i, c in enumerate(names)]
        rt = self.start()
        c, actions = Carousel("practice", cards), []
        for _ in range(30):
            d = rt.decide("HaloPractice", c.read())
            actions.append(d.action)
            if d.action == "wait":
                continue
            if d.intent not in ("halo_card_next", "halo_card_prev"):
                break
            c.pos = (c.pos + (1 if d.intent == "halo_card_next" else -1)) % len(cards)
        self.assertEqual((d.intent, d.detail["card"]["name"]), ("halo_decide", "施設効率相談"))
        self.assertEqual(actions.count("wait"), 1)            # only the first card is read twice
        rt = self.start()
        c = Carousel("practice", cards)
        decide(rt, "HaloPractice", c.read())                   # › from 歌唱力
        sliding = {**practice("安定感強化練習+", None), "dots": {"count": 5, "selected": 1}}
        self.assertEqual(rt.decide("HaloPractice", {"season": 2, **sliding}).reason, "halo_card_settling")
        skipped = {**practice("みんなで休憩", 43, "short"), "dots": {"count": 5, "selected": 2}}
        self.assertEqual(rt.decide("HaloPractice", {"season": 2, **skipped}).reason, "halo_card_settling")

    def test_alike_cards_are_told_apart_by_the_page_dot(self):
        # H2d live 2026-11-06 S3: two locked 出場条件 cards in a row with the same +185000 bubble; the dot moved 5 -> 6.
        rt = self.start()
        rt.decide("HaloPractice", {"season": 2, **practice("歌唱力強化練習", 100)})
        cards = [{**audition(n, r, f), "dots": {"count": 4, "selected": i}} for i, (n, r, f) in enumerate(
            [("ビギナーズ！", 100, 2000), ("出場条件", None, 185000), ("出場条件", None, 185000), ("ヒット", 100, 6000)])]
        a = Carousel("audition", cards)
        a.read = lambda: {"season": 3, "stamina_pct": 60, "advice": dict(NONE), **a.cards[a.pos]}
        rt.memory.halo_state["week"]["tried"].append("practice")
        d = a.run(rt)
        self.assertEqual((d.intent, d.detail["card"]["fans"], d.detail["cards"]), ("halo_decide", 6000, 4))

    def test_unsettled_reading_and_lost_card_stop(self):
        rt = self.start()
        names = ("歌唱力強化練習", "安定感強化練習", "表現力強化練習", "みんなで休憩", "施設効率相談", "総合能力強化練習",
                 "ビジュアル効率相談", "安定感強化練習+")
        moving = [rt.decide("HaloPractice", {"season": 2, **practice(name, 100)}) for name in names]
        self.assertTrue(all(d.action == "wait" for d in moving[:nav.WingNavRuntime.HALO_SETTLE_WAITS]))
        self.assertEqual(moving[-1].reason, "halo_reading_unsettled")
        rt = self.start()
        cards = [practice("歌唱力強化練習", 100), practice("施設効率相談", 100, "best"), practice("みんなで休憩", 40)]
        c = Carousel("practice", cards)
        c.cards = cards + []
        decide(rt, "HaloPractice", c.read())            # survey starts at 歌唱力
        for card in cards[1:] + cards[:1]:
            decide(rt, "HaloPractice", {"season": 2, **card})
        d = decide(rt, "HaloPractice", {"season": 2, **practice("表現力強化練習", 100)})
        self.assertEqual(d.reason, "halo_carousel_lost")


class LiveAndPagesTests(unittest.TestCase):
    def test_live_select_then_confirm(self):
        rt = runtime()
        self.assertEqual(rt.decide("HaloLiveConfirm", {}).reason, "halo_live_confirm_not_opened")
        rt.decide("HaloHub", hub(hub_button="first_live", season=2))
        d = decide(rt, "HaloLiveSelect", {"live_rates": [79, 79, 78], "live_selected": 0})
        self.assertEqual(d.intent, "halo_live_challenge")
        self.assertEqual(rt.decide("HaloLiveSelect", {"live_rates": [79, 79, 78], "live_selected": 0}).action, "wait")
        self.assertEqual(rt.decide("HaloLiveConfirm", {}).intent, "halo_live_challenge")
        rt = runtime()
        d = decide(rt, "HaloLiveSelect", {"live_rates": [62, 97, 98], "live_selected": 0})
        self.assertEqual(d.intent, "halo_live_card_3")
        self.assertEqual(decide(rt, "HaloLiveSelect", {"live_rates": [62, 97, 98], "live_selected": 2}).intent,
                         "halo_live_challenge")

    def test_plain_pages(self):
        rt = runtime()
        for screen, intent in (("HaloReport", "halo_report_next"), ("HaloSeasonEnd", "commu_ok"),
                               ("HaloGoals", "halo_goals_close"), ("HaloRecruitConfirm", "halo_recruit_ok"),
                               ("HaloTeamFull", "halo_team_full_close")):
            self.assertEqual(rt.decide(screen, {}).intent, intent, screen)
        self.assertEqual(rt.decide("HaloRecruit", {}).reason, "halo_recruit_unread")

    def test_survey_and_growth_taps_may_repeat(self):
        # H2d live 2026-11-06: the 4th 次 of a 4-card チーム合同練習 survey was refused (repeat_without_progress).
        from agent.core import halo_growth
        rt = runtime()
        rt.begin()
        self.assertGreater(nav.REPEAT_LIMITS["halo_card_next"], rt.HALO_SURVEY_MAX)
        self.assertEqual(nav.REPEAT_LIMITS["halo_growth_cap_vo"], halo_growth.MAX_TAPS)
        tap = nav._click("halo_card_next", "halo_survey")
        self.assertEqual([rt.act("HaloPractice", tap)[0] for _ in range(10)], [True] * 10)
        rest = nav._click("halo_rest", "test")
        self.assertEqual([rt.act("HaloHub", rest)[1] for _ in range(nav.MAX_SAME_INTENT_IN_A_ROW + 1)][-1],
                         "repeat_without_progress")            # other Halo taps keep the default limit

    def test_resume_dialog_of_a_halo_produce(self):
        # H2d 2026-11-06: a host restart resumes Maa's own Halo Produce through プロデュース再開; say "Halo" has no
        # difficulty, the idol/card must still match, and W.I.N.G. stays W.I.N.G.-only.
        from agent.core import wing_resume as wr
        seen = wr.parse_scenario([('say"Halo"編', (0, 0, 1, 1)), ("シーズン1/残り2週", (0, 0, 1, 1))])
        self.assertEqual(seen, {"scenario": "halo", "difficulty": None, "season": 1, "weeks_left": 2})
        dialog = {"idol": "黛冬優子", "title": "誘爆ハートビート", **seen}
        ok = wr.compare(dialog, p_idol="黛冬優子", p_title="誘爆ハートビート", scenario="halo")
        self.assertEqual(ok["verdict"], "match")
        self.assertEqual(wr.compare(dialog, p_idol="黛冬優子", p_title="誘爆ハートビート")["verdict"], "mismatch")
        self.assertEqual(wr.compare(dialog, p_idol="櫻木真乃", p_title="誘爆ハートビート", scenario="halo")["verdict"],
                         "mismatch")
        self.assertEqual(wr.compare(dict(dialog, difficulty="middle"), p_idol="黛冬優子", p_title="誘爆ハートビート",
                                    scenario="halo")["verdict"], "mismatch")
        self.assertIn("HaloHub", nav.RULES["ResumeDialog"].expect)

    def test_recruit_picks_a_candidate_not_in_the_team(self):
        # H2d live 2026-11-05: the first candidate already joined (勧誘済) -> its tile is skipped, the next one is
        # tapped once, the header must change, then チームに勧誘 once.
        rt = runtime("dig")
        free = {"recruited": False, "recruit_name": "八宮めぐる", "recruit_tiles": [True, False, False, False, False]}
        self.assertEqual(rt.decide("HaloRecruit", free).intent, "halo_recruit_join")
        self.assertEqual(rt.decide("HaloRecruit", free).reason, "halo_recruit_join_pending")      # never a second 勧誘
        rt = runtime("dig")
        joined = {"recruited": True, "recruit_name": "風野灯織", "recruit_tiles": [True, False, False, False, False]}
        d = rt.decide("HaloRecruit", joined)
        self.assertEqual((d.intent, d.detail["tile"]), ("halo_recruit_tile_2", 2))
        self.assertEqual(rt.decide("HaloRecruit", joined).reason, "halo_recruit_tile_pending")  # header not changed
        self.assertEqual(rt.decide("HaloRecruit", dict(free)).intent, "halo_recruit_join")
        rt.decide("HaloRecruitConfirm", {})
        self.assertNotIn("recruit", rt.memory.halo_state)                                       # next page starts fresh
        # live 2026-11-15: a fading 勧誘 frame after OK (new member 勧誘済) is waited out, never a tile tap
        after = dict(joined, recruit_tiles=[True, True, False, False, False])
        self.assertEqual(rt.decide("HaloRecruit", after).reason, "halo_recruit_ok_pending")
        for _ in range(rt.HALO_RECRUIT_WAITS):
            d = rt.decide("HaloRecruit", after)
        self.assertEqual(d.reason, "halo_recruit_page_stayed")
        rt.decide("HaloHub", hub())
        self.assertNotIn("recruit_ok", rt.memory.halo_state)
        rt = runtime("dig")
        for _ in range(rt.HALO_RECRUIT_WAITS + 2):          # the tap, then frames whose header never changes
            d = rt.decide("HaloRecruit", joined)
        self.assertEqual(d.reason, "halo_recruit_tile_no_effect")
        rt = runtime("dig")
        self.assertEqual(rt.decide("HaloRecruit", dict(joined, recruit_tiles=[True] * 5)).reason, "halo_recruit_no_candidate")
        rt = runtime("dig")
        self.assertEqual(rt.decide("HaloRecruit", {"recruited": True, "recruit_name": "x"}).reason, "halo_recruit_unread")
        self.assertEqual(rt.decide("HaloGrowth", {}).reason, "halo_growth_not_opened")     # only Maa's own visit
        self.assertEqual(rt.decide("FesBirthSkillDialog", {}).reason, "fes_birth_without_evaluation")
        rt.memory.wing_won = True
        self.assertEqual(rt.decide("FesBirthSkillDialog", {}).intent, "fes_birth_ok")
        # H2d live 2026-11-06: the give-up's フェスアイドル誕生 led to the skill dialog, which the tap must accept.
        self.assertIn("FesBirthSkillDialog", nav.RULES["FesBirth"].expect)


@unittest.skipUnless(hs.DEFAULT_OUT.is_file() and hs.DEFAULT_OCR.is_file(), "private Halo samples (H1) not present")
class H1ReplayTests(unittest.TestCase):
    """Real H1 frames: the plain pages' taps land on the user's own tapped cell, and two チーム合同練習 visits drive the
    circular carousel (frames indexed by their page dots)."""
    @classmethod
    def setUpClass(cls):
        from agent.core import wing_screen as wsc
        from tools.wing_demo_replay import load_rgb
        cls.wsc, cls.load_rgb = wsc, staticmethod(load_rgb)
        cls.manifest = json.loads(hs.DEFAULT_OUT.read_text(encoding="utf-8"))
        cls.ocr = hs.load_ocr()
        cls.classifier = wsc.ScreenClassifier(wsc.load_nodes(), wsc.BoxBackend(), {"SC.Wing.State": lambda p, f: True})
        cls.rows = sorted((p, label) for label, ps in cls.manifest.items() for p in ps)

    def read(self, path):
        frame = self.wsc.Frame(self.ocr.get(path, ()), lambda p=Path(path): self.load_rgb(p), key=path)
        screen = self.classifier.classify(frame).screen
        return screen, self.wsc.read_screen(screen, self.classifier.evaluator, frame)

    def test_plain_pages_tap_the_users_cell(self):
        checked = 0
        for path, label in self.rows:
            if label not in ("HaloReport", "HaloSeasonEnd", "HaloGoals", "HaloRecruitConfirm", "HaloTeamFull",
                             "HaloTeamInfo", "HaloInheritance"):
                continue
            cell = re.search(r"-r(\d\d)c(\d\d)-", Path(path).name)
            if cell is None:
                continue                    # a live failure frame (no user tap): read by other tests
            screen, read = self.read(path)
            d = runtime().decide(screen, read)
            x, y, w, h = d.target
            cx, cy = int(cell.group(2)) * 40 + 20, int(cell.group(1)) * 40 + 20
            self.assertTrue(x <= cx <= x + w and y <= cy <= y + h, (path, d.intent))
            checked += 1
        self.assertGreaterEqual(checked, 15)

    def test_affection_already_learned_is_not_tapped(self):
        # H2d live 2026-11-06: a week after Maa bought 親愛度+10 the card shows 「習得済み」 (greyed, no +); the white
        # pill used to read as a lit + and the tap had no effect.
        frames = [p for p in self.manifest.get("HaloGrowth", []) if Path(p).name == "halo-growth-affection-learned.png"]
        if not frames:
            self.skipTest("H2d frame not present")
        screen, read = self.read(frames[0])
        g = read.get("growth") or {}
        self.assertEqual((screen, g.get("tab"), g.get("affection_learned"), g.get("affection_plus")),
                         ("HaloGrowth", "other", True, False))
        self.assertEqual(g.get("points", {}).get("unity"), 54)

    def test_recruit_reads_the_joined_badge(self):
        # H1: the user's チームに勧誘 taps (r12c24) were on candidates not yet in the team; H2d live: the first
        # candidate already joined (勧誘済) -> stop before the greyed button.
        frames = self.manifest["HaloRecruit"]
        joins = [p for p in frames if "-r12c24-" in Path(p).name]
        self.assertGreaterEqual(len(joins), 3)
        for path in joins:
            screen, read = self.read(path)
            self.assertEqual((screen, read.get("recruited")), ("HaloRecruit", False), Path(path).name)
            d = runtime().decide(screen, read)
            self.assertEqual(d.intent, "halo_recruit_join")
            self.assert_cell(d, path)
        joined = [p for p in frames if Path(p).name == "halo-recruit-joined.png"]
        if joined:
            screen, read = self.read(joined[0])
            self.assertEqual((screen, read.get("recruited"), read.get("recruit_tiles")),
                             ("HaloRecruit", True, [True, False, False, False, False]))
            self.assertIn("灯織", read.get("recruit_name") or "")
            d = runtime().decide(screen, read)
            self.assertEqual(d.intent, "halo_recruit_tile_2")
            x, y, w, h = d.target
            self.assertTrue(602 <= x and x + w <= 742 and 553 <= y and y + h <= 640, d.target)   # inside tile 2
        for path in [p for p in frames if re.search(r"-r15c(16|27)-", Path(p).name)]:
            # the H1 user's own candidate taps land inside the tile Maa would tap for that column
            cell = re.search(r"-r15c(\d\d)-", Path(path).name)
            n = (int(cell.group(1)) * 40 + 20 - 450) // 150 + 1
            x, y, w, h = nav.TARGETS[f"halo_recruit_tile_{n}"][0]
            self.assertTrue(x <= int(cell.group(1)) * 40 + 20 <= x + w and y <= 620 <= y + h, (Path(path).name, n))

    def assert_cell(self, d, path):
        cell = re.search(r"-r(\d\d)c(\d\d)-", Path(path).name)
        x, y, w, h = d.target
        cx, cy = int(cell.group(2)) * 40 + 20, int(cell.group(1)) * 40 + 20
        self.assertTrue(x <= cx <= x + w and y <= cy <= y + h, (Path(path).name, d.intent))

    def test_start_pages_tap_the_users_cell(self):
        # H1 start (the user's taps) and the weekly Auto book pages borrowed for 設定履歴 / プリセット.
        select, = self.manifest["ProduceSelect"]
        screen, read = self.read(select)
        d = starter().decide(screen, read)
        self.assertEqual(d.intent, "halo_scenario_next")
        self.assert_cell(d, select)
        empty, filled = sorted(self.manifest["BookSelect"])
        rt = starter()
        d = rt.decide(*self.read(empty))
        self.assertEqual(d.intent, "halo_book_auto")
        self.assert_cell(d, empty)
        rt.decide("Loading", {})
        d = rt.decide(*self.read(filled))
        self.assertEqual((d.intent, d.detail["stats"]), ("halo_book_decide", {"vo": 150, "da": 50, "vi": 50, "me": 40}))
        self.assert_cell(d, filled)
        if "BookHistory" in self.manifest:
            history, = self.manifest["BookHistory"]
            rt = starter(book="history")
            rt.memory.halo_setup["history"] = True
            d = rt.decide(*self.read(history))
            self.assertEqual(d.intent, "halo_history_call")
            self.assert_cell(d, history)
        if "BookPreset" in self.manifest:
            preset, = self.manifest["BookPreset"]
            rt = starter(book="preset1")
            rt.memory.halo_setup["preset"] = True
            self.assertEqual(rt.decide(*self.read(preset)).intent, "halo_preset_set_1")

    def test_growth_visits_tap_the_users_cells(self):
        # S2W1 (Vo / Da / Vi >>, 習得する, 成長確認) and S2W8 その他 (親愛度+10, 習得する) on H1.
        frames = {Path(p).name[8:21]: p for p in self.manifest["HaloGrowth"] + self.manifest["HaloGrowthConfirm"]}
        rt = runtime()
        rt.decide("HaloHub", hub(affection_value=10))
        for stamp, intent in (("T100921707228", "halo_growth_stat_vo"), ("T100926628691", "halo_growth_stat_da"),
                              ("T100931712501", "halo_growth_stat_vi")):
            path = frames[stamp]
            d = rt.decide(*self.read(path))
            self.assertEqual(d.intent, intent, stamp)
            self.assert_cell(d, path)
        # H1 also filled メンタル (r14c14) before 習得する; Maa leaves 集中力 alone and learns.
        d = rt.decide(*self.read(frames["T100952113206"]))
        self.assertEqual(d.intent, "halo_growth_learn")
        self.assert_cell(d, frames["T100952113206"])
        d = rt.decide(*self.read(frames["T101016924167"]))
        self.assertEqual(d.intent, "halo_growth_confirm")
        self.assert_cell(d, frames["T101016924167"])
        rt = runtime()
        rt.decide("HaloHub", hub(affection_value=10))
        st = rt.memory.halo_state["growth"]
        st.update(stage="affection", tab_other=True)
        d = rt.decide(*self.read(frames["T102836914740"]))
        self.assertEqual(d.intent, "halo_growth_affection")
        self.assert_cell(d, frames["T102836914740"])
        d = rt.decide(*self.read(frames["T102919336605"]))
        self.assertEqual(d.intent, "halo_growth_learn")
        self.assert_cell(d, frames["T102919336605"])

    def visit(self, start, end):
        frames = {}
        for path, label in self.rows:
            stamp = Path(path).name[9:17]
            if label == "HaloPractice" and start <= stamp <= end:
                _, read = self.read(path)
                frames[read["dots"]["selected"]] = read
        return frames

    def drive(self, frames, route="dig"):
        n = len(frames)
        self.assertEqual(sorted(frames), list(range(n)))
        rt = runtime(route)
        rt.decide("HaloHub", hub(season=frames[0]["season"]))
        pos, taps = 0, 0
        for _ in range(4 * n):
            d = decide(rt, "HaloPractice", frames[pos])
            if d.intent not in ("halo_card_next", "halo_card_prev"):
                return rt, d, pos, taps
            taps += 1
            pos = (pos + (1 if d.intent == "halo_card_next" else -1)) % n
        self.fail("carousel did not finish")

    def test_all_short_visit_moves_on(self):
        # 2026-11-01 camp: 歌唱力 58 / 安定感+ 30 / 表現力+ 60 / みんなで休憩 86 / ビジュアル効率相談 62, all 不足
        rt, d, pos, taps = self.drive(self.visit("10363647", "10384797"))
        self.assertEqual((d.intent, taps, pos), ("halo_tab_audition", 5, 0))

    def test_facility_card_is_reached_and_decided(self):
        # S2 visit 10:56-10:57 (6 cards, 施設効率相談 70 % 不足) with the 11:09 frame of the same card at 100 % (ベスト代用)
        frames = self.visit("10562032", "10573671")
        later = {Path(p).name[9:17]: p for p, label in self.rows if label == "HaloPractice"}["11095448"]
        frames[5] = self.read(later)[1]
        rt, d, pos, taps = self.drive(frames)
        self.assertEqual((d.intent, pos, taps), ("halo_decide", 5, 7))
        self.assertIn("施設", d.detail["card"]["name"])
        self.assertTrue(rt.memory.halo.facility_done)


if __name__ == "__main__":
    unittest.main()
