"""W.I.N.G. integration: scorer + navigation + audition/live hooks + auto team, offline, zero input.

Covers the default wiring (produce_config -> RunMemory -> wing_policy score mode), the audition survey and hook
bookkeeping, the Live play intents (guarded, refused), the auto-team confirmation flow (Python preflight
and the UI proposal helpers), and a replay smoke test over the private demo recording."""
from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

from agent.core import produce_config as pc
from agent.core import wing_nav as nav

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "dev_assets" / "private" / "recordings" / "wing-demo-20261005-0058-01" / "index.jsonl"
OCR = ROOT / "dev_assets" / "private" / "wing-demo-analysis" / "ocr.jsonl"
# Empty rooms (supports read as 0): no optimistic support bound beats the planned audition, so no preview check tap
# (tests/test_wing_room_preview.py covers the taps).
ROOMS = [{"id": "lesson_vo", "kind": "lesson", "attribute": "Vo", "label_ok": True, "selected": False, "level": 3,
          "supports": 0},
         {"id": "work_talk", "kind": "work", "attribute": None, "label_ok": True, "selected": False, "level": 2,
          "supports": 0}]
WEEK = {"season": 4, "weeks_left": 6, "stats": {"Vo": 1229, "Da": 296, "Vi": 247, "Me": 526}, "fans": 209_933,
        "sp": 38, "stamina_pct": 76, "rooms": ROOMS, "selected_room": None}
PAGE = {"season": 4, "weeks_left": 6, "reward": 100_000, "difficulty": 19, "required_rank": "B",
        "recommended": {"Vo": 450, "Me": 450}, "trend": {"Vo": 1, "Da": 2, "Vi": 3}}


def runtime(config=None, **hooks) -> nav.WingNavRuntime:
    config = config or pc.ProduceConfig.from_dict(pc.default_config("true_end"))
    rt = nav.WingNavRuntime(nav.RunMemory.from_produce_config(config), hooks=hooks)
    rt.begin()
    return rt


def live_state(turn=1, confidence_card="c2"):
    return {"turn": turn, "stats": {"Vo": 1300, "Da": 300, "Vi": 250, "Me": 550},
            "judges": [{"attribute": "Vo", "trend_rank": 1, "remaining": 1.0},
                       {"attribute": "Da", "trend_rank": 2, "remaining": 1.0},
                       {"attribute": "Vi", "trend_rank": 3, "remaining": 1.0}],
            "options": [{"id": "c1", "attribute": "Da", "multiplier": 1.5},
                        {"id": confidence_card, "attribute": "Vo", "multiplier": 2.5},
                        {"id": "c3", "attribute": "Vi", "multiplier": 1.5}],
            "memory_gauge": 0, "mental": 500, "mental_max": 550}


def play_hook(action="skill", option_id="c2", target="Vo", confidence="ok"):
    return lambda r, m: {"action": "play", "reason": "own_policy",
                         "decision": {"action": action, "option_id": option_id, "target": target,
                                      "confidence": confidence}}


