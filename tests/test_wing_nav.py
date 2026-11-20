"""W.I.N.G. navigation framework contract (wing.json + agent.core.wing_nav); offline, no Maa, no input."""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest import mock

from agent.core import wing_nav as nav
from agent.core import wing_screen as wsc
from tools import check_project as cp
from tools import wing_nav_build as build

ROOT = Path(__file__).resolve().parent.parent


class PipelineContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.nodes = wsc.load_nodes()
        cls.nav = json.loads(build.OUTPUT.read_text(encoding="utf-8"))
        cls.wrapped = {k: (build.OUTPUT, v) for k, v in cls.nodes.items()}

    def test_committed_file_matches_generator(self):
        self.assertEqual(build.OUTPUT.read_text(encoding="utf-8"), build.render(build.build()))

    def test_loop_order_is_rule_order(self):
        order = wsc.dispatch_order(self.nodes)
        self.assertEqual([s for _, s in order], [r.screen for r in nav.SCREENS])
        self.assertEqual(len({r.screen for r in nav.SCREENS}), len(nav.SCREENS))
        for _, screen in order:
            self.assertIn(wsc.SCREEN_PREFIX + screen, self.nodes, screen)
        self.assertEqual(self.nodes[wsc.LOOP]["on_error"], ["SC.Wing.Stop.Lost"])

    def test_loop_asks_frequent_screens_first_and_every_handler_is_gated_by_the_classification(self):
        loop = self.nodes[wsc.LOOP]["next"]
        priority = self.nodes[wsc.PRIORITY]["next"]
        self.assertEqual(loop[-1], "SC.Wing.Halted")
        self.assertEqual(sorted(loop[:-1]), sorted(priority))
        self.assertEqual(loop[:len(build.HOT_SCREENS)], ["SC.Wing.On." + s for s in build.HOT_SCREENS])
        for handler in loop[:-1]:
            screen = handler[len("SC.Wing.On."):]
            self.assertEqual(self.nodes[handler]["all_of"][0], "SC.Wing.Is." + screen, handler)

    def test_all_disabled_and_only_table_bound_clicks(self):
        from agent.core import pipeline_actions as pa
        table = pa.default_table()
        bound = table.node_actions(include_disabled=True)
        for name, node in self.nav.items():
            self.assertIs(node["enabled"], False, name)
            action = node.get("action", "DoNothing")
            if action in cp.INPUT_ACTIONS:
                self.assertEqual(action, "Click", name)
                self.assertTrue(name.startswith("SC.Wing.Click."), name)
                intent = name.rsplit(".", 1)[1]
                self.assertEqual(bound[name], nav.action_id(intent), name)
                self.assertEqual(node["target"], list(table.get(bound[name]).rect), name)
                # 2026-10-11 review P1-5: the table enables exactly the live-verified targets (user W.I.N.G. approval).
                self.assertEqual(table.get(bound[name]).enabled, nav.TARGETS[intent][1], name)

    def test_every_action_node_is_guarded_with_success_and_error_paths(self):
        acts = [n for n in self.nav if n.startswith("SC.Wing.Click.")]
        self.assertTrue(acts)
        for name in acts:
            node = self.nav[name]
            screen, intent = name[len("SC.Wing.Click."):].rsplit(".", 1)
            self.assertIn(intent, nav.screen_intents(screen), name)
            self.assertEqual(node["all_of"], ["SC.Wing.Pick." + screen + "." + intent, wsc.SCREEN_PREFIX + screen,
                                              "SC.Wing.State.Settled"], name)
            self.assertEqual(self.nav["SC.Wing.Pick." + screen + "." + intent]["custom_recognition_param"],
                             {"screen": screen, "intent": intent})
            self.assertTrue(cp._is_positive_guard(node, self.wrapped), name)
            self.assertEqual(node["action"], "Click")
            self.assertEqual(node["on_error"], ["SC.Wing.Stop.ClickUnconfirmed"])
            (after,) = node["next"]
            self.assertTrue(cp._is_positive_guard(self.nodes[after], self.wrapped), after)
            self.assertEqual(self.nodes[after]["on_error"], ["SC.Wing.Stop.Uncertain"])
            # 2026-10-09c: the success guard is the Agent's one-OCR classification of the new frame
            expect = self.nodes["SC.Wing.Expect." + screen]
            self.assertEqual((expect["custom_recognition"], expect["custom_recognition_param"]["want"]),
                             ("SC.Wing.State", "expect"))
            self.assertEqual(expect["custom_recognition_param"]["screens"], list(nav.RULES[screen].expect))
            for ref in expect["custom_recognition_param"]["screens"]:
                self.assertIn(wsc.SCREEN_PREFIX + ref, self.nodes, ref)

    def test_handlers_follow_their_rule(self):
        for rule in nav.SCREENS:
            on = self.nav["SC.Wing.On." + rule.screen]
            # 2026-10-09c: cheap Agent pre-guard (one whole-frame OCR per frame), then the native screen guard
            self.assertEqual(on["all_of"][0], "SC.Wing.Is." + rule.screen)
            self.assertEqual(self.nav["SC.Wing.Is." + rule.screen]["custom_recognition_param"],
                             {"want": "is", "screen": rule.screen})
            on = dict(on, all_of=on["all_of"][1:])
            # 2026-10-25 speed batch: wait and decision handlers rely on Is (no second native guard); stop/finish keep
            # it; every Click node keeps the native guard on the tapped frame.
            if rule.kind == "wait":
                self.assertEqual(on["all_of"], ["SC.Wing.State.Wait." + rule.screen])
                self.assertEqual(on["next"], ["SC.Wing.Nav.Loop"])
            elif rule.kind in ("stop", "finish"):
                self.assertEqual(on["all_of"], [wsc.SCREEN_PREFIX + rule.screen, "SC.Wing.State.Active"])
                self.assertNotIn("next", on)
                self.assertIn(on["custom_action"], ("SC.Wing.Stop", "SC.Wing.Finish"))
            else:
                clicks = ["SC.Wing.Click." + rule.screen + "." + i for i in nav.screen_intents(rule.screen)]
                self.assertTrue(clicks, rule.screen)
                self.assertEqual(on["all_of"], ["SC.Wing.State.Round." + rule.screen])
                for click in clicks:
                    self.assertIn(wsc.SCREEN_PREFIX + rule.screen, self.nav[click]["all_of"], click)
                self.assertEqual(on["next"], clicks + ["SC.Wing.Hold." + rule.screen, "SC.Wing.Requeue",
                                                       "SC.Wing.Halted"])
                self.assertTrue(rule.expect, rule.screen)
                if rule.kind == "fixed":
                    self.assertIn(rule.intent, nav.TARGETS)

    def test_custom_actions_are_registered(self):
        registered = cp.registered_custom_actions(ROOT)
        for node in self.nav.values():
            if node.get("action") == "Custom":
                self.assertIn(node["custom_action"], registered)

    def test_rects_inside_canvas_and_reroll_never_targeted(self):
        reroll = (909, 624, 166, 77)   # Reward 再抽選 (stamina 30): must stay outside every rect
        for intent, ((x, y, w, h), _) in nav.TARGETS.items():
            self.assertTrue(x >= 0 and y >= 0 and x + w <= 1280 and y + h <= 720, intent)
            rx, ry, rw, rh = reroll
            if intent in ("reward_ok",):
                self.assertTrue(x >= rx + rw or x + w <= rx, intent)

    def test_consuming_screens_stop(self):
        # 使用確認 is a gated decision whose only intent is the consuming start (wing-produce-start).
        self.assertEqual(nav.RULES["StartConfirm"].kind, "decide")
        self.assertEqual(nav.screen_intents("StartConfirm"), ("start_confirm_ok",))
        # 2026-10-10: the 獲得報酬 再抽選 confirmation (stamina 30) is the second consuming intent; opt-in + launcher.
        self.assertEqual(nav.CONSUMING_INTENTS, {"start_confirm_ok": "wing-produce-start",
                                                 "reward_reroll_ok": "wing-reward-reroll-confirm"})
        self.assertEqual(nav.RULES["ItemUsed"].kind, "stop")          # second demo: a used item is never acted on
        # 大富豪 (batch c): menu/discard always stop; the other pages decide but stop unless the run opted in
        # (start + from_home + minigame "before_start", tests/test_wing_full_auto.py).
        self.assertEqual(nav.RULES["MinigameMenu"].kind, "stop")
        rt = nav.WingNavRuntime()
        rt.begin()
        for screen in ("MinigameTop", "MinigameCourse", "MinigameRules", "MinigameGame", "MinigameResult"):
            self.assertEqual(rt.decide(screen, {}).reason, "minigame_not_automated", screen)
        # 2026-10-14: オプション is read (プロデュース設定, agent/core/wing_options.py); Maa's own read pass only wheels the
        # list and taps ← (both unverified until a live sample); without the launcher's settings record it stops.
        self.assertEqual((nav.RULES["Options"].kind, nav.screen_intents("Options")),
                         ("decide", ("options_scroll_down", "options_back", "options_unread_off", "options_popup_off",
                                     "options_save", "options_saved_close")))
        self.assertEqual(rt.decide("Options", {}).reason, "settings_not_automated")
        order = [r.screen for r in nav.SCREENS]
        self.assertLess(order.index("MinigameRules"), order.index("Tutorial"))   # its rule pages look like a tutorial
        self.assertLess(order.index("HomeMinigamePopup"), order.index("Home"))
        self.assertLess(order.index("ItemUseConfirm"), order.index("Hub"))
        self.assertLess(order.index("ReplaceDialog"), order.index("ForgetSelect"))
        self.assertLess(order.index("ActiveSkills"), order.index("Schedule"))

    def test_third_demo_dialogs_stop_and_end_flow_ranks_before_commu(self):
        """Third demo: 諦める / team edits / 売却 mode / popups over フェスアイドル詳細 are never answered;
        the end pages (OK at the commu OK place) are ranked before the commu family, the destructive dialog first
        among them but after the named help popups (アイドルの応援とは mentions ジュエル)."""
        # 2026-11-01: 諦める is answered only for a say "Halo" give-up Maa opened itself (RunOptions.halo_giveup).
        self.assertEqual(nav.RULES["AbandonDialog"].kind, "decide")
        self.assertEqual(nav.WingNavRuntime().decide("AbandonDialog", {}).reason, "abandon_dialog_not_automated")
        for screen, reason in (("UnitEditDialog", "team_edit_not_automated"),
                               ("RewardSellMode", "reward_sell_mode"),
                               ("FesDetailPopup", "end_popup_not_automated"), ("IdolList", "outside_produce")):
            self.assertEqual((nav.RULES[screen].kind, nav.RULES[screen].reason), ("stop", reason), screen)
        order = [r.screen for r in nav.SCREENS]
        for page in ("EndDestructiveDialog", "Evaluation", "TotalEval", "ClearReward", "Reward", "RewardDetail",
                     "FesDetail"):
            self.assertLess(order.index(page), order.index("PassCommu"), page)
            self.assertLess(order.index(page), order.index("Commu"), page)
        self.assertLess(order.index("CheerHelp"), order.index("EndDestructiveDialog"))
        self.assertLess(order.index("EndDestructiveDialog"), order.index("Evaluation"))
        self.assertLess(order.index("RewardSellMode"), order.index("Reward"))
        self.assertLess(order.index("Reward"), order.index("RewardDetail"))      # the Ex-tab variant is looser
        self.assertLess(order.index("FesDetailPopup"), order.index("FesDetail"))
        # 売却する sits exactly where reward_ok is: only the sell-mode screen (a stop) may see it.
        self.assertIn("RewardDetail", nav.END_FLOW)
        self.assertEqual(nav.RULES["RewardDetail"].point, "produce_end")

    def test_item_use_is_never_confirmed(self):
        rt = nav.WingNavRuntime()
        rt.begin()
        decision = rt.decide("ItemUseConfirm")
        self.assertEqual((decision.action, decision.intent, decision.reason),
                         ("click", "item_use_cancel", "item_use_disabled"))
        self.assertFalse(nav.TARGETS["item_use_cancel"][1])          # no clean click on キャンセル yet
        self.assertEqual(rt.act("ItemUseConfirm", decision), (False, "target_not_verified"))
        self.assertEqual(rt.decide("ItemUsed").reason, "item_used_unexpectedly")
        # 使う buttons (each consumes a use) must stay outside the item targets of the same screen.
        uses = {"item_use_cancel": [(704, 549, 68, 41)],                  # 使用確認 (#2494)
                "item_popup_close": [(496, 479, 52, 30), (733, 464, 60, 25)]}   # プロデュースアイテム (#2501)
        for intent, buttons in uses.items():
            x, y, w, h = nav.TARGETS[intent][0]
            for ux, uy, uw, uh in buttons:
                self.assertTrue(x + w <= ux or x >= ux + uw or y + h <= uy or y >= uy + uh, intent)
        # 2026-10-14: the same-idol confirmation decides only inside the opt-in team edit; otherwise it stops.
        self.assertEqual(nav.WingNavRuntime(options=nav.RunOptions(mode="start")).decide("IdolPickerConfirm", {}).reason,
                         "team_edit_not_automated")
        # 2026-10-12: the picker is a decision point for the opt-in team edit only; without it the run stops there.
        self.assertEqual(nav.WingNavRuntime(options=nav.RunOptions(mode="start")).decide("IdolPicker", {}).reason,
                         "team_edit_not_automated")


