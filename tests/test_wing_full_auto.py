"""W.I.N.G. full-auto batch c (2026-10-09c): forget rule, 大富豪 before the Produce, home -> プロデュース選択, team
fingerprint on ユニット編成, アイドルの応援 in the win estimate. Offline, zero input."""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest import mock

from agent.core import wing_audition as wa
from agent.core import wing_nav as nav
from agent.core import wing_skill_panel as wsp
from agent.core import wing_sp
from agent.core import wing_unit_check as wuc

ROOT = Path(__file__).resolve().parent.parent
REC2 = ROOT / "dev_assets/private/recordings/wing-demo2-20261006-0106-02"
REC3 = ROOT / "dev_assets/private/recordings/wing-demo3-20261006-1643-03"
OCR2 = ROOT / "dev_assets/private/wing-demo2-analysis/ocr.jsonl"
OCR3 = ROOT / "dev_assets/private/wing-demo3-analysis/ocr-03.jsonl"
ASSETS = ROOT / "ui/game-assets/manifest.json"

INITIAL = [{"row": 1, "name": "ボーカルアピールI", "attributes": ("Vo",), "multiplier": 1.0, "kind": "live_skill"},
           {"row": 2, "name": "ダンスアピールI", "attributes": ("Da",), "multiplier": 1.0, "kind": "live_skill"},
           {"row": 3, "name": "ビジュアルアピールI", "attributes": ("Vi",), "multiplier": 1.0, "kind": "live_skill"},
           {"row": 4, "name": "メンタルキュアI", "attributes": (), "multiplier": 0.0, "kind": "live_skill",
            "description": "メンタル5%回復"}]


def runtime(**options):
    opts = {"mode": "start", "input_enabled": True, **options}
    return nav.WingNavRuntime(nav.RunMemory(primary="Vo"), options=nav.RunOptions(**opts))


class ForgetRuleTests(unittest.TestCase):
    def test_recovery_first_then_weakest_appeal(self):
        self.assertEqual(wsp.choose_forget(INITIAL, 3.0, "Vo", new_attributes=("Vo",))["reason"], "recovery_first")
        after_cure = [dict(r) for r in INITIAL[:3]] + [{"row": 4, "name": "x", "attributes": ("Vo",),
                                                        "multiplier": 3.0}]
        # Da/Vi ×1 weigh half in a Vo run (stats unknown) -> Da (row 2) before Vi (row 3) by row order
        choice = wsp.choose_forget(after_cure, 3.5, "Vo", new_attributes=("Vo",))
        self.assertEqual((choice["row"], choice["reason"]), (2, "weakest_appeal"))
        # with stats, the weaker attribute goes first: Vi 200 < Da 300 relative to Vo 600
        stats = {"Vo": 600, "Da": 300, "Vi": 200, "Me": 300}
        self.assertEqual(wsp.choose_forget(after_cure, 3.5, "Vo", new_attributes=("Vo",), stats=stats)["row"], 3)
        # the on-attribute ×1 is weaker than an off-attribute ×3 skill
        mixed = [{"row": 1, "attributes": ("Vo",), "multiplier": 1.0}, {"row": 2, "attributes": ("Da",), "multiplier": 3.0},
                 {"row": 3, "attributes": ("Vo",), "multiplier": 4.0}, {"row": 4, "attributes": ("Vo",), "multiplier": 3.0}]
        self.assertEqual(wsp.choose_forget(mixed, 3.0, "Vo", new_attributes=("Vo",))["row"], 1)

    def test_margin_and_no_weaker(self):
        held = [{"row": i, "attributes": ("Vo",), "multiplier": m} for i, m in enumerate((3.0, 3.5, 4.0, 5.0), 1)]
        self.assertEqual(wsp.choose_forget(held, 3.2, "Vo", new_attributes=("Vo",))["reason"], "no_weaker_skill")
        self.assertEqual(wsp.choose_forget(held, 3.5, "Vo", new_attributes=("Vo",))["row"], 1)
        self.assertEqual(wsp.choose_forget(held, 3.5, "Vo", new_attributes=("Vo",), margin=0.2)["row"], None)
        # an off-attribute ×3 in a Vo run (weight 0.5 -> 1.5) never replaces a Vo ×3
        self.assertIsNone(wsp.choose_forget(held, 3.0, "Vo", new_attributes=("Vi",))["row"])
        unread = [dict(held[0], multiplier=None)] + held[1:]
        self.assertEqual(wsp.choose_forget(unread, 5.0, "Vo")["reason"], "current_skill_unread")

    def test_plan_replaces_the_cure_and_skips_weak_live_skills(self):
        skills = [wing_sp.LiveSkill(r["name"], r["attributes"][0] if r["attributes"] else None, r["multiplier"])
                  for r in INITIAL]
        live = wing_sp.SPNode("live", "live_skill", 30, 3.0, ("Vo",))
        cap = wing_sp.SPNode("cap", "cap_up", 20, 25.0, ("Vo",))
        # cap far from the stat: caps_first_near_cap would otherwise put the cap first
        plan = wing_sp.plan_sp([live, cap], sp=30, primary="Vo", stat=500, cap=700, skills=skills)
        self.assertEqual((plan.steps[0].node_id, plan.steps[0].forget), ("live", "メンタルキュアI"))
        strong = [wing_sp.LiveSkill(f"s{i}", "Vo", 5.0) for i in range(4)]
        plan = wing_sp.plan_sp([live, cap], sp=50, primary="Vo", stat=500, cap=520, skills=strong)
        self.assertEqual([s.node_id for s in plan.steps], ["cap"])
        self.assertIn("live_skills_not_stronger", plan.reasons)
        # an upgrade still replaces its base without a forget of another slot
        up = wing_sp.SPNode("DIVE IN!+", "live_skill", 50, 6.0, ("Vo",), upgrades="s0")
        plan = wing_sp.plan_sp([up], sp=50, primary="Vo", stat=500, cap=520, skills=strong)
        self.assertEqual(plan.steps[0].forget, "s0")


