"""wing_nav plumbing of the W.I.N.G. T.E. settings (produce_config.wing_params) and the end-of-Produce gate
(agent.core.produce_end). Offline: no Maa, no input; LIVE_INPUT_ENABLED stays False."""
from __future__ import annotations

import unittest

from agent.core import produce_config as pc
from agent.core import produce_end as pe
from agent.core import wing_nav as nav


def default_config(goal="true_end"):
    return pc.ProduceConfig.from_dict(pc.default_config(goal))


class SettingsPlumbing(unittest.TestCase):
    def test_default_config_keeps_library_defaults(self):
        rt = nav.WingNavRuntime.from_produce_config(default_config())
        params = pc.wing_params(default_config())
        self.assertEqual(rt.policy_params, params["policy"])
        self.assertEqual(rt.params["items"], [])
        calls = {}

        def live(readings, memory, *rest):
            calls["live"] = rest
            return {"action": "wait", "reason": "x"}
        rt.overrides["live"] = live
        rt.decide("Live", {"auto": "ON"})
        # Default live params are empty -> the hook is called exactly as before (two arguments).
        self.assertEqual(calls["live"], () if not params["live"] else (params["live"],))

    def test_live_and_event_params_are_passed(self):
        rt = nav.WingNavRuntime(params={"live": {"focus_bonus": 0.0}, "event": {"default_index": 2,
                                                                                "accept_promises": False}})
        seen = {}

        def live(readings, memory, params=None):
            seen["live"] = params
            return {"action": "wait"}

        def event(options, **kw):
            seen["event"] = kw
            from agent.core.wing_events import EventChoice
            return EventChoice(2, "default_choice")
        rt.overrides.update(live=live, event=event)
        rt.decide("Live", {})
        self.assertEqual(seen["live"], {"focus_bonus": 0.0})
        d = rt.decide("Choice", {"options": {"Left": "a", "Center": "b", "Right": "c"}})
        self.assertEqual(seen["event"], {"default_index": 2, "accept_promises": False})
        self.assertEqual(d.intent, "choice_right")

    def test_event_default_matches_wing_events_defaults(self):
        rt = nav.WingNavRuntime.from_produce_config(default_config())
        a = rt.decide("Choice", {"options": {"Left": "はい", "Right": "いいえ"}})
        b = nav.WingNavRuntime().decide("Choice", {"options": {"Left": "はい", "Right": "いいえ"}})
        self.assertEqual((a.intent, a.reason), (b.intent, b.reason))

    def test_sp_and_items_params_are_carried(self):
        rt = nav.WingNavRuntime(params={"sp": {"cap_margin": 150}, "items": []})
        rt.memory.pending = "spend_sp"
        stop = rt.decide("Review", {"sp": 40})
        self.assertEqual(stop.reason, "sp_panel_reader_missing")
        self.assertEqual(stop.detail["sp_params"], {"cap_margin": 150})
        item = rt.decide("ItemSelect")
        self.assertEqual((item.action, item.reason), ("stop", "setup_requires_user"))   # attach (default) mode
        start = nav.WingNavRuntime(params={"items": ["p_secret_memo"]}, options=nav.RunOptions(mode="start"))
        refused = start.decide("ItemSelect", {"items_empty": True})
        self.assertEqual((refused.reason, refused.detail["items"]), ("p_items_not_supported", ["p_secret_memo"]))


class EndGatePlumbing(unittest.TestCase):
    def setUp(self):
        self.rt = nav.WingNavRuntime()
        self.rt.begin()

    def test_evaluation_creates_collector_and_continues(self):
        d = self.rt.decide("Evaluation", {"final_fans": 523163, "affection": 15, "texts": [], "dialog": False})
        self.assertEqual(d.intent, "eval_next")
        self.assertIsInstance(self.rt.memory.end, pe.EndCollector)
        self.assertEqual(self.rt.memory.end.values["final_fans"], 523163)

    def test_reward_hands_over_with_payload(self):
        # reading the reward tabs is on by default (verified taps); without it the page hands over with the payload
        self.rt = nav.WingNavRuntime(params={"end": {"read": False}})
        self.rt.begin()
        self.rt.decide("Evaluation", {"final_fans": 1000})
        d = self.rt.decide("Reward", {"texts": ["獲得報酬", "保護", "売却", "再抽選"]})
        self.assertEqual((d.action, d.reason), ("stop", "produce_end_review"))
        self.assertTrue(pe.payload_is_safe(d.detail["payload"]))
        self.assertEqual(nav.RULES["Reward"].kind, "decide")

    def test_destructive_dialog_hard_stops(self):
        d = self.rt.decide("FesDetail", {"texts": ["ノウハウブックを売却しますか"], "dialog": True})
        self.assertEqual((d.action, d.reason), ("stop", "produce_end_destructive_dialog"))
        stop = self.rt.decide("EndDestructiveDialog")
        self.assertEqual((stop.action, stop.reason), ("stop", "produce_end_destructive_dialog"))
        order = [r.screen for r in nav.SCREENS]
        self.assertLess(order.index("EndDestructiveDialog"), order.index("Reward"))
        self.assertLess(order.index("EndDestructiveDialog"), order.index("FesDetail"))

    def test_fes_birth_confirm_gate(self):
        self.rt.memory.wing_won = True
        ok = self.rt.decide("FesBirthConfirm", {"sp_lost": 10, "dialog": True})
        self.assertEqual(ok.intent, "fes_birth_ok")
        strict = nav.WingNavRuntime(end_config=pe.EndGateConfig(max_sp_lost=5))
        strict.memory.wing_won = True
        d = strict.decide("FesBirthConfirm", {"sp_lost": 10, "dialog": True})
        self.assertEqual((d.action, d.reason), ("stop", "produce_end_review"))

    def test_mission_finish_carries_payload(self):
        self.rt.decide("Evaluation", {})
        d = self.rt.decide("MissionPopup", {"mission_text": "TrueEndを見よう"})
        self.assertEqual(d.action, "finish")
        self.assertIn("payload", d.detail)
        self.assertTrue(pe.payload_is_safe(d.detail["payload"]))

    def test_no_target_overlaps_reroll_and_input_stays_off(self):
        self.assertFalse(nav.LIVE_INPUT_ENABLED)
        rx, ry, rw, rh = (909, 624, 166, 77)      # Reward 再抽選
        x, y, w, h = nav.TARGETS["reward_ok"][0]
        self.assertTrue(x >= rx + rw or x + w <= rx)


if __name__ == "__main__":
    unittest.main()
