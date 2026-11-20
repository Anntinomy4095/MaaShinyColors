"""K3 scan decision core replayed on the 2026-10-08 native 1280x720 ノウハウブック一覧 frames (stitched sequences).

Offline only: full-frame Maa OCR (tests/maa_probe.ocr_items) + pixels on private PNGs; no controller, no input. The
frames were taken with scrollbar drags, not wheel notches, so these sequences prove the decision rules and the
readers, not the wheel step. The 評価値順-selected popup frame does not exist: it is stitched from
knowhow_sort_options.png by swapping the デフォルト and ノウハウ評価値順 button pixels (text tokens unchanged).
"""
from __future__ import annotations

import unittest

from agent.core import knowhow_books as kb
from agent.core import knowhow_scan as ks
from tests.test_knowhow_native_20261008 import D, DETAIL_1020, _ready

if _ready():
    from tests import knowhow_scan_frames as kf


@unittest.skipUnless(_ready(), "需要 2026-10-08 know-how 私有原生帧、ppocr 模型、MaaFramework、numpy 与 Pillow")
class KnowhowScanNativeReplayTests(unittest.TestCase):
    _cache: dict = {}

    @classmethod
    def frame(cls, name):
        if name not in cls._cache:
            from tests.maa_probe import ocr_items, rgb
            path = D / (name + ".png")
            cls._cache[name] = kf.frame(rgb(path), ocr_items(path))
        return cls._cache[name]

    @classmethod
    def eval_selected_popup(cls):
        f = cls.frame("knowhow_sort_options").copy()
        a, b = ks.SORT_POPUP_KEYS["デフォルト"], ks.SORT_POPUP_KEYS[kb.SORT_EVAL]
        pa = f[a[1]:a[1] + a[3], a[0]:a[0] + a[2]].copy()
        f[a[1]:a[1] + a[3], a[0]:a[0] + a[2]] = f[b[1]:b[1] + b[3], b[0]:b[0] + b[2]]
        f[b[1]:b[1] + b[3], b[0]:b[0] + b[2]] = pa
        return kf.frame(f, cls.frame("knowhow_sort_options").texts)

    def test_scrollbars_and_sort_selection(self):
        top = ks.read_thumb(self.frame("knowhow_1020_detail_top"), ks.DETAIL_BAR_X, ks.DETAIL_TRACK)
        mid = ks.read_thumb(self.frame("knowhow_1020_detail_05"), ks.DETAIL_BAR_X, ks.DETAIL_TRACK)
        end = ks.read_thumb(self.frame("knowhow_1020_detail_bottom"), ks.DETAIL_BAR_X, ks.DETAIL_TRACK)
        self.assertEqual((top["at_top"], top["at_bottom"]), (True, False))
        self.assertEqual((mid["at_top"], mid["at_bottom"]), (False, False))
        self.assertEqual((end["at_top"], end["at_bottom"]), (False, True))
        self.assertIsNone(ks.read_thumb(self.frame("knowhow_rating_grid"), ks.DETAIL_BAR_X, ks.DETAIL_TRACK))
        for name in ("knowhow_rating_grid", "knowhow_default_grid", "knowhow_1020_detail_bottom"):
            grid = ks.read_thumb(self.frame(name), ks.GRID_BAR_X, ks.GRID_TRACK)
            self.assertEqual((grid["at_top"], grid["at_bottom"]), (True, False), name)
        self.assertEqual(ks.sort_popup_selection(self.frame("knowhow_sort_options")), ["デフォルト"])
        self.assertEqual(ks.sort_popup_selection(self.eval_selected_popup()), [kb.SORT_EVAL])
        self.assertEqual(ks.sort_popup_selection(self.frame("knowhow_rating_grid")), [])

    def test_date_key_selection_and_tap(self):
        # newest-books scan: 獲得順 drawn selected (stitched like eval_selected_popup) and the tap on its label
        f = self.frame("knowhow_sort_options").copy()
        a, b = ks.SORT_POPUP_KEYS["デフォルト"], ks.SORT_POPUP_KEYS[kb.SORT_DATE]
        pa = f[a[1]:a[1] + a[3], a[0]:a[0] + a[2]].copy()
        f[a[1]:a[1] + a[3], a[0]:a[0] + a[2]] = f[b[1]:b[1] + b[3], b[0]:b[0] + b[2]]
        f[b[1]:b[1] + b[3], b[0]:b[0] + b[2]] = pa
        self.assertEqual(ks.sort_popup_selection(kf.frame(f, self.frame("knowhow_sort_options").texts)), [kb.SORT_DATE])
        rect = next(x["rect"] for x in ks.ACTIONS if x["id"] == "knowhow-sort-key-date")
        label = next(t["box"] for t in self.frame("knowhow_sort_options").texts if t["text"] == kb.SORT_DATE)
        cx, cy = label[0] + label[2] / 2, label[1] + label[3] / 2
        self.assertTrue(rect[0] <= cx < rect[0] + rect[2] and rect[1] <= cy < rect[1] + rect[3])
        bx, by, bw, bh = b
        self.assertTrue(bx < rect[0] and rect[0] + rect[2] < bx + bw and by < rect[1] and rect[1] + rect[3] < by + bh)

    def test_selected_tile_keeps_its_grid_signature(self):
        plain = kb.parse_list_tiles(self.frame("knowhow_rating_grid").texts, self.frame("knowhow_rating_grid"))["tiles"]
        for name in ("knowhow_1020_detail_top", "knowhow_1010_protected_top"):
            f = self.frame(name)
            tiles = kb.parse_list_tiles(f.texts, f)["tiles"]
            self.assertEqual(ks._rows(tiles), ks._rows(plain), name)

    def test_stitched_scan_switches_sort_and_reads_the_1020_book(self):
        s = ks.ScanSession(ks.ScanOptions.from_params({"max_books": 2}), kf.read_texts)
        seq = ([("knowhow_default_grid", "open_sort"), ("knowhow_sort_options", "pick_sort"), (None, "apply_sort"),
                ("knowhow_rating_grid", "open_tile")]
               # _09 already shows the last row (21 rows, unclipped): the book is complete there, _bottom is not needed
               + [(n, "scroll_detail") for n in DETAIL_1020[:-2]]
               + [(DETAIL_1020[-2], "open_tile"), ("knowhow_1010_protected_top", "scroll_detail")])
        decisions = []
        for name, expected in seq:
            image = self.eval_selected_popup() if name is None else self.frame(name)
            d = s.observe(image)
            if d.reason == "detail_settling":       # a drag frame (not one wheel notch): its unchanged re-capture
                d = s.observe(image)
            decisions.append(d)
            self.assertEqual(d.kind, expected, (name, d.to_dict()))
        self.assertEqual(decisions[1].key, kb.SORT_EVAL)
        self.assertEqual((decisions[3].row, decisions[3].col), (0, 0))
        self.assertEqual((decisions[-2].row, decisions[-2].col), (0, 1))
        r = s.result()
        self.assertEqual(r["orig_sort"], "デフォルト")
        self.assertEqual((r["owned_total"], r["grid"]["count"]), (514, 15))
        self.assertFalse(r["grid_complete"])                   # 15 of 514 tiles: never a whole-library claim
        self.assertTrue(r["partial"])
        self.assertTrue(r["sort_restore_needed"])               # stopped feeding mid-scan: sort still 評価値順
        self.assertEqual(r["grid"]["values"], [1020, 1010, 1000, 990, 980, 975, 975, 965, 960, 960, 960, 955, 955, 955, 950])
        [book] = r["books"]
        self.assertTrue(book["complete"])
        self.assertEqual((book["title"], book["eval_value"], book["protected"], book["knowhow_count"], len(book["rows"])),
                         ("【オイサラバエル】樋口円香", 1020, False, 21, 21))
        self.assertNotIn("rows.lv5_mismatch", book["unknowns"])
        self.assertEqual(r["grid"]["marks"]["4"], True)          # マーク read where no ✓ covers the tile
        self.assertEqual(s.detail["index"], 1)                  # 1010 book open, first page read

    def test_detail_frame_order_is_enforced(self):
        s = ks.ScanSession(ks.ScanOptions(recover_home=False), kf.read_texts)
        self.assertEqual(s.observe(self.frame("knowhow_rating_grid")).kind, "open_tile")
        self.assertEqual(s.observe(self.frame("knowhow_1020_detail_top")).kind, "scroll_detail")
        bottom = self.frame("knowhow_1020_detail_bottom")             # a jump with no overlapping rows
        self.assertEqual(s.observe(bottom).reason, "detail_settling")
        d = s.observe(bottom)
        self.assertEqual((d.kind, d.reason), ("stop", "detail_overlap_lost"))
        s = ks.ScanSession(ks.ScanOptions(recover_home=False), kf.read_texts)
        s.observe(self.frame("knowhow_rating_grid"))
        d = s.observe(self.frame("knowhow_1010_protected_top"))       # ✓ on another tile than the one opened
        self.assertEqual((d.kind, d.reason), ("stop", "detail_binding_lost"))
        s = ks.ScanSession(ks.ScanOptions(recover_home=False), kf.read_texts)
        s.observe(self.frame("knowhow_rating_grid"))
        d = s.observe(self.frame("knowhow_1020_detail_05"))           # panel not at the top after opening
        self.assertEqual((d.kind, d.reason), ("stop", "detail_not_at_top"))

    def test_modes_popups_and_destructive_frames_stop_without_input(self):
        cases = {"knowhow_protect_mode": "mode_protect", "knowhow_sale_mode": "destructive_marker",
                 "knowhow_mark_popup": "popup_mark", "knowhow_filter_top": "popup_filter",
                 "knowhow_search_category_top": "popup_search", "idol_list_support_top": "unknown_screen"}
        for name, reason in cases.items():
            s = ks.ScanSession(ks.ScanOptions(), kf.read_texts)
            d = s.observe(self.frame(name))
            with self.subTest(frame=name):
                self.assertEqual((d.kind, d.reason), ("stop", reason))
                self.assertEqual(s.inputs, 0)
        s = ks.ScanSession(ks.ScanOptions(), kf.read_texts)
        self.assertEqual(s.observe(self.frame("knowhow_default_grid")).kind, "open_sort")
        d = s.observe(self.frame("knowhow_filter_top"))               # a different popup than the requested 並び替え
        self.assertEqual((d.kind, d.reason), ("stop", "sort_popup_missing"))


if __name__ == "__main__":
    unittest.main()