class DefaultWiringTests(unittest.TestCase):
    def test_scorer_is_the_default_and_rules_stay_selectable(self):
        config = pc.ProduceConfig.from_dict(pc.default_config("true_end"))
        self.assertEqual((config.manual["strategy"], config.manual["live_mode"]), ("vo_score", "policy"))
        memory = nav.RunMemory.from_produce_config(config)
        self.assertEqual((memory.strategy, memory.goal, memory.live_mode, memory.target_unit),
                         ("vo_score", "true_end", "policy", 1))
        self.assertEqual(pc.ProduceConfig.from_task_options({"goal": "true_end", "primary_stat": "Da"})
                         .manual["strategy"], "da_score")
        data = pc.default_config("true_end")
        data["manual"]["strategy"] = "vo_highest"
        self.assertEqual(nav.RunMemory.from_produce_config(pc.ProduceConfig.from_dict(data)).strategy, "vo_highest")
        with self.assertRaises(ValueError):
            nav.RunMemory.from_produce_config(pc.ProduceConfig.from_dict(pc.default_config()))   # official Auto

    def test_week_decision_uses_the_static_audition_table_without_survey(self):
        # 2026-10-09: the audition list is known in advance; the tab is opened only to enter the chosen audition.
        rt = runtime()
        decision = rt.decide("Schedule", WEEK)
        self.assertNotEqual(decision.reason, "survey_auditions")
        seen = rt.memory.auditions_seen
        self.assertEqual((seen["source"], seen["key"], seen["rank_floor"]), ("table", [4, 6], "B"))
        self.assertEqual([p["reward"] for p in seen["pages"]], [15_000, 25_000, 50_000, 100_000])
        week = rt.memory.last_week_decision
        self.assertEqual((week["kind"], week["audition_id"]), ("audition", "s4_100k"))
        self.assertIn("table_fallback", json.dumps(week))
        self.assertTrue(decision.intent == "schedule_audition_tab" or decision.reason == "sp_learn_handover",
                        decision)
        # entering: the shown page is verified against the table (reward tier + read rank gate)
        rt.memory.pending, rt.memory.pending_audition = "audition", 100_000
        bad = rt.decide("AuditionTab", {**PAGE, "required_rank": "A"})
        self.assertEqual((bad.action, bad.reason), ("stop", "audition_table_mismatch"))
        self.assertEqual(rt.decide("AuditionTab", {**PAGE, "reward": 70_000}).reason, "audition_table_mismatch")
        self.assertEqual(rt.decide("AuditionTab", PAGE).intent, "schedule_decide")

    def test_survey_only_when_the_table_lacks_the_season(self):
        rt = runtime()
        survey = rt.decide("Schedule", {**WEEK, "season": 5})           # not a table season (e.g. 本戦)
        self.assertEqual(survey.reason, "survey_auditions")

    def test_locked_page_on_the_way_is_turned_not_entered(self):
        rt = runtime()
        rt.decide("Schedule", WEEK)
        rt.memory.pending, rt.memory.pending_audition = "audition", 50_000
        turn = rt.decide("AuditionLocked", {**PAGE, "required_rank": None})   # 100k locked, planned 50k
        self.assertEqual((turn.intent, turn.reason), ("audition_next", "audition_page_locked"))
        rt.memory.pending_audition = 100_000
        self.assertEqual(rt.decide("AuditionLocked", PAGE).reason, "audition_locked_overlay")

    def test_week_decision_runs_in_score_mode_after_the_survey(self):
        rt = runtime()
        rt.memory.audition_survey = True                                 # explicit survey mode (old flow)
        survey = rt.decide("Schedule", WEEK)
        self.assertEqual((survey.intent, survey.reason), ("schedule_audition_tab", "survey_auditions"))
        page = rt.decide("AuditionTab", PAGE)
        self.assertEqual(page.intent, "audition_next")
        done = rt.decide("AuditionTab", PAGE)          # same reward again: last page -> back to the lesson tab
        self.assertEqual((done.intent, done.reason), ("schedule_lesson_tab", "survey_done"))
        self.assertEqual(rt.memory.auditions_seen["rank_floor"], "B")
        decision = rt.decide("Schedule", WEEK)
        week = rt.memory.last_week_decision
        self.assertEqual(week["plan"]["mode"], "score")
        self.assertEqual(week["plan"]["live_mode"] if week["kind"] == "audition" else "policy", "policy")
        self.assertEqual((week["kind"], week["audition_id"]), ("audition", "s4_100k"))
        # SP before the audition: the panel reader is not wired -> the default sp_mode hands over to the user.
        self.assertTrue(decision.intent == "schedule_audition_tab" or decision.reason == "sp_learn_handover",
                        decision)
        self.assertNotIn("audition_list_unread", json.dumps(week))

    def test_rules_strategy_still_decides(self):
        data = pc.default_config("true_end")
        data["manual"]["strategy"] = "vo_highest"
        rt = runtime(pc.ProduceConfig.from_dict(data))
        rt.memory.auditions_seen = {"key": [4, 6], "pages": [], "trend": None, "done": True}
        rt.decide("Schedule", WEEK)
        self.assertNotEqual(rt.memory.last_week_decision["plan"].get("mode"), "score")


