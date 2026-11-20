"""B6 Business (営業), sampling first: rules, Pipeline contract and offline replay through tools/run_tasks.py.

Sample mode replays use stand-ins (Fes main for "a non-Home page with the back key", the dimmed-Home synthetic for
"a pop-up over it") plus the native business top of 2026-10-03 05:50 (three CLEAR slots). B6b ``sample_collect_one``
replays use that native frame, the 2026-10-01 Tea Time and error-1012 captures (converted to PNG) and a synthetic
business top whose slot 1 shows the Tea Time card (stand-in for "slot 1 no longer CLEAR after the claim").
"""
import json
import os
import datetime as dt
import shutil
import unittest
from pathlib import Path
from unittest import mock

from agent.core import business_task as bt
from tools import run_tasks as launcher
from tests.test_task_launcher import HAVE_MAA, TABLE, pipeline_nodes, private_ready

ROOT = Path(__file__).resolve().parent.parent
PRIVATE = ROOT / "dev_assets/private"
NOW = dt.datetime(2026, 10, 3, 6, 0, tzinfo=dt.timezone.utc)
P = "dev_assets/private/"
HOME = P + "manual-capture-kept-20261002/home-20261003-nobadge.png"  # 営業 button 「完了まで あと7:15」
HOME_DIMMED = P + "manual-capture-kept-20261002/home-20261003-nobadge-dimmed-synthetic.png"
STAND_IN = P + "grade-run-03c-entry-frames/frame-008.png"  # non-Home page with the undimmed lower-left back key
# Live Home 2026-10-03 15:22: plain 営業 icon with a red 「3」 badge, no timer (the first label crop scored 0.933 here
# and the task stopped at the entry); and the 06:21 Home with the 営業 timer 6:09 and other badges.
HOME_BADGE = P + "manual-capture-kept-20261002/home-20261003-business-badge.png"
HOME_0621 = P + "manual-capture-kept-20261002/home-20261003-0621-timer.png"
FRAMES = (HOME, HOME_DIMMED, STAND_IN, HOME_BADGE, HOME_0621)
B = P + "business-native-20261003-frames/"
TOP_CLEAR = B + "frame-003.png"                    # native business top: 3 CLEAR slots, 一括営業 grey, 一括完了 pink
TOP_SLOT1_TEA = B + "slot1-teatime-synthetic.png"  # frame-003 with slot 1 replaced by a Tea Time card (not CLEAR)
LIVE_20261012 = P + "run_logs/samples/business-top-20261012T012554057901Z/"  # home + 3 Tea Time tops (live)
TOP_TEATIME = B + "teatime-20261001.png"           # 2026-10-01: all three slots in Tea Time (営業完了 smaller)
ERROR_1012 = B + "error-1012-20261001.png"         # 2026-10-01: エラー / エラーコード: 1012 after 一括完了
COLLECT = {"营业模式": "采样并收取一个（sample_collect_one）"}
B6B_FRAMES = (TOP_CLEAR, TOP_SLOT1_TEA, TOP_TEATIME, ERROR_1012)
# B6d: the settled WORK REPORT after 営業完了 of slot 1 (live 2026-10-03 19:10, copied from run_logs/samples/
# business-top-20261003T091040428732Z) and a synthetic business top whose slot-1 card is blank (stand-in for an empty
# slot: no native empty-slot sample exists yet). The dispatch screen has no sample either: STAND_IN (a non-business
# page with the undimmed lower-left back key) stands in for it.
REPORT = B + "claim1/frame-020.png"
REPORT_ANIM = B + "claim1/frame-006.png"           # report counting up: no 「Touch to Next」 yet (guard false)
# B6e: the NATIVE business top with slot 1 empty (「未選択」 / 「営業先選択」, live 2026-10-03 20:07, browser_tasks/
# 20261003-200711-267631) replaces the B6d blank synthetic card, which no longer counts as empty (no positive text).
TOP_EMPTY1 = B + "bulk/top-slot1-empty.png"
TOP_BLANK1 = B + "slot1-empty-synthetic.png"
DISPATCH = {"营业模式": "采样派出页（sample_dispatch_open）"}
B6D_FRAMES = (REPORT, REPORT_ANIM, TOP_EMPTY1, TOP_BLANK1)
# B6e synthetic stand-ins (marked as such; no native sample yet): all three slots empty with 一括営業 white and 一括完了
# grey (slot-1 empty card copied to slots 2/3, grey 一括営業 pixels pasted over 一括完了), and "nothing left to do"
# (Tea Time cards, both bulk buttons grey) for the final verification.
# B6g: the all-empty top is now NATIVE (live 2026-10-03 21:25, browser_tasks/20261003-212520-799739): three 「未選択」,
# 一括営業 white, 一括完了 disabled = dark muted pink (not grey). The "done" stand-in keeps Tea Time cards but uses the
# native disabled buttons (grey 一括営業 from frame-003, dark-pink 一括完了 from all-empty).
TOP_ALL_EMPTY = B + "all-empty.png"
TOP_DONE = B + "bulk/synthetic-done-native-buttons.png"
# Batch 4: 一括営業 is off by default; the B6e-B6h replays exercise the explicit "verify" mode with a team. These synthetic
# / native frames have no confirmation dialog after 一括営業, so a proven dispatch ends dispatched_binding_unverified.
BULK_DEFAULT = {"营业模式": "一括收放（默认）"}
BULK = {**BULK_DEFAULT, "营业一括派出": "核对后派出（需填写队伍）", "营业队伍": "芹沢あさひ"}
B6E_FRAMES = (TOP_EMPTY1, TOP_ALL_EMPTY, TOP_DONE)
# B6f: the paged WORK REPORT after 一括完了 (live 2026-10-03 20:50, run_logs/samples/business-top-20261003T105038071911Z
# copied to bulk/run2050/): page 1 with 「Touch to Next」 (010), page 2 counting up (014), page 2 with ‹ › arrows and
# 「Touch to Next」 (017), then the closing screen with 「次へ」 and no Touch to Next (020; also browser_tasks/
# 20261003-205131-734283 = bulk/report-paged-p2.png, the screen the live host still shows).
PAGE1 = B + "bulk/run2050/frame-010.png"
PAGE2_ANIM = B + "bulk/run2050/frame-014.png"
PAGE2 = B + "bulk/run2050/frame-017.png"
PAGED = B + "bulk/report-paged-p2.png"
B6F_FRAMES = (PAGE1, PAGE2_ANIM, PAGE2, PAGED)
GIFT = P + "wing_samples/2026-10-10/20261010T120957833661Z-capture.png"  # 「ファンからのプレゼント」 (private)
REWARD = P + "wing_samples/2026-10-10/20261010T121256455696Z-capture.png"  # 獲得報酬 (Exスキル, OK / 再営業へ)
SELECT = P + "wing_samples/2026-10-10/20261010T121541223722Z-capture.png"  # 営業アイドル選択 (3 prefilled 12 h slots)