class ForgetClickChainTests(unittest.TestCase):
    """忘れるスキル選択 -> 選択 -> スキル入れ替え確認 OK, decided on synthetic readings; the targets are still unverified, so
    each test patches them verified to exercise the chain a single live sample will enable."""

    def session(self):
        rt = runtime(mode="attach", sp_mode="auto")
        rt.begin()
        rt.memory.pending = "spend_sp"
        rt.memory.sp_session = {"key": ["k"], "sp_start": 86, "taps": 1, "learned": [], "skipped": [], "catalog": {
            "skill_node_c08_r06": {"name": "あなたと、月の満ちる頃", "kind": "live_skill", "state": "learnable"}},
            "last_tap": "skill_node_c08_r06", "owned": "read", "stats": {"Vo": 400, "Da": 150, "Vi": 150, "Me": 150},
            "caps": None, "reentered": False, "unstable": 0,
            "learning": {"intent": "skill_node_c08_r06", "cost": 20, "sp_before": 86, "name": "あなたと、月の満ちる頃",
                         "kind": "live_skill", "multiplier": 3.0, "attributes": ["Vi"], "forget": None,
                         "cancelled": False, "confirmed": True, "tier": "p_card"}}
        return rt

    def test_unverified_select_hands_over_with_the_choice(self):
        rt = self.session()
        d = rt.decide("ForgetSelect", {"forget_rows": INITIAL})
        # forget_select_* rows are verified now (live W.I.N.G. 2026-10-12): the planned row is tapped once
        self.assertEqual((d.action, d.intent, d.detail["choice"]["row"]), ("click", "forget_select_4", 4))

    def test_full_chain_once_targets_are_verified(self):
        patched = {**nav.TARGETS, **{k: (nav.TARGETS[k][0], True) for k in
                                     ("forget_select_4", "replace_ok", "replace_dialog_cancel")}}
        with mock.patch.dict(nav.TARGETS, patched):
            rt = self.session()
            d = rt.decide("ForgetSelect", {"forget_rows": INITIAL})
            self.assertEqual((d.intent, d.reason), ("forget_select_4", "sp_forget_recovery_first"))
            self.assertTrue(rt.act("ForgetSelect", d)[0])
            # a second ForgetSelect frame after the tap never taps another row
            self.assertEqual(rt.decide("ForgetSelect", {"forget_rows": INITIAL}).reason, "sp_forget_select_no_dialog")
            good = {"forget": "メンタルキュアI", "new": "あなたと、月の満ちる頃", "ok_box": (686, 534, 42, 27),
                    "cancel_box": (525, 537, 92, 22)}
            d = rt.decide("ReplaceDialog", {"replace": good})
            self.assertEqual((d.intent, d.reason), ("replace_ok", "sp_replace_confirmed"))
            # the taller dialog (OK lower than the measured rect) stops instead of tapping
            low = dict(good, ok_box=(752, 600, 42, 27))
            self.assertEqual(rt.decide("ReplaceDialog", {"replace": low}).reason, "sp_replace_ok_layout_unmeasured")
            # back on the panel: learned + SP − cost -> the held skills are updated (the cure replaced)
            rt.memory.live_skills = [dict(r) for r in INITIAL]
            panel = wsp.read_panel([])  # not canonical: build a minimal canonical reading instead
            r = {"sp": 66, "panel": {"canonical": True, "slots": {}}, "skill_info": {"state": "learned", "cost": 20},
                 "skill_text": {"name": "あなたと、月の満ちる頃"}}
            rt.decide("Review", r)
            names = [k["name"] for k in rt.memory.live_skills]
            self.assertNotIn("メンタルキュアI", names)
            self.assertIn("あなたと、月の満ちる頃", names)
            self.assertIsNotNone(panel)

    def test_mismatching_dialog_cancels_only_inside_the_measured_rect(self):
        patched = {**nav.TARGETS, "replace_dialog_cancel": (nav.TARGETS["replace_dialog_cancel"][0], True)}
        with mock.patch.dict(nav.TARGETS, patched):
            rt = self.session()
            rt.memory.sp_session["learning"]["forget"] = {"row": 4, "name": "メンタルキュアI"}
            wrong = {"forget": "ダンスアピールI", "new": "あなたと、月の満ちる頃", "ok_box": (686, 534, 42, 27),
                     "cancel_box": (525, 537, 92, 22)}
            self.assertEqual(rt.decide("ReplaceDialog", {"replace": wrong}).intent, "replace_dialog_cancel")
            rt = self.session()
            rt.memory.sp_session["learning"]["forget"] = {"row": 4, "name": "メンタルキュアI"}
            self.assertEqual(rt.decide("ReplaceDialog", {"replace": dict(wrong, cancel_box=None)}).reason,
                             "sp_replace_cancel_layout_unmeasured")