class AuditionBookkeepingTests(unittest.TestCase):
    def test_hook_updates_pending_pages_and_records_the_win(self):
        rt = runtime()
        rt.memory.pending, rt.memory.pending_audition = "audition", 100_000
        first = rt.decide("AuditionTab", {**PAGE, "reward": 50_000})
        self.assertEqual((first.intent, rt.memory.audition_pages), ("audition_next", 1))
        enter = rt.decide("AuditionTab", PAGE)
        self.assertEqual(enter.intent, "schedule_decide")
        self.assertEqual((rt.memory.pending, rt.memory.audition_pages), (None, 0))
        self.assertEqual(rt.memory.entered_audition, {"season": 4, "reward": 100_000})
        self.assertEqual(rt.decide("AuditionPass").action, "wait")
        self.assertEqual(rt.memory.wins, [(4, 100_000)])
        self.assertIsNone(rt.memory.entered_audition)

    def test_decline_stops_with_the_hook_reason(self):
        rt = runtime()
        rt.memory.pending, rt.memory.pending_audition = "audition", 100_000
        rt.memory.audition_pages = 5
        stop = rt.decide("AuditionTab", {**PAGE, "reward": 50_000})
        self.assertEqual((stop.action, stop.reason), ("stop", "audition_declined:planned_audition_not_found"))
        self.assertEqual(rt.decide("AuditionTab", {"reward": None}).action, "stop")


SIG = {1: [10] * 72, 2: [120] * 72, 3: [230] * 72}


def focus(phase, *, turn=1, trend=(3, 1, 2), selected=None, tooltip=None, left=(), stars=None):
    """Focus-policy readings as wing_screen.read_live produces them (all-Vo hand, judges Vo/Da/Vi)."""
    judges = [{"attribute": a, "trend_rank": t, "left": a in left, "our_dealt": 0.0}
              for a, t in zip(("Vo", "Da", "Vi"), trend)]
    state = {"turn": turn, "judges": judges, "options": [{"id": f"card{i}", "attribute": "Vo"} for i in (1, 2, 3)],
             "memory_gauge": 30, "mental": 500, "mental_max": 550, "our_stars": None, "rival_stars": None}
    if stars:
        state["our_stars"], state["rival_stars"] = stars
    return {"live_phase": phase, "auto": "OFF", "live_state": state, "selected_card": selected,
            "hand_sigs": [None if i == selected else SIG[i] for i in (1, 2, 3)], "tooltip": tooltip}


def tip(mult, appeal=1315, buff=80):
    return {"name": "x", "multiplier": mult, "attribute": "Vo", "appeal": appeal, "buff": buff}