class BusinessRuleTests(unittest.TestCase):
    def test_config_defaults_and_fixed_fields(self):
        config = bt.BusinessConfig.from_params({})
        self.assertEqual((config.mode, config.slots, config.business, config.team, config.duration_hours, config.gift),
                         ("bulk_default", 1, "雑誌インタビュー", "", 12, False))
        self.assertEqual(bt.BusinessConfig.from_params({"mode": "collect_and_dispatch", "slots": 3}).slots, 3)
        for bad in ({"mode": "auto"}, {"slots": 0}, {"slots": 4}, {"slots": True}, {"business": "ミニライブ"},
                    {"duration_hours": 6}, {"gift": True}, {"soda": 1}, {"team": 5}):
            with self.assertRaises(ValueError, msg=bad):
                bt.BusinessConfig.from_params(bad)

    def slots(self, *states, **extra):
        return [bt.Slot(i + 1, s, remaining="あと7:15" if s == "in_progress" else None, **extra)
                for i, s in enumerate(states)]

    def test_plan_claims_first_never_touches_running_slots(self):
        config = bt.BusinessConfig(mode="collect_and_dispatch", team="ユニットA")
        plan = bt.plan_slots(config, self.slots("completed", "in_progress", "empty"))
        self.assertEqual((plan.claims, plan.dispatches, plan.stop), ((1,), (), ()))  # re-observe before dispatching
        plan = bt.plan_slots(config, self.slots("empty", "in_progress", "empty"))
        self.assertEqual(plan.dispatches, (1,))  # default: one slot
        plan = bt.plan_slots(bt.BusinessConfig(team="ユニットA", slots=3), self.slots("empty", "in_progress", "empty"))
        self.assertEqual(plan.dispatches, (1, 3))  # the in-progress slot is never overwritten
        running = [bt.Slot(1, "in_progress", "雑誌インタビュー", "ユニットA", "あと3:00"), bt.Slot(2, "empty"), bt.Slot(3, "empty")]
        self.assertEqual(bt.plan_slots(config, running).dispatches, ())  # the configured business already runs

    def test_plan_stops_on_unknowns_and_uncertain_results(self):
        config = bt.BusinessConfig(mode="collect_and_dispatch", team="ユニットA")
        self.assertIn("slot_state_unknown", bt.plan_slots(config, self.slots("empty", "unknown")).stop)
        self.assertIn("slot_list_incomplete", bt.plan_slots(config, [bt.Slot(2, "empty")]).stop)
        self.assertIn("in_progress_without_timer", bt.plan_slots(config, [bt.Slot(1, "in_progress")]).stop)
        self.assertEqual(bt.plan_slots(bt.BusinessConfig(), self.slots("empty")).stop, ("team_not_configured",))
        state = bt.log_state([{"event": "claim_intent"}, {"event": "error_1012"}])
        self.assertEqual(state.uncertain, 1)
        self.assertIn("uncertain_result_today_never_retried", bt.plan_slots(config, self.slots("completed"), state).stop)
        self.assertEqual(bt.log_state([{"event": "dispatch_intent"}]).uncertain, 1)  # no conclusion = uncertain
        self.assertEqual(bt.log_state([{"event": "claim_intent"}, {"event": "claimed"}]), bt.LogState(claimed=1))

    def test_claim_rules_first_clear_slot_and_uncertain_today(self):
        self.assertEqual(bt.BusinessConfig.from_params({"mode": "sample_collect_one"}).mode, "sample_collect_one")
        self.assertEqual(bt.first_claim(("completed",) * 3), (1, None))
        self.assertEqual(bt.first_claim(("other", "completed", "completed")), (2, None))
        self.assertEqual(bt.first_claim(("other",) * 3), (None, "no_completed_slot"))
        # A first CLEAR slot that is uncertain today stops everything; a later slot is never claimed instead.
        self.assertEqual(bt.first_claim(("completed",) * 3, frozenset({1})), (None, "uncertain_slot_today"))
        self.assertEqual(bt.first_claim(("other", "completed", "other"), frozenset({1})), (2, None))
        self.assertEqual(bt.first_claim(("completed",), unscoped=True), (None, "uncertain_result_today"))
        events = [{"event": "claim_intent", "slot": 1}, {"event": "claimed", "slot": 1},
                  {"event": "claim_intent", "slot": 2}, {"event": "claim_uncertain", "slot": 2},
                  {"event": "claim_intent", "slot": 3}]
        self.assertEqual(bt.uncertain_slots(events), (frozenset({2, 3}), False))  # 3: intent without conclusion
        self.assertEqual(bt.uncertain_slots([{"event": "error_1012"}]), (frozenset(), True))
        self.assertEqual(bt.uncertain_slots([{"event": "claim_intent"}]), (frozenset(), True))
        self.assertEqual(bt.log_state(events).uncertain, 2)

    def test_b6d_rules_empty_slot_report_and_takeover(self):
        self.assertEqual(bt.BusinessConfig.from_params({"mode": "sample_dispatch_open"}).mode, "sample_dispatch_open")
        self.assertEqual(bt.classify_slot(True, True, []), "completed")
        self.assertEqual(bt.classify_slot(False, False, []), "other")  # B6e: blank card is not positive evidence
        self.assertEqual(bt.classify_slot(False, False, ["未選択", "営業先選択"]), "empty")
        self.assertEqual(bt.classify_slot(False, True, []), "other")  # CLEAR or 営業完了 anchor alone
        for busy in (["ティータイム残り", "1時間36分"], ["あと7:15"], ["雑誌インタビュー"], ["解放", "ジュエル"], ["3"]):
            self.assertEqual(bt.classify_slot(False, False, busy), "other", busy)
        self.assertEqual(bt.classify_slot(False, False, ["営業を選択"]), "other")

    def test_tea_time_rules(self):
        live = ["雑誌インタビュー", "モチベーションボーナスー", "ティータイム残り", "営業完了", "2時間33分", "TeaTime"]
        self.assertEqual(bt.classify_slot(False, False, live), "tea_time")
        self.assertEqual(bt.classify_slot(False, True, live), "other")  # a CLEAR / label anchor is never Tea Time
        self.assertEqual(bt.classify_slot(False, False, ["ティータイム残り", "1時間36分"]), "other")  # no 営業完了
        self.assertEqual(bt.tea_time_left_minutes([live, [], ["ティータイム残り 45分", "営業完了"]]), 153)
        self.assertIsNone(bt.tea_time_left_minutes([["あと7:15"]]))
        T, C, E, O = "tea_time", "completed", "empty", "other"
        self.assertEqual(bt.bulk_decide((T, T, T), "enabled", "disabled"), (None, "tea_time_wait"))
        self.assertEqual(bt.bulk_decide((C, T, T), "enabled", "disabled"), (None, "tea_time_wait"))  # never mixed
        self.assertEqual(bt.bulk_decide((T, T, T), "disabled", "disabled"), (None, "nothing_to_do"))
        self.assertEqual(bt.bulk_decide((O, O, O), "enabled", "disabled"), (None, "slots_unverified"))
        self.assertIn("tea_time_wait", bt.RETURN_FAILURE_OUTCOMES)
        self.assertEqual(bt.classify_slot(False, False, ["営業先選択"]), "empty")
        self.assertEqual(bt.first_empty(("completed", "empty", "empty")), (2, None))
        self.assertEqual(bt.first_empty(("completed", "other", "completed")), (None, "no_empty_slot"))
        self.assertEqual(bt.first_empty(("empty", "empty"), frozenset({1})), (None, "uncertain_slot_today"))
        self.assertEqual((bt.report_number("500"), bt.report_number("237,500人"), bt.report_number(None)),
                         (500, 237500, None))
        live = [{"event": "claim_intent", "slot": 1}, {"event": "claim_uncertain", "slot": 1}]  # 2026-10-03 19:10
        self.assertEqual(bt.takeover_slot(live), 1)
        self.assertIsNone(bt.takeover_slot([]))
        self.assertIsNone(bt.takeover_slot(live + [{"event": "claim_intent", "slot": 2}]))  # two uncertain slots
        self.assertIsNone(bt.takeover_slot(live + [{"event": "error_1012"}]))               # unscoped
        resolved = live + [{"event": "work_report", "slot": 1},
                           {"event": "claimed", "slot": 1, "resolved": True}]
        self.assertEqual(bt.uncertain_slots(resolved), (frozenset(), False))
        self.assertEqual(bt.log_state(resolved), bt.LogState(claimed=1))
        self.assertIsNone(bt.takeover_slot(resolved))
        # A plain ``claimed`` (no resolved flag) never clears an earlier claim_uncertain of the same slot.
        self.assertEqual(bt.uncertain_slots(live + [{"event": "claimed", "slot": 1}]), (frozenset({1}), False))

    def test_b6e_bulk_rules(self):
        E, C, O = "empty", "completed", "other"
        self.assertEqual(bt.BusinessConfig.from_params({}).mode, "bulk_default")
        # 一括完了 first whenever a slot is CLEAR (and the button is pink); never twice in one run.
        self.assertEqual(bt.bulk_decide((E, C, C), "enabled", "enabled"), ("complete", None))
        self.assertEqual(bt.bulk_decide((C, C, C), "enabled", "disabled"), ("complete", None))
        self.assertEqual(bt.bulk_decide((C, C, C), "disabled", "disabled"), (None, "bulk_button_unavailable"))
        self.assertEqual(bt.bulk_decide((C, O, O), "enabled", "x", {"complete"}), (None, "bulk_complete_incomplete"))
        self.assertEqual(bt.bulk_decide((C, C, O), "enabled", "x", blocked={2}), (None, "uncertain_slot_today"))
        # then 一括営業 when a slot is positively empty and the button is white
        self.assertEqual(bt.bulk_decide((E, E, E), "disabled", "enabled", {"complete"}), ("dispatch", None))
        self.assertEqual(bt.bulk_decide((E, O, O), "disabled", "unknown"), (None, "bulk_button_unavailable"))
        self.assertEqual(bt.bulk_decide((E, O, O), "disabled", "enabled", {"dispatch"}),
                         (None, "bulk_dispatch_incomplete"))
        # success only with nothing left: no CLEAR, no empty, both buttons grey
        self.assertEqual(bt.bulk_decide((O, O, O), "disabled", "disabled", {"complete", "dispatch"}),
                         (None, "collected_and_dispatched"))
        self.assertEqual(bt.bulk_decide((O, O, O), "disabled", "disabled"), (None, "nothing_to_do"))
        self.assertEqual(bt.bulk_decide((O, O, O), "enabled", "disabled"), (None, "slots_unverified"))  # Tea Time
        self.assertEqual(bt.bulk_decide((O, O, O), "unknown", "disabled"), (None, "slots_unverified"))
        # pending presses: intent -> uncertain until proven, refused or resolved by a later stable top
        intent = {"event": "bulk_intent", "action": "complete"}
        self.assertEqual(bt.bulk_pending([intent]), {"complete": "intent_without_result"})
        for close in ("bulk_completed", "bulk_refused", "bulk_resolved"):
            self.assertEqual(bt.bulk_pending([intent, {"event": close, "action": "complete"}]), {})
        dispatch = [{"event": "bulk_intent", "action": "dispatch"},
                    {"event": "bulk_uncertain", "action": "dispatch", "reason": "dialog_after_press"}]
        self.assertEqual(bt.bulk_pending(dispatch), {"dispatch": "dialog_after_press"})
        self.assertEqual(bt.uncertain_slots(dispatch), (frozenset(), True))  # blocks single claims today
        self.assertEqual(bt.uncertain_slots(dispatch, include_bulk=False), (frozenset(), False))
        self.assertEqual(bt.log_state(dispatch).uncertain, 1)
        self.assertEqual(bt.bulk_resolution({"dispatch": "x"}, (O, O, O)), (("dispatch",), None))
        self.assertEqual(bt.bulk_resolution({"dispatch": "x"}, (E, O, O)), ((), "uncertain_result_today"))
        self.assertEqual(bt.bulk_resolution({"complete": "x"}, (E, E, O)), (("complete",), None))
        self.assertEqual(bt.bulk_resolution({"complete": "x"}, (C, E, O)), ((), "uncertain_result_today"))
        # a refused tap closes the claim intent (nothing was sent); errors still block the whole day
        refused = [{"event": "claim_intent", "slot": 2}, {"event": "claim_refused", "slot": 2}]
        self.assertEqual(bt.uncertain_slots(refused), (frozenset(), False))
        self.assertEqual(bt.log_state(refused).uncertain, 0)
        # 2026-10-03 10:05: the refused tap was logged claim_uncertain before B6e; a claim_refused for it unblocks slot 2.
        live = [{"event": "claim_intent", "slot": 2}, {"event": "claim_uncertain", "slot": 2},
                {"event": "claim_refused", "slot": 2}]
        self.assertEqual(bt.uncertain_slots(live), (frozenset(), False))
        self.assertEqual(bt.uncertain_slots([{"event": "business_error"}], include_bulk=False), (frozenset(), True))
        self.assertEqual(bt.dialog_forbidden(["スタミナを回復しますか", "ジュエル"]), ["ジュエル", "スタミナ", "回復"])
        self.assertEqual(bt.dialog_forbidden(["一括営業しますか？"]), [])

    def test_pipeline_contract(self):
        nodes = pipeline_nodes()
        self.assertNotEqual(nodes["SC.Business.Entry"].get("custom_action"), "SC.FailTask")
        open_node, back = nodes["SC.Business.OpenBusiness"], nodes["SC.Business.Return"]
        for node, action in ((open_node, "home-business"), (back, "nav-back")):
            self.assertEqual((node["enabled"], node["max_hit"], node["target"]),
                             (False, 1, list(TABLE.get(action).rect)))
            self.assertFalse(TABLE.get(action).consuming)
        self.assertEqual(open_node["all_of"], ["SC.Common.Anchor.Home.FesText", "SC.Common.Variant.HomeMission",
                                               "SC.Common.Color.HomeUndimmed", "SC.Business.Anchor.Home.Label",
                                               "SC.Business.Plan.Open"])
        self.assertEqual(nodes["SC.Business.HomeClean"]["any_of"], nodes["SC.Common.HomeClean"]["any_of"])
        self.assertEqual(back["all_of"], ["SC.Business.BackKey.Ready", "SC.Business.Check.Return"])
        self.assertIn("SC.Common.Color.BackUndimmed", nodes["SC.Business.BackKey.Ready"]["all_of"])
        main = nodes["SC.Business.Main"]["next"]
        # While sampling nothing that can click (pop-up closer, back navigation) comes before the read-only sampler.
        self.assertLess(main.index("[JumpBack]SC.Business.Sample"), main.index("[JumpBack]SC.Popup.CloseKnown"))
        self.assertLess(main.index("SC.Business.ReturnBlocked"), main.index("[JumpBack]SC.Popup.CloseKnown"))
        self.assertEqual(main[0], "SC.Business.Halt")
        clicks = {n for n, v in nodes.items() if n.startswith("SC.Business.") and v.get("action") == "Click"}
        self.assertEqual(clicks, {"SC.Business.OpenBusiness", "SC.Business.Return", "SC.Business.Claim.Slot1",
                                  "SC.Business.Claim.Slot2", "SC.Business.Claim.Slot3", "SC.Business.Report.Next",
                                  "SC.Business.Open.Slot1", "SC.Business.Open.Slot2", "SC.Business.Open.Slot3",
                                  "SC.Business.DispatchBack", "SC.Business.Bulk.Complete", "SC.Business.Bulk.Dispatch",
                                  "SC.Business.Bulk.ReportNext", "SC.Business.Report.PageNext", "SC.Business.Reward.Redispatch",
                                  "SC.Business.Select.Decide", "SC.Business.Select.Back"})
        # B6d: WORK REPORT next (inside the 「Touch to Next」 glyphs, far below the 詳細 buttons), empty-slot open (card
        # body of each slot) and one back key on the dispatch screen; all non-consuming, frame-bound, bounded hits.
        report_next = TABLE.get("business-report-next")
        x, y, w, h = report_next.rect
        self.assertTrue(455 <= x and x + w <= 820 and 640 <= y and y + h <= 682 and y > 560, report_next.rect)
        self.assertEqual(nodes["SC.Business.Report.Next"]["all_of"], ["SC.Business.Report.Guard",
                                                                     "SC.Business.Plan.ReportNext"])
        self.assertEqual(nodes["SC.Business.Report.Next"]["max_hit"], 1)
        self.assertEqual(nodes["SC.Business.Report.Guard"]["all_of"], [
            "SC.Business.Anchor.Report.Title", "SC.Business.Anchor.Report.Clear", "SC.Business.Anchor.Report.Labels",
            "SC.Business.Anchor.Report.TouchNext"])
        cards = {1: (130, 85, 487, 585), 2: (511, 85, 868, 585), 3: (892, 85, 1250, 585)}
        for k, (x0, y0, x1, y1) in cards.items():
            action, node = TABLE.get(f"business-slot-open-{k}"), nodes[f"SC.Business.Open.Slot{k}"]
            x, y, w, h = action.rect
            self.assertTrue(x0 <= x and y0 <= y and x + w <= x1 and y + h <= y1, (k, action.rect))
            self.assertEqual(node["all_of"], ["SC.Business.Top.Guard", f"SC.Business.Plan.Open{k}"])
            self.assertEqual((node["enabled"], node["max_hit"], node["target"]), (False, 1, list(action.rect)))
        dispatch_back = nodes["SC.Business.DispatchBack"]
        self.assertEqual((dispatch_back["max_hit"], dispatch_back["target"], dispatch_back["all_of"]),
                         (1, list(TABLE.get("nav-back").rect), ["SC.Business.BackKey.Ready",
                                                                "SC.Business.Dispatch.Page",
                                                                "SC.Business.Check.DispatchBack"]))
        # B6h review #3: positive dispatch-page evidence (custom check; no native sample yet -> never true).
        self.assertEqual(nodes["SC.Business.Dispatch.Page"]["custom_recognition_param"], {"stage": "dispatch_page"})
        self.assertIn("SC.Business.DispatchBack", TABLE.get("nav-back").nodes)
        for name in ("Report.Next", "Open.Slot1", "Open.Slot2", "Open.Slot3", "DispatchBack"):
            action = TABLE.get(TABLE.node_actions()[f"SC.Business.{name}"])
            self.assertFalse(action.consuming, name)
            self.assertEqual(nodes[f"SC.Business.{name}"]["target"], list(action.rect))
        self.assertEqual([a.id for a in TABLE.actions if a.family == "business"], [
            "home-business", "business-slot-complete-1", "business-slot-complete-2", "business-slot-complete-3",
            "business-report-next", "business-slot-open-1", "business-slot-open-2", "business-slot-open-3",
            "business-bulk-complete", "business-bulk-dispatch", "business-report-next-page",
            "business-reward-redispatch", "business-dispatch-decide"])
        self.assertIsNone(launcher.LIVE_TASK_CLICK_CAPS["Business"])   # no click caps
        # B6f: 「次へ」 of the paged WORK REPORT, only on the paged guard (no Touch to Next); 「Touch to Next」 only on the
        # Report.Guard variant (its TouchNext anchor), never on 次へ.
        page = TABLE.get("business-report-next-page")
        x, y, w, h = page.rect
        self.assertTrue(1097 <= x and 628 <= y and x + w <= 1258 and y + h <= 699, page.rect)
        self.assertFalse(page.consuming)
        page_node = nodes["SC.Business.Report.PageNext"]
        self.assertEqual((page_node["enabled"], page_node["max_hit"], page_node["target"], page_node["all_of"]),
                         (False, 3, list(page.rect), ["SC.Business.Report.AnyPage", "SC.Business.Plan.PageNext"]))
        self.assertEqual(nodes["SC.Business.Report.AnyPage"]["any_of"],
                         ["SC.Business.Report.Paged", "SC.Business.Report.Gift"])
        # 2026-10-10: 「ファンからのプレゼント」 page = title + 次へ templates (+ banner OCR in the Agent); never Touch to Next.
        self.assertEqual(nodes["SC.Business.Report.Gift"]["all_of"],
                         ["SC.Business.Anchor.Report.Title", "SC.Business.Anchor.Report.NextButton"])
        self.assertEqual(nodes["SC.Business.Report.Paged"]["all_of"], [
            "SC.Business.Anchor.Report.Title", "SC.Business.Anchor.Report.Clear", "SC.Business.Anchor.Report.Labels",
            "SC.Business.Anchor.Report.NextButton"])
        self.assertIn("SC.Business.Anchor.Report.TouchNext", nodes["SC.Business.Report.Guard"]["all_of"])
        self.assertLess(main.index("[JumpBack]SC.Business.Report.PageNext"), main.index("[JumpBack]SC.Business.Hold"))
        # B6e: the bulk buttons (一括営業 x ~892..1056, 一括完了 x ~1087..1251, y ~622..698), non-consuming, guarded by the
        # business top, the enabled colour and the frame-bound plan; Touch to Next of bulk reports <= 4.
        for key, action_id, (x0, y0, x1, y1) in (("Complete", "business-bulk-complete", (1087, 622, 1251, 698)),
                                                 ("Dispatch", "business-bulk-dispatch", (892, 622, 1056, 698))):
            action, node = TABLE.get(action_id), nodes[f"SC.Business.Bulk.{key}"]
            x, y, w, h = action.rect
            self.assertTrue(x0 <= x and y0 <= y and x + w <= x1 and y + h <= y1, (key, action.rect))
            self.assertFalse(action.consuming)
            self.assertEqual((node["enabled"], node["max_hit"], node["target"]), (False, 3, list(action.rect)))
            colour = "SC.Business.Color.BulkComplete.Pink" if key == "Complete" else "SC.Business.Color.BulkDispatch.White"
            self.assertEqual(node["all_of"], ["SC.Business.Top.Guard", colour, f"SC.Business.Plan.Bulk{key}"])
            self.assertEqual(node["next"], ["SC.Business.Bulk.Sent"])
            self.assertEqual(node["on_error"], ["SC.Business.Bulk.Refused", "SC.Business.Abort"])
        bulk_next = nodes["SC.Business.Bulk.ReportNext"]
        self.assertEqual((bulk_next["max_hit"], bulk_next["target"], bulk_next["all_of"]),
                         (bt.BULK_MAX_REPORTS, list(report_next.rect),
                          ["SC.Business.Report.Guard", "SC.Business.Plan.BulkReportNext"]))
        self.assertIn("SC.Business.Bulk.ReportNext", report_next.nodes)
        self.assertFalse({"business-gift-create", "business-stamina", "business-soda"} & set(TABLE.ids))
        # B6b: one non-consuming 営業完了 action per slot, disjoint, inside the pink button of the native frame; the claim
        # node needs the business-top guard and the frame-bound plan; 一括完了 / 一括営業 / 差し入れ are never actions.
        buttons = {1: (228, 512, 389, 568), 2: (610, 512, 770, 568), 3: (991, 512, 1152, 568)}
        for k, (x0, y0, x1, y1) in buttons.items():
            action, node = TABLE.get(f"business-slot-complete-{k}"), nodes[f"SC.Business.Claim.Slot{k}"]
            x, y, w, h = action.rect
            self.assertTrue(x0 <= x and y0 <= y and x + w <= x1 and y + h <= y1, (k, action.rect))
            self.assertFalse(action.consuming)
            self.assertEqual(action.family, "business")
            self.assertEqual((node["enabled"], node["max_hit"], node["target"]), (False, 1, list(action.rect)))
            self.assertEqual(node["all_of"], ["SC.Business.Top.Guard", f"SC.Business.Plan.Claim{k}"])
            self.assertEqual(nodes[f"SC.Business.Plan.Claim{k}"]["custom_recognition_param"], {"step": "claim", "slot": k})
        self.assertEqual(nodes["SC.Business.Top.Guard"]["all_of"], ["SC.Business.Anchor.Top.Title",
                                                                    "SC.Business.Anchor.Top.Talk", "SC.Business.BackKey.Ready"])
        self.assertFalse({"business-claim-all", "business-dispatch-all", "business-gift"} & set(TABLE.ids))
        self.assertEqual(nodes["SC.Business.Observe.Popup"]["next"],
                         ["SC.Popup.Reward.Close", "SC.Popup.MissionCompleted.Close", "SC.Business.Abort"])
        for name in ("Error", "TopWait", "Claim.Slot1", "Claim.Slot3", "Observe", "Observe.Popup", "Hold",
                     "ReportTakeover", "ReportWait", "Report.Next", "AfterReport", "Open.Slot1", "DispatchObserve",
                     "DispatchBack", "DispatchReturned", "Bulk.Complete", "Bulk.Dispatch", "BulkObserve",
                     "BulkReportWait", "Bulk.ReportNext", "Report.PageNext"):
            self.assertLess(main.index(f"[JumpBack]SC.Business.{name}"), main.index("[JumpBack]SC.Business.Return"))
            self.assertLess(main.index(f"[JumpBack]SC.Business.{name}"), main.index("[JumpBack]SC.Popup.CloseKnown"))
        self.assertLess(main.index("[JumpBack]SC.Business.Error"), main.index("[JumpBack]SC.Business.Sample"))
        self.assertLess(main.index("[JumpBack]SC.Business.TopWait"), main.index("[JumpBack]SC.Business.Claim.Slot1"))
        self.assertLess(main.index("[JumpBack]SC.Business.ReportTakeover"), main.index("[JumpBack]SC.Business.AtHome"))
        self.assertLess(main.index("[JumpBack]SC.Business.TopTakeover"), main.index("[JumpBack]SC.Business.TopWait"))
        self.assertLess(main.index("[JumpBack]SC.Business.TopTakeover"), main.index("[JumpBack]SC.Nav.BackOne"))
        self.assertIn("SC.Business.Color.BulkComplete.Dim", nodes)
        self.assertLess(main.index("[JumpBack]SC.Business.ReportWait"), main.index("[JumpBack]SC.Business.Report.Next"))
        self.assertLess(main.index("[JumpBack]SC.Business.DispatchObserve"),
                        main.index("[JumpBack]SC.Business.DispatchBack"))
        self.assertLess(main.index("[JumpBack]SC.Business.DispatchBack"), main.index("[JumpBack]SC.Business.Hold"))
        self.assertLess(main.index("[JumpBack]SC.Business.TopWait"), main.index("[JumpBack]SC.Business.Bulk.Complete"))
        self.assertLess(main.index("[JumpBack]SC.Business.BulkReportWait"),
                        main.index("[JumpBack]SC.Business.Bulk.ReportNext"))
        self.assertLess(main.index("[JumpBack]SC.Business.Bulk.ReportNext"), main.index("[JumpBack]SC.Business.Hold"))
        task = launcher.load_interface().tasks["Business"]
        self.assertTrue(task.get("default_check", False))  # 2026-10-11: in the daily preset (再営業へ → 決定 by default)
        plan, = launcher.plan_tasks(launcher.load_interface(), {"tasks": ["Business"]})
        self.assertEqual(plan.pipeline_override["SC.Business.Config.Mode"]["attach"], {"mode": "bulk_default"})
        self.assertEqual(nodes["SC.Business.Config.Mode"]["attach"], {"mode": "bulk_default"})
        plan, = launcher.plan_tasks(launcher.load_interface(), {"tasks": [
            {"name": "Business", "option": {"营业模式": "只采样"}}]})
        self.assertEqual(plan.pipeline_override["SC.Business.Config.Mode"]["attach"], {"mode": "sample"})
        plan, = launcher.plan_tasks(launcher.load_interface(), {"tasks": [
            {"name": "Business", "option": {"营业模式": "收取并派出（未启用）", "营业槽位数": "2"}}]})
        self.assertEqual(plan.pipeline_override["SC.Business.Config.Mode"]["attach"], {"mode": "collect_and_dispatch"})
        self.assertEqual(plan.pipeline_override["SC.Business.Config.Slots"]["attach"], {"slots": 2})
        plan, = launcher.plan_tasks(launcher.load_interface(), {"tasks": [{"name": "Business", "option": COLLECT}]})
        self.assertEqual(plan.pipeline_override["SC.Business.Config.Mode"]["attach"], {"mode": "sample_collect_one"})


