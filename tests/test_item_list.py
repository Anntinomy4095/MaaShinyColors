"""アイテム一覧 reader (agent/core/item_list.py): read-only, names + 所持数 by OCR, nothing guessed.

OCR rows follow the layout of the one sample (side-browser JPEG of the 消費アイテム tab upscaled to 1280x720; counts
changed). The frame is synthetic: only the selected-tab pink is drawn."""
import unittest
from pathlib import Path

import numpy as np

from agent.core import item_list as il

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "dev_assets/private/sidebrowser-knowhow-20261008/item_list_consumables.jpg"
ROWS = [
    ("消費アイテム", 0.918, (28, 109, 148, 32)), ("トレーニング", 0.996, (28, 267, 146, 33)),
    ("アイテムー覧", 0.991, (48, 19, 157, 33)),
    ("フェスエントリーチケッ", 0.999, (270, 534, 227, 31)),   # second row: 所持数 not visible -> never read
    ("リカバリーソーダ30", 0.997, (288, 154, 197, 27)), ("听持数", 0.916, (305, 389, 55, 23)),
    ("使う", 0.99, (360, 431, 54, 36)), ("ト5", 0.691, (364, 558, 45, 33)), ("12", 1.0, (433, 382, 53, 29)),
    ("リカバリーソーダ60", 0.993, (530, 154, 198, 27)), ("所持数", 0.987, (533, 386, 72, 29)), ("34", 1.0, (676, 382, 54, 29)),
    ("リカバリーソーダ100", 0.995, (766, 154, 212, 27)), ("所持数", 0.99, (781, 389, 64, 25)), ("56", 1.0, (921, 385, 51, 25)),
    ("フェスエントリーチケッ", 0.999, (1000, 140, 226, 30)), ("所持数", 0.949, (1025, 389, 64, 25)),
    ("150", 0.537, (1072, 252, 88, 72)), ("卜", 0.777, (1102, 169, 26, 26)), ("1,234", 1.0, (1142, 383, 74, 29)),
]


def frame(tab="consumable"):
    image = np.full((720, 1280, 3), 235, np.uint8)
    if tab:
        y0, y1 = il.TABS[tab]
        image[y0 + 10:y0 + 22, 40:120] = (230, 90, 170)   # pink label pixels (RGB)
    return image


class ItemListTests(unittest.TestCase):
    def test_reads_visible_cards_by_name(self):
        out = il.recognise(frame(), ROWS, frame_name="f")
        self.assertTrue(out["screen_ok"])
        self.assertEqual(out["tab"], "consumable")
        self.assertEqual({k: v["count"] for k, v in out["items"].items()},
                         {"stamina_drink": 12, "stamina_drink_60": 34, "stamina_drink_100": 56, "fes_ticket": 1234})
        self.assertAlmostEqual(out["items"]["fes_ticket"]["confidence"], 0.777)   # two-line name, low last glyph
        self.assertFalse(out["calibrated"])

    def test_unknown_names_and_unclean_counts_are_not_imported(self):
        rows = [r for r in ROWS if r[0] != "リカバリーソーダ30"] + [("ヒントが害かれた手紙", 0.936, (288, 154, 197, 27))]
        out = il.recognise(frame(), rows)
        self.assertNotIn("stamina_drink", out["items"])
        # 2026-10-12 whitelist: an untracked name is only counted, never imported or reported by name
        self.assertEqual((out["unmatched"], out["ignored"]), ([], 1))
        rows = [r if r[0] != "34" else ("34", 0.6, r[2]) for r in ROWS]
        out = il.recognise(frame(), rows)
        self.assertNotIn("stamina_drink_60", out["items"])
        self.assertEqual(out["rejected"][0]["reason"], "low_ocr")
        rows = [r if r[0] != "56" else ("5b", 1.0, r[2]) for r in ROWS]
        self.assertNotIn("stamina_drink_100", il.recognise(frame(), rows)["items"])

    def test_screen_guards_refuse_the_frame(self):
        self.assertEqual(il.recognise(frame(), [r for r in ROWS if r[0] != "アイテムー覧"])["refused"], "screen_guard_failed")
        self.assertEqual(il.recognise(frame(None), ROWS)["refused"], "tab_unknown")
        self.assertEqual(il.recognise(frame(), ROWS + [("キャンセル", 0.99, (500, 500, 90, 30))])["refused"], "dialog_overlay")
        self.assertEqual(il.recognise(np.zeros((360, 640, 3), np.uint8), ROWS)["refused"], "frame_not_1280x720")

    def test_module_never_sends_input(self):
        source = (ROOT / "agent/core/item_list.py").read_text(encoding="utf-8")
        for forbidden in ("click", "post_", "controller", "run_task", "import maa"):
            self.assertNotIn(forbidden, source)

    @unittest.skipUnless(SAMPLE.is_file(), "需要私有 アイテム一覧 样本")
    def test_sample_tab_pixels(self):
        from PIL import Image

        image = Image.open(SAMPLE).convert("RGB").resize(il.CANVAS, Image.LANCZOS)
        self.assertEqual(il.selected_tabs(image), ["consumable"])


if __name__ == "__main__":
    unittest.main()
