"""Full-screen gacha announcement: Maa closes it with one tap, and stops with its own reason when that does not work.

2026-10-27 live: after a new-gacha cut-in SKIP (or from a cold Startup) a static banner stayed on screen — letterboxed
art and, in the lower band, a title line ending in 「…ガシャ」 plus a 「開催期間 …」 pill. The same day the user asked
Maa to close it itself: one tap on the plain band below the pill (``popup-gacha-announcement-close``). A tap that
leaves the banner on screen is not repeated; then, or when input is disabled, the task ends with
``gacha_announcement_unhandled`` instead of ``unknown_or_unhandled_state``.

The static contract tests always run. The replay tests need MaaFramework v5.12.3, the private template bundle, the OCR
model and the private frames (absolute paths below, never copied into the repo); they skip when anything is missing.
"""
import copy
import re
import unittest
from pathlib import Path
from unittest import mock

from agent.core.pipeline_actions import default_table
from tools import replay_common_pipeline as harness
from tools import run_tasks
from tools.check_project import load_jsonc

ROOT = Path(__file__).resolve().parent.parent
PIPELINE = ROOT / "assets/resource/base/pipeline"
STOP = "SC.Common.Stop.GachaAnnouncement"
GUARD = "SC.Popup.GachaAnnouncement.Guard"
CLOSE = "SC.Popup.GachaAnnouncement.Close"
SETTLED = "SC.Popup.GachaAnnouncement.Settled"
ACTION = "popup-gacha-announcement-close"
REASON = "gacha_announcement_unhandled"
PLAYER_TEXT = "卡池公告挡住首页：请在游戏里关闭后再开始"
FAIL = ["SC.Common.FailTask"]

# Observed OCR text of the lower band (2026-10-27 banner, 2026-10-20 new-card promo) and of screens that must not match.
TITLE_LINES = ("「限定入リHAPPYBIRTHDAY円香スクンプガシヤ」開催！", "「限定入りHAPPYBIRTHDAY円香スタンプガシヤ開催！",
               "色づく風で熾る灯は灯織・ルカスタンプガシヤ", "「限定入り HAPPY BIRTHDAY 円香スタンプガシャ」開催！",
               "「キャスティングコレクション~色んな愛の形編~」開催！")   # 2026-10-30: a gacha series without 「ガシャ」
PERIOD_LINES = ("開催期間 2026/10/27～2026/10/28 23:59", "開催期間", "開催期間 2026")
OTHER_TEXT = ("Touch", "WUUGJU", "NEW!!", "引き終わっています", "すでに", "每日0:00更新", "ミッション", "ガシャ履歴", "ショップ",
              "ピックアップ", "メンテナンスを実施", "エラー")

PRIVATE_ROOTS = (ROOT / "dev_assets" / "private", Path(r"C:\Users\public\maa\dev_assets\private"))
BANNER_LATER = "run_logs/diagnostics/failures/20261026T150401549082Z.png"  # 15:04 Startup failure frame (same banner)
# 2026-10-30 after maintenance: a gacha series banner whose title line has no 「ガシャ」
# (「キャスティングコレクション〜色んな愛の形編〜」開催！); Startup stopped on unknown_or_unhandled_state.
CASTING_BANNER = "live-20261030/gacha-announcement-casting.png"
# 2026-10-31 live: the 摩美々 stamp gacha banner after a new-gacha SKIP; a grey web haze in the top band failed the <=20
# letterbox (NewGacha.Settled never matched, Startup and WeeklyProduce stopped on gacha_announcement_unhandled).
MAMIMI_BANNER = "live-20261031/gacha-announcement-mamimi.png"
OCR_FILES = ("det.onnx", "rec.onnx", "keys.txt")


def nodes():
    found = {}
    for path in sorted(PIPELINE.rglob("*.json")):
        found.update(load_jsonc(path))
    return found