class RuntimeDecisions(unittest.TestCase):
    def setUp(self):
        self.rt = nav.WingNavRuntime()
        self.rt.begin()

    def test_act_always_refused(self):
        decision = self.rt.decide("CommuSkip")
        self.assertEqual(decision.intent, "commu_skip")
        self.assertEqual(self.rt.act("CommuSkip", decision), (False, "live_input_disabled"))
        self.assertEqual(self.rt.acts, 0)

    def test_unverified_target_and_repeat_are_refused(self):
        tap = nav._click("unit_page_prev", "test")       # verified 2026-10-13; patched back to test the gate
        with mock.patch.dict(nav.TARGETS, {"unit_page_prev": (nav.TARGETS["unit_page_prev"][0], False)}):
            self.assertEqual(self.rt.act("UnitSelect", tap), (False, "target_not_verified"))
        skip = self.rt.decide("CommuSkip")
        for _ in range(nav.MAX_SAME_INTENT_IN_A_ROW - 1):
            self.rt.act("CommuSkip", skip)
        self.assertEqual(self.rt.act("CommuSkip", skip)[1], "live_input_disabled")
        self.assertEqual(self.rt.act("CommuSkip", skip)[1], "repeat_without_progress")

    def test_unknown_wait_and_stop(self):
        self.assertEqual(self.rt.decide(None).action, "wait")
        self.assertEqual(self.rt.decide("Loading").action, "wait")
        stop = self.rt.decide("StartConfirm")
        self.assertEqual((stop.action, stop.reason), ("stop", "spend_not_enabled"))
        self.assertEqual(self.rt.decide("MissionPopup").action, "finish")

    def test_wait_stall_stops(self):
        for _ in range(nav.WAIT_LIMIT_DEFAULT):
            self.assertTrue(self.rt.active_for("WeekCard", True))
        self.assertFalse(self.rt.active_for("WeekCard", True))
        self.assertEqual(self.rt.finished, "wait_stalled")

    def test_live_auto(self):
        # 2026-10-05: wing_audition.live_plan now exists; without the user's game_auto opt-in it never turns Auto on
        # (2026-10-05 integration: it stops with the hook's reason instead of waiting until the stall limit)
        off = self.rt.decide("Live", {"auto": "OFF"})
        self.assertEqual((off.action, off.reason), ("stop", "live:live_state_unread"))
        # the hook-less fallback is only for the user's explicit game_auto opt-in (memory.live_mode)
        self.rt.overrides["live"] = None
        self.assertEqual(self.rt.decide("Live", {"auto": "OFF"}).reason, "live_agent_mode_requires_wing_audition")
        self.rt.memory.live_mode = "game_auto"
        self.assertEqual(self.rt.decide("Live", {"auto": "OFF"}).intent, "live_auto")
        self.assertEqual(self.rt.decide("Live", {"auto": "ON"}).action, "wait")
        self.rt.decide("Live", {"auto": "OFF"})
        self.rt.decide("Live", {"auto": "OFF"})
        self.assertEqual(self.rt.decide("Live", {"auto": "OFF"}).reason, "live_auto_toggle_no_effect")
        self.assertEqual(self.rt.decide("Live", {}).action, "wait")

    def test_event_choice_uses_wing_events(self):
        d = self.rt.decide("Choice", {"options": {"Left": "夏葉なら大丈夫", "Center": "二週間後ボーカルレッスン", "Right": "まずは"}})
        self.assertEqual((d.intent, d.reason), ("choice_center", "accept_promise"))
        self.assertEqual(self.rt.decide("Choice", {"options": {"Left": "a"}}).reason, "choice_options_unread")

    def test_week_hook_maps_to_rooms(self):
        class Decision(dict):
            def to_dict(self):
                return dict(self)
        hook = mock.Mock(return_value=Decision(kind="lesson", option_id="lesson_vo", pre_actions=[]))
        rt = nav.WingNavRuntime(hooks={"week": hook})
        rt.begin()
        rooms = [{"id": "lesson_vo", "kind": "lesson", "attribute": "Vo", "label_ok": True, "selected": False}]
        readings = {"season": 2, "weeks_left": 7, "stats": {"Vo": 388, "Da": 173, "Vi": 118, "Me": 248},
                    "fans": 1545, "sp": 286, "stamina_pct": 100, "rooms": rooms, "selected_room": None}
        rt.memory.auditions_seen = {"key": [2, 7], "pages": [], "trend": None, "done": True}  # survey already done
        self.assertEqual(rt.decide("Schedule", readings).intent, "schedule_room_1")
        state = hook.call_args[0][0]
        self.assertEqual((state.season, state.week, state.fans), (2, 2, 1545))
        readings["selected_room"] = "lesson_vo"
        self.assertEqual(rt.decide("Schedule", readings).intent, "schedule_decide")
        readings["stats"] = None
        for _ in range(nav.SP_UNREAD_REREADS):            # a few re-reads first (live a9: animated week digit)
            d = rt.decide("Schedule", readings)
            # live 2026-10-25: the Schedule rule's "stop" fallback must not turn a counted re-read into a stop
            self.assertEqual((d.action, d.reason), ("wait", "state_unreadable_reread"))
        d = rt.decide("Schedule", readings)
        self.assertEqual((d.action, d.reason), ("stop", "state_unreadable"))

    def test_sp_unread_at_cap_is_reread_then_stops(self):
        rt = nav.WingNavRuntime(hooks={"week": mock.Mock(return_value={"kind": None, "rule": "sp_unread_at_cap"})})
        rt.begin()
        rooms = [{"id": "lesson_vo", "kind": "lesson", "attribute": "Vo", "label_ok": True, "selected": True}]
        readings = {"season": 3, "weeks_left": 7, "stats": {"Vo": 898, "Da": 216, "Vi": 144, "Me": 265},
                    "fans": 57458, "sp": None, "stamina_pct": 52, "rooms": rooms, "selected_room": "lesson_vo"}
        rt.memory.auditions_seen = {"key": [3, 7], "pages": [], "trend": None, "done": True}
        for _ in range(nav.SP_UNREAD_REREADS):
            d = rt.decide("Schedule", readings)
            self.assertEqual((d.action, d.reason), ("wait", "sp_unread_reread"))
        d = rt.decide("Schedule", readings)
        self.assertEqual((d.action, d.reason), ("stop", "week_decision_None"))

    def test_missing_scorer_stops(self):
        with mock.patch.dict(nav.SCORER_HOOKS, {"event": ("agent.core.no_such_module", "f")}):
            d = self.rt.decide("Choice", {"options": {"Left": "a", "Right": "b"}})
        self.assertEqual(d.reason, "scorer_missing:agent.core.no_such_module:f")

    def test_audition_fallback_matches_planned_reward(self):
        with mock.patch.object(nav, "resolve_hook", return_value=(None, "x:y")):
            # attach on the tab without any plan: back to the lesson tab (2026-10-09 live seg2 fix)
            self.assertEqual(self.rt.decide("AuditionTab", {"reward": 10000}).reason, "resume_from_audition_tab")
            self.rt.memory.pending = "rest"
            self.assertEqual(self.rt.decide("AuditionTab", {"reward": 10000}).reason, "audition_tab_without_plan")
            self.rt.memory.pending, self.rt.memory.pending_audition = "audition", 10000
            self.assertEqual(self.rt.decide("AuditionTab", {"reward": 15000}).intent, "audition_next")
            self.assertEqual(self.rt.decide("AuditionTab", {"reward": 10000}).intent, "schedule_decide")