@unittest.skipUnless(HAVE_MAA and all((ROOT / f).is_file() for f in FRAMES), "需要 MaaFramework v5.12.3 与私有帧")
class BusinessReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not private_ready():
            raise unittest.SkipTest("私有回放数据不完整")

    def setUp(self):
        self.root = PRIVATE / "run_logs" / ("_unittest_b6_" + self.id().rsplit(".", 1)[-1])
        shutil.rmtree(self.root, ignore_errors=True)
        self.addCleanup(shutil.rmtree, self.root, True)

    def run_task(self, frames, option=None, **extra):
        # Card OCR (3 slots per frame) is slow on a loaded machine: generous windows, static frames end them anyway.
        extra.setdefault("top_wait_seconds", bt.TOP_WAIT_SECONDS)
        options = {"business": {"log_root": self.root, "now": lambda: NOW, "sample_root": self.root / "samples",
                                "sample_frames": 2, "sample_seconds": 0.0, "sample_interval": 0.0,
                                "settle_seconds": 0.0, "observe_seconds": 1.5,
                                "observe_min_seconds": 0.0, "confirm_seconds": 0.0,
                                "report_wait_seconds": bt.REPORT_WAIT_SECONDS,
                                "report_no_effect_seconds": 0.5, "dispatch_seconds": 12.0,
                                "dispatch_sample_seconds": 0.0, "dispatch_return_seconds": 12.0, **extra}}
        task = {"name": "Business", "option": option or {"营业模式": "只采样"}}  # B6e: the default is bulk_default
        steps = [(ROOT / f[0], f[1]) if isinstance(f, tuple) else (ROOT / f, "click") for f in frames]
        summary = launcher.run({"tasks": [task]}, offline_steps=steps,
                               simulate_input=True, policy={"live_input_enabled": False}, summary_path=False,
                               save_diagnostics=False, task_options=options)
        task, = summary["tasks"]
        report, = [r for r in task["reports"] if r["kind"] == "business"]
        return task, report

    def test_sample_mode_opens_saves_frames_and_returns_home(self):
        task, report = self.run_task([HOME, STAND_IN, HOME])
        self.assertEqual((task["status"], report["outcome"]), ("succeeded", "sampled"), task)
        self.assertEqual(task["simulated_actions"], ["home-business", "nav-back"])
        for point, action in zip(task["simulated_clicks"], task["simulated_actions"]):
            self.assertTrue(TABLE.get(action).contains(point), (point, action))
        frames = sorted((self.root / "samples").glob("business-top-*/frame-*.png"))
        self.assertGreaterEqual(len(frames), 3)  # the Home before the tap + >= 2 of the business screen
        self.assertEqual(task["node_trace"][-1], "SC.Business.FinishClean")

    def test_entry_label_ignores_timer_and_badge(self):
        for home in (HOME_BADGE, HOME_0621):
            with self.subTest(home=home):
                task, report = self.run_task([home, STAND_IN, home])
                self.assertEqual((task["status"], report["outcome"]), ("succeeded", "sampled"), task)
                self.assertEqual(task["simulated_actions"], ["home-business", "nav-back"])

    def test_no_usable_back_key_stops_without_any_further_click(self):
        task, report = self.run_task([HOME, HOME_DIMMED])
        self.assertEqual((task["status"], report["outcome"]), ("failed", "sampled_not_returned"))
        self.assertEqual(task["simulated_actions"], ["home-business"])
        self.assertTrue(list((self.root / "samples").glob("business-top-*/frame-*.png")))

    def test_collect_and_dispatch_is_not_enabled(self):
        task, report = self.run_task([HOME], option={"营业模式": "收取并派出（未启用）"})
        self.assertEqual((task["status"], report["outcome"]), ("failed", "needs_sampling"))
        self.assertEqual(task["simulated_actions"], [])

    def test_sample_mode_on_the_native_business_top_never_taps_a_slot(self):
        if not all((ROOT / f).is_file() for f in B6B_FRAMES):
            self.skipTest("缺少营业原生帧")
        task, report = self.run_task([HOME, TOP_CLEAR, HOME])
        self.assertEqual((task["status"], report["outcome"]), ("succeeded", "sampled"), task)
        self.assertEqual(task["simulated_actions"], ["home-business", "nav-back"])