class LivePlayIntentTests(unittest.TestCase):
    def test_focus_policy_inspects_switches_and_hits_trend1(self):
        self.assertFalse(nav.LIVE_INPUT_ENABLED)
        rt = runtime(live=play_hook(confidence="low"))     # the numeric scorer's confidence no longer hands over
        card = rt.decide("Live", focus("select_card"))
        self.assertEqual((card.action, card.intent, card.reason), ("click", "live_card_1", "live_inspect_unknown_card"))
        self.assertEqual(card.detail["expect"]["next_phase"], ["select_judge"])
        self.assertEqual(rt.act("Live", card), (False, "live_input_disabled"))
        switch = rt.decide("Live", focus("select_judge", selected=1, tooltip=tip(3.5)))
        self.assertEqual((switch.intent, switch.reason), ("live_card_2", "live_inspect_unknown_card"))
        play = rt.decide("Live", focus("select_judge", selected=2, tooltip=tip(5.0)))
        self.assertEqual((play.intent, play.reason), ("live_judge_2", "live_focus_trend1"))     # Da = trend #1
        timing = rt.decide("Live", focus("timing"))
        self.assertEqual(timing.intent, "live_appeal_timing")
        # next turn: the cached pictures pick the 5x card at once, no inspection
        nxt = rt.decide("Live", focus("select_card", turn=2))
        self.assertEqual((nxt.intent, nxt.reason), ("live_card_2", "live_best_known_card"))
        judge = rt.decide("Live", focus("select_judge", turn=2, selected=2, tooltip=tip(5.0)))
        self.assertEqual(judge.intent, "live_judge_2")
        # trend #1 left: the best remaining trend (Vi, trend 2)
        rt.decide("Live", focus("timing", turn=2))
        rt.decide("Live", focus("select_card", turn=3, left=("Da",)))
        third = rt.decide("Live", focus("select_judge", turn=3, selected=2, tooltip=tip(5.0), left=("Da",)))
        self.assertEqual((third.intent, third.reason), ("live_judge_3", "live_trend1_left_focus_trend2"))

    def test_targets_and_refusals(self):
        rt = runtime()
        # 2026-10-09b: the Vo judge is verified by geometry + the identical judge 2/3 widgets; 思い出 stays unverified
        unverified = {i for i in nav.TARGETS if i.startswith("live_") and not nav.TARGETS[i][1]}
        self.assertEqual(unverified, {"live_memory"})
        for intent in ("live_card_1", "live_card_2", "live_card_3", "live_judge_1", "live_judge_2", "live_judge_3",
                       "live_memory", "live_appeal_timing"):
            want = "live_input_disabled" if nav.TARGETS[intent][1] else "target_not_verified"
            self.assertEqual(rt.act("Live", nav.NavDecision("click", intent, nav.TARGETS[intent][0])),
                             (False, want))
        self.assertEqual(rt.acts, 0)

    def test_uncertainty_waits_or_stops_with_a_reason(self):
        rt = runtime(live=play_hook())
        self.assertEqual(rt.decide("Live", focus(None)).reason, "live:phase_unread")       # animation: bounded wait
        for _ in range(nav.LIVE_UNREAD_LIMIT):
            last = rt.decide("Live", focus(None))
        # 2026-10-29: a gap that outlasts the re-reads hands the Live to the game's Auto (its toggle read OFF) ...
        self.assertEqual((last.action, last.intent, last.reason), ("click", "live_auto", "live_unread_auto"))
        self.assertEqual(rt.decide("Live", {**focus(None), "auto": "ON"}).reason, "live:game_auto_secured")
        # ... and stops with its reason when the Auto toggle is unread too.
        rt = runtime(live=play_hook())
        for _ in range(nav.LIVE_UNREAD_LIMIT + 1):
            last = rt.decide("Live", {**focus(None), "auto": None})
        self.assertEqual((last.action, last.reason), ("stop", "live_phase_unread"))
        cases = [
            (play_hook(), focus("select_card", trend=(None, None, None)), "live_trend_unread"),
            # selected card unread: no resume adoption (live seg22 adopts a read selection and continues)
            (play_hook(), focus("select_judge", selected=None, tooltip=tip(3.5)), "live_selected_card_unread"),
            (lambda r, m: {"action": "stop", "reason": "live_state_unread"}, {}, "live:live_state_unread"),
            (lambda r, m: None, {}, "live_hook_bad_result"),
        ]
        for hook, readings, reason in cases:
            with self.subTest(reason):
                rt = runtime(live=hook)
                if readings:
                    readings = {**readings, "auto": None}     # Auto toggle unread: no handover, the reason stops
                if reason in ("live_selected_card_unread", "live_trend_unread"):   # bounded re-reads first
                    want = "live:selected_card_settling" if reason == "live_selected_card_unread" else "live:turn_reread"
                    for _ in range(nav.LIVE_SELECTED_WAITS):
                        self.assertEqual(rt.decide("Live", readings).reason, want)
                d = rt.decide("Live", readings)
                self.assertEqual((d.action, d.reason), ("stop", reason))
        resumed = runtime(live=play_hook()).decide("Live", focus("select_judge", selected=1, tooltip=tip(3.5)))
        self.assertEqual(resumed.action, "click")                    # mid-turn resume adopts the selected card
        again = runtime(live=play_hook())
        again.decide("Live", focus("select_card"))
        # 2026-10-12: one re-tap after a deselect (tap on another card while one was selected), then a stop.
        self.assertEqual(again.decide("Live", focus("select_card")).reason, "live_reselect_after_deselect")
        self.assertEqual(again.decide("Live", focus("select_card")).reason, "live_step_not_confirmed")
        # 2026-10-14 live a7: the game never fires a bar by itself; a resumed timing phase is pressed
        resumed_bar = runtime(live=play_hook()).decide("Live", focus("timing"))
        self.assertEqual((resumed_bar.intent, resumed_bar.reason), ("live_appeal_timing", "live_timing"))

    def test_secured_first_hands_the_rest_to_auto_only_with_the_opt_in(self):
        readings = focus("select_card", turn=6, left=("Vo", "Da"), stars=(120, [20, 15]))
        policy = runtime(live=play_hook())
        policy.policy_params["auto_when_first"] = False
        self.assertNotEqual(policy.decide("Live", readings).intent, "live_auto")     # opt-out: never Auto
        default = runtime(live=play_hook())                                          # Auto
        self.assertEqual(default.decide("Live", readings).intent, "live_auto")       # once 1st is secured
        top = runtime(live=play_hook())
        top.memory.live_top_appeal = ["Da"]                                           # focus(): Da is trend #1
        lead = focus("select_card", turn=4, left=("Da",), stars=(40, [20]))
        self.assertEqual(top.decide("Live", lead).intent, "live_auto")               # most stars + trend-#1 TA
        rt = runtime(live=play_hook())
        rt.memory.live_mode = "game_auto"
        auto = rt.decide("Live", readings)
        self.assertEqual((auto.intent, auto.reason), ("live_auto", "live_first_secured"))
        self.assertEqual(rt.decide("Live", {**readings, "auto": "ON"}).reason, "live:game_auto_secured")
        rt.decide("ResultJudge")
        self.assertFalse(rt.memory.live_auto_secured)
        close = focus("select_card", turn=6, left=("Vo",), stars=(40, [20]))
        rt2 = runtime(live=play_hook())
        rt2.memory.live_mode = "game_auto"
        self.assertEqual(rt2.decide("Live", close).intent, "live_card_1")            # not secured: keep playing

    def test_animation_only_switches_to_auto_for_an_observed_secured_lead(self):
        """A retired judge after our appeal is not proof of Top Appeal ownership."""
        rt = runtime(live=play_hook())
        rt.memory.live_trend = {"Vo": 3, "Da": 1, "Vi": 2}
        rt.memory.live_step = {"turn": 3, "step": "timing", "target": "Da"}
        d = rt.decide("Live", focus(None, trend=(None, None, None), left=("Da",), stars=(120, [20, 15])))
        self.assertEqual((d.intent, d.reason), ("live_auto", "live_first_secured"))
        self.assertTrue(rt.memory.live_auto_secured)
        self.assertEqual(rt.decide("Live", {**focus(None), "auto": "ON"}).reason, "live:game_auto_secured")
        other = runtime(live=play_hook())                       # trend #2 judge left: keep waiting
        other.memory.live_trend = {"Vo": 3, "Da": 1, "Vi": 2}
        other.memory.live_step = {"turn": 3, "step": "timing", "target": "Vi"}
        self.assertEqual(other.decide("Live", focus(None, left=("Vi",))).reason, "live:phase_unread")
        off = runtime(live=play_hook())                         # opt-out
        off.policy_params["auto_when_first"] = False
        off.memory.live_trend = {"Vo": 3, "Da": 1, "Vi": 2}
        off.memory.live_step = {"turn": 3, "step": "timing", "target": "Da"}
        self.assertEqual(off.decide("Live", focus(None, left=("Da",))).reason, "live:phase_unread")
        for stars in (None, (8, [40, 30]), (40, [20])):
            with self.subTest(stars=stars):
                uncertain = runtime(live=play_hook())
                uncertain.memory.live_trend = {"Vo": 3, "Da": 1, "Vi": 2}
                uncertain.memory.live_step = {"turn": 3, "step": "timing", "target": "Da"}
                self.assertEqual(uncertain.decide("Live", focus(None, left=("Da",), stars=stars)).action, "wait")
                self.assertFalse(uncertain.memory.live_auto_secured)
                self.assertFalse(uncertain.memory.live_top_appeal)
                # At the next selectable turn, the same retirement must not become a claimed TA either.
                self.assertNotEqual(uncertain.decide("Live", focus("select_card", turn=4, left=("Da",), stars=stars)).intent,
                                    "live_auto")
                self.assertFalse(uncertain.memory.live_top_appeal)

    def test_real_live_plan_never_auto_and_never_waits_silently(self):
        rt = runtime()
        on = rt.decide("Live", {"auto": "ON"})
        self.assertEqual((on.action, on.reason), ("stop", "live:game_auto_on_without_permission"))
        off = rt.decide("Live", {"auto": "OFF"})
        self.assertEqual((off.action, off.reason), ("stop", "live:live_state_unread"))
        rt.memory.live_mode = "game_auto"              # explicit opt-in, still gated: 1st place required -> no Auto
        self.assertEqual(rt.decide("Live", {"auto": "OFF"}).action, "stop")

    def test_stop_latches_the_first_reason_and_live_hold_is_bounded(self):
        rt = runtime()
        rt.stop("Live", "live:live_state_unread")
        rt.stop(None, "noDecision")
        self.assertEqual(rt.finished, "live:live_state_unread")
        pipeline = json.loads((ROOT / "assets" / "resource" / "base" / "pipeline" / "wing.json")
                              .read_text(encoding="utf-8"))
        self.assertIn("SC.Wing.State.Wait.Live", pipeline["SC.Wing.Hold.Live"]["all_of"])
        self.assertTrue(all(node.get("enabled") is False for node in pipeline.values()))
        clicks = [n for n, node in pipeline.items() if node.get("action") == "Click"]
        self.assertTrue(clicks and all(n.startswith("SC.Wing.Click.") for n in clicks))
        rt = runtime()
        rt.active_for("Live", True)
        rt.active_for(None)                               # the generic On.* guard is not progress
        self.assertEqual(rt._waits, 1)
        for _ in range(nav.WAIT_LIMITS["Live"]):
            rt.active_for("Live", True)
            rt.active_for(None)
        self.assertEqual(rt.finished, "wait_stalled")


