"""Replay the observed event fan notice without re-running business claims."""
import unittest
from pathlib import Path

from tools import run_tasks
from tests.test_task_launcher import HOME, private_ready

ROOT = Path(__file__).resolve().parents[1]
NOTICE = ROOT / "dev_assets/private/live-20261019/business-return-popup.png"
HELP = NOTICE.with_name("produce-event-help.png")
RECEIPT = NOTICE.with_name("produce-event-receipt.png")
PAGE = NOTICE.with_name("produce-event-page.png")


@unittest.skipUnless(private_ready() and all(p.is_file() for p in (NOTICE, HELP, RECEIPT, PAGE)), "需要本地实机奖励提示与 Maa 资源")
class EventFanRewardReplayTests(unittest.TestCase):
    def test_notice_closes_and_reaches_home(self):
        result = run_tasks.run(
            {"tasks": ["Startup"], "task_timeout_s": 15},
            offline_steps=[(p, "click") for p in (NOTICE, HELP, RECEIPT, PAGE, ROOT / HOME)],
            simulate_input=True, policy={"live_input_enabled": False},
            summary_path=False, save_diagnostics=False,
        )
        task, = result["tasks"]
        self.assertEqual(task["status"], "succeeded", task)
        self.assertEqual(task["simulated_actions"], ["popup-event-fan-reward-ok", "popup-help-close",
                                                   "popup-event-fan-receipt-ok", "nav-back"])
        self.assertEqual(result["real_input_attempts"], 0)


AFTER_MISSION = ROOT / "dev_assets/private/popup-samples-20261101/mission-completed-after-produce.png"
MISSION_THEN = AFTER_MISSION.with_name("event-reward-after-mission.png")


@unittest.skipUnless(private_ready() and AFTER_MISSION.is_file() and MISSION_THEN.is_file(), "需要 2026-11-01 Produce 后首页弹窗原生帧")
class MissionThenEventRewardReplayTests(unittest.TestCase):
    def test_event_reward_after_mission_completed_is_closed_too(self):
        # 2026-11-01 live (after a Halo give-up): 「ミッション達成」 OK, then 「イベント累計報酬獲得」 at once; Startup failed
        # because MissionCompleted.Settled did not accept the second popup.
        result = run_tasks.run(
            {"tasks": ["Startup"], "task_timeout_s": 15},
            offline_steps=[(AFTER_MISSION, "click"), (MISSION_THEN, "click"), (ROOT / HOME, "click")],
            simulate_input=True, policy={"live_input_enabled": False},
            summary_path=False, save_diagnostics=False,
        )
        task, = result["tasks"]
        self.assertEqual(task["status"], "succeeded", task)
        self.assertEqual(task["simulated_actions"], ["popup-mission-completed-ok", "popup-event-fan-reward-ok"])
        self.assertEqual(result["real_input_attempts"], 0)

    @unittest.skipUnless(all(MISSION_THEN.with_name(f"scenario-event-{n}.png").is_file()
                             for n in ("help", "mission-receipt", "play-confirm", "page")), "需要 2026-11-01 シナリオイベント页原生帧")
    def test_event_reward_ok_returns_home_through_the_scenario_event_page(self):
        # 2026-11-01 live: the OK opened シナリオイベント「果てにふれるのなら」: 「今回のシナリオイベントについて」 help (閉じる),
        # 「ミッション達成累積報酬」 (already in the Present BOX, OK), 「シナリオ再生確認」 (キャンセル, never 再生する), then ←.
        scenario = [MISSION_THEN.with_name(f"scenario-event-{n}.png") for n in ("help", "mission-receipt", "play-confirm", "page")]
        result = run_tasks.run(
            {"tasks": ["Startup"], "task_timeout_s": 20},
            offline_steps=[(p, "click") for p in (AFTER_MISSION, MISSION_THEN, *scenario, ROOT / HOME)],
            simulate_input=True, policy={"live_input_enabled": False},
            summary_path=False, save_diagnostics=False,
        )
        task, = result["tasks"]
        self.assertEqual(task["status"], "succeeded", task)
        self.assertEqual(task["simulated_actions"], ["popup-mission-completed-ok", "popup-event-fan-reward-ok", "popup-help-close",
                                                   "popup-reward-ok", "popup-scenario-play-cancel", "nav-back"])
        self.assertIn("SC.Nav.ScenarioEvent.Back", task["node_trace"])
        self.assertEqual(result["real_input_attempts"], 0)


EVENT_REWARD = ROOT / "dev_assets/private/popup-samples-20261026/event-reward-popup-home.png"
EVENT_RECEIPT = EVENT_REWARD.with_name("event-mission-receipt-on-training-event.png")


