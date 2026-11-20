"""Home ガシャ free-draw hint (agent/core/gacha_hint.py): pixel gate + OCR text; read-only, positive only when sure."""
import unittest
from pathlib import Path

import numpy as np

from agent.core import gacha_hint as gh

ROOT = Path(__file__).resolve().parent.parent
P = ROOT / "dev_assets" / "private"
FREE = P / "manual-capture-kept-20261002/home-20261003-nobadge.png"            # 「1日1回無料」
SHIMMER = P / "captures-20261008-101907Z/frame-000043.png"                     # same tag, white glint across it
PLAIN = P / "shop-20261002-01-home-frames/frame-001.png"                      # plain ガシャ plate
PLAIN_LATE = P / "sample-live-20261011"                                         # 2026-10-11 homes: plain plate


def rows(text, score=0.98):
    return [(text, score, (400, 636, 96, 24))]


class ClassifyTests(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(gh.kind_of("1日1回無料"), "free_daily")
        self.assertEqual(gh.kind_of("無料10連"), "free_ten")
        self.assertEqual(gh.kind_of("10連無料"), "free_ten")
        self.assertEqual(gh.kind_of("単発無料"), "free_single")
        self.assertEqual(gh.kind_of("1回無料"), "free")      # shimmer-cut 「1日1回無料」: never claimed as a single pull
        self.assertEqual(gh.kind_of("回無料"), "free")
        self.assertIsNone(gh.kind_of("超成功発生中"))
        self.assertIsNone(gh.kind_of("NEW!!"))

    def test_states(self):
        self.assertEqual(gh.classify(0.0, None)["state"], "no_tag")
        self.assertEqual(gh.classify(0.10, rows("1日1回無料"))["state"], "unknown")
        self.assertEqual(gh.classify(0.70, None)["state"], "unread")
        self.assertEqual(gh.classify(0.70, rows("1日1回無料", 0.5))["state"], "unread")
        self.assertEqual(gh.classify(0.70, rows("超成功発生中"))["state"], "tag_other")
        out = gh.classify(0.70, rows("1日1回無料"))
        self.assertEqual((out["state"], out["kind"], out["fill_pm"]), ("free", "free_daily", 700))

    def test_ocr_runs_only_when_the_tag_is_there(self):
        calls = []

        def ocr(image, roi):
            calls.append(roi)
            return rows("1日1回無料")

        plain = np.zeros((720, 1280, 3), np.uint8)
        self.assertEqual(gh.read(plain, ocr)["state"], "no_tag")
        self.assertEqual(calls, [])
        tagged = plain.copy()
        tagged[630:668, 390:502] = (20, 100, 245)          # BGR orange
        self.assertEqual(gh.read(tagged, ocr)["kind"], "free_daily")
        self.assertEqual(calls, [gh.OCR_ROI])

    def test_module_never_sends_input(self):
        source = (ROOT / "agent/core/gacha_hint.py").read_text(encoding="utf-8")
        for forbidden in ("click", "post_", "controller", "run_task", "import maa"):
            self.assertNotIn(forbidden, source)


@unittest.skipUnless(FREE.is_file() and PLAIN.is_file(), "需要私有首页帧")
class NativeFrameTests(unittest.TestCase):
    def fill(self, path):
        from PIL import Image

        return gh.tag_fill(Image.open(path))

    def test_tag_fill_on_native_frames(self):
        self.assertGreater(self.fill(FREE), 0.6)
        self.assertEqual(self.fill(PLAIN), 0.0)
        if SHIMMER.is_file():
            self.assertGreater(self.fill(SHIMMER), gh.TAG_FILL_POSITIVE)
        for path in sorted(PLAIN_LATE.glob("*.png"))[:80] if PLAIN_LATE.is_dir() else ():
            self.assertLessEqual(self.fill(path), gh.TAG_FILL_NEGATIVE, path.name)

    def test_other_orange_tag_is_not_free(self):
        from PIL import Image

        image = np.asarray(Image.open(FREE).convert("RGB")).copy()
        image[626:672, 386:508] = image[626:672, 503:625]      # the アイドル 「超成功発生中」 tag moved onto ガシャ
        out = gh.read(Image.fromarray(image), lambda img, roi: rows("超成功発生中"))
        self.assertEqual(out["state"], "tag_other")


if __name__ == "__main__":
    unittest.main()