def full_cards():
    p = {"1040010010": {"te_flag": True, "level": 50, "rarity": "SSR", "live_skill_attr": "Vo", "live_skill_mult": 3.0}}
    s = {}
    for i, attr in enumerate(("Vo", "Vo", "Vo", "Vo", "Da", "Vi"), start=2):
        s[f"20500{i}0010"] = {"level": 50, "rarity": "SSR", "uncap": 4, "live_skill_attr": attr,
                               "live_skill_mult": 2.5, "live_skill_name": f"skill{i}", "passive_attr": attr,
                               "passive_pct": 30, "passive_rate": 30, "passive_cond": 1.0, "mastery_stamina": 3,
                               "mastery_sp": 3, "mastery_cap": 0, "audition_mastery": 0, "events_value": 20,
                               "stats": {"Vo": 200, "Da": 100, "Vi": 100, "Me": 100}}
    return {"p": sorted(p), "s": sorted(s)}, {**p, **s}


class AutoTeamConfirmationTests(unittest.TestCase):
    def auto_config(self):
        data = pc.default_config("true_end")
        data["team"] = {**pc.DEFAULT_TEAM, "mode": "auto", "primary_stat": "Vo"}
        return pc.ProduceConfig.from_dict(data)

    def test_ids_only_is_rejected_and_refused(self):
        owned = {"p": ["1040010010"], "s": ["2050020010", "2050030010"]}
        result = pc.run_preflight(self.auto_config(), owned)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reasons"], ["auto_team_rejected"])
        self.assertIsNone(result["team_plan"]["proposal"])
        self.assertEqual(pc.run_preflight(self.auto_config(), None)["reasons"], ["owned_cards_unknown"])

    def test_proposal_needs_confirmation_then_runs_as_manual(self):
        owned, features = full_cards()
        result = pc.run_preflight(self.auto_config(), owned, features)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reasons"], ["team_needs_confirmation"])
        proposal = result["team_plan"]["proposal"]
        self.assertEqual(proposal["p_card"], "1040010010")
        self.assertEqual(len(proposal["s_cards"]), 4)
        self.assertEqual(pc.duplicate_idols(pc.team_cards(proposal)), {})
        self.assertIn(result["team_plan"]["status"], ("ok", "needs_confirmation"))
        # the user's 「采用到卡位」 = the proposal becomes the manual team
        data = pc.default_config("true_end")
        data["team"] = proposal
        accepted = pc.ProduceConfig.from_dict(data)
        self.assertEqual(pc.run_preflight(accepted, owned), {"ok": True, "reasons": [], "team_plan": {
            **pc.auto_team_plan(accepted.team), "stop": False}})
        memory = nav.RunMemory.from_produce_config(accepted)
        self.assertEqual([c["id"] for c in memory.team], pc.team_cards(proposal))
        self.assertEqual(nav.WingNavRuntime(memory).team_conflicts(memory.team), [])

    def test_invalid_scorer_proposal_is_not_offered(self):
        owned, _ = full_cards()
        clash = lambda cards, primary: {"status": "ok", "p_card": "1040020010", "guest": owned["s"][4],
                                        "starters": owned["s"][:4], "confidence": 1.0}
        owned2 = {"p": owned["p"] + ["1040020010"], "s": owned["s"]}
        plan = pc.auto_team_plan({"mode": "auto", "primary_stat": "Vo"}, owned2, scorer=clash)
        self.assertEqual((plan["reason"], plan["proposal"]), ("auto_team_invalid_proposal", None))

    def test_manual_team_not_owned_is_refused(self):
        data = pc.default_config("true_end")
        data["team"] = {**pc.DEFAULT_TEAM, "p_card": "1040010010"}
        result = pc.run_preflight(pc.ProduceConfig.from_dict(data), {"p": [], "s": ["2050020010"]})
        self.assertEqual(result["reasons"], ["team_card_not_owned"])

    def test_propose_tool_writes_nothing_on_dry_run(self):
        from tools import auto_team_propose as tool
        owned, features = full_cards()
        result = tool.propose(owned, features, "Vo")
        self.assertEqual((result["version"], result["reason"]), (1, "team_needs_confirmation"))
        self.assertTrue(subprocess.run(["git", "check-ignore", "-q", "ui/game-assets/team-proposal.js"],
                                       cwd=ROOT).returncode == 0)


