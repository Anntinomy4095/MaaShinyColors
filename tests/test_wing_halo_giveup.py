"""say "Halo" give-up through the W.I.N.G. navigator: RunOptions.halo_giveup, the wing-giveup family
and the host hold v2 for 「諦める」."""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from agent.core import pipeline_actions as pa
from agent.core import produce_config as pc
from agent.core import webview_controller as wc
from agent.core import wing_nav as nav
from tests.test_wing_live_wiring import enabled_table

ROOT = Path(__file__).resolve().parents[1]
H0 = ROOT / "dev_assets/private/wing_samples/2026-11-01"
H0_FRAMES = ["20261101T072141845281Z-r03c30-before.png", "20261101T072141845281Z-r03c30-after.png",
             "20261101T072712350188Z-capture.png", "20261101T072724129545Z-r16c29-after.png",
             "20261101T072808643330Z-capture.png", "20261101T072827599718Z-capture.png",
             "20261101T073010366636Z-capture.png", "20261101T073104822444Z-capture.png",
             "20261101T073125061758Z-capture.png", "20261101T073143128659Z-capture.png"]
READ = {"abandon_hold_hint": True, "abandon_button": True}


def runtime(giveup=True):
    return nav.WingNavRuntime(options=nav.RunOptions(input_enabled=True, halo_giveup=giveup))


class HaloGiveUpDecisionTests(unittest.TestCase):
    def test_hub_without_the_option_stops(self):
        decision = runtime(giveup=False).decide("HaloHub", {})
        self.assertEqual((decision.action, decision.reason), ("stop", "halo_not_automated"))

    def test_hub_opens_the_dialog_once_then_holds_giveup(self):
        rt = runtime()
        self.assertEqual(rt.decide("HaloHub", {}).intent, "giveup_open")
        self.assertEqual(rt.decide("HaloHub", {}).reason, "giveup_open_no_effect")     # the tap did not open it
        rt = runtime()
        rt.decide("HaloHub", {})
        decision = rt.decide("AbandonDialog", READ)
        self.assertEqual((decision.action, decision.intent), ("click", "giveup_hold"))
        self.assertEqual(rt.memory.giveup, "held")
        self.assertEqual(rt.decide("AbandonDialog", READ).reason, "abandon_dialog_not_automated")   # never twice
        self.assertEqual(rt.decide("FesBirth", {}).intent, "fes_birth")

    def test_dialog_maa_did_not_open_or_did_not_read_stops(self):
        self.assertEqual(runtime().decide("AbandonDialog", READ).reason, "abandon_dialog_not_automated")
        self.assertEqual(runtime(giveup=False).decide("AbandonDialog", READ).reason, "abandon_dialog_not_automated")
        rt = runtime()
        rt.decide("HaloHub", {})
        self.assertEqual(rt.decide("AbandonDialog", {"abandon_hold_hint": True}).reason, "abandon_dialog_unread")
        self.assertEqual(rt.memory.giveup, "opened")

    def test_fes_birth_still_needs_an_evaluation_or_the_giveup(self):
        self.assertEqual(runtime().decide("FesBirth", {}).reason, "fes_birth_without_evaluation")

    def test_giveup_actions_are_admitted_only_with_the_option(self):
        from tools import run_tasks

        ids = {"wing-giveup-open", "wing-giveup-hold"}
        with mock.patch.object(pa, "_DEFAULT", enabled_table()):
            self.assertFalse(ids & run_tasks.wing_task_actions())
            self.assertEqual(ids & run_tasks.wing_task_actions(giveup=True), ids)
            plan = run_tasks.wing_plan({"name": "Wing", "option": {"halo_giveup": True}})
            self.assertTrue(run_tasks.wing_plan_giveup(plan))
            self.assertFalse(run_tasks.wing_plan_giveup(run_tasks.wing_plan({"name": "Wing", "option": {}})))
        table = pa.default_table()
        self.assertEqual({a.family for a in table.actions if a.id in ids}, {"wing-giveup"})
        self.assertFalse(any(table.get(i).consuming for i in ids))

    def test_hold_v2_adds_only_the_giveup(self):
        self.assertEqual(wc.HOLD_ACTIONS[wc.HOLD_PROTOCOL_V2] - wc.HOLD_ACTIONS[wc.HOLD_PROTOCOL], {"wing-giveup-hold"})
        self.assertNotIn("wing-giveup-hold", wc.HOLD_ACTIONS[wc.HOLD_PROTOCOL])
        self.assertTrue(wc.HOLD_MIN_MS <= nav.GIVEUP_HOLD_MS <= wc.HOLD_MAX_MS)


@unittest.skipUnless(all((H0 / n).is_file() for n in H0_FRAMES), "需要 2026-11-01 H0 实机帧")
class HaloGiveUpReplayTests(unittest.TestCase):
    def test_h0_frames_give_up_and_reach_home(self):
        # H0 live: Hub → 「諦める ホームに戻る」 → dialog (long press) → フェスアイドル誕生 → 確認 → 総合評価 →
        # クリア報酬 → 獲得報酬 OK (books are read by KnowhowScan afterwards) → 詳細 → はづき → ミッション達成.
        from tests.test_wing_live_wiring import OfflineMaaReplayTests
        from tools import run_tasks as launcher

        cfg = pc.default_config("true_end")
        cfg["manual"].update(reward_ok=True, reward_read=False)
        images, advances = launcher.load_frames([(H0 / n, "click") for n in H0_FRAMES])
        with mock.patch.object(pa, "_DEFAULT", enabled_table()):
            out = launcher.run_wing_offline(images, advances, {"halo_giveup": True, "produce_config": cfg, "sp_mode": "skip"},
                                            extra_override=OfflineMaaReplayTests.SHORT_AFTER, timeout_s=300.0)
        scenario, session = out["scenario"], out["session"]
        self.assertEqual(scenario.actions, ["wing-giveup-open", "wing-giveup-hold", "wing-fes-birth", "wing-fes-birth-ok",
                                            "wing-total-eval-tap", "wing-clear-reward-ok", "wing-reward-ok",
                                            "wing-fes-detail-ok", "wing-tap-textbox"])
        self.assertEqual(scenario.holds, [("wing-giveup-hold", nav.GIVEUP_HOLD_MS)])
        self.assertEqual(scenario.spends, [])
        self.assertEqual(session.runtime.finished, "produce_finished_home", session.runtime.log[-4:])

    def test_without_the_option_the_hub_is_left_alone(self):
        from tests.test_wing_live_wiring import OfflineMaaReplayTests
        from tools import run_tasks as launcher

        images, advances = launcher.load_frames([(H0 / H0_FRAMES[0], "click")])
        with mock.patch.object(pa, "_DEFAULT", enabled_table()):
            out = launcher.run_wing_offline(images, advances, {"sp_mode": "skip"},
                                            extra_override=OfflineMaaReplayTests.SHORT_AFTER, timeout_s=120.0)
        self.assertEqual(out["scenario"].actions, [])
        self.assertEqual(out["session"].runtime.finished, "halo_not_automated")


if __name__ == "__main__":
    unittest.main()