class UnitNavigation(unittest.TestCase):
    """ユニット編成: 20 units on 2 tab pages; the team must not repeat an idol (P, S1–S4, guest)."""

    def decide(self, readings, target=7, team=(), **options):
        rt = nav.WingNavRuntime(nav.RunMemory(target_unit=target, team=list(team)),
                                options=nav.RunOptions(mode="start", **options))
        rt.begin()
        return rt.decide("UnitSelect", readings)

    def test_select_tab_on_current_page(self):
        d = self.decide({"unit_index": 3, "unit_total": 20, "unit_page": 0})
        self.assertEqual(d.intent, "unit_tab_7")
        self.assertEqual(d.target, nav.TARGETS["unit_tab_7"][0])

    def test_page_switch_then_tab(self):
        self.assertEqual(self.decide({"unit_index": 3, "unit_total": 20, "unit_page": 0}, target=15).intent,
                         "unit_page_next")
        self.assertEqual(self.decide({"unit_index": 12, "unit_total": 20, "unit_page": 1}, target=4).intent,
                         "unit_page_prev")
        self.assertEqual(self.decide({"unit_index": 12, "unit_total": 20, "unit_page": 1}, target=15).intent,
                         "unit_tab_5")

    def test_page_arrows_unverified(self):
        # 2026-10-12: › has a clean native pair (recording captures-20261011-164549Z #79 -> #82); ‹ enabled 2026-10-13
        # with the user's approval (live frame). Without live input the act is still refused.
        self.assertTrue(nav.TARGETS["unit_page_next"][1])
        self.assertTrue(nav.TARGETS["unit_page_prev"][1])
        rt = nav.WingNavRuntime(nav.RunMemory(target_unit=4), options=nav.RunOptions(mode="start"))
        rt.begin()
        d = rt.decide("UnitSelect", {"unit_index": 12, "unit_total": 20, "unit_page": 1})
        self.assertEqual(d.intent, "unit_page_prev")
        self.assertEqual(rt.act("UnitSelect", d), (False, "live_input_disabled"))

    def test_target_unit_reached_still_stops(self):
        self.assertEqual(self.decide({"unit_index": 7, "unit_total": 20, "unit_page": 0}).reason,
                         "team_fingerprint_unverified")
        # Only the user's explicit confirmation of the unit's team lets start mode continue (次へ, not a spend).
        self.assertEqual(self.decide({"unit_index": 7, "unit_total": 20, "unit_page": 0}, team_confirmed=True).intent,
                         "unit_next")
        attach = nav.WingNavRuntime(nav.RunMemory(target_unit=7))
        attach.begin()
        self.assertEqual(attach.decide("UnitSelect", {"unit_index": 7, "unit_total": 20, "unit_page": 0}).reason,
                         "setup_requires_user")

    def test_guards(self):
        self.assertEqual(self.decide({"unit_index": 3, "unit_total": 19, "unit_page": 0}).reason,
                         "unit_count_unexpected")
        self.assertEqual(self.decide({"unit_index": 3, "unit_total": 20}).reason, "unit_page_unread")
        self.assertEqual(self.decide({}, target=21).reason, "target_unit_invalid")

    def test_duplicate_idol_stops(self):
        team = [{"id": "p1", "kind": "P", "idol": "夏葉"}, {"id": "s1", "idol": "摩美々"},
                {"id": "s2", "idol": "夏葉"}]
        d = self.decide({"unit_index": 3, "unit_total": 20, "unit_page": 0}, team=team)
        self.assertEqual((d.reason, d.detail["idols"]), ("team_duplicate_idol", ["夏葉"]))
        ok = [{"id": "p1", "kind": "P", "idol": "夏葉"}, {"id": "s1", "idol": "摩美々"}]
        self.assertEqual(self.decide({"unit_index": 3, "unit_total": 20, "unit_page": 0}, team=ok).intent,
                         "unit_tab_7")