@unittest.skipUnless(private_ready() and EVENT_REWARD.is_file(), "需要 2026-10-26 活动累计奖励提示原生帧与 Maa 资源")
class EventRewardReplayTests(unittest.TestCase):
    """「イベント累計報酬獲得」 (shown on home after a Produce, OK links to the event): the same
    layout as the fan-count notice, so the same guard (title + body + OK) and the same OK action handle it."""

    def test_notice_without_fan_count_closes_through_the_same_ok(self):
        result = run_tasks.run(
            {"tasks": ["Startup"], "task_timeout_s": 15},
            offline_steps=[(EVENT_REWARD, "click"), (ROOT / HOME, "click")],
            simulate_input=True, policy={"live_input_enabled": False},
            summary_path=False, save_diagnostics=False,
        )
        task, = result["tasks"]
        self.assertEqual(task["status"], "succeeded", task)
        self.assertEqual(task["simulated_actions"], ["popup-event-fan-reward-ok"])
        self.assertIn("SC.Popup.EventFanReward.Close", task["node_trace"])
        self.assertEqual(result["real_input_attempts"], 0)

    @unittest.skipUnless(EVENT_RECEIPT.is_file(), "需要 2026-10-26 育成イベント收据弹窗原生帧")
    def test_ok_leads_to_the_mission_receipt_which_only_closes(self):
        # 2026-10-26 live: the OK opened 育成イベント「秋のアイドル強化週間」 under 「ミッション達成累積報酬」 (items already in
        # the Present BOX); its OK is the reward-popup OK position. The page's ← is SC.Nav.TrainingEvent.Back (BackOne).
        result = run_tasks.run(
            {"tasks": ["Startup"], "task_timeout_s": 15},
            offline_steps=[(EVENT_REWARD, "click"), (EVENT_RECEIPT, "click"), (ROOT / HOME, "click")],
            simulate_input=True, policy={"live_input_enabled": False},
            summary_path=False, save_diagnostics=False,
        )
        task, = result["tasks"]
        self.assertEqual(task["status"], "succeeded", task)
        self.assertEqual(task["simulated_actions"], ["popup-event-fan-reward-ok", "popup-reward-ok"])
        self.assertIn("SC.Popup.EventMissionReceipt.Close", task["node_trace"])
        self.assertEqual(result["real_input_attempts"], 0)

    @unittest.skipUnless(EVENT_RECEIPT.is_file() and PAGE.is_file(), "需要 2026-10-19/26 活动页原生帧")
    def test_guards_on_the_saved_frames(self):
        from tests.maa_probe import NodeProbe

        probe = NodeProbe()
        self.assertTrue(probe.hits(EVENT_REWARD, "SC.Popup.EventFanReward.Guard"))
        self.assertTrue(probe.hits(NOTICE, "SC.Popup.EventFanReward.Guard"))
        self.assertFalse(probe.hits(EVENT_RECEIPT, "SC.Popup.EventFanReward.Guard"))
        self.assertTrue(probe.hits(EVENT_RECEIPT, "SC.Popup.EventMissionReceipt.Guard"))
        self.assertFalse(probe.hits(EVENT_RECEIPT, "SC.Popup.Reward.Guard"))         # one node per frame on that OK
        self.assertFalse(probe.hits(RECEIPT, "SC.Popup.EventMissionReceipt.Guard"))   # the fan-count receipt
        for anchor in ("Kind", "Help", "RewardIdol"):                                # page text under the dimmed popup
            self.assertTrue(probe.hits(EVENT_RECEIPT, f"SC.Common.Anchor.TrainingEvent.{anchor}"), anchor)
        self.assertFalse(probe.hits(EVENT_RECEIPT, "SC.Nav.TrainingEvent.Guard"))     # ← dimmed: the popup goes first
        self.assertFalse(probe.hits(PAGE, "SC.Nav.TrainingEvent.Guard"))              # another event's page
        self.assertTrue(probe.hits(PAGE, "SC.Common.Color.BackUndimmed"))             # the same ← when undimmed


@unittest.skipUnless(private_ready() and (ROOT / "dev_assets/private/live-20261020/login-anniversary.png").is_file(),
                     "需要本地新偶像预告画面与 Maa 资源")
class NewIdolPreviewReplayTests(unittest.TestCase):
    def test_observed_new_idol_preview_has_only_a_skip_action(self):
        frame = ROOT / "dev_assets/private/live-20261020/login-anniversary.png"
        result = run_tasks.run(
            {"tasks": ["Startup"], "task_timeout_s": 15},
            offline_steps=[(frame, "click"), (ROOT / HOME, "click")], simulate_input=True,
            policy={"live_input_enabled": False}, summary_path=False, save_diagnostics=False,
        )
        task, = result["tasks"]
        self.assertEqual(task["status"], "succeeded", task)
        self.assertEqual(task["simulated_actions"], ["popup-new-gacha-skip"])
        self.assertIn("SC.Popup.NewGacha.Close", task["node_trace"])
        self.assertEqual(result["real_input_attempts"], 0)


if __name__ == "__main__":
    unittest.main()
