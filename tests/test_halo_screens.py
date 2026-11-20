"""H2a: say "Halo" screen recognition.

The in-run pages stop by name without RunOptions.halo_route (H2b) and are dispatched before the W.I.N.G. pages
that share their スケジュール選択 / 決定 / トラブル率 / オーディション labels. The replay classifies every H1 sample frame
(tools/halo_samples.py manifest + whole-frame OCR boxes) with the generated Pipeline; private frames, skipped when
absent."""
import json
import unittest

from agent.core import wing_nav as nav
from agent.core import wing_screen as wsc
from tools import halo_samples as hs

HALO_PAGES = ("HaloRecruitConfirm", "HaloTeamFull", "HaloGrowthConfirm", "HaloLiveConfirm", "HaloLiveSelect",
              "HaloGoals", "HaloAudition", "HaloPractice", "HaloLesson", "HaloRecruit", "HaloGrowth", "HaloReport",
              "HaloSeasonEnd", "HaloTeamInfo", "HaloInheritance")
# Pages with no guard yet: the frame stays unknown (sampled, then a safe stop); never a click. Since H2c
# the book page, 継承一覧 and スキル一括変更 have their own screens.
GAPS: dict = {}


class HaloRuleTests(unittest.TestCase):
    def test_pages_stop_by_name_without_a_route(self):
        runtime = nav.WingNavRuntime()
        for screen in HALO_PAGES:
            self.assertEqual(nav.RULES[screen].kind, "decide", screen)
            self.assertEqual(runtime.decide(screen, {}).reason, "halo_not_automated", screen)
        self.assertEqual(runtime.decide("FesBirthSkillDialog", {}).reason, "fes_birth_skill_dialog")

    def test_dispatched_before_the_wing_pages_they_resemble(self):
        order = [screen for _, screen in wsc.dispatch_order(wsc.load_nodes())]
        first_wing = min(order.index(s) for s in ("Schedule", "AuditionTab", "ScheduleBusy", "SeasonEndCommu"))
        for screen in HALO_PAGES:
            self.assertLess(order.index(screen), first_wing, screen)
        for popup in ("HaloRecruitConfirm", "HaloTeamFull", "HaloGrowthConfirm", "HaloLiveConfirm", "HaloGoals"):
            self.assertLess(order.index(popup), order.index("HaloLesson"), popup)
        self.assertLess(order.index("FesBirthSkillDialog"), order.index("FesBirth"))

    def test_halo_pages_tap_only_halo_targets(self):
        # H2b: the Halo pages' own taps are the wing-halo family; the season-end OK and 誕生 reuse the W.I.N.G. rects.
        shared = {"commu_ok", "giveup_open", "fes_birth_ok"}
        for screen in HALO_PAGES + ("HaloHub", "FesBirthSkillDialog"):
            for intent in nav.screen_intents(screen):
                self.assertTrue(intent.startswith("halo_") or intent in shared, (screen, intent))


@unittest.skipUnless(hs.DEFAULT_OUT.is_file() and hs.DEFAULT_OCR.is_file(), "private Halo samples (H1) not present")
class HaloReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads(hs.DEFAULT_OUT.read_text(encoding="utf-8"))
        cls.table, cls.misses = hs.classify(cls.manifest, "boxes")

    def test_every_sample_frame_reads_as_its_screen(self):
        self.assertGreaterEqual(sum(len(f) for f in self.manifest.values()), 400)
        self.assertEqual([m for m in self.misses if m[0] not in GAPS], [])

    def test_gaps_never_read_as_a_clickable_page(self):
        for label, allowed in GAPS.items():
            for screen in self.table.get(label, ()):
                self.assertIn(screen, allowed, (label, dict(self.table[label])))


LIVE_INHERITANCE = hs.PRIVATE / "live-20261106/halo-inheritance-success.png"


@unittest.skipUnless(LIVE_INHERITANCE.is_file(), "private H2d frame not present")
class NativeAnchorTests(unittest.TestCase):
    """H2d live 2026-11-06: Maa's ROI OCR merged the 継承 stats row into one string, so the native Stats anchor (the
    click node's guard) missed while the whole-frame classification hit. The native anchors must hit that frame."""
    def test_inheritance_anchors_hit_with_roi_ocr(self):
        from tools.wing_demo_replay import load_rgb
        from tools.wing_sample_ocr import WingRecognizer
        nodes = json.loads((hs.ROOT / "assets/resource/base/pipeline/wing.json").read_text(encoding="utf-8"))
        image = load_rgb(LIVE_INHERITANCE)[:, :, ::-1].copy()
        rec = WingRecognizer()
        for name in nodes["SC.Wing.Screen.HaloInheritance"]["all_of"]:
            node = nodes[name]
            hit = rec.run(image, {k: node[k] for k in ("recognition", "roi", "expected", "threshold")})["hit"]
            self.assertTrue(hit, name)
        self.assertEqual(rec.input_attempts, [] if isinstance(rec.input_attempts, list) else 0)


LIVE_AUDITIONS = {"halo-audition-s2-live-card.png": 14093, "halo-audition-s3-locked.png": 114204}


@unittest.skipUnless(all((hs.PRIVATE / "live-20261106" / n).is_file() for n in LIVE_AUDITIONS),
                     "private H2d frames not present")
