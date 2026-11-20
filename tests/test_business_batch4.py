"""Batch 4 (2026-10-08, offline): 一括営業 bound to the user's business / team / 12 h before anything is confirmed.

- ``bulk_dispatch`` off (default): 一括完了 + WORK REPORTs only; empty slots are left (``collected_dispatch_disabled``).
- ``verify`` needs a team; after the one 一括営業 press the dispatch preview text must name 雑誌インタビュー, 「12時間」 and
  every team name with no resource word. There is no confirm action (no native sample of that dialog), so a matching
  preview stops ``dispatch_confirm_not_enabled``, a different one ``dispatch_binding_mismatch``; nothing is confirmed.
- A dispatch proven without any confirmation (the game dispatched directly) is ``dispatched_binding_unverified``.

The two preview dialogs are SYNTHETIC (rendered text over the dimmed native all-empty top); they only stand in for the
unsampled dialog's text and are never evidence of its layout.
"""
import json
import shutil
import unittest
from pathlib import Path

from agent.core import business_task as bt
from tests import test_business_task as tbt  # module import: its TestCase classes are not collected twice
from tests.test_business_task import BULK, BULK_DEFAULT, HOME, REPORT, REPORT_ANIM, TOP_ALL_EMPTY, TOP_DONE, TOP_EMPTY1
from tests.test_task_launcher import HAVE_MAA, pipeline_nodes

ROOT = Path(__file__).resolve().parent.parent
SYN_DIR = "dev_assets/private/business-batch4-synthetic/"
PREVIEW_OK = SYN_DIR + "dispatch-preview-match.png"
PREVIEW_BAD = SYN_DIR + "dispatch-preview-mismatch.png"
FONT = Path("C:/Windows/Fonts/YuGothB.ttc")
VERIFY_NO_TEAM = {**BULK_DEFAULT, "营业一括派出": "核对后派出（需填写队伍）"}


def build_previews():
    from PIL import Image, ImageDraw, ImageFont

    base = Image.open(ROOT / TOP_ALL_EMPTY).convert("RGB")
    font = ImageFont.truetype(str(FONT), 34)
    for target, lines in ((PREVIEW_OK, ["一括営業確認", "雑誌インタビュー 12時間", "芹沢あさひ", "営業を開始しますか？"]),
                          (PREVIEW_BAD, ["一括営業確認", "TV出演 6時間", "黛冬優子", "営業を開始しますか？"])):
        path = ROOT / target
        if path.is_file():
            continue
        image = base.point(lambda v: int(v * 0.45))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((300, 150, 980, 560), radius=18, fill=(250, 250, 252))
        for i, line in enumerate(lines):
            draw.text((340, 190 + 80 * i), line, font=font, fill=(40, 40, 60))
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(path)


