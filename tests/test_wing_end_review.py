"""W.I.N.G. end of run (2026-10-10, first fully automatic run): opt-in SP spend-down at the last chance, re-observe
after plain advance taps (TE scene skipped by the game), and the 獲得報酬 review (Ex skills, knowhow books, Lv5 保護,
opt-in 再抽選, never 売却). Offline; no game, no input."""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest import mock

from agent.core import knowhow_books as kb
from agent.core import produce_end as pe
from agent.core import wing_nav as nav
from agent.core import wing_screen as wsc
from agent.core import wing_sp as sp

ROOT = Path(__file__).resolve().parent.parent
OCR = ROOT / "dev_assets/private/pending-20261010d/ocr"
LIVE_PNG = ROOT / "dev_assets/private/wing_samples/2026-10-10/20261010T073201859397Z-capture.png"
DEMO3 = ROOT / "dev_assets/private/recordings/wing-demo3-20261006-1643-03"
REWARD_INTENTS = ("reward_tab_ex", "reward_tab_knowhow", "reward_item_1", "reward_item_2", "reward_book_protect",
                  "reward_reroll", "reward_reroll_ok")


def hubfinal_nodes():
    """The HubFinal panel of the 2026-10-10 run (decision log 20261010T183031 #109): Me cap 30, Vo passive 40
    (3ターン以前), Vo Live 50 (unaffordable), Vo cap 50 behind the passive."""
    return [sp.SPNode("me_cap", "cap_up", 30, 50.0, ("Me",)),
            sp.SPNode("vo_passive", "passive", 40, 0.1, ("Vo",)),
            sp.SPNode("vo_live", "live_skill", 50, 2.8, ("Vo",)),
            sp.SPNode("vo_cap", "cap_up", 50, 50.0, ("Vo",), requires=("vo_passive",))]


SKILLS = [sp.LiveSkill("a", "Vo", 3.5), sp.LiveSkill("b", None, 3.0), sp.LiveSkill("c", "Vo", 3.5),
          sp.LiveSkill("d", "Vo", 5.0)]


class SpendDownTests(unittest.TestCase):
    def plan(self, sp_now, **params):
        return sp.plan_sp(hubfinal_nodes(), sp=sp_now, primary="Vo", stat=1303, cap=1351, skills=SKILLS,
                          future_sp=0, last_chance=True, params=params or None)

    def test_live_run_case_is_unchanged_by_default(self):
        plan = self.plan(46)
        self.assertEqual([s.node_id for s in plan.steps], ["vo_passive"])
        self.assertEqual(plan.sp_left, 6)

    def test_default_keeps_leftover_sp(self):
        plan = self.plan(80)
        self.assertEqual([s.node_id for s in plan.steps], ["vo_passive"])
        self.assertEqual(plan.sp_left, 40)                       # Leftover SP is fine
        self.assertNotIn("spend_down_last_chance", plan.reasons)

    def test_opt_in_spends_down_on_other_caps(self):
        plan = self.plan(80, spend_down_last_chance=True)
        self.assertEqual([s.node_id for s in plan.steps], ["vo_passive", "me_cap"])
        self.assertEqual(plan.steps[-1].tier, "spend_down")
        self.assertEqual(plan.sp_left, 10)
        self.assertIn("spend_down_last_chance", plan.reasons)

    def test_opt_in_needs_the_last_chance(self):
        plan = sp.plan_sp(hubfinal_nodes(), sp=80, primary="Vo", stat=1303, cap=1351, skills=SKILLS, future_sp=0,
                          params={"spend_down_last_chance": True})
        self.assertEqual(plan.sp_left, 40)

    def test_spend_down_maximises_spent_sp_and_skips_dead_passives(self):
        nodes = [sp.SPNode("da_cap", "cap_up", 30, 30.0, ("Da",)), sp.SPNode("vi_cap", "cap_up", 50, 30.0, ("Vi",)),
                 sp.SPNode("dead", "passive", 20, 0.0, ())]          # condition unmet -> value 0, never bought
        plan = sp.plan_sp(nodes, sp=55, primary="Vo", stat=1000, cap=1351, future_sp=0, last_chance=True,
                          params={"spend_down_last_chance": True})
        self.assertEqual([s.node_id for s in plan.steps], ["vi_cap"])
        self.assertEqual(plan.sp_left, 5)