if __name__ == "__main__":
    unittest.main()


class SelectedCardSettleTest(unittest.TestCase):
    def test_unread_selected_card_waits_bounded_then_stops(self):
        import unittest.mock as um
        from agent.core import wing_live_policy as wlp
        rt = nav.WingNavRuntime()
        r = {"live_phase": "select_judge", "live_state": {"turn": 1}}
        with um.patch.object(wlp, "plan_turn", return_value={"action": "stop", "reason": "live_selected_card_unread"}):
            got = [rt._live_play(r, {}).action for _ in range(nav.LIVE_SELECTED_WAITS + 1)]
        self.assertEqual(got, ["wait"] * nav.LIVE_SELECTED_WAITS + ["stop"])

    def test_unread_turn_hands_the_live_to_game_auto(self):
        """2026-10-29 user (wing4b stopped on live_selected_card_unread): after the re-reads, Auto instead of a stop."""
        import unittest.mock as um
        from agent.core import wing_live_policy as wlp
        rt = nav.WingNavRuntime()
        r = {"live_phase": "select_judge", "live_state": {"turn": 1}, "auto": "OFF"}
        with um.patch.object(wlp, "plan_turn", return_value={"action": "stop", "reason": "live_selected_card_unread"}):
            got = [rt._live_play(r, {}) for _ in range(nav.LIVE_SELECTED_WAITS + 1)]
        self.assertEqual([d.action for d in got], ["wait"] * nav.LIVE_SELECTED_WAITS + ["click"])
        self.assertEqual((got[-1].intent, got[-1].reason), ("live_auto", "live_unread_auto"))
        self.assertTrue(rt.memory.live_auto_secured)
        self.assertEqual(rt.log[-1], {"event": "live_unread_auto", "reason": "live_selected_card_unread"})