class MinigameTests(unittest.TestCase):
    def test_off_by_default_every_page_stops(self):
        rt = runtime()
        rt.begin()
        for screen in ("MinigameTop", "MinigameCourse", "MinigameGame", "MinigameGameEnd", "MinigameResult",
                       "MinigameRules"):
            self.assertEqual(rt.decide(screen, {}).reason, "minigame_not_automated", screen)
        self.assertEqual(rt.decide("MinigameMenu", {}).reason, "minigame_not_automated")
        attach = nav.WingNavRuntime(options=nav.RunOptions())
        attach.begin()
        self.assertEqual(attach.decide("Home", {"home_produce_label": True}).reason, "outside_produce")
        with self.assertRaises(ValueError):
            nav.RunOptions(minigame="before_start")              # attach mode
        with self.assertRaises(ValueError):
            nav.RunOptions(mode="start", minigame="before_start")  # needs from_home

    def test_one_game_then_produce(self):
        rt = runtime(from_home=True, minigame="before_start")
        rt.begin()
        steps = [("Home", {"home_minigame_label": True}, "home_minigame_menu"),
                 ("HomeMinigamePopup", {"popup_minigame": True}, "home_minigame"),
                 ("MinigameTop", {}, "minigame_daifugo"),
                 ("MinigameRules", {"rules_page": (1, 4)}, "tutorial_next"),
                 ("MinigameRules", {"rules_page": (4, 4)}, "popup_close"),
                 ("MinigameCourse", {"course_first": True}, "minigame_course_1")]
        for screen, r, intent in steps:
            d = rt.decide(screen, r)
            self.assertEqual(d.intent, intent, (screen, d))
        self.assertEqual(rt.decide("MinigameGame", {"turn_bar": False}).action, "wait")
        for _ in range(5):
            d = rt.decide("MinigameGame", {"turn_bar": True, "pass_enabled": True})
            self.assertEqual(d.intent, "minigame_pass")
            self.assertTrue(rt.act("MinigameGame", d)[0])          # repeated passes are allowed (REPEAT_LIMITS)
        self.assertEqual(rt.decide("MinigameGameEnd", {}).intent, "minigame_touch_next")   # live 2026-10-15
        self.assertEqual(rt.decide("MinigameGameEnd", {}).action, "wait")                  # never a second tap
        self.assertEqual(rt.decide("MinigameResult", {"next_box": (605, 603, 60, 30)}).intent, "minigame_result_next")
        settle = rt.decide("MinigameResult", {"produce_box": (568, 624, 140, 26), "cheer_notice": True})
        self.assertEqual(settle.action, "wait")                  # live 2026-10-15: first frame of the panel is ignored
        d = rt.decide("MinigameResult", {"produce_box": (568, 624, 140, 26), "cheer_notice": True})
        self.assertEqual(d.intent, "minigame_to_produce_low")
        self.assertTrue(rt.memory.minigame["done"] and rt.memory.cheer)
        # after the game the home screen goes to プロデュース (once)
        self.assertEqual(rt.decide("Home", {"home_produce_label": True}).intent, "home_produce")
        self.assertEqual(rt.decide("Home", {"home_produce_label": True}).reason, "home_produce_no_effect")

    def test_ambiguity_stops(self):
        rt = runtime(from_home=True, minigame="before_start")
        rt.begin()
        self.assertEqual(rt.decide("Home", {}).reason, "home_label_unread")
        self.assertEqual(rt.decide("MinigameCourse", {"course_first": False}).reason, "minigame_course_not_at_start")
        rt.decide("MinigameCourse", {"course_first": True})
        self.assertEqual(rt.decide("MinigameGame", {"turn_bar": True, "pass_enabled": False}).reason,
                         "minigame_lead_requires_user")
        self.assertEqual(rt.decide("MinigameResult", {}).reason, "minigame_result_button_unread")
        self.assertEqual(rt.decide("MinigameRules", {}).reason, "minigame_rules_page_unread")
        rt.memory.minigame["passes"] = nav.MINIGAME_MAX_PASSES
        self.assertEqual(rt.decide("MinigameGame", {"turn_bar": True, "pass_enabled": True}).reason,
                         "minigame_too_long")

    def test_home_without_minigame_and_after_start(self):
        rt = runtime(from_home=True)
        rt.begin()
        self.assertEqual(rt.decide("Home", {"home_produce_label": True}).intent, "home_produce")
        rt.memory.start_attempted = True
        self.assertEqual(rt.decide("Home", {"home_produce_label": True}).reason, "outside_produce")
        # home_produce was verified live on 2026-10-12: the act gate sends it
        rt = runtime(from_home=True)
        rt.begin()
        d = rt.decide("Home", {"home_produce_label": True})
        self.assertEqual(rt.act("Home", d), (True, "sent"))