class ReobserveTests(unittest.TestCase):
    def test_commu_after_final_may_reach_the_end_flow(self):
        for screen in ("CommuSkip", "RankPyramid"):
            self.assertIn("Evaluation", nav.RULES[screen].expect)
        self.assertTrue(nav.RULES["CommuSkip"].loose)
        self.assertFalse(nav.RULES["FesBirthConfirm"].loose)        # consuming / irreversible flows stay strict
        self.assertFalse(nav.RULES["Review"].loose)

    def test_builder_marks_loose_expect_nodes(self):
        from tools import wing_nav_build as build
        nodes = build.build()
        self.assertTrue(nodes["SC.Wing.Expect.CommuSkip"]["custom_recognition_param"].get("loose"))
        self.assertNotIn("loose", nodes["SC.Wing.Expect.LearnConfirm"]["custom_recognition_param"])
        self.assertIn("SC.Wing.Screen.RewardRerollConfirm", nodes)
        self.assertIn("SC.Wing.Read.Reward.Reroll", nodes)

    def test_reward_actions_are_new_unverified_wing_end_actions(self):
        verified = {"reward_tab_ex", "reward_tab_knowhow", "reward_item_1"}   # live frame 2026-10-10
        for intent in REWARD_INTENTS:
            self.assertEqual(nav.TARGETS[intent][1], intent in verified, intent)
            self.assertEqual(nav.intent_family(intent), "wing-end")
        self.assertEqual(nav.action_id("reward_reroll_ok"), "wing-reward-reroll-confirm")
        # 保護 rect stays clear of the detail pane's 売却 button (x >= 452) and OK stays the only verified end tap
        x, _, w, _ = nav.TARGETS["reward_book_protect"][0]
        self.assertLess(x + w, 452)


def reward(tab, **extra):
    base = {"reward_tab": tab, "texts": [], "dialog": False}
    base.update(extra)
    return base


def book(level, protected=False, partial=False):
    return {"title": "【ひとりじゃない夜のこと】風野灯織", "idol": "風野灯織", "stats": {"vo": 10, "da": 10, "vi": 10, "me": 10},
            "rows": [{"name": "VocalUP", "level": level, "effect": "Vocal+10", "clipped": False}],
            "protected": protected, "max_level": level, "rows_partial": partial}