class SameIdolOcrTests(unittest.TestCase):
    def test_one_character_misread_is_unverified_not_another_idol(self):
        """2026-10-14 live: the Hub bubble 「灯織」 read as 「灯罐」 stopped an attach run as resume_config_mismatch."""
        self.assertIs(nav.same_idol("灯織", "風野灯織"), True)
        self.assertIsNone(nav.same_idol("灯罐", "風野灯織"))
        self.assertIs(nav.same_idol("真乃", "風野灯織"), False)
        self.assertIs(nav.same_idol("めぐる", "風野灯織"), False)


class ReporterEventTests(unittest.TestCase):
    """2026-10-14 live 記者 event (S3 week start): menu-less commu lines and the two-button choice; default refuse."""
    TEXTS = {
        "invite": [("是非とも、お話を伺いたいと", (258, 618, 324, 26)), ("『風野灯織様のご活躍を拝見し、", (269, 583, 347, 33)),
                   ("プロデューサー", (273, 541, 192, 28))],
        "choice": [("取材を受ける", (141, 222, 193, 36)), ("密着取材の依頼だ", (258, 586, 202, 28)),
                   ("どうしようか……", (261, 616, 199, 28)), ("プロデューサー", (273, 541, 192, 28)),
                   ("取材を受けない", (912, 222, 228, 36))],
        "refused": [("やめておこう。", (257, 585, 161, 31)), ("残念だが今回は都合がつかないな", (257, 615, 379, 30)),
                    ("プロデューサー", (272, 538, 193, 32))],
        "ordinary": [("ありがとうございます", (257, 585, 300, 31)), ("プロデューサー", (273, 541, 192, 28))],
    }

    def classify(self, key):
        import numpy as np
        from agent.core import wing_screen as wsc
        cls = wsc.ScreenClassifier(customs={"SC.Wing.State": lambda p, f: True})
        frame = wsc.Frame(self.TEXTS[key], lambda: np.zeros((720, 1280, 3), np.uint8), key=key)
        return cls.classify(frame).screen

    def test_reporter_flow_refuses_and_advances(self):
        rt = nav.WingNavRuntime()
        rt.active = True
        for key, screen, intent in (("invite", "ReporterCommu", "tap_textbox"), ("choice", "ReporterChoice", "reporter_refuse"),
                                    ("refused", "ReporterCommu", "tap_textbox")):
            self.assertEqual(self.classify(key), screen, key)
            self.assertEqual(rt.decide(screen, {}).intent, intent, key)
        x, y, w, h = nav.TARGETS["reporter_refuse"][0]
        self.assertTrue(912 <= x and x + w <= 1140 and 222 <= y and y + h <= 258)   # inside 「取材を受けない」

    def test_ordinary_menu_less_line_is_not_a_reporter_commu(self):
        self.assertNotEqual(self.classify("ordinary"), "ReporterCommu")
