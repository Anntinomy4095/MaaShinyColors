"""ノウハウブック一覧: per-sort tile overlays, 絞り込みON and the 絞り込み popup count on 2026-10-12 native frames.

The user operated the game (sorts, filter, one sale) while Maa only captured
(dev_assets/private/knowhow-sample-20261012/). Offline: project Maa OCR + pixels, no controller, no input. Expected
values were read by eye. Skips when the private frames, the OCR model or MaaFramework are absent.
"""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

from agent.core import knowhow_books as kb

ROOT = Path(__file__).resolve().parents[1]
D = ROOT / "dev_assets" / "private" / "knowhow-sample-20261012"
MODEL = ROOT / "dev_assets" / "private" / "models" / "ppocr_v6_small"
FRAMES = ("001", "083", "088", "105", "118", "117", "122", "130")


def _path(prefix):
    hits = sorted(D.glob(prefix + "-*.png")) if D.is_dir() else []
    return hits[0] if len(hits) == 1 else None


def _ready() -> bool:
    return (all(_path(f) for f in FRAMES) and all((MODEL / n).is_file() for n in ("det.onnx", "rec.onnx", "keys.txt"))
            and all(importlib.util.find_spec(m) is not None for m in ("maa", "numpy", "PIL")))


@unittest.skipUnless(_ready(), "需要 2026-10-12 know-how 私有原生帧、ppocr 模型、MaaFramework、numpy 与 Pillow")
class KnowhowSortOverlayNativeTests(unittest.TestCase):
    _cache: dict = {}

    @classmethod
    def frame(cls, prefix):
        if prefix not in cls._cache:
            from tests.maa_probe import ocr_items, rgb
            path = _path(prefix)
            cls._cache[prefix] = (ocr_items(path), rgb(path))
        return cls._cache[prefix]

    def overlays(self, prefix):
        r = kb.parse_list_tiles(*self.frame(prefix))
        self.assertTrue(r["ok"], prefix)
        return r, [t["overlay"] for t in r["tiles"]]

    def test_rating_and_vocal_overlays(self):
        r, values = self.overlays("083")
        self.assertEqual(r["sort"], "ノウハウ評価値順")
        self.assertEqual(values, [1020, 1010, 1000, 990, 980, 975, 975, 965, 960, 960, 960, 955, 955, 955, 950])
        r, values = self.overlays("088")
        self.assertEqual(r["sort"], "Vocal上昇値順")
        self.assertEqual(values, [150] * 6 + [140] * 9)

    def test_acquisition_dates(self):
        r, values = self.overlays("105")
        self.assertEqual((r["sort"], r["direction"]), ("獲得順", "desc"))
        self.assertEqual(values[:6], ["2026-10-12", "2026-10-10", "2026-10-06", "2026-10-04", "2026-09-06", "2026-09-06"])
        self.assertEqual(values, sorted(values, reverse=True))

    def test_newest_books_scan_opens_the_latest_tile_on_the_date_sorted_list(self):
        # ScanOptions.newest: the list already in 獲得順 ▼ (#105) needs no sort input; the first decision opens grid index
        # 1 (2026-10-10: the older of the two newest books is read first) through a tap slot inside its cover.
        from agent.core import knowhow_grid as kg
        from agent.core import knowhow_scan as ks
        from tests import knowhow_scan_frames as kf

        texts, image = self.frame("105")
        s = ks.ScanSession(ks.ScanOptions(newest=2, recover_home=False), kf.read_texts)
        d = s.observe(kf.frame(image, texts))
        self.assertEqual((d.kind, d.reason, d.notes[0]), ("open_tile", "read_new_book", "index=1"))
        self.assertEqual((s.orig_sort, s.current_sort), ("獲得順", "獲得順"))
        self.assertEqual(s.grid[0][0], "2026-10-12")
        self.assertEqual([s.grid[i][0] for i in range(5)],
                         ["2026-10-12", "2026-10-10", "2026-10-06", "2026-10-04", "2026-09-06"])
        x, y, w, h = kg.slot_rect(d.key)
        tile = kb.list_tile_box(0, 1)
        self.assertTrue(tile[0] <= x and x + w <= tile[0] + tile[2] and tile[1] <= y and y + h <= tile[1] + tile[3])

    def test_default_sort_has_no_overlay_and_filter_state_is_read(self):
        r, values = self.overlays("001")
        self.assertEqual((r["sort"], r["filter_active"], r["owned"]), ("デフォルト", False, 516))
        self.assertEqual(values, [None] * 15)
        r = kb.parse_list_tiles(*self.frame("118"))
        self.assertEqual((r["sort"], r["filter_active"], r["owned"]), ("Lv順", True, 498))

    def test_filter_popup_count_before_apply(self):
        counts = [kb.parse_filter_popup(self.frame(p)[0]) for p in ("117", "122", "130")]
        self.assertEqual(counts, [{"open": True, "count": n} for n in (25, 403, 195)])
        self.assertEqual(kb.parse_filter_popup(self.frame("001")[0]), {"open": False, "count": None})


if __name__ == "__main__":
    unittest.main()