class RewardReviewTests(unittest.TestCase):
    def runtime(self, verified=True, **end):
        # allow_reward_reroll: the launcher authority (2026-10-11 review P1-7: no re-roll is planned without it).
        rt = nav.WingNavRuntime(params={"end": end} if end else None,
                                options=nav.RunOptions(input_enabled=True, allow_reward_reroll=True))
        rt.begin()
        rt.memory.end = pe.EndCollector()
        patcher = mock.patch.dict(nav.TARGETS, {i: (nav.TARGETS[i][0], verified) for i in REWARD_INTENTS})
        patcher.start()
        self.addCleanup(patcher.stop)
        return rt

    def walk(self, rt, frames):
        out = []
        for screen, r in frames:
            d = rt.decide(screen, r)
            out.append((d.action, d.intent or d.reason))
        return out

    def test_full_review_reads_ex_and_books_and_protects_lv5_then_hands_over(self):
        rt = self.runtime(protect_lv5=True)     # 2026-10-12 (U08): protection is opt-in, default off
        ex = [{"name": "Vocal初期値+10", "idol": "風野灯織", "tier": "bronze", "target": "produce"}]
        steps = self.walk(rt, [
            ("Reward", reward("knowhow", reward_items=1, reward_selected=None, reward_book=None)),
            ("RewardDetail", reward("ex", ex_skills=ex, ex_rows_cut=False)),
            ("Reward", reward("knowhow", reward_items=1, reward_selected=None, reward_book=None)),
            ("RewardDetail", reward("knowhow", reward_items=1, reward_selected=0, reward_book=book(5))),
            ("RewardDetail", reward("knowhow", reward_items=1, reward_selected=0, reward_book=book(5, True))),
        ])
        self.assertEqual(steps[:4], [("click", "reward_tab_ex"), ("click", "reward_tab_knowhow"),
                                     ("click", "reward_item_1"), ("click", "reward_book_protect")])
        self.assertEqual(steps[4], ("stop", "produce_end_review"))
        decision = rt.decide("RewardDetail", reward("knowhow", reward_items=1, reward_selected=0,
                                                    reward_book=book(5, True)))
        payload = decision.detail["payload"]
        summary = payload["summary"]
        self.assertEqual(summary["knowhow_books"][0]["level"], 5)
        self.assertTrue(summary["knowhow_books"][0]["protected"])
        self.assertEqual(summary["ex_skills"][0]["name"], "Vocal初期値+10")
        self.assertEqual(summary["reward_review"]["protected"], [0])
        self.assertIn("knowhow_lv5_protected", [n["key"] for n in payload["notifications"]])
        self.assertTrue(pe.payload_is_safe(payload))

    def test_no_protect_below_lv5_and_no_reroll_by_default(self):
        rt = self.runtime()
        steps = self.walk(rt, [
            ("RewardDetail", reward("ex", ex_skills=[])),
            ("Reward", reward("knowhow", reward_items=1, reward_selected=None, reward_book=None,
                              reroll={"left": 1, "total": 1, "stamina": 30}, stamina_now=300)),
            ("RewardDetail", reward("knowhow", reward_items=1, reward_selected=0, reward_book=book(3),
                                    reroll={"left": 1, "total": 1, "stamina": 30}, stamina_now=300)),
        ])
        self.assertEqual([s[1] for s in steps], ["reward_tab_knowhow", "reward_item_1", "produce_end_review"])

    def test_opt_in_reroll_only_without_lv5_and_bounded(self):
        rt = self.runtime(reroll=True)
        frames = [("RewardDetail", reward("ex", ex_skills=[])),
                  ("Reward", reward("knowhow", reward_items=1, reward_selected=None, reward_book=None)),
                  ("RewardDetail", reward("knowhow", reward_items=1, reward_selected=0, reward_book=book(3),
                                          reroll={"left": 1, "total": 1, "stamina": 30}, stamina_now=300))]
        steps = self.walk(rt, frames)
        self.assertEqual(steps[-1], ("click", "reward_reroll"))
        # the confirmation: refused at the gate without the launcher's reroll authority
        d = rt.decide("RewardRerollConfirm", {"reroll_dialog": {"stamina": 30}})
        self.assertEqual(d.intent, "reward_reroll_ok")
        authorized = rt.options
        rt.options = nav.RunOptions(input_enabled=True)          # the launcher authority withdrawn
        self.assertEqual(rt.act("RewardRerollConfirm", d), (False, "reroll_not_enabled"))
        rt.options = authorized
        self.assertEqual(rt.memory.reward_review["rerolls"], 0)
        # after one re-roll the count is 0: no second one
        rt.memory.reward_review["rerolls"] = 1
        rt.memory.reward_review["pending"] = None
        rt.memory.reward_review["books"] = {0: {**book(3), "index": 0}}
        d = rt.decide("RewardDetail", reward("knowhow", reward_items=1, reward_selected=0, reward_book=book(3),
                                             reroll={"left": 0, "total": 1, "stamina": 30}, stamina_now=270))
        self.assertEqual(d.reason, "produce_end_review")
        self.assertTrue(any(n.startswith("reroll_skipped") for n in rt.memory.reward_review["notes"]))

    def test_reroll_never_with_a_lv5_book(self):
        rt = self.runtime(reroll=True, protect_lv5=False)
        steps = self.walk(rt, [
            ("RewardDetail", reward("ex", ex_skills=[])),
            ("Reward", reward("knowhow", reward_items=1, reward_selected=None, reward_book=None)),
            ("RewardDetail", reward("knowhow", reward_items=1, reward_selected=0, reward_book=book(5),
                                    reroll={"left": 1, "total": 1, "stamina": 30}, stamina_now=300))])
        self.assertEqual(steps[-1], ("stop", "produce_end_review"))
        self.assertIn("reroll_skipped:lv5_book", rt.memory.reward_review["notes"])

    def test_unplanned_reroll_dialog_stops(self):
        rt = self.runtime(reroll=True)
        self.assertEqual(rt.decide("RewardRerollConfirm", {"reroll_dialog": {"stamina": 30}}).reason,
                         "reroll_dialog_unplanned")

    def test_unverified_targets_end_the_review_with_a_note(self):
        rt = self.runtime(verified=False)
        d = rt.decide("Reward", reward("knowhow", reward_items=1))
        self.assertEqual((d.action, d.reason), ("stop", "produce_end_review"))
        self.assertIn("target_unverified:reward_tab_ex", rt.memory.reward_review["notes"])

    def test_unconfirmed_tap_stops_without_retry(self):
        rt = self.runtime()
        rt.decide("Reward", reward("knowhow", reward_items=1))               # -> tab_ex
        for _ in range(nav.REWARD_UNSETTLED):
            self.assertEqual(rt.decide("Reward", reward("knowhow", reward_items=1)).action, "wait")
        d = rt.decide("Reward", reward("knowhow", reward_items=1))
        self.assertEqual((d.action, d.reason), ("stop", "reward_tab_unconfirmed"))

    def test_read_off_hands_over_at_once(self):
        rt = self.runtime(read=False)
        self.assertEqual(rt.decide("Reward", reward("knowhow", reward_items=1)).reason, "produce_end_review")

    def test_press_ok_option_continues_after_a_complete_review(self):
        rt = self.runtime(press_ok=True)
        steps = self.walk(rt, [
            ("RewardDetail", reward("ex", ex_skills=[], ex_rows_cut=False)),
            ("Reward", reward("knowhow", reward_items=1, reward_selected=None, reward_book=None)),
            ("RewardDetail", reward("knowhow", reward_items=1, reward_selected=0, reward_book=book(3)))])
        self.assertEqual(steps, [("click", "reward_tab_knowhow"), ("click", "reward_item_1"), ("click", "reward_ok")])
        self.assertNotIn("missing", rt.memory.reward_review)

    def test_press_ok_option_stays_on_the_page_when_the_reading_is_partial(self):
        # ANN-29: books 2-4 (or 保護) not readable -> no OK; hand over with the missing parts for the user.
        # EX skills are no longer required before OK.
        rt = self.runtime(verified=False, press_ok=True)
        d = rt.decide("Reward", reward("knowhow", reward_items=1))
        self.assertEqual((d.action, d.reason), ("stop", "produce_end_review"))
        self.assertEqual(rt.memory.reward_review["missing"], ["knowhow_books"])
        self.assertEqual(rt.end_payload["reward_missing"], ["knowhow_books"])
        self.assertEqual(rt.memory.end.values["reward_review"]["missing"], ["knowhow_books"])

    def test_press_ok_ignores_clipped_ex_list_and_book_rows(self):
        # 2026-10-27 live (W.I.N.G. 连跑): the EX list and the book rows needed scrolling; OK no longer waits for them.
        rt = self.runtime(press_ok=True)
        rt.memory.reward_review = {"ex": [], "ex_partial": True, "books": {0: {**book(5), "index": 0, "rows_partial": True}},
                                   "items": 1, "taps": 0, "protected": [], "rerolls": 0, "reroll_cap": None,
                                   "pending": None, "unsettled": 0, "notes": [], "complete": False, "done": True}
        self.assertEqual(rt._reward_missing(), [])
        rt = self.runtime(press_ok=False)
        rt.memory.reward_review = {"ex": None, "books": {}, "items": 1}
        self.assertEqual(rt._reward_missing(), ["ex_skills", "book_1"])     # without OK the full list is still reported

    def test_press_ok_option_partial_books_and_unprotected_lv5_are_missing(self):
        rt = self.runtime(press_ok=True, protect_lv5=True)
        rt.memory.reward_review = {"ex": [], "ex_partial": False, "books": {0: {**book(5), "index": 0}}, "items": 3,
                                   "taps": 0, "protected": [], "rerolls": 0, "reroll_cap": None, "pending": None,
                                   "unsettled": 0, "notes": [], "complete": False, "done": True}
        self.assertEqual(rt._reward_missing(), ["book_2", "book_3", "book_1_protect"])
        d = rt.decide("RewardDetail", reward("knowhow", reward_items=3, reward_selected=0, reward_book=book(5)))
        self.assertEqual((d.action, d.reason), ("stop", "produce_end_review"))

    def test_press_ok_with_reading_off_keeps_the_user_choice(self):
        rt = self.runtime(press_ok=True, read=False)
        self.assertEqual(rt._reward_missing(), [])