def private_root():
    """First private root that has every frame, the private template bundle and the OCR model; else None."""
    for root in PRIVATE_ROOTS:
        frames = [root / name for name in (harness.GACHA_BANNER, BANNER_LATER, CASTING_BANNER, MAMIMI_BANNER, harness.PROMO, harness.GACHA_PAGE,
                                           harness.NEW_GACHA, harness.NEW_GACHA_NO_SKIP, harness.BIRTHDAY, harness.HOME)]
        if (all(frame.is_file() for frame in frames) and (root / "resource_native" / "manifest.json").is_file()
                and all((root / "models" / "ppocr_v6_small" / name).is_file() for name in OCR_FILES)):
            return root
    return None


def with_startup_override(build_override):
    """The harness override plus the supervised-startup entry (title required), as tools/run_tasks.py builds it."""
    def merged(names, enable):
        override = build_override(names, enable)
        override.update(copy.deepcopy(run_tasks.supervised_startup_override()))
        return override
    return merged


try:
    from maa.library import Library
    HAVE_MAA = Library.version() == harness.FRAMEWORK
except Exception:  # pragma: no cover - environment without MaaFramework
    HAVE_MAA = False


class GachaAnnouncementContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.nodes = nodes()

    def test_stop_only_fails_the_task_with_its_reason(self):
        stop = self.nodes[STOP]
        self.assertEqual(stop["recognition"], "And")
        self.assertEqual((stop["action"], stop["custom_action"]), ("Custom", "SC.FailTask"))
        self.assertEqual(stop["custom_action_param"], {"reason": REASON})
        for key in ("next", "target", "on_error", "enabled"):
            self.assertNotIn(key, stop)
        self.assertEqual(stop["all_of"], ["SC.Common.Anchor.GachaAnnouncement.Period",
                                          "SC.Common.Anchor.GachaAnnouncement.Title"])

    def test_anchors_are_two_ocr_texts_in_the_lower_band(self):
        title, period = (self.nodes[f"SC.Common.Anchor.GachaAnnouncement.{part}"] for part in ("Title", "Period"))
        for anchor in (title, period):
            self.assertEqual(anchor["recognition"], "OCR")
            x, y, w, h = anchor["roi"]
            self.assertTrue(x >= 0 and x + w <= 1280 and y >= 495 and y + h <= 640, anchor["roi"])  # below the banner art
            self.assertEqual(len(anchor["expected"]), 1)

    def test_expected_text_matches_the_observed_band_and_nothing_else(self):
        title, period = (re.compile(self.nodes[f"SC.Common.Anchor.GachaAnnouncement.{part}"]["expected"][0])
                         for part in ("Title", "Period"))
        for line in TITLE_LINES:
            self.assertTrue(title.search(line), line)
        for line in PERIOD_LINES:
            self.assertTrue(period.search(line), line)
        for line in OTHER_TEXT:
            self.assertFalse(title.search(line) and period.search(line), line)
        for line in TITLE_LINES:        # a title line alone is not the pill
            self.assertFalse(period.search(line), line)
        for line in PERIOD_LINES:       # and the pill alone is not a gacha title
            self.assertFalse(title.search(line), line)

    def test_guard_checks_the_letterbox_before_any_ocr(self):
        guard = self.nodes[GUARD]
        self.assertEqual(guard["all_of"][:2], ["SC.Popup.GachaAnnouncement.TopBar", "SC.Popup.GachaAnnouncement.BottomBar"])
        self.assertEqual(guard["all_of"][2:], self.nodes[STOP]["all_of"])
        # top band <=40 (2026-10-31 web haze), bottom band <=20; both 20000 of 46080 pixels
        for bar, upper in zip(guard["all_of"][:2], ([40, 40, 40], [20, 20, 20])):
            node = self.nodes[bar]
            self.assertEqual((node["recognition"], node["upper"], node["count"]), ("ColorMatch", upper, 20000))
        self.assertEqual(self.nodes[CLOSE]["all_of"], guard["all_of"])

    def test_one_tap_below_the_pill_that_is_never_repeated_on_the_same_banner(self):
        close = self.nodes[CLOSE]
        self.assertFalse(close["enabled"])            # enabled only through the action table
        self.assertEqual(close["action"], "Click")
        x, y, w, h = close["target"]
        self.assertTrue(y >= 628 and y + h <= 672, close["target"])   # below the 開催期間 pill, above the art's edge
        self.assertEqual(close["next"], [SETTLED])
        self.assertEqual(close["on_error"], [STOP] + FAIL)
        settled = self.nodes[SETTLED]["any_of"]
        self.assertNotIn(GUARD, settled)               # an unchanged banner is not "left": no second tap
        self.assertIn("SC.Nav.Gacha.Guard", settled)   # a tap that opens the gacha page is backed out by nav-back
        self.assertIn("SC.Popup.NewGacha.Guard", settled)

    def test_the_action_is_one_enabled_non_consuming_popup_tap(self):
        table = default_table()
        found = [a for a in table.actions if CLOSE in a.nodes]
        self.assertEqual([a.id for a in found], [ACTION])
        action, = found
        self.assertEqual((action.family, action.consuming, action.enabled), ("popup", False, True))
        self.assertEqual(list(action.rect), self.nodes[CLOSE]["target"])
        self.assertNotIn(STOP, table.node_actions(include_disabled=True))

    def test_dispatcher_closes_first_and_stops_when_the_close_is_unavailable(self):
        close_known = self.nodes["SC.Popup.CloseKnown"]
        self.assertIn(GUARD, close_known["any_of"])
        after = close_known["next"][close_known["next"].index(CLOSE):]
        self.assertEqual(after[:2], [CLOSE, STOP])
        self.assertEqual(self.nodes["SC.Common.GoHome"]["on_error"], [STOP] + FAIL)
        for cutin in ("SC.Popup.NewGacha.Close", "SC.Popup.Birthday.Close"):
            self.assertEqual(self.nodes[cutin]["on_error"], [STOP] + FAIL, cutin)

    def test_every_successor_list_that_accepts_the_cutin_accepts_the_banner(self):
        for name, node in self.nodes.items():
            if name != SETTLED and "SC.Popup.NewGacha.Guard" in node.get("any_of", []):
                self.assertIn(GUARD, node["any_of"], name)
        for name in ("SC.Popup.NewGacha.Settled", "SC.Popup.Birthday.Settled"):
            self.assertIn(GUARD, self.nodes[name]["any_of"], name)

    def test_player_text_in_the_task_outcomes_and_the_scan_panels(self):
        for rel in ("ui/components/outcomes.js", "ui/components/card-scan.js"):
            text = (ROOT / rel).read_text(encoding="utf-8")
            self.assertIn(f'{REASON}: "{PLAYER_TEXT}"', text, rel)