class ProduceSelectTests(unittest.TestCase):
    def test_tab_difficulty_and_cheer(self):
        rt = runtime()
        rt.begin()
        self.assertEqual(rt.decide("ProduceSelectOther", {"train_tab": True}).intent, "produce_tab_train")
        self.assertEqual(rt.decide("ProduceSelectOther", {}).reason, "produce_tab_unread")
        d = rt.decide("ProduceSelect", {"difficulty": "beginner", "cheer": True})
        self.assertEqual(d.intent, "produce_difficulty_middle")
        self.assertTrue(rt.memory.cheer)
        self.assertEqual(rt.decide("ProduceSelect", {"difficulty": "middle"}).intent, "produce_next")
        self.assertEqual(rt.decide("ProduceSelect", {"difficulty": None}).reason, "difficulty_unread")
        # U01: one tap per run; a tab that does not change stops instead of tapping again
        self.assertEqual(rt.decide("ProduceSelect", {"difficulty": "master"}).reason, "difficulty_not_selected")

    def test_configured_difficulty_is_selected(self):
        for want in ("beginner", "master"):
            rt = nav.WingNavRuntime(nav.RunMemory(primary="Vo", difficulty=want),
                                    options=nav.RunOptions(mode="start", input_enabled=True))
            rt.begin()
            d = rt.decide("ProduceSelect", {"difficulty": "middle"})
            self.assertEqual((d.intent, d.reason), (f"produce_difficulty_{want}", f"select_wing_{want}"))
            self.assertEqual(rt.act("ProduceSelect", d), (True, "sent"))
            d = rt.decide("ProduceSelect", {"difficulty": want})
            self.assertEqual((d.intent, d.reason), ("produce_next", f"start_mode_wing_{want}"))
        attach = nav.WingNavRuntime(options=nav.RunOptions())
        attach.begin()
        self.assertEqual(attach.decide("ProduceSelectOther", {"train_tab": True}).reason, "setup_requires_user")

    def test_cheer_raises_the_estimate(self):
        ctx = dict(primary="Vo", stats={"Vo": 400, "Me": 200}, recommended={"Vo": 450, "Me": 400},
                   trend={"Vo": 1, "Da": 2, "Vi": 3}, memory_level=1)
        plain = wa.audition_win_estimate(wa.AuditionContext(**ctx))
        cheer = wa.audition_win_estimate(wa.AuditionContext(**ctx, cheer=True))
        self.assertGreater(cheer["p_pass"], plain["p_pass"])
        self.assertEqual(wa.audition_win_estimate(wa.AuditionContext(**ctx, cheer=False)), plain)

        class S:
            stats, trend, memory_level, cheer = {"Vo": 400}, None, None, True
        self.assertTrue(wa.context_from_wing(S(), None, "Vo").cheer)
        memory = nav.RunMemory()
        memory.cheer = True
        self.assertTrue(memory.snapshot()["cheer"])
        fresh = nav.RunMemory()
        fresh.restore(memory.snapshot())
        self.assertTrue(fresh.cheer)