class DispatchBindingRuleTests(unittest.TestCase):
    def test_config_and_gate(self):
        self.assertEqual(bt.BusinessConfig.from_params({}).bulk_dispatch, "off")
        self.assertEqual(bt.BusinessConfig.from_params({"team": " 芹沢あさひ "}).team, "芹沢あさひ")
        with self.assertRaises(ValueError):
            bt.BusinessConfig.from_params({"bulk_dispatch": "always"})
        self.assertEqual(bt.bulk_dispatch_gate(bt.BusinessConfig()), "collected_dispatch_disabled")
        self.assertEqual(bt.bulk_dispatch_gate(bt.BusinessConfig(bulk_dispatch="verify")), "team_not_configured")
        self.assertEqual(bt.bulk_dispatch_gate(bt.BusinessConfig(bulk_dispatch="verify", team=" 、/ ")),
                         "team_not_configured")
        self.assertIsNone(bt.bulk_dispatch_gate(bt.BusinessConfig(bulk_dispatch="verify", team="芹沢あさひ")))
        self.assertEqual(bt.team_names("芹沢あさひ、黛冬優子/和泉愛依"), ["芹沢あさひ", "黛冬優子", "和泉愛依"])
        self.assertIn("collected_dispatch_disabled", bt.SUCCESS_OUTCOMES)
        self.assertTrue({"dispatch_confirm_not_enabled", "dispatch_binding_mismatch"} <= bt.HALT_OUTCOMES)
        self.assertFalse(bt.RETURN_FAILURE_OUTCOMES & (bt.SUCCESS_OUTCOMES | bt.HALT_OUTCOMES))

    def test_binding_of_a_preview(self):
        config = bt.BusinessConfig(team="芹沢あさひ、黛冬優子", bulk_dispatch="verify")
        ok = ["一括営業確認", "雑誌インタビュー １２時間", "芹沢あさひ", "黛冬優子"]
        self.assertEqual(bt.dispatch_binding(ok, config), {"matched": True, "missing": [], "forbidden": []})
        self.assertEqual(bt.dispatch_binding(ok[:3], config)["missing"], ["team"])  # every name must be present
        self.assertEqual(bt.dispatch_binding(["一括営業", "雑誌インタビュー 6時間", "芹沢あさひ黛冬優子"], config)["missing"],
                         ["duration"])
        self.assertEqual(bt.dispatch_binding(["営業開始", "TV出演 12時間", "芹沢あさひ", "黛冬優子"], config)["missing"],
                         ["business"])
        stamina = bt.dispatch_binding(ok + ["スタミナを消費します"], config)
        self.assertEqual((stamina["matched"], stamina["forbidden"]), (False, ["スタミナ"]))
        # Not a dispatch preview at all: unknown screen, handled as before (needs_sampling).
        self.assertIsNone(bt.dispatch_binding(["ホーム", "プレゼント", "ミッション"], config))
        self.assertIsNone(bt.dispatch_binding([], config))

    def test_readback_records_slots_without_the_business_name(self):
        config = bt.BusinessConfig(team="x", bulk_dispatch="verify")
        before = ("empty", "completed", "empty")
        after = [["雑誌インタビュー", "あと11:59"], [], ["TV出演"]]
        self.assertEqual(bt.dispatch_readback(before, after, config), [3])

    def test_pipeline_options_and_python_defaults_agree(self):
        nodes = pipeline_nodes()
        self.assertEqual(nodes["SC.Business.Config.Dispatch"]["attach"], {"bulk_dispatch": "off"})
        self.assertEqual(nodes["SC.Business.Config.Team"]["attach"], {"team": ""})
        self.assertNotIn("team", nodes["SC.Business.Config.Target"]["attach"])
        config_nodes = nodes["SC.Business.Entry"]["custom_action_param"]["config_nodes"]
        # 2026-10-11: 收取后 (redispatch) has its own carrier node after the dispatch one
        self.assertEqual(config_nodes[-3:], ["SC.Business.Config.Team", "SC.Business.Config.Dispatch",
                                             "SC.Business.Config.Redispatch"])
        self.assertEqual(nodes["SC.Business.Config.Redispatch"]["attach"], {"redispatch": bt.DEFAULT_REDISPATCH})
        tasks = json.loads((ROOT / "assets/tasks/business.json").read_text(encoding="utf-8"))
        option = tasks["option"]["营业一括派出"]
        self.assertEqual(option["default_case"], "关闭（只收取）")
        modes = {c["name"]: c["pipeline_override"]["SC.Business.Config.Dispatch"]["attach"]["bulk_dispatch"]
                 for c in option["cases"]}
        self.assertEqual(set(modes.values()), set(bt.BULK_DISPATCH_MODES))
        self.assertEqual(modes[option["default_case"]], bt.DEFAULT_BULK_DISPATCH)
        team = tasks["option"]["营业队伍"]
        self.assertEqual((team["type"], team["inputs"][0]["default"]), ("input", ""))
        for case in tasks["option"]["营业内容"]["cases"]:
            self.assertNotIn("team", case["pipeline_override"]["SC.Business.Config.Target"]["attach"])


def frames_ready():
    return HAVE_MAA and FONT.is_file() and all((ROOT / f).is_file()
                                               for f in (HOME, REPORT, REPORT_ANIM, TOP_ALL_EMPTY, TOP_DONE, TOP_EMPTY1))