@unittest.skipUnless(HAVE_MAA and all((ROOT / f).is_file() for f in FRAMES + B6B_FRAMES), "需要 MaaFramework v5.12.3 与营业私有帧")
class BusinessCollectOneReplayTests(unittest.TestCase):
    """B6b ``sample_collect_one``: one 営業完了 tap on the first CLEAR slot, then read-only observation."""
    setUpClass = BusinessReplayTests.setUpClass
    setUp = BusinessReplayTests.setUp
    run_task = BusinessReplayTests.run_task

    def events(self):
        return [{k: e[k] for k in ("event", "slot", "reason") if k in e} for e in bt.tc.JsonlLog(
            "business", self.root, lambda: NOW).events()]

    def test_claims_first_clear_slot_once_and_returns_home(self):
        task, report = self.run_task([HOME, TOP_CLEAR, TOP_SLOT1_TEA, HOME], option=COLLECT,
                                     observe_seconds=bt.OBSERVE_SECONDS)
        self.assertEqual((task["status"], report["outcome"]), ("succeeded", "claimed"), task)
        self.assertEqual(task["simulated_actions"], ["home-business", "business-slot-complete-1", "nav-back"])
        for point, action in zip(task["simulated_clicks"], task["simulated_actions"]):
            self.assertTrue(TABLE.get(action).contains(point), (point, action))
        self.assertEqual(self.events(), [{"event": "claim_intent", "slot": 1}, {"event": "claimed", "slot": 1}])
        self.assertTrue(list((self.root / "samples").glob("business-top-*/frame-*.png")))

    def test_first_clear_slot_is_slot_two_when_slot_one_is_tea_time(self):
        task, report = self.run_task([HOME, TOP_SLOT1_TEA, TOP_SLOT1_TEA], option=COLLECT)
        self.assertEqual(task["simulated_actions"], ["home-business", "business-slot-complete-2"], task)
        self.assertEqual((task["status"], report["outcome"]), ("failed", "claim_uncertain"))

    def test_slot_still_clear_is_uncertain_and_never_retried_today(self):
        task, report = self.run_task([HOME, TOP_CLEAR, TOP_CLEAR], option=COLLECT)
        self.assertEqual((task["status"], report["outcome"]), ("failed", "claim_uncertain"), task)
        self.assertEqual(task["simulated_actions"], ["home-business", "business-slot-complete-1"])
        self.assertEqual(self.events(), [{"event": "claim_intent", "slot": 1},
                                         {"event": "claim_uncertain", "slot": 1, "reason": "slot_still_clear_after_tap"}])
        task, report = self.run_task([HOME, TOP_CLEAR], option=COLLECT)  # same game day, same log
        self.assertEqual((task["status"], report["outcome"]), ("failed", "uncertain_slot_today"), task)
        self.assertEqual(task["simulated_actions"], ["home-business"])
        self.assertEqual(len(self.events()), 2)

    def test_unknown_screen_after_tap_stops_without_click(self):
        task, report = self.run_task([HOME, TOP_CLEAR, STAND_IN], option=COLLECT)
        self.assertEqual((task["status"], report["outcome"]), ("failed", "needs_sampling"), task)
        self.assertEqual(task["simulated_actions"], ["home-business", "business-slot-complete-1"])
        self.assertEqual(self.events()[-1]["event"], "claim_uncertain")

    def test_error_dialog_after_tap_is_uncertain_and_never_tapped(self):
        task, report = self.run_task([HOME, TOP_CLEAR, ERROR_1012], option=COLLECT)
        self.assertEqual((task["status"], report["outcome"]), ("failed", "business_error_uncertain"), task)
        self.assertEqual(task["simulated_actions"], ["home-business", "business-slot-complete-1"])
        last = self.events()[-1]
        self.assertEqual((last["event"], last["slot"]), ("claim_uncertain", 1))
        self.assertTrue(last["reason"].startswith("error_dialog:"), last)

    def test_error_dialog_before_any_claim_stops(self):
        task, report = self.run_task([HOME, ERROR_1012], option=COLLECT)
        self.assertEqual((task["status"], report["outcome"]), ("failed", "business_error_uncertain"), task)
        self.assertEqual(task["simulated_actions"], ["home-business"])
        self.assertEqual(self.events(), [])

    def test_tea_time_slots_are_not_claimed(self):
        task, report = self.run_task([HOME, TOP_TEATIME, HOME], option=COLLECT)
        self.assertEqual((task["status"], report["outcome"]), ("succeeded", "no_completed_slot"), task)
        self.assertEqual(task["simulated_actions"], ["home-business", "nav-back"])
        self.assertEqual(self.events(), [])



@unittest.skipUnless(HAVE_MAA and all((ROOT / f).is_file() for f in FRAMES + B6B_FRAMES + B6D_FRAMES),
                     "需要 MaaFramework v5.12.3 与营业私有帧（含 WORK REPORT）")
class BusinessReportDispatchReplayTests(unittest.TestCase):
    """B6d: WORK REPORT after 営業完了 (and at a takeover) and ``sample_dispatch_open``."""
    setUpClass = BusinessReplayTests.setUpClass
    setUp = BusinessReplayTests.setUp
    run_task = BusinessReplayTests.run_task

    def seed(self, *records):
        log = bt.tc.JsonlLog("business", self.root, lambda: NOW)
        for record in records:
            log.append(record)

    def full_events(self):
        return bt.tc.JsonlLog("business", self.root, lambda: NOW).events()

    def check_points(self, task):
        for point, action in zip(task["simulated_clicks"], task["simulated_actions"]):
            self.assertTrue(TABLE.get(action).contains(point), (point, action))

    def test_claim_work_report_touch_next_then_top_and_home(self):
        task, report = self.run_task([HOME, TOP_CLEAR, REPORT, TOP_EMPTY1, HOME], option=COLLECT)
        self.assertEqual((task["status"], report["outcome"]), ("succeeded", "claimed"), task)
        self.assertEqual(task["simulated_actions"], ["home-business", "business-slot-complete-1",
                                                     "business-report-next", "nav-back"])
        self.check_points(task)
        events = self.full_events()
        self.assertEqual([e["event"] for e in events], ["claim_intent", "work_report", "claimed", "top_after_report"])
        work = events[1]
        self.assertEqual((work["slot"], work["exp"], work["fans"]), (1, 500, 237500), work)
        self.assertIn("インタビュー", work["business"] or "")
        self.assertEqual((events[2]["slot"], events[2]["via"]), (1, "work_report"))
        self.assertEqual(events[3]["kinds"], ["empty", "completed", "completed"])
        self.assertEqual(bt.uncertain_slots(events), (frozenset(), False))

    def test_takeover_resolves_the_live_uncertain_claim(self):
        # The 2026-10-03 19:10 log: claim_intent -> claim_uncertain (unknown_screen_after_tap) for slot 1.
        self.seed({"event": "claim_intent", "slot": 1}, {"event": "claim_uncertain", "slot": 1,
                                                          "reason": "unknown_screen_after_tap"})
        task, report = self.run_task([REPORT, TOP_EMPTY1, HOME], option=COLLECT)
        self.assertEqual((task["status"], report["outcome"]), ("succeeded", "claimed"), task)
        self.assertEqual(task["simulated_actions"], ["business-report-next", "nav-back"])
        self.check_points(task)
        events = self.full_events()
        self.assertEqual([e["event"] for e in events][2:], ["work_report", "claimed", "top_after_report"])
        self.assertTrue(events[2]["takeover"])
        self.assertEqual((events[3]["slot"], events[3]["resolved"], events[3]["via"]),
                         (1, True, "work_report_takeover"))
        self.assertEqual(bt.uncertain_slots(events), (frozenset(), False))

    def test_takeover_without_an_attributable_slot_only_closes_the_report(self):
        task, report = self.run_task([REPORT, TOP_EMPTY1, HOME], option=COLLECT)
        self.assertEqual((task["status"], report["outcome"]), ("succeeded", "report_closed"), task)
        self.assertEqual(task["simulated_actions"], ["business-report-next", "nav-back"])
        events = self.full_events()
        self.assertEqual([e["event"] for e in events], ["work_report", "top_after_report"])
        self.assertIsNone(events[0]["slot"])

    def test_sample_mode_never_taps_a_work_report(self):
        task, report = self.run_task([REPORT], option={"营业模式": "只采样"})
        self.assertEqual((task["status"], report["outcome"]), ("failed", "work_report_on_screen"), task)
        self.assertEqual(task["simulated_actions"], [])
        self.assertEqual(self.full_events(), [])

    def test_touch_to_next_without_effect_stops(self):
        task, report = self.run_task([HOME, TOP_CLEAR, REPORT, REPORT], option=COLLECT)
        self.assertEqual((task["status"], report["outcome"]), ("failed", "report_next_no_effect"), task)
        self.assertEqual(task["simulated_actions"], ["home-business", "business-slot-complete-1",
                                                     "business-report-next"])
        self.assertEqual([e["event"] for e in self.full_events()], ["claim_intent", "work_report", "claimed"])

    def test_collect_one_never_taps_a_second_report(self):
        task, report = self.run_task([HOME, TOP_CLEAR, REPORT, (REPORT_ANIM, 3), REPORT], option=COLLECT,
                                     observe_seconds=20.0)
        # A second report after Touch to Next is never tapped (collect_one closes exactly one report).
        self.assertEqual((task["status"], report["outcome"]), ("failed", "report_next_no_effect"), task)
        self.assertEqual(task["simulated_actions"], ["home-business", "business-slot-complete-1",
                                                     "business-report-next"])

    def test_dispatch_open_taps_first_empty_slot_and_never_leaves_an_unverified_page(self):
        # B6h review #3: STAND_IN (a non-business page with the undimmed back key) is no dispatch-page evidence, and
        # no native dispatch page exists yet: the page is only sampled, the back key is never pressed.
        task, report = self.run_task([HOME, TOP_EMPTY1, STAND_IN], option=DISPATCH)
        self.assertEqual((task["status"], report["outcome"]), ("failed", "dispatch_not_returned"), task)
        self.assertEqual(task["simulated_actions"], ["home-business", "business-slot-open-1"])
        self.check_points(task)
        self.assertNotIn("SC.Business.DispatchBack", task["node_trace"])
        self.assertIn("dispatch_page_unverified", report["notes"])
        self.assertEqual(self.full_events(), [])  # opening the screen is not a dispatch: nothing is logged

    def test_dispatch_open_never_taps_clear_or_tea_time_slots(self):
        for top in (TOP_CLEAR, TOP_TEATIME, TOP_SLOT1_TEA):
            with self.subTest(top=top):
                task, report = self.run_task([HOME, top, HOME], option=DISPATCH)
                self.assertEqual((task["status"], report["outcome"]), ("succeeded", "no_empty_slot"), task)
                self.assertEqual(task["simulated_actions"], ["home-business", "nav-back"])

    def test_dispatch_open_resolves_a_reobserved_uncertain_slot_first(self):
        # Slot 1 had an uncertain claim; the business top now shows it without CLEAR -> resolved, then opened.
        self.seed({"event": "claim_intent", "slot": 1}, {"event": "claim_uncertain", "slot": 1})
        task, report = self.run_task([HOME, TOP_EMPTY1, STAND_IN], option=DISPATCH)
        self.assertEqual((task["status"], report["outcome"]), ("failed", "dispatch_not_returned"), task)
        self.assertEqual(task["simulated_actions"], ["home-business", "business-slot-open-1"])
        resolved = self.full_events()[2]
        self.assertEqual((resolved["event"], resolved["slot"], resolved["resolved"], resolved["via"]),
                         ("claimed", 1, True, "top_reobserved"))

    def test_reobserved_top_resolves_slot_one_and_claims_slot_two(self):
        # Live state after a host restart that drops the 19:10 WORK REPORT: slot 1 no longer CLEAR, slots 2/3 CLEAR.
        self.seed({"event": "claim_intent", "slot": 1}, {"event": "claim_uncertain", "slot": 1,
                                                          "reason": "unknown_screen_after_tap"})
        task, report = self.run_task([HOME, TOP_EMPTY1, REPORT, TOP_EMPTY1, HOME], option=COLLECT)
        self.assertEqual((task["status"], report["outcome"]), ("succeeded", "claimed"), task)
        self.assertEqual(task["simulated_actions"], ["home-business", "business-slot-complete-2",
                                                     "business-report-next", "nav-back"])
        self.check_points(task)
        events = self.full_events()
        self.assertEqual([(e["event"], e.get("slot")) for e in events[2:]], [
            ("claimed", 1), ("claim_intent", 2), ("work_report", 2), ("claimed", 2), ("top_after_report", 2)])
        self.assertEqual(events[2]["via"], "top_reobserved")
        self.assertEqual(bt.uncertain_slots(events), (frozenset(), False))

    def test_slot_still_clear_stays_blocked_after_reobservation(self):
        self.seed({"event": "claim_intent", "slot": 1}, {"event": "claim_uncertain", "slot": 1})
        task, report = self.run_task([HOME, TOP_CLEAR], option=COLLECT)
        self.assertEqual((task["status"], report["outcome"]), ("failed", "uncertain_slot_today"), task)
        self.assertEqual(task["simulated_actions"], ["home-business"])
        self.assertEqual(len(self.full_events()), 2)

    def test_dispatch_screen_not_opened_stops_without_back(self):
        task, report = self.run_task([HOME, TOP_EMPTY1, TOP_EMPTY1], option=DISPATCH)
        self.assertEqual((task["status"], report["outcome"]), ("failed", "dispatch_screen_not_opened"), task)
        self.assertEqual(task["simulated_actions"], ["home-business", "business-slot-open-1"])

    def test_dispatch_screen_without_back_key_stops(self):
        task, report = self.run_task([HOME, TOP_EMPTY1, HOME_DIMMED], option=DISPATCH)
        self.assertEqual((task["status"], report["outcome"]), ("failed", "dispatch_not_returned"), task)
        self.assertEqual(task["simulated_actions"], ["home-business", "business-slot-open-1"])

    def test_dispatch_mode_takes_over_a_work_report_first(self):
        self.seed({"event": "claim_intent", "slot": 1}, {"event": "claim_uncertain", "slot": 1})
        task, report = self.run_task([REPORT, TOP_EMPTY1, STAND_IN], option=DISPATCH)
        self.assertEqual((task["status"], report["outcome"]), ("failed", "dispatch_not_returned"), task)
        self.assertEqual(task["simulated_actions"], ["business-report-next", "business-slot-open-1"])
        self.assertEqual(bt.uncertain_slots(self.full_events()), (frozenset(), False))


def scenario_hits(scenario, x, y):
    """The actions ReplayScenario.click would resolve for (x, y): same families, the armed permit's recognition node
    (overlapping rects across families are bound by node, e.g. W.I.N.G. vs business) and the action allowlist."""
    permit = getattr(scenario.frame_permits, "_armed", None) if scenario.frame_binding is not None else None
    hits = scenario.table.at((int(x), int(y)), families=scenario.families,
                             node=permit.node_name if permit is not None else None)
    if scenario.action_allowlist is not None:
        hits = [a for a in hits if a.id in scenario.action_allowlist]
    return hits


@unittest.skipUnless(HAVE_MAA and all((ROOT / f).is_file() for f in FRAMES + B6B_FRAMES + B6D_FRAMES + B6E_FRAMES),
                     "需要 MaaFramework v5.12.3 与营业私有帧（含 B6e 一括 样本）")