class UnitFingerprintTests(unittest.TestCase):
    TEAM = {"P": "1040020080", "S": ["2040050080", "2040060080", "2040230040", "2040040090"], "guest": "2040070080"}

    def cards(self, **override):
        base = {"P": "1040020080", "S1": "2040050080", "S2": "2040060080", "S3": "2040230040", "S4": "2040040090",
                "guest": "2040070080"}
        base.update(override)
        return {slot: {"card_id": cid, "status": "accepted", "reason": "ok", "score": 0.9,
                       "candidates": [[cid, 0.9, 0.8]]} for slot, cid in base.items()}

    def test_compare(self):
        self.assertEqual(wuc.compare(self.cards(), self.TEAM)["verdict"], "match")
        swapped = self.cards(S1="2040060080", S2="2040050080")
        self.assertEqual(wuc.compare(swapped, self.TEAM)["verdict"], "match")          # S order is free
        self.assertEqual(wuc.compare(self.cards(P="1040130050"), self.TEAM)["verdict"], "mismatch")
        unread = self.cards()
        unread["S3"] = {"card_id": None, "status": "rejected", "reason": "verify_failed",
                        "candidates": [["2040230040", 0.82, 0.53], ["2040230110", 0.70, 0.42], ["1040100140", 0.66, 0.38]]}
        self.assertEqual(wuc.compare(unread, self.TEAM)["verdict"], "match")           # targeted check
        unread["S3"]["candidates"][0][2] = 0.45
        self.assertEqual(wuc.compare(unread, self.TEAM)["verdict"], "uncertain")
        self.assertEqual(wuc.compare(None, self.TEAM)["verdict"], "uncertain")

    def test_unit_decisions(self):
        rt = runtime()
        rt.begin()
        rt.memory.team_slots = dict(self.TEAM)
        rt.memory.target_unit = 7               # the default became ユニット1 on 2026-10-14
        base = {"unit_index": 7, "unit_total": 20, "unit_page": 0}
        d = rt.decide("UnitSelect", {**base, "unit_cards": self.cards()})
        self.assertEqual((d.intent, d.reason), ("unit_next", "unit_fingerprint_match"))
        d = rt.decide("UnitSelect", {**base, "unit_cards": self.cards(P="1040130050")})
        self.assertEqual(d.reason, "team_fingerprint_mismatch")
        d = rt.decide("UnitSelect", {**base, "unit_cards": None})
        self.assertEqual(d.reason, "team_fingerprint_unverified")
        confirmed = runtime(team_confirmed=True)
        confirmed.begin()
        confirmed.memory.team_slots = dict(self.TEAM)
        confirmed.memory.target_unit = 7
        self.assertEqual(confirmed.decide("UnitSelect", {**base, "unit_cards": None}).reason, "unit_confirmed_by_user")

    def test_unit_search(self):
        rt = runtime(unit_search=True)
        rt.begin()
        rt.memory.team_slots = dict(self.TEAM)
        rt.memory.target_unit = 7               # the default became ユニット1 on 2026-10-14
        wrong = self.cards(P="1040130050")
        d = rt.decide("UnitSelect", {"unit_index": 7, "unit_total": 20, "unit_page": 0, "unit_cards": wrong})
        self.assertEqual((d.intent, d.reason), ("unit_tab_1", "unit_search"))
        d = rt.decide("UnitSelect", {"unit_index": 1, "unit_total": 20, "unit_page": 0, "unit_cards": wrong})
        self.assertEqual(d.intent, "unit_tab_2")
        d = rt.decide("UnitSelect", {"unit_index": 2, "unit_total": 20, "unit_page": 0, "unit_cards": self.cards()})
        self.assertEqual((d.intent, rt.memory.target_unit), ("unit_next", 2))
        # nothing on page 1 -> › to page 2 (2026-10-13: both arrows verified), then the page-2 tabs
        rt = runtime(unit_search=True)
        rt.begin()
        rt.memory.team_slots = dict(self.TEAM)
        rt.memory.unit_search = {"checked": {str(u): "mismatch" for u in range(1, 11)}, "taps": 9}
        d = rt.decide("UnitSelect", {"unit_index": 10, "unit_total": 20, "unit_page": 0, "unit_cards": wrong})
        self.assertEqual((d.intent, d.reason), ("unit_page_next", "unit_search_page"))
        d = rt.decide("UnitSelect", {"unit_index": 17, "unit_total": 20, "unit_page": 1, "unit_cards": wrong})
        self.assertEqual(d.intent, "unit_tab_1")          # ユニット11 sits in the first tab position of page 2
        # 2026-10-14 live: after › the counter still reads the old unit 10 while the tabs show page 2 -> pick a tab
        # there (the cards on screen are unit 10's, already checked; never fingerprinted again).
        d = rt.decide("UnitSelect", {"unit_index": 10, "unit_total": 20, "unit_page": 1, "unit_cards": self.cards()})
        self.assertEqual((d.intent, d.reason), ("unit_tab_1", "unit_search"))
        # all 20 checked, the team is in none of them -> stop (a card in no unit must stop clearly)
        rt.memory.unit_search = {"checked": {str(u): "mismatch" for u in range(1, 21)}, "taps": 21}
        rt.memory.target_unit = None
        self.assertEqual(rt.decide("UnitSelect", {"unit_index": 20, "unit_total": 20, "unit_page": 1,
                                                  "unit_cards": wrong}).reason, "team_not_found_in_units")
        # starting on page 2 searches page 2 first, then ‹ back to page 1
        rt = runtime(unit_search=True)
        rt.begin()
        rt.memory.team_slots = dict(self.TEAM)
        rt.memory.unit_search = {"checked": {str(u): "mismatch" for u in range(11, 21)}, "taps": 10}
        d = rt.decide("UnitSelect", {"unit_index": 20, "unit_total": 20, "unit_page": 1, "unit_cards": wrong})
        self.assertEqual(d.intent, "unit_page_prev")