@unittest.skipUnless(HAVE_MAA and private_root() is not None,
                     "需要MaaFramework v5.12.3、私有模板包、OCR模型与私有帧（2026-10-27 公告、新卡宣传页、过场帧、ガシャ页）")
class GachaAnnouncementReplayTests(unittest.TestCase):
    """GoHome replayed on the real frames with the real Maa Tasker and a simulated controller (no host, no device)."""

    @classmethod
    def setUpClass(cls):
        root = private_root()
        step = harness._s
        home = (harness.HOME, "click")
        cases = (
            harness.Case("banner-close", step((harness.GACHA_BANNER, "click"), home), (ACTION,), "home"),
            harness.Case("banner-startup-frame-close", step((BANNER_LATER, "click"), home), (ACTION,), "home"),
            harness.Case("promo-close", step((harness.PROMO, "click"), home), (ACTION,), "home"),
            harness.Case("casting-banner-close", step((CASTING_BANNER, "click"), home), (ACTION,), "home"),
            harness.Case("mamimi-banner-close", step((MAMIMI_BANNER, "click"), home), (ACTION,), "home"),
            harness.Case("new-gacha-skip-then-mamimi-close", step((harness.NEW_GACHA, "click"), (MAMIMI_BANNER, "click"),
                                                                  home), ("popup-new-gacha-skip", ACTION), "home"),
            harness.Case("new-gacha-skip-then-banner-close", step((harness.NEW_GACHA, "click"),
                                                                  (harness.GACHA_BANNER, "click"), home),
                         ("popup-new-gacha-skip", ACTION), "home"),
            harness.Case("birthday-skip-then-promo-close", step((harness.BIRTHDAY, "click"), (harness.PROMO, "click"),
                                                                home), ("popup-story-skip", ACTION), "home"),
            harness.Case("banner-to-gacha-page", step((harness.GACHA_BANNER, "click"), (harness.GACHA_PAGE, "click"),
                                                      home), (ACTION, "nav-back"), "home"),
            harness.Case("banner-click-no-effect", step((harness.GACHA_BANNER, harness.STAY),), (ACTION,), "fail",
                         reason=REASON),
            harness.Case("banner-input-disabled", step((harness.GACHA_BANNER, "click"),), (), "fail",
                         enable_input=False, reason=REASON),
            harness.Case("home-direct", step(home), (), "home"),
            harness.Case("gacha-page-is-not-the-banner", step((harness.GACHA_PAGE, "click"), home), ("nav-back",),
                         "home"),
            harness.Case("cutin-without-banner-band", step((harness.NEW_GACHA_NO_SKIP, "click"),), (), "fail",
                         reason="unknown_or_unhandled_state", note="合成帧：过场去掉按钮；没有下半部标题行与開催期間，仍是未知画面"),
        )
        startup = (harness.Case("startup-entry-banner", step((harness.GACHA_BANNER, "click"),), (), "fail",
                                reason=REASON),)
        with mock.patch.object(harness, "PRIVATE", root), \
                mock.patch.object(harness, "ocr_model_dir", lambda: root / "models" / "ppocr_v6_small"):
            report = harness.replay(cases, bundle=root / "resource_native")
            # 监督启动（要求标题画面）的入口：不是标题时以卡池公告原因停止，不点击
            with mock.patch.object(harness, "ENTRY", "SC.Startup.Entry"), \
                    mock.patch.object(harness, "build_override", with_startup_override(harness.build_override)):
                startup_report = harness.replay(startup, bundle=root / "resource_native")
        cls.by_id = {case["id"]: case for case in report["cases"] + startup_report["cases"]}
        cls.reports = (report, startup_report)

    def test_every_case_passes_without_real_input(self):
        for report in self.reports:
            self.assertEqual([c["id"] for c in report["cases"] if not c["passed"]], [])
            self.assertEqual(report["real_input_attempts"], 0)

    def test_each_banner_gets_exactly_one_tap(self):
        for case_id in ("banner-close", "banner-startup-frame-close", "promo-close", "casting-banner-close",
                        "mamimi-banner-close", "banner-click-no-effect"):
            self.assertEqual(len(self.by_id[case_id]["simulated_clicks"]), 1, case_id)

    def test_unclosed_banner_stops_with_its_reason(self):
        for case_id in ("banner-click-no-effect", "banner-input-disabled", "startup-entry-banner"):
            result = self.by_id[case_id]
            self.assertEqual(result["fail_reasons"], [REASON], case_id)
            self.assertEqual(result["node_trace"][-1], STOP, case_id)
        self.assertEqual(self.by_id["startup-entry-banner"]["simulated_clicks"], [])

    def test_clean_home_gacha_page_and_lookalike_cutin_are_not_the_banner(self):
        for case_id in ("home-direct", "gacha-page-is-not-the-banner"):
            self.assertEqual(self.by_id[case_id]["node_trace"][-1], harness.HOME_END, case_id)
        self.assertEqual(self.by_id["cutin-without-banner-band"]["fail_reasons"], ["unknown_or_unhandled_state"])


if __name__ == "__main__":
    unittest.main()