@unittest.skipUnless(frames_ready(), "需要 MaaFramework v5.12.3、营业私有帧与日文字体")
class DispatchBindingReplayTests(unittest.TestCase):
    setUp = tbt.BusinessReplayTests.setUp
    run_task = tbt.BusinessReplayTests.run_task
    full_events = tbt.BusinessReportDispatchReplayTests.full_events

    @classmethod
    def setUpClass(cls):
        tbt.BusinessReplayTests.setUpClass.__func__(cls)
        build_previews()

    def kinds(self):
        return [(e["event"], e.get("action")) for e in self.full_events()]

    def test_default_collects_and_leaves_empty_slots(self):
        task, report = self.run_task([HOME, TOP_EMPTY1, REPORT, (REPORT_ANIM, 3), REPORT, TOP_ALL_EMPTY, HOME],
                                     option=BULK_DEFAULT, bulk_observe_seconds=bt.BULK_OBSERVE_SECONDS)
        self.assertEqual((task["status"], report["outcome"]), ("succeeded", "collected_dispatch_disabled"), task)
        self.assertEqual(task["simulated_actions"], ["home-business", "business-bulk-complete", "business-report-next",
                                                     "business-report-next", "nav-back"])
        self.assertNotIn(("bulk_intent", "dispatch"), self.kinds())

    def test_default_on_an_all_empty_top_never_presses_dispatch(self):
        task, report = self.run_task([HOME, TOP_ALL_EMPTY, HOME], option=BULK_DEFAULT)
        self.assertEqual((task["status"], report["outcome"]), ("succeeded", "collected_dispatch_disabled"), task)
        self.assertEqual(task["simulated_actions"], ["home-business", "nav-back"])
        self.assertEqual(self.full_events(), [])

    def test_verify_without_a_team_returns_home_without_pressing(self):
        task, report = self.run_task([HOME, TOP_ALL_EMPTY, HOME], option=VERIFY_NO_TEAM)
        self.assertEqual((task["status"], report["outcome"]), ("failed", "team_not_configured"), task)
        self.assertEqual(task["simulated_actions"], ["home-business", "nav-back"])
        self.assertEqual(self.full_events(), [])

    def test_matching_preview_is_bound_but_never_confirmed(self):
        task, report = self.run_task([HOME, TOP_ALL_EMPTY, PREVIEW_OK], option=BULK, bulk_observe_seconds=4.0)
        self.assertEqual((task["status"], report["outcome"]), ("failed", "dispatch_confirm_not_enabled"), task)
        self.assertEqual(task["simulated_actions"], ["home-business", "business-bulk-dispatch"])
        events = self.full_events()
        self.assertEqual([(e["event"], e.get("action")) for e in events],
                         [("bulk_intent", "dispatch"), ("bulk_uncertain", "dispatch")])
        self.assertEqual(events[-1]["reason"], "dispatch_preview_not_confirmed")
        self.assertEqual(events[-1]["binding"], {"matched": True, "missing": [], "forbidden": []})
        # A later run (earlier uncertain results only warn) decides from the top: still empty, so
        # 一括営業 opens the preview again; its confirm is still never pressed.
        task, report = self.run_task([HOME, TOP_ALL_EMPTY, PREVIEW_OK], option=BULK, bulk_observe_seconds=4.0)
        self.assertEqual((task["status"], report["outcome"]), ("failed", "dispatch_confirm_not_enabled"), task)
        self.assertEqual(task["simulated_actions"], ["home-business", "business-bulk-dispatch"])
        self.assertIn("uncertain_result_earlier_today", report["notes"])

    def test_different_preview_is_a_binding_mismatch(self):
        task, report = self.run_task([HOME, TOP_ALL_EMPTY, PREVIEW_BAD], option=BULK, bulk_observe_seconds=4.0)
        self.assertEqual((task["status"], report["outcome"]), ("failed", "dispatch_binding_mismatch"), task)
        self.assertEqual(task["simulated_actions"], ["home-business", "business-bulk-dispatch"])
        binding = self.full_events()[-1]["binding"]
        self.assertFalse(binding["matched"])
        self.assertTrue({"business", "duration", "team"} <= set(binding["missing"]), binding)

    def test_dispatch_without_confirmation_is_flagged_unverified(self):
        task, report = self.run_task([HOME, TOP_ALL_EMPTY, TOP_DONE, HOME], option=BULK)
        self.assertEqual((task["status"], report["outcome"]), ("failed", "dispatched_binding_unverified"), task)
        self.assertEqual(task["simulated_actions"], ["home-business", "business-bulk-dispatch", "nav-back"])
        dispatched = [e for e in self.full_events() if e["event"] == "bulk_dispatched"][0]
        self.assertEqual(dispatched["binding"], "not_verified_no_confirmation")
        self.assertEqual(bt.bulk_pending(self.full_events()), {})


if __name__ == "__main__":
    unittest.main()