class BusinessBulkReplayTests(unittest.TestCase):
    """B6e ``bulk_default``: 一括完了 -> WORK REPORTs -> 一括営業 -> verified business top -> Home."""
    setUpClass = BusinessReplayTests.setUpClass
    setUp = BusinessReplayTests.setUp
    run_task = BusinessReplayTests.run_task
    seed = BusinessReportDispatchReplayTests.seed
    full_events = BusinessReportDispatchReplayTests.full_events
    check_points = BusinessReportDispatchReplayTests.check_points

    def bulk(self, frames, **extra):
        extra.setdefault("bulk_observe_seconds", 3.0)
        return self.run_task(frames, option=BULK, **extra)

    def kinds(self):
        return [(e["event"], e.get("action")) for e in self.full_events()]

    def refusing(self, action_id):
        original = launcher.ReplayScenario.click

        def click(scenario, x, y):
            hits = scenario_hits(scenario, x, y)
            if len(hits) == 1 and hits[0].id == action_id:  # stands in for a host refusal before any gesture
                scenario.frame_permits.take()  # a host refusal consumes the armed permit, as in ReplayScenario.click
                scenario.refused.append((int(x), int(y)))
                return False
            return original(scenario, x, y)
        return mock.patch.object(launcher.ReplayScenario, "click", click)

    def test_default_mode_collects_reads_reports_dispatches_and_verifies(self):
        task, report = self.bulk([HOME, TOP_EMPTY1, REPORT, (REPORT_ANIM, 3), REPORT, TOP_ALL_EMPTY, TOP_DONE, HOME],
                                 bulk_observe_seconds=bt.BULK_OBSERVE_SECONDS)
        self.assertEqual((task["status"], report["outcome"]), ("failed", "dispatched_binding_unverified"), task)
        self.assertEqual(task["simulated_actions"], ["home-business", "business-bulk-complete", "business-report-next",
                                                     "business-report-next", "business-bulk-dispatch", "nav-back"])
        self.check_points(task)
        self.assertEqual(self.kinds(), [("bulk_intent", "complete"), ("bulk_completed", "complete"),
                                        ("work_report", None), ("work_report", None), ("bulk_intent", "dispatch"),
                                        ("bulk_dispatched", "dispatch")])
        reports = [e for e in self.full_events() if e["event"] == "work_report"]
        self.assertEqual([(r["bulk"], r["index"], r["exp"], r["fans"]) for r in reports],
                         [(True, 1, 500, 237500), (True, 2, 500, 237500)])
        tops = [d for d in report["decisions"] if d.get("stage") == "Top"]
        self.assertEqual(tops[0]["kinds"], ["empty", "completed", "completed"])  # native: positive 未選択 text
        self.assertEqual(tops[0]["buttons"], ["enabled", "enabled"])
        self.assertEqual((tops[-2]["kinds"], tops[-2]["buttons"]), (["empty"] * 3, ["disabled", "enabled"]))  # native
        self.assertEqual(tops[-1]["buttons"], ["disabled", "disabled"])
        self.assertEqual(bt.bulk_pending(self.full_events()), {})

    def test_all_clear_top_with_grey_dispatch_button_collects_first(self):
        task, report = self.bulk([HOME, TOP_CLEAR, REPORT, TOP_ALL_EMPTY, TOP_DONE, HOME])
        self.assertEqual((task["status"], report["outcome"]), ("failed", "dispatched_binding_unverified"), task)
        self.assertEqual(task["simulated_actions"], ["home-business", "business-bulk-complete", "business-report-next",
                                                     "business-bulk-dispatch", "nav-back"])

    def test_nothing_to_do_returns_home(self):
        task, report = self.bulk([HOME, TOP_DONE, HOME])
        self.assertEqual((task["status"], report["outcome"]), ("succeeded", "nothing_to_do"), task)
        self.assertEqual(task["simulated_actions"], ["home-business", "nav-back"])
        self.assertEqual(self.full_events(), [])

    def test_tea_time_is_never_pressed(self):
        # 2026-10-12: Tea Time is recognised positively (tea_time_wait), never pressed, and the task leaves via ←.
        task, report = self.bulk([HOME, TOP_TEATIME, HOME])
        self.assertEqual((task["status"], report["outcome"]), ("failed", "tea_time_wait"), task)
        self.assertEqual(task["simulated_actions"], ["home-business", "nav-back"])
        top = [d for d in report["decisions"] if d.get("stage") == "Top"][-1]
        self.assertEqual(top["kinds"], ["tea_time"] * 3)
        self.assertEqual(top["tea_time_left_minutes"], 96)  # 「ティータイム残り 1時間36分」
        self.assertEqual(self.full_events(), [])

    def test_live_20261012_tea_time_top_is_tea_time_wait(self):
        frames = [LIVE_20261012 + f"frame-00{k}.png" for k in (1, 4)]  # live home + live Tea Time top (replay steps per tap)
        if not all(os.path.exists(f) for f in frames):
            self.skipTest("private 2026-10-12 business samples not present")
        task, report = self.bulk(frames + [HOME])
        self.assertEqual((task["status"], report["outcome"]), ("failed", "tea_time_wait"), task)
        self.assertEqual(task["simulated_actions"], ["home-business", "nav-back"])
        top = [d for d in report["decisions"] if d.get("stage") == "Top"][-1]
        self.assertEqual((top["kinds"], top["buttons"]), (["tea_time"] * 3, ["enabled", "disabled"]))
        self.assertEqual(top["tea_time_left_minutes"], 153)
        self.assertEqual(self.full_events(), [])

    def test_blank_card_is_not_an_empty_slot(self):
        # B6d blank synthetic: no 未選択 text, so slot 1 is "other"; slots 2/3 CLEAR -> 一括完了 only.
        task, report = self.bulk([HOME, TOP_BLANK1, TOP_BLANK1])
        self.assertEqual(task["simulated_actions"], ["home-business", "business-bulk-complete"], task)
        self.assertEqual((task["status"], report["outcome"]), ("failed", "bulk_result_uncertain"))
        self.assertEqual(self.kinds(), [("bulk_intent", "complete"), ("bulk_uncertain", "complete")])

    def test_error_dialog_after_bulk_complete_stops_the_run_and_a_later_run_rechecks(self):
        task, report = self.bulk([HOME, TOP_CLEAR, ERROR_1012])
        self.assertEqual((task["status"], report["outcome"]), ("failed", "business_error_uncertain"), task)
        self.assertEqual(task["simulated_actions"], ["home-business", "business-bulk-complete"])
        self.assertEqual(self.kinds(), [("bulk_intent", "complete"), ("business_error", "complete")])
        # The later run only notes it and decides from the business top (still CLEAR -> press).
        task, report = self.bulk([HOME, TOP_CLEAR])
        self.assertEqual(task["simulated_actions"], ["home-business", "business-bulk-complete"], task)
        self.assertIn("uncertain_result_earlier_today", report["notes"])
        self.assertEqual(self.kinds()[2:4], [("recheck", None), ("bulk_intent", "complete")])

    def test_unknown_screen_after_bulk_dispatch_is_sampled_not_tapped(self):
        task, report = self.bulk([HOME, TOP_ALL_EMPTY, HOME_DIMMED])
        self.assertEqual((task["status"], report["outcome"]), ("failed", "needs_sampling"), task)
        self.assertEqual(task["simulated_actions"], ["home-business", "business-bulk-dispatch"])
        self.assertEqual(self.kinds(), [("bulk_intent", "dispatch"), ("bulk_uncertain", "dispatch")])
        # Later the top shows nothing left -> nothing pressed; the earlier press only warned.
        task, report = self.bulk([HOME, TOP_DONE, HOME])
        self.assertEqual((task["status"], report["outcome"]), ("succeeded", "nothing_to_do"), task)
        self.assertEqual(task["simulated_actions"], ["home-business", "nav-back"])
        self.assertEqual(self.kinds()[-1], ("recheck", None))
        self.assertEqual(bt.bulk_pending(self.full_events()), {})

    def test_refused_bulk_tap_is_not_uncertain(self):
        with self.refusing("business-bulk-complete"):
            task, report = self.bulk([HOME, TOP_CLEAR])
        self.assertEqual((task["status"], report["outcome"]), ("failed", "click_refused_not_sent"), task)
        self.assertEqual(task["simulated_actions"], ["home-business"])
        # B6g: re-guarded and retried twice in the same run, every attempt refused -> 3 refusals, nothing sent.
        self.assertEqual(self.kinds(), [("bulk_intent", "complete"), ("bulk_refused", "complete")] * 3)
        self.assertEqual(bt.uncertain_slots(self.full_events()), (frozenset(), False))
        task, report = self.bulk([HOME, TOP_CLEAR, REPORT, TOP_ALL_EMPTY, TOP_DONE, HOME])  # a later run may press
        self.assertEqual((task["status"], report["outcome"]), ("failed", "dispatched_binding_unverified"), task)

    def test_refused_single_claim_is_not_uncertain(self):
        # The 2026-10-03 10:05 live case: the host refused 営業完了 (frame binding) -> it was logged claim_uncertain.
        with self.refusing("business-slot-complete-1"):
            task, report = self.run_task([HOME, TOP_CLEAR], option=COLLECT)
        self.assertEqual((task["status"], report["outcome"]), ("failed", "click_refused_not_sent"), task)
        self.assertEqual([(e["event"], e["slot"]) for e in self.full_events()],
                         [("claim_intent", 1), ("claim_refused", 1)])
        self.assertEqual(bt.uncertain_slots(self.full_events()), (frozenset(), False))

    def test_takeover_report_then_bulk(self):
        self.seed({"event": "claim_intent", "slot": 1}, {"event": "claim_uncertain", "slot": 1})
        task, report = self.bulk([REPORT, TOP_ALL_EMPTY, TOP_DONE, HOME])
        self.assertEqual((task["status"], report["outcome"]), ("failed", "dispatched_binding_unverified"), task)
        self.assertEqual(task["simulated_actions"], ["business-report-next", "business-bulk-dispatch", "nav-back"])


@unittest.skipUnless(HAVE_MAA and all((ROOT / f).is_file() for f in FRAMES + B6B_FRAMES + B6D_FRAMES + B6E_FRAMES
                                      + B6F_FRAMES), "需要 MaaFramework v5.12.3 与营业私有帧（含 B6f 分页报告）")
class BusinessPagedReportReplayTests(unittest.TestCase):
    """B6f: the paged WORK REPORT (Touch to Next per page, then 「次へ」) after 一括完了, and its takeover."""
    setUpClass = BusinessReplayTests.setUpClass
    setUp = BusinessReplayTests.setUp
    run_task = BusinessReplayTests.run_task
    bulk = BusinessBulkReplayTests.bulk
    kinds = BusinessBulkReplayTests.kinds
    seed = BusinessReportDispatchReplayTests.seed
    full_events = BusinessReportDispatchReplayTests.full_events
    check_points = BusinessReportDispatchReplayTests.check_points

    def test_live_2050_sequence_pages_then_next_then_dispatch(self):
        task, report = self.bulk([HOME, TOP_EMPTY1, PAGE1, (PAGE2_ANIM, 3), PAGE2, PAGED, TOP_ALL_EMPTY, TOP_DONE,
                                  HOME], bulk_observe_seconds=bt.BULK_OBSERVE_SECONDS)
        self.assertEqual((task["status"], report["outcome"]), ("failed", "dispatched_binding_unverified"), task)
        self.assertEqual(task["simulated_actions"], [
            "home-business", "business-bulk-complete", "business-report-next", "business-report-next",
            "business-report-next-page", "business-bulk-dispatch", "nav-back"])
        self.check_points(task)
        self.assertEqual(self.kinds(), [("bulk_intent", "complete"), ("bulk_completed", "complete"),
                                        ("work_report", None), ("work_report", None), ("bulk_intent", "dispatch"),
                                        ("bulk_dispatched", "dispatch")])

    def test_gift_page_after_the_paged_report_is_logged_and_closed_with_next(self):
        # Live 2026-10-10: 「ファンからのプレゼント」 follows the 次へ of the paged report (private sample).
        if not (ROOT / GIFT).is_file():
            self.skipTest("缺少 2026-10-10 礼物页样本")
        task, report = self.bulk([HOME, TOP_EMPTY1, PAGE1, (PAGE2_ANIM, 3), PAGE2, PAGED, GIFT, TOP_ALL_EMPTY,
                                  TOP_DONE, HOME], bulk_observe_seconds=bt.BULK_OBSERVE_SECONDS)
        self.assertEqual(task["simulated_actions"][:6], [
            "home-business", "business-bulk-complete", "business-report-next", "business-report-next",
            "business-report-next-page", "business-report-next-page"], task)
        self.check_points(task)
        gift = [e for e in self.full_events() if e["event"] == "work_report_gift"]
        self.assertEqual(len(gift), 1, self.full_events())
        self.assertTrue(gift[0]["bulk"])
        self.assertNotEqual(report["outcome"], "report_next_unverified")

    def test_full_live_1010_sequence_default_redispatches_and_decides(self):
        if not all((ROOT / f).is_file() for f in (GIFT, REWARD, SELECT)):
            self.skipTest("缺少 2026-10-10 礼物 / 报酬 / 选择页样本")
        task, report = self.bulk([HOME, TOP_EMPTY1, PAGE1, (PAGE2_ANIM, 3), PAGE2, PAGED, GIFT, REWARD, SELECT, HOME],
                                 bulk_observe_seconds=bt.BULK_OBSERVE_SECONDS)
        self.assertEqual(task["simulated_actions"], [
            "home-business", "business-bulk-complete", "business-report-next", "business-report-next",
            "business-report-next-page", "business-report-next-page", "business-reward-redispatch", "business-dispatch-decide"], task)
        self.check_points(task)
        self.assertNotIn("再営業", " ".join(task["simulated_actions"]))
        events = [e["event"] for e in self.full_events()]
        self.assertIn("reward_page", events)
        self.assertIn("select_page", events)
        select = [e for e in self.full_events() if e["event"] == "select_page"][0]
        print("select_page reading:", json.dumps(select, ensure_ascii=True))
        reward = [e for e in self.full_events() if e["event"] == "reward_page"][0]
        print("reward_page reading:", json.dumps(reward, ensure_ascii=True))

    def test_gift_page_without_effect_stops_after_one_press(self):
        if not (ROOT / GIFT).is_file():
            self.skipTest("缺少 2026-10-10 礼物页样本")
        task, report = self.bulk([GIFT, GIFT], bulk_observe_seconds=bt.BULK_OBSERVE_SECONDS)
        self.assertLessEqual(task["simulated_actions"].count("business-report-next-page"), 1, task)

    def test_takeover_on_the_paged_screen_uses_only_next(self):
        # The live host is still on the 次へ screen (page 2/2) after the 20:50 run.
        self.seed({"event": "bulk_intent", "action": "complete"},
                  {"event": "bulk_completed", "action": "complete", "via": "work_report"})
        task, report = self.bulk([PAGED, TOP_ALL_EMPTY, TOP_DONE, HOME])
        self.assertEqual((task["status"], report["outcome"]), ("failed", "dispatched_binding_unverified"), task)
        self.assertEqual(task["simulated_actions"], ["business-report-next-page", "business-bulk-dispatch", "nav-back"])
        self.check_points(task)
        work = [e for e in self.full_events() if e["event"] == "work_report"]
        self.assertEqual(len(work), 1)
        self.assertTrue(work[0]["takeover"] and work[0]["paged"])
        self.assertEqual((work[0]["exp"], work[0]["fans"]), (500, 237500))

    def test_next_without_effect_stops(self):
        task, report = self.bulk([PAGED, PAGED])
        self.assertEqual((task["status"], report["outcome"]), ("failed", "report_next_no_effect"), task)
        self.assertEqual(task["simulated_actions"], ["business-report-next-page"])

    def test_collect_one_closes_the_paged_screen_after_touch_to_next(self):
        task, report = self.run_task([HOME, TOP_CLEAR, REPORT, PAGED, TOP_EMPTY1, HOME], option=COLLECT)
        self.assertEqual((task["status"], report["outcome"]), ("succeeded", "claimed"), task)
        self.assertEqual(task["simulated_actions"], ["home-business", "business-slot-complete-1", "business-report-next",
                                                     "business-report-next-page", "nav-back"])
        self.assertEqual([e["event"] for e in self.full_events()],
                         ["claim_intent", "work_report", "claimed", "top_after_report"])