@unittest.skipUnless(shutil.which("node"), "node not installed")
class AutoTeamUiTests(unittest.TestCase):
    def test_proposal_helpers_and_start_blocker(self):
        script = (
            "import { cleanProposal, proposalChanges } from './ui/components/team-proposal.js';"
            "import { teamCheck } from './ui/components/team-rules.js';"
            "import { readFileSync } from 'node:fs';"
            "const schema = JSON.parse(readFileSync('ui/fixtures/schema.json', 'utf8'));"
            "const annotations = JSON.parse(readFileSync('ui/annotations.json', 'utf8'));"
            "const note = annotations.option_groups['Produce编队'];"
            "const state = { schema, annotations, prefs: {} };"
            "const keys = schema.tasks.find((t) => t.name === 'AutoProduce').options"
            "  .filter((k) => annotations.options?.[k]?.group === 'Produce编队');"
            "const slots = keys.map((key) => ({ key, slot: annotations.options[key].slot }));"
            "const good = cleanProposal({ version: 1, status: 'needs_confirmation', confidence: 2, reasons: ['x', 5],"
            "  proposal: { p_card: '1040010010', s_cards: ['2050020010', '2050030010', '2050040010', '2050050010'], guest: '2050060010' } });"
            "const bad = cleanProposal({ version: 1, proposal: { p_card: '2040010010', s_cards: [], guest: 'x' } });"
            "const dup = cleanProposal({ version: 1, proposal: { p_card: '1040010010', s_cards: ['2050020010', '2050020010', '2050040010', '2050050010'], guest: '2050060010' } });"
            "const auto = teamCheck(state, 'AutoProduce', { option: { Produce编队方式: '自动编队' } });"
            "console.log(JSON.stringify({ good, bad: bad.proposal, dup: dup.proposal,"
            "  changes: proposalChanges(state, note, slots, good.proposal), auto }));")
        result = subprocess.run(["node", "--input-type=module", "-e", script], cwd=ROOT, capture_output=True,
                                text=True, encoding="utf-8", timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout)
        self.assertEqual(out["good"]["confidence"], 1)
        self.assertEqual(out["good"]["reasons"], ["x"])
        self.assertIsNone(out["bad"])
        self.assertIsNone(out["dup"])
        changes = out["changes"]
        self.assertEqual(changes["Produce编队方式"], "指定编队")
        self.assertEqual(changes["ProduceP卡"], "1040010010")
        self.assertEqual(changes["Produce客串"], "2050060010")
        self.assertEqual([changes[f"ProduceS卡{i}"] for i in range(1, 5)],
                         ["2050020010", "2050030010", "2050040010", "2050050010"])
        self.assertTrue(out["auto"]["auto"])
        # 2026-10-14 user: 推荐编队 targets a P the user picks first; without it the start is blocked
        self.assertEqual(out["auto"]["mode"], "recommend")
        self.assertIn("未选择 P 偶像", out["auto"]["errors"])


