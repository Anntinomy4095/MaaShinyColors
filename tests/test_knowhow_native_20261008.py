"""ノウハウブック一覧 K1 recognition replayed on the 2026-10-08 native 1280x720 frames.

Offline only: full-frame Maa OCR (tests/maa_probe.ocr_items, the project's ppocr model) + pixel checks on private PNGs;
no controller, no input. Expected values were read by eye from the frames. Skips cleanly when the private frames, the
OCR model or MaaFramework are absent.
"""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

from agent.core import knowhow_books as kb

ROOT = Path(__file__).resolve().parents[1]
D = ROOT / "dev_assets" / "private" / "live-capture-20261008-knowhow"
MODEL = ROOT / "dev_assets" / "private" / "models" / "ppocr_v6_small"
DETAIL_1020 = (["knowhow_1020_detail_top"] + [f"knowhow_1020_detail_{i:02d}" for i in range(1, 10)]
               + ["knowhow_1020_detail_bottom"])
NORMAL = DETAIL_1020 + ["knowhow_1010_protected_top", "knowhow_default_grid", "knowhow_rating_grid",
                        "knowhow_restored_default", "knowhow_filter_cancelled", "knowhow_protect_mode_closed",
                        "knowhow_sale_mode_closed"]
POPUPS = {**{f: "filter" for f in ["knowhow_filter_top", "knowhow_filter_bottom", "knowhow_search_ability_draft"]
             + [f"knowhow_filter_{i:02d}" for i in range(1, 8)]},
          **{f: "search" for f in ["knowhow_search_category_top", "knowhow_search_ability_groups_top",
                                   "knowhow_search_ability_groups_01", "knowhow_search_ability_groups_02",
                                   "knowhow_search_ability_groups_bottom"]},
          "knowhow_mark_popup": "mark", "knowhow_mark_popup_bottom": "mark", "knowhow_sort_options": "sort"}
MODES = {"knowhow_protect_mode": "protect", "knowhow_sale_mode": "sale"}
ALL = NORMAL + list(POPUPS) + list(MODES)

# 【オイサラバエル】樋口円香, read by eye across the 11 overlapping frames (names normalised: NFKC, no spaces, ◯/◎).
BOOK_1020 = [("DanceUP", 1), ("思い出(ノウハウ)+", 5), ("おはよう街角の朝ごはん(Vo)", 4), ("HOPPINJAM(Vo)", 5),
             ("基礎能力値UP(+3%)(W.I.N.G.)", 4), ("Dance適正◎", 4), ("オールラウンダー◯", 5), ("オールラウンダー◎", 4),
             ("スロースターター", 5), ("パーフェクトリィ(G.R.A.D.)", 3), ("Vocal上限+", 5), ("Vocal上限++", 5),
             ("Vocal上限+++", 4), ("メンタル上限+", 4), ("アピールUP(思い出低)", 5), ("スペシャリスト(G.R.A.D.)", 5),
             ("アピールUP(思い出高&低)(G.R.A.D.)", 4), ("ロマンチスト(G.R.A.D.)", 4), ("LandingPoint大成功(SP)++", 5),
             ("エキシビションマッチ+", 5), ("エントリー目標達成(Vo)", 2)]
RATING_VALUES = [1020, 1010, 1000, 990, 980, 975, 975, 965, 960, 960, 960, 955, 955, 955, 950]
RATING_LV5 = [10, 14, 12, 14, 14, 14, 12, 12, 12, 12, 13, 13, 18, 13, 14]
DEFAULT_PILLS = [(0, 13), (10, 13), (3, 9), (6, 13), (5, 11), (7, 13), (12, 21), (1, 4), (12, 21), (14, 21),
                 (6, 12), (13, 20), (12, 20), (7, 11), (9, 15)]


def _ready() -> bool:
    if not all((D / (f + ".png")).is_file() for f in ALL):
        return False
    if not all((MODEL / name).is_file() for name in ("det.onnx", "rec.onnx", "keys.txt")):
        return False
    return all(importlib.util.find_spec(m) is not None for m in ("maa", "numpy", "PIL"))