class ResumeDetectionTests(unittest.TestCase):
    """Mid-Produce detection on the first Hub (user: 识别中途produce，配置不一样要警告选择继续还是中断重开)."""

    def rt(self, **options):
        rt = nav.WingNavRuntime(nav.RunMemory(p_idol="風野灯織"), options=nav.RunOptions(**options))
        rt.begin()
        return rt

    def test_match_mismatch_unverified(self):
        rt = self.rt()
        d = rt.decide("Hub", {"hub_speaker": "灯織", "hub_cheer": True})
        self.assertNotIn(d.reason, ("resume_config_mismatch", "resume_identity_unverified"))
        self.assertEqual(rt.memory.resume_info["verdict"], "match")
        self.assertTrue(rt.memory.cheer)
        rt = self.rt()
        d = rt.decide("Hub", {"hub_speaker": "夏葉", "season": 2, "weeks_left": 5})
        self.assertEqual(d.reason, "resume_config_mismatch")
        self.assertEqual(d.detail["resume"]["seen_idol"], "夏葉")
        self.assertEqual(len(d.detail["resume"]["choices"]), 2)
        ok = self.rt(resume_mismatch_ok=True)
        self.assertNotEqual(ok.decide("Hub", {"hub_speaker": "夏葉"}).reason, "resume_config_mismatch")
        self.assertEqual(ok.memory.resume_info["user_choice"], "continue")
        # attach + unread speaker: continue (warning logged); start: stop with the warning
        self.assertNotEqual(self.rt().decide("Hub", {}).reason, "resume_identity_unverified")
        self.assertEqual(self.rt(mode="start").decide("Hub", {}).reason, "resume_identity_unverified")

    def test_own_produce_is_not_checked_and_saved_segments_are(self):
        rt = self.rt(mode="start")
        rt.memory.start_attempted = True
        self.assertNotEqual(rt.decide("Hub", {"hub_speaker": "夏葉"}).reason, "resume_config_mismatch")
        # 2026-10-11 review P1-2: a restored segment (identity matched in WingSession.begin) still runs the idol
        # check; only an unread bubble is tolerated, even in start mode.
        rt = self.rt()
        rt.memory.segment_resumed = True
        self.assertEqual(rt.decide("Hub", {"hub_speaker": "夏葉"}).reason, "resume_config_mismatch")
        rt = self.rt(mode="start")
        rt.memory.segment_resumed = True
        self.assertNotEqual(rt.decide("Hub", {}).reason, "resume_identity_unverified")
        rt = self.rt()
        rt.decide("Hub", {"hub_speaker": "灯織"})
        self.assertNotEqual(rt.decide("Hub", {"hub_speaker": "夏葉"}).reason, "resume_config_mismatch")  # once
        self.assertTrue(nav.same_idol("灯織", "風野灯織"))
        self.assertFalse(nav.same_idol("夏葉", "風野灯織"))
        self.assertIsNone(nav.same_idol(None, "風野灯織"))

    @unittest.skipUnless(OCR2.is_file(), "private W.I.N.G. recordings not present")
    def test_hub_reader_on_native_frames(self):
        from agent.core import wing_screen as wsc
        from tools import wing_nav_build as build
        nodes = wsc.load_nodes((wsc.PIPELINE_DIR / "wing_draft.json",))
        nodes.update(build.build())
        ev = wsc.Evaluator(nodes, wsc.BoxBackend(), {})
        for seq in (459, 515, 1080, 1480):
            r = wsc.read_screen("Hub", ev, wsc.Frame(_ocr(OCR2, seq), lambda: None, key=str(seq)))
            self.assertEqual((r["hub_speaker"], r["hub_cheer"]), ("灯織", True), seq)