@unittest.skipUnless(HAVE_MAA and all((ROOT / f).is_file() for f in FRAMES + B6B_FRAMES + B6D_FRAMES + B6E_FRAMES),
                     "需要 MaaFramework v5.12.3 与营业私有帧（含 B6g 全空首页）")
class BusinessAllEmptyRaceReplayTests(unittest.TestCase):
    """B6g: native all-empty top (一括完了 dark pink = disabled), start on the business top, refused-tap retry."""
    setUpClass = BusinessReplayTests.setUpClass
    setUp = BusinessReplayTests.setUp
    run_task = BusinessReplayTests.run_task
    bulk = BusinessBulkReplayTests.bulk
    kinds = BusinessBulkReplayTests.kinds
    full_events = BusinessReportDispatchReplayTests.full_events
    check_points = BusinessReportDispatchReplayTests.check_points

    def refusing(self, action_id, times):
        original = launcher.ReplayScenario.click
        left = [times]

        def click(scenario, x, y):
            hits = scenario_hits(scenario, x, y)
            if len(hits) == 1 and hits[0].id == action_id and left[0] > 0:  # host refusal before any gesture
                left[0] -= 1
                scenario.frame_permits.take()  # a host refusal consumes the armed permit, as in ReplayScenario.click
                scenario.refused.append((int(x), int(y)))
                return False
            return original(scenario, x, y)
        return mock.patch.object(launcher.ReplayScenario, "click", click)

    def test_all_empty_top_dispatches(self):
        task, report = self.bulk([HOME, TOP_ALL_EMPTY, TOP_DONE, HOME])
        self.assertEqual((task["status"], report["outcome"]), ("failed", "dispatched_binding_unverified"), task)
        self.assertEqual(task["simulated_actions"], ["home-business", "business-bulk-dispatch", "nav-back"])
        self.check_points(task)
        top = next(d for d in report["decisions"] if d.get("stage") == "Top")
        self.assertEqual((top["kinds"], top["buttons"]), (["empty"] * 3, ["disabled", "enabled"]))

    def test_run_starting_on_the_business_top_takes_it_over(self):
        # Live 21:24: the previous run stopped on the business top; the next run timed out with 0 clicks.
        task, report = self.bulk([TOP_ALL_EMPTY, TOP_DONE, HOME])
        self.assertEqual((task["status"], report["outcome"]), ("failed", "dispatched_binding_unverified"), task)
        self.assertEqual(task["simulated_actions"], ["business-bulk-dispatch", "nav-back"])

    def test_refused_dispatch_is_reguarded_and_retried_once_sent(self):
        with self.refusing("business-bulk-dispatch", 1):
            task, report = self.bulk([HOME, TOP_ALL_EMPTY, TOP_DONE, HOME])
        self.assertEqual((task["status"], report["outcome"]), ("failed", "dispatched_binding_unverified"), task)
        self.assertEqual(task["simulated_actions"], ["home-business", "business-bulk-dispatch", "nav-back"])
        self.assertEqual(self.kinds(), [("bulk_intent", "dispatch"), ("bulk_refused", "dispatch"),
                                        ("bulk_intent", "dispatch"), ("bulk_dispatched", "dispatch")])
        self.assertEqual(bt.bulk_pending(self.full_events()), {})

    def test_three_refusals_stop_without_any_press_sent(self):
        with self.refusing("business-bulk-dispatch", 5):
            task, report = self.bulk([HOME, TOP_ALL_EMPTY])
        self.assertEqual((task["status"], report["outcome"]), ("failed", "click_refused_not_sent"), task)
        self.assertEqual(task["simulated_actions"], ["home-business"])
        self.assertEqual([k for k in self.kinds() if k[0] == "bulk_refused"], [("bulk_refused", "dispatch")] * 3)
        self.assertEqual(bt.bulk_pending(self.full_events()), {})


@unittest.skipUnless(HAVE_MAA and all((ROOT / f).is_file() for f in FRAMES + B6B_FRAMES + B6D_FRAMES + B6E_FRAMES),
                     "需要 MaaFramework v5.12.3 与营业私有帧")
class BusinessB6hReviewReplayTests(unittest.TestCase):
    """B6h review #1/#2 through the real launcher, Maa recognition and native frames."""
    setUpClass = BusinessReplayTests.setUpClass
    setUp = BusinessReplayTests.setUp
    run_task = BusinessReplayTests.run_task
    seed = BusinessReportDispatchReplayTests.seed
    full_events = BusinessReportDispatchReplayTests.full_events

    def test_review1_tea_time_slot_never_resolves_a_scoped_1012(self):
        # Slot 1 holds a 1012 record; the top reads [Tea Time, CLEAR, CLEAR]: slot 1 is unknown, never resolved.
        # The daily bulk mode only warns since 2026-10-28 (test_business_bulk_rechecks_earlier_uncertain_records).
        for option in (COLLECT,):
            with self.subTest(option=option):
                shutil.rmtree(self.root, ignore_errors=True)
                self.seed({"event": "error_1012", "slot": 1})
                task, report = self.run_task([HOME, TOP_SLOT1_TEA], option=option, bulk_observe_seconds=3.0)
                self.assertEqual((task["status"], report["outcome"]), ("failed", "uncertain_slot_today"), task)
                self.assertEqual(task["simulated_actions"], ["home-business"])
                self.assertEqual([e["event"] for e in self.full_events()], ["error_1012"])

    def test_review2_unscoped_1012_blocks_dispatch_open(self):
        self.seed({"event": "error_1012"})
        task, report = self.run_task([HOME, TOP_EMPTY1], option=DISPATCH)
        self.assertEqual((task["status"], report["outcome"]), ("failed", "uncertain_result_today"), task)
        self.assertEqual(task["simulated_actions"], [])
        self.assertEqual(len(self.full_events()), 1)


try:  # the runtime imports maa (custom recognition/action base classes)
    from agent.core import business_custom as bc
except Exception:  # pragma: no cover - environment without MaaFramework
    bc = None


class _Frame:
    """A fake screen: the Pipeline nodes that hit on it, the OCR lines of each slot card and fixed-box OCR."""

    def __init__(self, hits=(), cards=((), (), ()), texts=None):
        self.hits, self.cards, self.texts = set(hits), [list(c) for c in cards], dict(texts or {})

    def tobytes(self):
        return repr((sorted(self.hits), self.cards, sorted(self.texts.items()))).encode()


class _Vision:
    def __init__(self, context):
        self.context = context

    def hit(self, node, image):
        return node in image.hits

    def text(self, image, box):
        return image.texts.get(tuple(box))


class _Context:
    def __init__(self, mode):
        self.mode = mode

    def get_node_data(self, name):
        return {"attach": {"mode": self.mode}} if name == "SC.Business.Config.Mode" else {}