@unittest.skipUnless(DEMO.is_file() and OCR.is_file(), "private W.I.N.G. demo recording not present")
class ReplaySmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tools import wing_demo_replay
        cls.report = wing_demo_replay.replay("boxes")

    def test_full_stack_replay(self):
        summary = self.report["summary"]
        self.assertEqual(summary["crashes"], 0, self.report["crashes"])
        self.assertEqual((summary["unguarded_click_intents"], summary["clicks_sent"], summary["input_attempts"]),
                         (0, 0, 0))
        self.assertGreater(summary["decision_points"], 100)
        live = [p for p in self.report["decision_points"] if p["screen"] == "Live"]
        self.assertTrue(live)
        for point in live:     # Live waits only on an unread phase / a timing bar it did not start (bounded)
            self.assertIn(point["sequential"]["action"], ("stop", "click", "wait"), point)
            if point["sequential"]["action"] == "wait":
                # 2026-10-15: the trend-#1 Top Appeal switches to Auto mid-animation; the game then plays
                self.assertIn(point["sequential"]["reason"], ("live:phase_unread", "live:timing_not_ours",
                                                              "live:game_auto_secured"), point)
        weeks = [p for p in self.report["decision_points"] if p.get("timeline")]
        self.assertTrue(weeks)
        for point in weeks:
            self.assertNotIn("error", point["timeline"]["scorer_on_hand_state"])


if __name__ == "__main__":
    unittest.main()