class FanTotalTests(unittest.TestCase):
    """H2d live 2026-11-06: the schedule page's fan total, read by Maa's ROI OCR (the people icon reads 「18」 and
    stays outside the ROI)."""
    def test_fan_total_with_roi_ocr(self):
        from agent.core import halo_screen
        from tools.wing_demo_replay import load_rgb
        from tools.wing_sample_ocr import WingRecognizer
        nodes = json.loads((hs.ROOT / "assets/resource/base/pipeline/wing.json").read_text(encoding="utf-8"))
        node = nodes["SC.Wing.Read.Halo.Fans"]
        rec = WingRecognizer()
        for name, fans in LIVE_AUDITIONS.items():
            image = load_rgb(hs.PRIVATE / "live-20261106" / name)[:, :, ::-1].copy()
            out = rec.run(image, {k: node[k] for k in ("recognition", "roi", "expected", "threshold") if k in node})
            texts = [(t["text"], t["box"]) for t in out["texts"]]
            self.assertEqual(halo_screen.parse_total_fans(texts), fans, name)


DISBAND = hs.PRIVATE / "wing_samples" / "2026-11-15"
DISBAND_FRAMES = {"20261115T035028394885Z-wait.png": ("HaloTeamEdit", [True] * 4),
                  "20261115T035117835820Z-wait.png": ("HaloLeaveConfirm", "小宮果穂"),
                  "20261115T035125494497Z-wait.png": ("HaloTeamEdit", [False, False, True, True]),
                  "20261115T035159404825Z-look.png": ("HaloTeamEdit", [False] * 4)}


@unittest.skipUnless(all((DISBAND / n).is_file() for n in DISBAND_FRAMES), "private 2026-11-15 frames not present")
class DisbandFrameTests(unittest.TestCase):
    """Live 2026-11-15 (S2 week 1): チーム編集 and チーム離脱確認 with Maa's ROI OCR; the dialog wins over the page."""
    def test_pages_and_buttons(self):
        from agent.core import halo_screen
        from tools.wing_demo_replay import load_rgb
        from tools.wing_sample_ocr import WingRecognizer
        nodes = json.loads((hs.ROOT / "assets/resource/base/pipeline/wing.json").read_text(encoding="utf-8"))
        rec = WingRecognizer()

        def run(image, name):
            node = nodes[name]
            return rec.run(image, {k: node[k] for k in ("recognition", "roi", "expected", "threshold") if k in node})

        for name, (screen, want) in DISBAND_FRAMES.items():
            image = load_rgb(DISBAND / name)[:, :, ::-1].copy()
            hit = next(s for s in ("HaloLeaveConfirm", "HaloTeamEdit")
                       if all(run(image, a)["hit"] for a in nodes[f"SC.Wing.Screen.{s}"]["all_of"]))
            self.assertEqual(hit, screen, name)
            if screen == "HaloTeamEdit":
                texts = [(t["text"], t["box"]) for t in run(image, "SC.Wing.Read.Halo.LeaveButtons")["texts"]]
                self.assertEqual(halo_screen.parse_leave_buttons(texts), want, name)
            else:
                texts = run(image, "SC.Wing.Read.Halo.LeaveQuestion")["texts"]
                self.assertIn(want, "".join(t["text"] for t in texts))


class GrowthBottomTests(unittest.TestCase):
    """Live 2026-11-15 S3: Vo filled to its cap, the bottom bar's top 「1,474」 read as 「,474」 (Maa ROI OCR) or as
    「1,474」 plus a 「474」 box; both used to leave the colour unread and the run stopped (halo_growth_status_unread)."""
    FRAME = hs.ROOT / "dev_assets/private/ocr-eval/20261115/20261115T220918-HaloGrowth-637369dc.png"

    def test_tail_box_and_lost_leading_digit(self):
        from agent.core import halo_growth as hg
        base = [("960", [366, 634, 49, 24]), ("960", [365, 665, 49, 25])]
        cap = [("1,474", [199, 664, 64, 26])]
        self.assertEqual(hg.parse_bottom(base + cap + [(",474", [211, 634, 53, 23])])["vo"], [1474, 1474])
        self.assertEqual(hg.parse_bottom(base + cap + [("1,474", [205, 634, 60, 23]), ("474", [230, 634, 35, 23])])["vo"],
                         [1474, 1474])
        self.assertEqual(hg.parse_bottom(base + cap + [(",464", [211, 634, 53, 23])])["vo"], [1464, 1474])
        # 「,980」 under a 1,020 cap cannot be 1,980 (over the cap) nor 980 (no comma below 1,000): unread
        self.assertIsNone(hg.parse_bottom([("1,020", [205, 664, 60, 26]), (",980", [211, 634, 53, 23])])["vo"])
        self.assertEqual(hg.parse_bottom(base)["da"], [960, 960])

    @unittest.skipUnless(FRAME.is_file(), "需要 2026-11-15 成長页截图")
    def test_live_frame(self):
        from agent.core import halo_growth as hg
        from tools.wing_demo_replay import load_rgb
        from tools.wing_sample_ocr import WingRecognizer
        nodes = json.loads((hs.ROOT / "assets/resource/base/pipeline/wing.json").read_text(encoding="utf-8"))
        node = nodes["SC.Wing.Read.Halo.GrowthBottom"]
        image = load_rgb(self.FRAME)[:, :, ::-1].copy()
        texts = WingRecognizer().run(image, {k: node[k] for k in ("recognition", "roi", "expected", "threshold") if k in node})["texts"]
        self.assertEqual(hg.parse_bottom([(t["text"], t["box"]) for t in texts]),
                         {"vo": [1474, 1474], "da": [960, 960], "vi": [1135, 1135], "me": [130, 500]})


if __name__ == "__main__":
    unittest.main()