class FrameOcrSpeedTests(unittest.TestCase):
    """One whole-frame OCR per frame answers every anchor/reader (wing_custom.FrameOcrBackend)."""

    def test_single_ocr_per_frame_and_same_classification(self):
        from agent.core import wing_custom as wc
        from agent.core import wing_screen as wsc
        if not OCR2.is_file():
            self.skipTest("private W.I.N.G. recordings not present")
        texts = _ocr(OCR2, 459)

        class Inner:
            calls = 0

            def ocr(self, node, frame):
                Inner.calls += 1
                self.last = node
                return texts
        cache = {}
        backend = wc.FrameOcrBackend(Inner(), cache, "d1")
        nodes = wsc.load_nodes()
        fast = wsc.ScreenClassifier(nodes, backend, {"SC.Wing.State": lambda p, f: True})
        slow = wsc.ScreenClassifier(nodes, wsc.BoxBackend(), {"SC.Wing.State": lambda p, f: True})
        frame = wsc.Frame((), lambda: None, key="x")
        self.assertEqual(fast.classify(frame).screen, slow.classify(wsc.Frame(texts, lambda: None)).screen)
        self.assertEqual(Inner.calls, 1)
        wsc.read_screen("Hub", fast.evaluator, frame)
        self.assertEqual(Inner.calls, 1)
        session = wc.WingSession()
        with self.assertRaises(ValueError):
            session.begin({"ocr": "fast"})


def _ocr(path, seq):
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row["seq"] == seq:
            return [(t, tuple(b)) for t, b in row["texts"]]
    raise KeyError(seq)


@unittest.skipUnless(OCR2.is_file() and OCR3.is_file() and REC2.is_dir() and REC3.is_dir(),
                     "private W.I.N.G. recordings not present")
