"""「マッチライブ更新」: the season-switch notice on the Match page is closed with its 閉じる.

2026-10-31 daily (ui-20261031-043350): the notice (season switched, ranking titles already granted, one 閉じる) was
on screen after the Fes menu tap; Match timed out on it, and Missions and Business then stopped on the same frame.
The notice is now a known popup: OCR title + body + 閉じる guard, one non-consuming tap
(``popup-match-season-close``), and a tap that leaves it open is not repeated.

The replay tests need MaaFramework v5.12.3, the private template bundle, the OCR model and the private frames; they
skip when anything is missing.
"""
import unittest
from pathlib import Path
from unittest import mock

from agent.core.pipeline_actions import default_table
from tools import replay_common_pipeline as harness
from tools.check_project import load_jsonc

ROOT = Path(__file__).resolve().parent.parent
PIPELINE = ROOT / "assets/resource/base/pipeline"
GUARD = "SC.Popup.MatchSeason.Guard"
CLOSE = "SC.Popup.MatchSeason.Close"
SETTLED = "SC.Popup.MatchSeason.Settled"
ACTION = "popup-match-season-close"
SEASON = "live-20261031/match-season-update.png"
MATCH_MAIN = harness.MATCH_READER + "match_main_top.png"
OCR_FILES = ("det.onnx", "rec.onnx", "keys.txt")


def nodes():
    found = {}
    for path in sorted(PIPELINE.rglob("*.json")):
        found.update(load_jsonc(path))
    return found


class MatchSeasonContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.nodes = nodes()

    def test_guard_is_title_body_and_close_text(self):
        anchors = [self.nodes[name] for name in self.nodes[GUARD]["all_of"]]
        self.assertEqual([a["recognition"] for a in anchors], ["OCR"] * 3)
        self.assertEqual([a["expected"] for a in anchors], [["マッチライブ更新"], ["シーズンが切り替わりました"], ["閉じる"]])
        self.assertEqual(self.nodes[CLOSE]["all_of"], self.nodes[GUARD]["all_of"])

    def test_one_tap_inside_the_close_button(self):
        close = self.nodes[CLOSE]
        self.assertIs(close["enabled"], False)   # the launcher enables it with the action table
        self.assertEqual(close["max_hit"], 1)
        self.assertEqual(close["next"], [SETTLED])
        self.assertEqual(close["on_error"], ["SC.Common.FailTask"])
        x, y, w, h = close["target"]
        self.assertTrue(557 <= x and x + w <= 722 and 512 <= y and y + h <= 588)   # 閉じる art in the native frame
        self.assertNotIn(GUARD, self.nodes[SETTLED]["any_of"])   # still open after the tap: no second tap

    def test_the_action_is_one_enabled_non_consuming_popup_tap(self):
        action = default_table().get(ACTION)
        self.assertEqual((action.family, action.consuming, action.enabled), ("popup", False, True))
        self.assertEqual(list(action.nodes), [CLOSE])
        self.assertEqual(list(action.rect), self.nodes[CLOSE]["target"])

    def test_known_popup_and_match_menu_accept_it(self):
        known = self.nodes["SC.Popup.CloseKnown"]
        self.assertIn(GUARD, known["any_of"])
        self.assertLess(known["next"].index(CLOSE), known["next"].index("SC.Popup.NotHandled"))
        self.assertIn(GUARD, self.nodes["SC.Match.Menu.Tapped"]["any_of"])


def private_root():
    for root in (ROOT / "dev_assets" / "private", Path(r"C:\Users\public\maa\dev_assets\private")):
        frames = [root / name for name in (SEASON, MATCH_MAIN, harness.HOME)]
        if (all(f.is_file() for f in frames) and (root / "resource_native" / "manifest.json").is_file()
                and all((root / "models" / "ppocr_v6_small" / name).is_file() for name in OCR_FILES)):
            return root
    return None


try:
    from maa.library import Library
    HAVE_MAA = Library.version() == harness.FRAMEWORK
except Exception:  # pragma: no cover - environment without MaaFramework
    HAVE_MAA = False


@unittest.skipUnless(HAVE_MAA and private_root(), "需要 MaaFramework v5.12.3、私有模板包、OCR 模型与 2026-10-31 帧")
class MatchSeasonReplayTests(unittest.TestCase):
    """GoHome replayed on the real frames with the real Maa Tasker and a simulated controller (no host, no device)."""

    @classmethod
    def setUpClass(cls):
        root = private_root()
        step = harness._s
        home = (harness.HOME, "click")
        cases = (
            harness.Case("season-close-then-back", step((SEASON, "click"), (MATCH_MAIN, "click"), home),
                         (ACTION, "nav-back"), "home"),
            harness.Case("season-click-no-effect", step((SEASON, harness.STAY),), (ACTION,), "fail"),
            harness.Case("season-input-disabled", step((SEASON, "click"),), (), "fail", enable_input=False),
        )
        with mock.patch.object(harness, "PRIVATE", root), \
                mock.patch.object(harness, "ocr_model_dir", lambda: root / "models" / "ppocr_v6_small"):
            report = harness.replay(cases, bundle=root / "resource_native")
        cls.report = report
        cls.by_id = {case["id"]: case for case in report["cases"]}

    def test_every_case_passes_without_real_input(self):
        self.assertEqual([c["id"] for c in self.report["cases"] if not c["passed"]], [])
        self.assertEqual(self.report["real_input_attempts"], 0)

    def test_the_notice_gets_exactly_one_tap(self):
        for case_id in ("season-close-then-back", "season-click-no-effect"):
            self.assertEqual(len(self.by_id[case_id]["simulated_clicks"]) - (case_id == "season-close-then-back"), 1,
                             case_id)


if __name__ == "__main__":
    unittest.main()