@unittest.skipUnless(bc is not None, "需要 MaaFramework Python 包（营业 Runtime）")
class BusinessB6hReviewRuntimeTests(unittest.TestCase):
    """B6h review: the four reachable gaps, each driven through the runtime's real recognition
    entry points (Begin -> Plan.Open -> Check.LeftHome -> Check.Top x2 -> Plan.X / Check.X) with fake screens.
    No Maa recognition, no input; the log is a real JsonlLog under dev_assets/private."""
    TEA = ("ティータイム残り", "1時間36分")
    EMPTY = ("未選択", "営業先選択")

    def setUp(self):
        self.root = PRIVATE / "run_logs" / ("_unittest_b6h_" + self.id().rsplit(".", 1)[-1])
        shutil.rmtree(self.root, ignore_errors=True)
        self.addCleanup(shutil.rmtree, self.root, True)
        self.t = 0.0

    def clock(self):
        return self.t

    def runtime(self, mode, *seed, started=True):
        rt = bc.BusinessRuntime(log_root=self.root, now=lambda: NOW, vision=_Vision, sample_root=self.root / "s",
                                clock=self.clock, settle_seconds=0.0, confirm_seconds=0.0, observe_min_seconds=0.0)
        rt._save = lambda run, image: None
        rt.card_texts = lambda context, image, box: image.cards[[list(b) for b in bc.SLOT_CARD_BOXES].index(list(box))]
        rt.error_text = lambda context, image: None
        rt.dialog_texts = lambda context, image: []
        for record in seed:
            rt.log.append(record)
        self.ctx = _Context(mode)
        if started:
            rt.begin(self.ctx, {"config_nodes": list(bc.CONFIG_NODES)})
        else:
            with mock.patch.object(bc.BusinessRuntime, "started", lambda self, run: None):
                rt.begin(self.ctx, {"config_nodes": list(bc.CONFIG_NODES)})
        return rt

    def events(self, rt):
        return [e["event"] for e in rt.log.events()]

    @staticmethod
    def top(clear=(), cards=((), (), ()), buttons=()):
        hits = {bc.TOP_GUARD, bc.BACK_READY, *buttons}
        for k in clear:
            hits.update(bc.SLOT_NODES[k - 1])
        return _Frame(hits, cards)

    def open_top(self, rt, top, frames=2):
        """Plan.Open (Home) -> Check.LeftHome -> Check.Top on ``frames`` equal readings."""
        self.assertTrue(rt.plan(self.ctx, _Frame({bc.HOME_GUARD}), {"step": "open"}))
        self.assertTrue(rt.check(self.ctx, _Frame(), {"stage": "left_home"}))
        for _ in range(frames):
            self.t += 1.0
            rt.check(self.ctx, top, {"stage": "top"})

    # -- #1 an unknown slot state never resolves a slot-scoped uncertain record
    def test_review1_unknown_slot_keeps_scoped_1012_uncertain_in_collect_mode(self):
        rt = self.runtime("sample_collect_one", {"event": "error_1012", "slot": 1})
        top = self.top(clear=(2, 3), cards=(self.TEA, (), ()))  # states other / completed / completed
        self.open_top(rt, top)
        self.assertEqual((rt.run.phase, rt.run.outcome), ("finished", "uncertain_slot_today"))
        self.assertFalse(rt.plan(self.ctx, top, {"step": "claim", "slot": 2}))
        self.assertEqual(self.events(rt), ["error_1012"])  # nothing resolved, no intent
        self.assertEqual(bt.uncertain_slots(rt.log.events()), (frozenset({1}), False))

    def test_review1_unknown_slot_keeps_claim_uncertain_in_bulk_and_dispatch_modes(self):
        seed = ({"event": "claim_intent", "slot": 1}, {"event": "claim_uncertain", "slot": 1})
        top = self.top(clear=(2, 3), cards=(self.TEA, (), ()),
                       buttons=("SC.Business.Color.BulkComplete.Pink", "SC.Business.Color.BulkDispatch.Grey"))
        rt = self.runtime("bulk_default", *seed)
        self.open_top(rt, top)
        # The daily bulk mode writes a recheck marker and decides from this top (一括完了 for 2, 3).
        self.assertIsNone(rt.run.outcome)
        self.assertIn("uncertain_result_earlier_today", rt.run.notes)
        self.assertEqual(self.events(rt), ["claim_intent", "claim_uncertain", "recheck"])
        self.assertEqual(bt.uncertain_slots(rt.log.events()), (frozenset(), False))
        shutil.rmtree(self.root, ignore_errors=True)
        rt = self.runtime("sample_dispatch_open", *seed)
        top = self.top(cards=(self.TEA, self.EMPTY, self.EMPTY))
        self.open_top(rt, top)
        self.assertEqual(rt.run.outcome, "uncertain_slot_today")
        self.assertFalse(rt.plan(self.ctx, top, {"step": "open_slot", "slot": 2}))
        self.assertEqual(self.events(rt), ["claim_intent", "claim_uncertain"])

    def test_review1_positive_empty_card_still_resolves(self):
        rt = self.runtime("sample_collect_one", {"event": "error_1012", "slot": 1})
        top = self.top(clear=(2, 3), cards=(self.EMPTY, (), ()))
        self.open_top(rt, top)
        self.assertEqual(rt.run.phase, "claim_ready")
        self.assertTrue(rt.plan(self.ctx, top, {"step": "claim", "slot": 2}))
        claimed = rt.log.events()[1]
        self.assertEqual((claimed["event"], claimed["slot"], claimed["via"]), ("claimed", 1, "top_reobserved"))

    # -- #2 an unscoped uncertain record blocks dispatch sampling until its owner is re-observed
    def test_review2_unscoped_record_blocks_dispatch_sampling_at_begin(self):
        for record in ({"event": "error_1012"}, {"event": "business_error"},
                       {"event": "bulk_intent", "action": "dispatch"}):
            with self.subTest(record=record):
                shutil.rmtree(self.root, ignore_errors=True)
                rt = self.runtime("sample_dispatch_open", record)
                self.assertEqual(rt.run.outcome, "uncertain_result_today")
                self.assertFalse(rt.plan(self.ctx, _Frame({bc.HOME_GUARD}), {"step": "open"}))

    def test_review2_unscoped_record_is_not_cleared_by_another_slots_empty_card(self):
        # Even past Begin (record present, started() bypassed) the top decision and the frame-bound plan stop.
        rt = self.runtime("sample_dispatch_open", {"event": "error_1012"}, started=False)
        top = self.top(cards=(self.EMPTY, self.TEA, self.TEA))
        self.open_top(rt, top)
        self.assertEqual(rt.run.outcome, "uncertain_result_today")
        self.assertFalse(rt.plan(self.ctx, top, {"step": "open_slot", "slot": 1}))
        # The record appears between the decision and the tap: Plan.Open1 re-reads the log and stops.
        shutil.rmtree(self.root, ignore_errors=True)
        rt = self.runtime("sample_dispatch_open")
        self.open_top(rt, top)
        self.assertEqual((rt.run.phase, rt.run.open_slot), ("open_ready", 1))
        rt.log.append({"event": "error_1012"})
        self.assertFalse(rt.plan(self.ctx, top, {"step": "open_slot", "slot": 1}))
        self.assertEqual(rt.run.outcome, "uncertain_result_today")
        self.assertEqual(bt.first_empty(("empty",), unscoped=True), (None, "uncertain_result_today"))

    # -- #3 the dispatch back key needs positive dispatch-page evidence
    def test_review3_unknown_page_with_back_key_is_never_left(self):
        rt = self.runtime("sample_dispatch_open")
        rt.dispatch_seconds = 12.0
        top = self.top(cards=(self.EMPTY, self.TEA, self.TEA))
        self.open_top(rt, top)
        self.assertTrue(rt.plan(self.ctx, top, {"step": "open_slot", "slot": 1}))
        unknown = _Frame({bc.BACK_READY})  # not the business top, not Home, undimmed back key: nothing else
        while not rt.run.finished:
            self.t += 1.0
            self.assertTrue(rt.check(self.ctx, unknown, {"stage": "dispatch"}))
            self.assertNotEqual(rt.run.phase, "dispatch_back_ready")
            self.assertFalse(rt.check(self.ctx, unknown, {"stage": "dispatch_page"}))
            self.assertFalse(rt.check(self.ctx, unknown, {"stage": "dispatch_back"}))
        self.assertEqual(rt.run.outcome, "dispatch_not_returned")
        self.assertIn("dispatch_page_unverified", rt.run.notes)
        # The state the review reached (dispatch_back_ready on the unknown page) no longer passes DispatchBack.
        shutil.rmtree(self.root, ignore_errors=True)
        rt = self.runtime("sample_dispatch_open")
        rt.run.phase = "dispatch_back_ready"
        self.assertFalse(rt.check(self.ctx, unknown, {"stage": "dispatch_page"}))
        self.assertFalse(rt.check(self.ctx, unknown, {"stage": "dispatch_back"}))
        self.assertEqual(rt.run.phase, "dispatch_back_ready")

    # -- #4 a WORK REPORT without valid readings is never read / claimed
    def report(self, partial):
        texts = {tuple(bc.REPORT_BOXES["exp"]): "500"} if partial else {}
        return _Frame({bc.REPORT_GUARD}, texts=texts)

    def run_single_report(self, rt, frame):
        rt.report_wait_seconds = 5.0
        while not rt.run.finished:
            self.t += 1.0
            rt.check(self.ctx, frame, {"stage": "report"})
            self.assertFalse(rt.plan(self.ctx, frame, {"step": "report_next"}))

    def test_review4_own_claim_report_without_readings_stays_uncertain(self):
        for partial in (False, True):
            with self.subTest(partial=partial):
                shutil.rmtree(self.root, ignore_errors=True)
                rt = self.runtime("sample_collect_one")
                top = self.top(clear=(1, 2, 3))
                self.open_top(rt, top)
                self.assertTrue(rt.plan(self.ctx, top, {"step": "claim", "slot": 1}))
                frame = self.report(partial)
                self.assertFalse(rt.check(self.ctx, frame, {"stage": "observe"}))
                self.assertEqual(rt.run.phase, "report_wait")
                self.run_single_report(rt, frame)
                self.assertEqual(rt.run.outcome, "report_unstable")
                self.assertEqual(self.events(rt), ["claim_intent", "claim_uncertain"])
                self.assertEqual(rt.log.events()[-1]["reason"], "report_readings_invalid")
                self.assertEqual(bt.uncertain_slots(rt.log.events()), (frozenset({1}), False))

    def test_review4_takeover_report_without_readings_resolves_nothing(self):
        rt = self.runtime("sample_collect_one", {"event": "claim_intent", "slot": 1},
                          {"event": "claim_uncertain", "slot": 1})
        frame = self.report(False)
        self.assertFalse(rt.check(self.ctx, frame, {"stage": "report_takeover"}))
        self.run_single_report(rt, frame)
        self.assertEqual(rt.run.outcome, "report_unstable")
        self.assertEqual(self.events(rt), ["claim_intent", "claim_uncertain"])
        self.assertEqual(bt.uncertain_slots(rt.log.events()), (frozenset({1}), False))

    def bulk_to_report(self, rt, frame):
        top = self.top(clear=(1, 2, 3), buttons=("SC.Business.Color.BulkComplete.Pink",
                                                 "SC.Business.Color.BulkDispatch.Grey"))
        self.open_top(rt, top)
        self.assertEqual(rt.run.phase, "bulk_ready")
        self.assertFalse(rt.check(self.ctx, top, {"stage": "top"}))  # B6g: the next fresh frame taps
        self.assertTrue(rt.plan(self.ctx, top, {"step": "bulk_complete"}))
        self.t += 1.0
        self.assertFalse(rt.check(self.ctx, frame, {"stage": "bulk_observe"}))
        self.assertEqual(rt.run.phase, "bulk_report_wait")

    def test_review4_bulk_report_without_readings_is_not_counted_or_completed(self):
        for partial in (False, True):
            with self.subTest(partial=partial):
                shutil.rmtree(self.root, ignore_errors=True)
                rt = self.runtime("bulk_default")
                rt.report_wait_seconds = 5.0
                frame = self.report(partial)
                self.bulk_to_report(rt, frame)
                while not rt.run.finished:
                    self.t += 1.0
                    rt.check(self.ctx, frame, {"stage": "bulk_report"})
                    self.assertFalse(rt.plan(self.ctx, frame, {"step": "bulk_report_next"}))
                self.assertEqual((rt.run.outcome, rt.run.bulk_reports), ("report_unstable", 0))
                self.assertEqual([(e["event"], e.get("reason")) for e in rt.log.events()],
                                 [("bulk_intent", None), ("bulk_uncertain", "report_readings_invalid")])
                self.assertEqual(bt.bulk_pending(rt.log.events()), {"complete": "report_readings_invalid"})

    def test_review4_valid_bulk_report_is_read_and_proves_the_press(self):
        rt = self.runtime("bulk_default")
        frame = _Frame({bc.REPORT_GUARD}, texts={tuple(bc.REPORT_BOXES["exp"]): "500",
                                                 tuple(bc.REPORT_BOXES["fans"]): "237,500人"})
        self.bulk_to_report(rt, frame)
        for _ in range(2):
            self.t += 1.0
            rt.check(self.ctx, frame, {"stage": "bulk_report"})
        self.assertEqual((rt.run.phase, rt.run.bulk_reports), ("bulk_report_ready", 1))
        self.assertEqual(self.events(rt), ["bulk_intent", "bulk_completed", "work_report"])
        self.assertTrue(rt.plan(self.ctx, frame, {"step": "bulk_report_next"}))

    def test_gift_page_is_a_variant_needing_banner_and_is_pressed_once_with_next(self):
        rt = self.runtime("bulk_default")
        lines = {tuple(bc.GIFT_BANNER_BOX): ["ファンからのプレゼント"],
                 tuple(bc.GIFT_TILES_BOX): ["×1", "×1", "×1", "x1"],
                 tuple(bc.GIFT_MESSAGE_BOX): ["所持枠を超えているものはプレゼントボックスに送られました"]}
        rt.box_texts = lambda vision, image, box, threshold=0.3: list(lines.get(tuple(box), []))
        gift = _Frame({bc.GIFT_GUARD})
        no_banner = _Frame({bc.GIFT_GUARD})
        vision = _Vision(self.ctx if hasattr(self, "ctx") else None)
        self.assertEqual(rt._report_variant(vision, gift), "gift")
        self.assertIsNone(_Frame() and rt._report_variant(vision, _Frame()))  # no template hit: never a report
        lines[tuple(bc.GIFT_BANNER_BOX)] = ["WORK"]
        self.assertIsNone(rt._report_variant(vision, no_banner))  # templates alone are not enough
        lines[tuple(bc.GIFT_BANNER_BOX)] = ["ファンからのプレゼント"]
        self.bulk_to_report(rt, gift)
        self.assertEqual(rt.run.report_variant, "gift")
        for _ in range(2):
            self.t += 1.0
            rt.check(self.ctx, gift, {"stage": "bulk_report"})
        self.assertEqual((rt.run.phase, rt.run.bulk_reports), ("bulk_report_ready", 0))  # not a counted report
        record = rt.log.events()[-1]
        self.assertEqual((record["event"], record["tiles_visible"], record["overflow_notice"]),
                         ("work_report_gift", 4, True))
        self.assertFalse(rt.plan(self.ctx, gift, {"step": "bulk_report_next"}))  # never Touch to Next
        self.assertTrue(rt.plan(self.ctx, gift, {"step": "page_next"}))  # the existing 次へ action
        self.assertEqual(rt.run.phase, "bulk_report_sent")

    def test_review_rules(self):
        self.assertEqual(bt.reobserve_blocked({1, 2, 3}, ("empty", "completed", "other")), ((1,), (3,)))
        self.assertEqual(bt.reobserve_blocked({3}, ("empty",)), ((), (3,)))  # missing reading = unknown
        self.assertTrue(bt.report_valid({"exp": 500, "fans": 237500, "business": None}))
        for bad in ({}, {"exp": 500, "fans": None}, {"exp": 0, "fans": 1}, {"exp": True, "fans": 1}, None):
            self.assertFalse(bt.report_valid(bad), bad)