class NativeFrameTests(unittest.TestCase):
    """New readers and screens on the native recordings with the generator's (not yet written) Pipeline nodes."""

    @classmethod
    def setUpClass(cls):
        from agent.core import wing_screen as wsc
        from tools import wing_nav_build as build
        nodes = wsc.load_nodes((wsc.PIPELINE_DIR / "wing_draft.json",))
        nodes.update(build.build())
        cls.nodes = nodes
        cls.classifier = wsc.ScreenClassifier(nodes, wsc.BoxBackend(), {"SC.Wing.State": lambda p, f: True})

    def frame(self, rec, ocr, seq):
        from PIL import Image
        import numpy as np
        from agent.core import wing_screen as wsc
        image = np.asarray(Image.open(rec / f"frame-{seq:06d}.png").convert("RGB"))
        return wsc.Frame(_ocr(ocr, seq), lambda: image, key=f"{rec.name}:{seq}")

    def read(self, rec, ocr, seq):
        from agent.core import wing_screen as wsc
        frame = self.frame(rec, ocr, seq)
        screen = self.classifier.classify(frame).screen
        return screen, wsc.read_screen(screen, self.classifier.evaluator, frame)

    def test_minigame_pages(self):
        cases = {6: "MinigameTop", 43: "MinigameCourse", 11: "MinigameRules", 75: "MinigameGame", 77: "MinigameGame",
                 161: "MinigameResult", 172: "MinigameResult", 131: "MinigameResult", 54: "MinigameMenu",
                 58: "MinigameMenu", 1: "HomeMinigamePopup", 195: "ProduceSelect"}
        for seq, want in cases.items():
            with self.subTest(seq=seq):
                self.assertEqual(self.read(REC2, OCR2, seq)[0], want)
        _, r = self.read(REC2, OCR2, 43)
        self.assertTrue(r["course_first"])
        self.assertEqual(self.read(REC2, OCR2, 11)[1]["rules_page"], (1, 4))
        self.assertEqual(self.read(REC2, OCR2, 20)[1]["rules_page"], (4, 4))
        for seq in (75, 84, 92, 107):
            _, r = self.read(REC2, OCR2, seq)
            self.assertEqual((r["turn_bar"], r["pass_enabled"]), (True, True), seq)
        for seq in (78, 85, 93):
            self.assertFalse(self.read(REC2, OCR2, seq)[1]["turn_bar"], seq)
        _, r = self.read(REC2, OCR2, 161)
        self.assertTrue(wsp.box_inside(r["next_box"], nav.TARGETS["minigame_result_next"][0]))
        _, r = self.read(REC2, OCR2, 172)
        self.assertTrue(wsp.box_inside(r["produce_box"], nav.TARGETS["minigame_to_produce"][0]))
        _, r = self.read(REC2, OCR2, 131)
        self.assertTrue(wsp.box_inside(r["produce_box"], nav.TARGETS["minigame_to_produce_low"][0]))
        self.assertTrue(r["cheer_notice"])
        self.assertTrue(self.read(REC2, OCR2, 1)[1]["popup_minigame"])
        self.assertTrue(self.read(REC2, OCR2, 195)[1]["cheer"])
        # the user's clicks of the clean pairs lie in the new rects
        for intent, point in (("minigame_daifugo", (655, 358)), ("minigame_course_1", (277, 488)),
                              ("minigame_pass", (549, 448)), ("minigame_pass", (527, 457)),
                              ("minigame_result_next", (677, 611)), ("minigame_to_produce", (651, 581)),
                              ("minigame_to_produce_low", (639, 616)), ("home_minigame", (741, 574)),
                              ("produce_difficulty_middle", (878, 379)), ("produce_difficulty_middle", (920, 379)),
                              ("produce_difficulty_beginner", (660, 397)), ("produce_difficulty_master", (1060, 375)),
                              ("home_produce", (1046, 672)), ("produce_tab_train", (925, 88))):
            x, y, w, h = nav.TARGETS[intent][0]
            self.assertTrue(x <= point[0] <= x + w and y <= point[1] <= y + h, (intent, point))

    def test_home_and_produce_select(self):
        screen, r = self.read(REC3, OCR3, 190)
        self.assertEqual(screen, "Home")
        self.assertTrue(r["home_produce_label"] and r["home_minigame_label"])
        screen, r = self.read(REC3, OCR3, 209)
        self.assertEqual(screen, "ProduceSelectOther")
        self.assertTrue(r["train_tab"])
        screen, r = self.read(REC3, OCR3, 218)
        self.assertEqual((screen, r["difficulty"]), ("ProduceSelect", "beginner"))

    def test_replace_dialog_buttons(self):
        from agent.core import wing_screen as wsc
        frame = self.frame(REC2, OCR2, 744)
        rep = wsc.read_screen("ReplaceDialog", self.classifier.evaluator, frame)["replace"]
        self.assertTrue(wsp.box_inside(rep["ok_box"], nav.TARGETS["replace_ok"][0]))
        self.assertTrue(wsp.box_inside(rep["cancel_box"], nav.TARGETS["replace_dialog_cancel"][0]))

    @unittest.skipUnless(ASSETS.is_file(), "local card reference icons not present")
    def test_unit_fingerprint_on_native_frames(self):
        from PIL import Image
        team = {"P": "1040020080", "S": ["2040050080", "2040060080", "2040230040", "2040040090"],
                "guest": "2040070080"}
        cards = wuc.read_unit_cards(Image.open(REC3 / "frame-000233.png"))
        self.assertEqual(wuc.compare(cards, team)["verdict"], "match")
        other = wuc.read_unit_cards(Image.open(REC3 / "frame-000236.png"))       # unit 6, another team
        self.assertEqual(wuc.compare(other, team)["verdict"], "mismatch")
        demo2 = wuc.read_unit_cards(Image.open(REC2 / "frame-000211.png"))      # same S, other P
        self.assertEqual(wuc.compare(demo2, team)["verdict"], "mismatch")


if __name__ == "__main__":
    unittest.main()