@unittest.skipUnless(_ready(), "需要 2026-10-08 know-how 私有原生帧、ppocr 模型、MaaFramework、numpy 与 Pillow")
class KnowhowNativeReplayTests(unittest.TestCase):
    _cache: dict = {}

    @classmethod
    def frame(cls, name):
        if name not in cls._cache:
            from tests.maa_probe import ocr_items, rgb
            path = D / (name + ".png")
            cls._cache[name] = (ocr_items(path), rgb(path))
        return cls._cache[name]

    def test_screen_classification_on_all_38_frames(self):
        self.assertEqual(len(ALL), 38)
        for name in ALL:
            texts, image = self.frame(name)
            c = kb.classify_knowhow_screen(texts, image)
            with self.subTest(frame=name):
                if name in NORMAL:
                    self.assertEqual((c["screen"], c["popup"], c["mode"], c["ok"]), ("list", None, "normal", True))
                    self.assertEqual((c["owned"], c["capacity"], c["direction"]), (514, 600, "desc"))
                    self.assertTrue(c["calibrated"])
                    self.assertIs(c["filter_active"], False)  # plain 「絞り込み」 (2026-10-12: 絞り込みON is now read)
                elif name in POPUPS:
                    self.assertEqual(c["popup"], POPUPS[name])
                    self.assertFalse(c["ok"])
                else:
                    self.assertEqual((c["screen"], c["mode"], c["ok"]), ("list", MODES[name], False))
        sorts = {n: kb.classify_knowhow_screen(*self.frame(n))["sort"]
                 for n in ("knowhow_default_grid", "knowhow_restored_default", "knowhow_rating_grid")}
        self.assertEqual(sorts, {"knowhow_default_grid": "デフォルト", "knowhow_restored_default": "デフォルト",
                                 "knowhow_rating_grid": kb.SORT_EVAL})

    def test_protect_mode_needs_pixels_and_both_modes_are_refused(self):
        texts, image = self.frame("knowhow_protect_mode")
        text_only = kb.classify_knowhow_screen(texts)
        self.assertEqual(text_only["refused"], [])            # 保護 mode adds no text at all
        self.assertIsNone(text_only["mode"])
        self.assertFalse(text_only["ok"])
        self.assertEqual(kb.parse_list_tiles(texts)["tiles"], [])
        self.assertEqual(kb.parse_list_tiles(texts, image)["tiles"], [])
        self.assertGreaterEqual(kb.classify_knowhow_screen(texts, image)["guards"]["protect_pink"], kb.MODE_PINK_MIN)
        texts, image = self.frame("knowhow_sale_mode")
        self.assertEqual(kb.parse_list_tiles(texts, image)["tiles"], [])
        self.assertIn("売却する", kb.classify_knowhow_screen(texts)["refused"])
        for name in POPUPS:
            self.assertEqual(kb.parse_list_tiles(*self.frame(name))["tiles"], [], name)

    def test_rating_grid_values_pills_marks_and_locks(self):
        out = kb.parse_list_tiles(*self.frame("knowhow_rating_grid"))
        tiles = out["tiles"]
        self.assertTrue(out["calibrated"])
        self.assertEqual(len(tiles), 15)
        self.assertEqual([t["value"] for t in tiles], RATING_VALUES)
        self.assertEqual([t["value"] for t in tiles[:3]], [1020, 1010, 1000])
        self.assertTrue(out["order_ok"])
        self.assertEqual([t["lv5"] for t in tiles], RATING_LV5)
        self.assertEqual({t["knowhow_count"] for t in tiles}, {21})
        pos = lambda key: [(t["row"], t["col"]) for t in tiles if t[key]]
        self.assertEqual(pos("marked"), [(0, 4), (2, 4)])
        self.assertEqual(pos("protected"), [(0, 1), (0, 3), (0, 4), (2, 4)])
        self.assertEqual(pos("checked"), [])
        self.assertEqual(pos("greyed"), [])
        self.assertEqual(len(pos("present")), 15)

    def test_default_grid_never_reports_values(self):
        out = kb.parse_list_tiles(*self.frame("knowhow_default_grid"))
        tiles = out["tiles"]
        self.assertEqual(out["sort"], "デフォルト")
        self.assertEqual({t["value"] for t in tiles}, {None})
        self.assertEqual([(t["lv5"], t["knowhow_count"]) for t in tiles], DEFAULT_PILLS)
        self.assertEqual([(t["row"], t["col"]) for t in tiles if t["protected"]], [(1, 3), (1, 4), (2, 3)])
        self.assertEqual([(t["row"], t["col"]) for t in tiles if t["marked"]], [])
        self.assertIsNone(tiles[0]["name"])                   # 283プロ book: no idol short name

    def test_1020_detail_sequence_merges_into_one_complete_book(self):
        pages = []
        for name in DETAIL_1020:
            texts, image = self.frame(name)
            page = kb.parse_detail_panel(texts, image)
            with self.subTest(frame=name):
                self.assertEqual(page["title"], "【オイサラバエル】樋口円香")
                self.assertEqual(page["stats"], {"vo": 60, "da": 20, "vi": 20, "me": 30})
                self.assertEqual(page["knowhow_count"], 21)
                self.assertIs(page["protected"], False)
                self.assertEqual(len(page["rows"]), 3)
                self.assertFalse(any(r["clipped"] for r in page["rows"]))
                for row in page["rows"]:
                    self.assertIn((row["name"], row["level"]), BOOK_1020)
            pages.append(page)
        merged = kb.merge_detail_pages(pages)
        self.assertEqual(merged["unknowns"], [])
        self.assertTrue(merged["complete"])
        self.assertEqual([(r["name"], r["level"]) for r in merged["rows"]], BOOK_1020)
        self.assertEqual(sum(1 for r in merged["rows"] if r["level"] >= 5), 10)  # = tile pill 「Lv.5↑x10/21」
        book = kb.book_from_detail(merged, eval_value=1020)
        self.assertTrue(book.complete)
        self.assertTrue(book.fingerprint.startswith("b-"))
        self.assertEqual(book.idol, "樋口円香")
        self.assertIs(book.protected, False)

    def test_repairs_are_recorded(self):
        rows = {r["name"]: r["repairs"] for n in ("knowhow_1020_detail_01", "knowhow_1020_detail_02", "knowhow_1020_detail_03",
                                                    "knowhow_1020_detail_07")
                for r in kb.parse_detail_panel(*self.frame(n))["rows"]}
        self.assertEqual(rows["Dance適正◎"], ["ring"])          # OCR drops ◎ entirely
        self.assertEqual(rows["オールラウンダー◎"], ["ring"])
        self.assertEqual(rows["オールラウンダー◯"], [])          # OCR reads 〇, normalised to ◯
        self.assertEqual(rows["基礎能力値UP(+3%)(W.I.N.G.)"], ["closing_paren"])
        self.assertEqual(rows["アピールUP(思い出高&低)(G.R.A.D.)"], ["abbreviation"])
        texts, _ = self.frame("knowhow_1020_detail_02")
        names = [r["name"] for r in kb.parse_detail_panel(texts)["rows"]]
        self.assertIn("Dance適正", names)                        # without pixels the ◎ is not invented

    def test_1010_protected_book_and_checked_tile(self):
        texts, image = self.frame("knowhow_1010_protected_top")
        page = kb.parse_detail_panel(texts, image)
        self.assertEqual(page["title"], "【ポシェットの中には】福丸小糸")
        self.assertEqual(page["stats"], {"vo": 110, "da": 30, "vi": 30, "me": 40})
        self.assertIs(page["protected"], True)
        tiles = kb.parse_list_tiles(texts, image)["tiles"]
        self.assertEqual([(t["row"], t["col"], t["value"], t["protected"]) for t in tiles if t["checked"]],
                         [(0, 1, 1010, True)])
        texts, image = self.frame("knowhow_1020_detail_top")
        self.assertEqual([t["value"] for t in kb.parse_list_tiles(texts, image)["tiles"] if t["checked"]], [1020])
        texts, image = self.frame("knowhow_rating_grid")
        self.assertTrue(kb.parse_detail_panel(texts, image)["empty"])


if __name__ == "__main__":
    unittest.main()