class BusinessLivePathTests(unittest.TestCase):
    """2026-10-07 click-path readiness: ``bulk_default`` and ``sample`` through the --live WebView2 gate with the
    production frame permit (frame_guard_enabled=True) against a fake host. Same actions as the offline replay; every
    request is a frame-bound pipeline-click, none carries a spend grant (business is free; allow_spend stays empty)."""
    setUpClass = BusinessReplayTests.setUpClass
    setUp = BusinessReplayTests.setUp
    full_events = BusinessReportDispatchReplayTests.full_events

    def live(self, frames, option):
        from agent.core import webview_controller as wc
        from tests.test_task_launcher import RISK_POLICY, FakePipelineHost

        host = FakePipelineHost([f if isinstance(f, tuple) else (f, "click") for f in frames])
        session = {"version": 2, "frame_binding_protocol": wc.PIPELINE_FRAME_BINDING_PROTOCOL, "host": "127.0.0.1",
                   "port": 50009, "token": "a" * 64, "pid": host.PID, "input_enabled": True, "browser_demo_only": False,
                   "transport": wc.PIPELINE_TRANSPORT, "actions": TABLE.ids, "consuming_actions": TABLE.consuming_ids,
                   "action_table_sha256": TABLE.sha256, "spend_secret": "07" * 32, "audit_root": str(PRIVATE / "run_logs"),
                   "limits": {"min_interval_ms": 800, "interval_jitter_max_ms": 600, "per_action_per_minute": 12,
                              "run_max": 600, "run_spend_max": 8, "grant_max_ttl_ms": 5000, "session_max_ms": 3_600_000}}
        gate = wc.WebViewInputGate(host, wc.RunConfig(True, frozenset(TABLE.families)),
                                   {"pipeline": wc.Endpoint("pipeline", session)}, wc.SpendGate(),
                                   wc.AuditLog(self.root), host.send, policy=RISK_POLICY,
                                   # Static replay frames advance on capture, not on OCR wall time.
                                   # Production frame-age expiry is covered by frame-guard tests.
                                   clock=lambda: float(host.capture_seq),
                                   limits=wc.ClickLimits(run_max=launcher.DEFAULT_MAX_CLICKS,
                                                         task_max=launcher.DEFAULT_TASK_MAX_CLICKS))
        options = {"business": {"log_root": self.root, "now": lambda: NOW, "sample_root": self.root / "samples",
                                "sample_frames": 2, "sample_seconds": 0.0, "sample_interval": 0.0,
                                "settle_seconds": 0.0, "observe_seconds": 1.5, "observe_min_seconds": 0.0,
                                "confirm_seconds": 0.0, "report_wait_seconds": bt.REPORT_WAIT_SECONDS,
                                "report_no_effect_seconds": 0.5, "dispatch_seconds": 12.0, "dispatch_sample_seconds": 0.0,
                                "dispatch_return_seconds": 12.0, "top_wait_seconds": bt.TOP_WAIT_SECONDS,
                                "bulk_observe_seconds": bt.BULK_OBSERVE_SECONDS}}
        summary = launcher.run({"tasks": [{"name": "Business", "option": option}]}, live=True, gate=gate,
                               policy=RISK_POLICY, summary_path=False, save_diagnostics=False, task_options=options,
                               frame_guard_enabled=True)
        task, = summary["tasks"]
        report, = [r for r in task["reports"] if r["kind"] == "business"]
        return summary, task, report, host

    def test_bulk_default_through_the_frame_guarded_live_gate(self):
        summary, task, report, host = self.live(
            [HOME, TOP_EMPTY1, REPORT, (REPORT_ANIM, 3), REPORT, TOP_ALL_EMPTY, TOP_DONE, HOME], BULK)
        self.assertEqual((task["status"], report["outcome"]), ("failed", "dispatched_binding_unverified"), task)
        actions = ["home-business", "business-bulk-complete", "business-report-next", "business-report-next",
                   "business-bulk-dispatch", "nav-back"]
        self.assertEqual([r["action"] for r in host.requests], actions)
        self.assertTrue(all(r["command"] == "pipeline-click" and "spend_grant" not in r and r["capture_seq"] > 0
                            and r["frame_binding_seq"] > 0 for r in host.requests))
        self.assertEqual([c["result"] for c in task["live_clicks"] if c.get("kind", "click") == "click"],
                         ["sent"] * len(actions))
        self.assertEqual((summary["real_input_attempts"], summary["allow_spend"]), (len(actions), []))
        self.assertEqual(bt.bulk_pending(self.full_events()), {})

    def test_sample_mode_through_the_frame_guarded_live_gate(self):
        summary, task, report, host = self.live([HOME, STAND_IN, HOME], {"营业模式": "只采样"})
        self.assertEqual((task["status"], report["outcome"]), ("succeeded", "sampled"), task)
        self.assertEqual([r["action"] for r in host.requests], ["home-business", "nav-back"])

    def test_error_dialog_after_bulk_complete_sends_nothing_more(self):
        summary, task, report, host = self.live([HOME, TOP_CLEAR, (ERROR_1012, "stay"), HOME], BULK)
        self.assertNotEqual(task["status"], "succeeded")
        self.assertEqual([r["action"] for r in host.requests], ["home-business", "business-bulk-complete"])


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(bc is not None, "需要 MaaFramework Python 包（营业 Runtime）")
class BusinessRewardSelectRuntimeTests(unittest.TestCase):
    """2026-10-10: 獲得報酬 (OK only, never 再営業へ) and 営業アイドル選択 (bind, then 決定 or ←) after the last report page.
    Fake screens through the runtime's real entry points; no input."""
    setUp = BusinessB6hReviewRuntimeTests.setUp
    clock = BusinessB6hReviewRuntimeTests.clock
    runtime = BusinessB6hReviewRuntimeTests.runtime

    def at_reward(self, redispatch="back"):
        import dataclasses
        rt = self.runtime("bulk_default")
        rt.run.config = dataclasses.replace(rt.run.config, redispatch=redispatch, team="")
        rt.report_wait_seconds = 30.0
        rt.run.phase, rt.run.bulk_at, rt.run.bulk_done = "bulk_report_sent", self.t, {"complete"}
        rt.run.report_variant = "gift"

        def rows(vision, image, box, threshold=0.3):
            lines = image.texts.get(tuple(box), [])
            return [{"text": t, "box": [box[0] + 4, box[1] + 4 + 20 * i, 80, 14]} for i, t in enumerate(lines)]
        rt.box_rows = rows
        return rt

    @staticmethod
    def reward_frame():
        return _Frame({bc.REWARD_GUARD}, texts={tuple(bc.REWARD_COLUMNS[0]): ["月岡恋鐘", "Visual上限+5"],
                                                tuple(bc.REWARD_COLUMNS[1]): ["白瀬咲耶", "イベント発生率UP(極小)"]})

    @staticmethod
    def select_frame(filled=5, business="雑誌インタビュー", duration="12時間00分", slots=3):
        texts = {}
        for k in range(slots):
            texts[tuple(bc.SELECT_NAME_BOX(k))] = [business]
            texts[tuple(bc.SELECT_DURATION_BOX(k))] = [duration]
            texts[tuple(bc.SELECT_LV_BOX(k))] = ["Lv.66 Lv.76 Lv.75 Lv.77 Lv.11"] if filled == 5 else ["Lv.66"]
        return _Frame({bc.SELECT_GUARD, bc.BACK_READY}, texts=texts)

    def to_select(self, rt):
        reward = self.reward_frame()
        self.t += 1.0
        self.assertFalse(rt.check(self.ctx, reward, {"stage": "bulk_observe"}))
        self.assertEqual(rt.run.phase, "reward_wait")
        for _ in range(2):
            self.t += 1.0
            rt.check(self.ctx, reward, {"stage": "reward"})
        self.assertEqual(rt.run.phase, "reward_ready")
        page = [e for e in rt.log.events() if e["event"] == "reward_page"][0]
        self.assertTrue(page["partial"] and not page["scrolled"] and page["count"] == 2, page)
        self.assertTrue(rt.plan(self.ctx, reward, {"step": "reward_redispatch"}))
        self.assertEqual(rt.run.phase, "reward_sent")
        sel = self.select_frame()
        self.t += 1.0
        self.assertFalse(rt.check(self.ctx, sel, {"stage": "post_observe"}))
        self.assertEqual(rt.run.phase, "select_wait")
        return sel

    def read_select(self, rt, frame):
        for _ in range(2):
            self.t += 1.0
            rt.check(self.ctx, frame, {"stage": "select"})

    def test_reward_page_logs_entries_and_presses_only_ok(self):
        rt = self.at_reward()
        self.to_select(rt)
        self.assertFalse(rt.plan(self.ctx, self.reward_frame(), {"step": "page_next"}))  # no 次へ plan on 獲得報酬
        self.assertFalse(rt.plan(self.ctx, self.reward_frame(), {"step": "reward_redispatch"}))  # one press per page

    def test_select_page_matched_default_leaves_with_back_not_decide(self):
        rt = self.at_reward()
        sel = self.to_select(rt)
        self.read_select(rt, sel)
        self.assertEqual((rt.run.phase, rt.run.back_outcome), ("select_back_ready", "collected_dispatch_disabled"))
        self.assertFalse(rt.plan(self.ctx, sel, {"step": "select_decide"}))
        record = [e for e in rt.log.events() if e["event"] == "select_page"][0]
        self.assertTrue(record["matched"] and len(record["slots"]) == 3, record)
        self.assertTrue(rt.plan(self.ctx, sel, {"step": "select_back"}))
        self.assertEqual(rt.run.phase, "select_back_sent")
        self.assertNotIn("bulk_intent", [e["event"] for e in rt.log.events()])
        self.t += 1.0
        rt.check(self.ctx, _Frame({bc.HOME_GUARD}), {"stage": "post_observe"})
        self.assertEqual(rt.run.outcome, "collected_dispatch_disabled")

    def test_select_page_decide_logs_intent_first_and_home_proves_it(self):
        rt = self.at_reward("decide")
        sel = self.to_select(rt)
        self.read_select(rt, sel)
        self.assertEqual(rt.run.phase, "select_ready")
        self.assertTrue(rt.plan(self.ctx, sel, {"step": "select_decide"}))
        self.assertEqual(rt.run.phase, "select_sent")
        events = rt.log.events()
        self.assertEqual((events[-1]["event"], events[-1]["action"], events[-1]["tap"]),
                         ("bulk_intent", "dispatch", "business-dispatch-decide"))
        self.assertEqual(bt.bulk_pending(events), {"dispatch": "intent_without_result"})
        self.assertFalse(rt.plan(self.ctx, sel, {"step": "select_decide"}))  # never twice
        for _ in range(2):
            self.t += 1.0
            rt.check(self.ctx, _Frame({bc.HOME_GUARD}), {"stage": "post_observe"})
        self.assertEqual(rt.run.outcome, "collected_and_redispatched")
        self.assertEqual(bt.bulk_pending(rt.log.events()), {})

    def test_start_animation_over_select_page_is_waited_out(self):
        """2026-10-14 live: Now Loading and the 「営」 animation kept 営業アイドル選択 visible > 8 s after 決定."""
        rt = self.at_reward("decide")
        sel = self.to_select(rt)
        self.read_select(rt, sel)
        self.assertTrue(rt.plan(self.ctx, sel, {"step": "select_decide"}))
        self.t += bt.REPORT_NO_EFFECT_SECONDS + 4
        rt.check(self.ctx, sel, {"stage": "post_observe"})
        self.assertIsNone(rt.run.outcome)
        for _ in range(2):
            self.t += 1.0
            rt.check(self.ctx, _Frame({bc.HOME_GUARD}), {"stage": "post_observe"})
        self.assertEqual(rt.run.outcome, "collected_and_redispatched")

    def test_select_page_binding_failures_never_press_decide(self):
        for label, frame in (("unfilled", self.select_frame(filled=4)),
                             ("business", self.select_frame(business="ミニライブ")),
                             ("duration", self.select_frame(duration="3時間00分")),
                             ("slots", self.select_frame(slots=2))):
            with self.subTest(label):
                shutil.rmtree(self.root, ignore_errors=True)
                rt = self.at_reward("decide")
                self.to_select(rt)
                self.read_select(rt, frame)
                self.assertEqual((rt.run.phase, rt.run.back_outcome),
                                 ("select_back_ready", "redispatch_binding_mismatch"))
                self.assertFalse(rt.plan(self.ctx, frame, {"step": "select_decide"}))
                self.assertNotIn("bulk_intent", [e["event"] for e in rt.log.events()])

    def test_empty_select_page_while_loading_is_waited_out_then_decided(self):
        # 2026-10-12 live: three 「未選択」 slots (決定 grey) for a few seconds after 再営業へ, then the previous teams
        empty = _Frame({bc.SELECT_GUARD, bc.BACK_READY}, texts={tuple(bc.SELECT_NAME_BOX(k)): ["未選択"] for k in range(3)})
        rt = self.at_reward("decide")
        self.to_select(rt)
        self.read_select(rt, empty)
        self.read_select(rt, empty)
        self.assertEqual((rt.run.phase, rt.run.back_outcome), ("select_wait", None))
        self.read_select(rt, self.select_frame())
        self.assertEqual(rt.run.phase, "select_ready")
        self.assertTrue(rt.plan(self.ctx, self.select_frame(), {"step": "select_decide"}))

    def test_uncertain_dispatch_today_is_never_repeated(self):
        rt = self.at_reward("decide")
        rt.log.append({"event": "bulk_uncertain", "action": "dispatch", "reason": "unknown_screen_after_decide"})
        sel = self.to_select(rt)
        self.read_select(rt, sel)
        self.assertEqual((rt.run.phase, rt.run.back_outcome), ("select_back_ready", "uncertain_result_today"))

    def test_unknown_screen_after_decide_is_uncertain_not_retried(self):
        rt = self.at_reward("decide")
        sel = self.to_select(rt)
        self.read_select(rt, sel)
        self.assertTrue(rt.plan(self.ctx, sel, {"step": "select_decide"}))
        while not rt.run.finished:
            self.t += 5.0
            rt.check(self.ctx, _Frame(), {"stage": "post_observe"})
        self.assertEqual(rt.run.outcome, "redispatch_unverified")
        self.assertEqual(bt.bulk_pending(rt.log.events()), {"dispatch": "unknown_screen_after_decide"})

    def test_pure_rules(self):
        config = bt.BusinessConfig()
        ok = [{"name": "雑誌インタビュー", "duration": "12時間00分", "filled": 5}] * 3
        self.assertTrue(bt.select_binding(ok, config)["matched"])
        self.assertFalse(bt.select_binding(ok, config, ["ジュエル"])["matched"])
        self.assertEqual(bt.BusinessConfig().redispatch, "decide")
        with self.assertRaises(ValueError):
            bt.BusinessConfig.from_params({"redispatch": "always"})
        self.assertEqual(bt.badge_kind((160, 90, 240)), "P")  # BGR pink
        self.assertEqual(bt.badge_kind((240, 140, 70)), "S")
        rows = [{"text": "月岡恋鐘", "box": [480, 135, 90, 25]}, {"text": "Visual上限+5", "box": [390, 178, 120, 25]}]
        self.assertEqual(bt.ex_skill_entries(rows, ["P"]),
                         [{"index": 0, "idol": "月岡恋鐘", "effect": "Visual上限+5", "badge": "P"}])