class KnowhowStoreTests(unittest.TestCase):
    def test_reward_book_goes_to_pending(self):
        inv = kb.KnowhowInventory()
        self.assertEqual(kb.ingest_reward_books(inv, [book(5, True), {"title": None}]), 1)
        rec = inv.pending[0]
        self.assertEqual((rec.idol, rec.source, rec.protected), ("風野灯織", "produce_end", True))
        self.assertEqual(rec.stats, {"vo": 10, "da": 10, "vi": 10, "me": 10})
        self.assertIn("knowhow_count", rec.unknowns)
        self.assertFalse(rec.complete)


def _frame(name):
    path = OCR / f"{name}.json"
    return [(d["text"], tuple(d["box"])) for d in json.loads(path.read_text(encoding="utf-8"))]


@unittest.skipUnless((OCR / "reward-live-20261010T073201.json").is_file() and LIVE_PNG.is_file()
                     and (DEMO3 / "frame-000658.png").is_file(), "private 獲得報酬 frames / offline OCR not present")
class RewardFrameTests(unittest.TestCase):
    """Offline project OCR (tools/explore_step.FrameOcr) of the live 獲得報酬 frame and third-demo #651/#658/#688."""

    def read(self, name, png):
        from tests.maa_probe import rgb
        return wsc.read_end("Reward", _frame(name), image=rgb(png))

    def test_live_frame_knowhow_tab_one_book_none_selected(self):
        r = self.read("reward-live-20261010T073201", LIVE_PNG)
        self.assertEqual((r["reward_tab"], r["stamina"], r["reward_items"], r["reward_selected"], r["reward_book"]),
                         ("knowhow", "300/300", 1, None, None))
        self.assertEqual(pe.end_screen_gate("Reward", r["texts"])["action"], "handover")   # page text is not a dialog

    def test_demo_detail_pane_reads_the_whole_book(self):
        r = self.read("demo3-000658", DEMO3 / "frame-000658.png")
        b = r["reward_book"]
        self.assertEqual(r["reward_selected"], 0)
        self.assertEqual((b["title"], b["idol"]), ("【あなたと、月の満ちる頃】月岡恋鐘", "月岡恋鐘"))
        self.assertEqual(b["stats"], {"vo": 10, "da": 10, "vi": 10, "me": 10})
        self.assertEqual(b["rows"], [{"name": "VisualUP", "level": 1, "effect": "Visual+10", "clipped": False}])
        self.assertEqual((b["protected"], b["max_level"], b["rows_partial"]), (False, 1, False))
        rec = kb.book_from_reward(b)
        self.assertEqual(rec.rows[0].name, "VisualUP")

    def test_demo_ex_tab_list_and_cut(self):
        r = self.read("demo3-000688", DEMO3 / "frame-000688.png")
        self.assertEqual(r["reward_tab"], "ex")
        self.assertIn("プロデュース開始時に絆+3", [e["name"] for e in r["ex_skills"]])
        self.assertTrue(r["ex_rows_cut"])                          # 9 skills: the list scrolls
        self.assertFalse(self.read("demo3-000651", DEMO3 / "frame-000651.png").get("ex_rows_cut"))

    def test_reroll_button_reading(self):
        self.assertEqual(wsc.parse_reward_reroll([("再抽選1/1", (921, 629, 143, 38)), ("消費スタミナ：30", (919, 669, 146, 23))]),
                         {"left": 1, "total": 1, "stamina": 30})
        self.assertIsNone(wsc.parse_reward_reroll([("再抽選", (921, 630, 89, 34))]))

    def test_targets_lie_on_the_live_widgets(self):
        tx, ty, tw, th = nav.TARGETS["reward_item_1"][0]
        self.assertTrue(738 <= tx and tx + tw <= 844 and 197 <= ty and ty + th <= 303)
        for intent, (bx, by, bw, bh) in (("reward_tab_ex", (20, 90, 165, 75)), ("reward_tab_knowhow", (20, 177, 165, 75)),
                                         ("reward_reroll", (912, 625, 165, 72))):
            x, y, w, h = nav.TARGETS[intent][0]
            self.assertTrue(bx <= x and x + w <= bx + bw and by <= y and y + h <= by + bh, intent)


if __name__ == "__main__":
    unittest.main()
