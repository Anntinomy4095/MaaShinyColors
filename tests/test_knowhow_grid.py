"""Drift-tolerant 一覧 grid locator (agent/core/knowhow_grid.py): synthetic tests + 2026-10-12 live-frame replay.

The live part replays ``dev_assets/private/knowhow-scan/calibrate-20261012T022320Z`` (41 frames; grid wheel steps
17→41) and its record ``knowhow-calibrate-20261012T022820Z-partial.json``; it is skipped when those private files, the
OCR model or MaaFramework are missing. No input is sent anywhere."""
from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path

import numpy as np

from agent.core import knowhow_books as kb
from agent.core import knowhow_grid as kg

ROOT = Path(__file__).resolve().parents[1]
CAL = ROOT / "dev_assets" / "private" / "knowhow-scan" / "calibrate-20261012T022320Z"
RECORD = ROOT / "dev_assets" / "private" / "knowhow-scan" / "knowhow-calibrate-20261012T022820Z-partial.json"
HAVE_MAA = importlib.util.find_spec("maa") is not None
GRID_FRAMES = range(17, 42)
OCR_FRAMES = (17, 22, 30, 33, 38, 40)


def T(text, x, y, w=60, h=20):
    return (text, (x, y, w, h), 0.99)


def _frames_ready() -> bool:
    return RECORD.is_file() and all((CAL / f"frame-{i:04d}.png").is_file() for i in GRID_FRAMES)


def _ocr_ready() -> bool:
    if not (_frames_ready() and HAVE_MAA):
        return False
    from tests.maa_probe import MODEL
    return MODEL.is_dir()


def _maa_ocr(image_rgb):
    """Full-frame project Maa OCR (same call as tests.test_knowhow_calibrate._maa_ocr; no controller input)."""
    from tests import maa_probe
    from tools.replay_ocr import MaaOcrRecognizer

    if maa_probe._OCR is None:
        maa_probe._OCR = MaaOcrRecognizer(maa_probe.MODEL)
    bgr = np.ascontiguousarray(image_rgb[:, :, ::-1])
    result = maa_probe._OCR.recognize({"name": "SC.Test.OCR", "roi": [0, 0, 1280, 720], "expected": "^.*$",
                                       "threshold": 0.0}, bgr)
    assert maa_probe._OCR.input_attempts == 0
    return [(i["text"], tuple(int(v) for v in i["box"]), float(i["score"])) for i in result["results"] if i["text"]]


# ------------------------------------------------------------------------------------------------- synthetic
def synthetic_grid(offset: float, cols_last_row=None, seed: int = 7):
    """White 一覧 canvas with unchecked toggles, noisy covers and grey pills at ``offset``; clipped to y 171..651."""
    rng = np.random.default_rng(seed)
    image = np.full((720, 1280, 3), 255, dtype=np.uint8)
    for x, y, w, h in kb.TOGGLE_INNER:
        image[y:y + h, x:x + w] = kb.TOGGLE_OFF_RGB
    image[164] = (127, 119, 123)
    canvas = np.full((720, 1280, 3), 255, dtype=np.uint8)
    rows = [k for k in range(-2, 6) if -200 < kg.GRID_Y + offset + k * kg.PITCH_Y < 720]
    tops = []
    for idx, k in enumerate(rows):
        top = round(kg.GRID_Y + offset + k * kg.PITCH_Y)
        tops.append(top)
        cols = range(kg.COLS) if cols_last_row is None or idx != len(rows) - 1 else range(cols_last_row)
        for c in cols:
            x = kg.column_x(c)
            y0, y1 = max(top, 0), min(top + kg.TILE, 720)
            if y1 > y0:
                canvas[y0:y1, x:x + kg.TILE] = rng.integers(0, 256, (y1 - y0, kg.TILE, 3), dtype=np.uint8)
            p0, p1 = top + kg.PILL_TOP, top + kg.PILL_TOP + kg.PILL_HEIGHT
            if p1 > 0 and p0 < 720:
                canvas[max(p0, 0):min(p1, 720), x - 6:x + 114] = kg.PILL_RGB
                t0, t1 = max(p0 + 6, 0), min(p0 + 21, 720)
                if t1 > t0:
                    canvas[t0:t1, x + 30:x + 100:3] = (255, 255, 255)   # pill text strokes
    image[kg.VIEW_TOP:kg.VIEW_BOTTOM] = canvas[kg.VIEW_TOP:kg.VIEW_BOTTOM]
    return image, tops


def list_texts():
    return [T("ノウハウブックー", 52, 19, 190, 32), T("覧", 241, 17, 37, 37), T("ノウハウ評価値順", 972, 109, 150, 27),
            T("▼降順", 1150, 105, 78, 33), T("514/600", 1153, 660, 93, 24)]


class SyntheticGridTests(unittest.TestCase):
    def test_offset_recovered_for_any_drift(self):
        for offset in np.arange(-78.5, 78.75, 6.5):
            with self.subTest(offset=float(offset)):
                image, tops = synthetic_grid(float(offset))
                out = kg.locate_rows([], image)
                self.assertEqual(out["source"], "pixels")
                self.assertTrue(out["ok"])
                self.assertLessEqual(abs(kg._wrap(out["offset_px"] - offset)), 1.0)
                self.assertAlmostEqual(out["pitch_measured"], 157.5, delta=0.6)
                complete = [r["y"] for r in out["rows"] if r["complete"]]
                self.assertIn(len(complete), (2, 3))
                expected = [t for t in tops if t >= kg.VIEW_TOP and t + kg.TILE_EXTENT <= kg.VIEW_BOTTOM]
                for y in expected:
                    self.assertTrue(any(abs(y - c) <= 1 for c in complete), (y, complete))
                for r in out["rows"]:
                    self.assertEqual(r["complete"], r["y"] >= 171 and r["y"] + 139 <= 652)

    def test_sparse_last_row_and_offset_range(self):
        image, _ = synthetic_grid(-40.0, cols_last_row=1)
        out = kg.locate_rows([], image)
        self.assertTrue(out["ok"])
        self.assertLessEqual(abs(out["offset_px"] + 40), 1.0)
        for offset in (-78.75, 0, 78.75, 200, -200):
            self.assertTrue(-78.75 < kg._wrap(offset) <= 78.75)

    def test_ocr_fallback_and_disagreement(self):
        image, tops = synthetic_grid(-30.0)
        texts = [T("Lv.5+x3/9", kg.column_x(c) + 2, y + 113, 104, 23)
                 for y in tops if kg.VIEW_TOP <= y + 124 < kg.VIEW_BOTTOM for c in range(kg.COLS)]
        ocr_only = kg.locate_rows(texts, None)
        self.assertEqual((ocr_only["source"], ocr_only["confidence"]), ("ocr", "medium"))
        self.assertLessEqual(abs(ocr_only["offset_px"] + 30), 2.0)
        both = kg.locate_rows(texts, image)
        self.assertEqual((both["source"], both["confidence"], both["reasons"]), ("pixels", "high", []))
        shifted = [(t, (b[0], b[1] + 40, b[2], b[3]), s) for t, b, s in texts]
        bad = kg.locate_rows(shifted, image)
        self.assertEqual(bad["confidence"], "low")
        self.assertFalse(bad["ok"])
        self.assertTrue(any(r.startswith("ocr_disagrees") for r in bad["reasons"]))
        nothing = kg.locate_rows([], np.full((720, 1280, 3), 255, dtype=np.uint8))
        self.assertEqual((nothing["ok"], nothing["offset_px"], nothing["reasons"]), (False, None, ["no_pills"]))

    def test_parse_tiles_only_on_complete_rows(self):
        image, tops = synthetic_grid(-45.0)
        texts = list_texts()
        complete = [y for y in tops if y >= kg.VIEW_TOP and y + kg.TILE_EXTENT <= kg.VIEW_BOTTOM]
        for gi, y in enumerate(complete):
            for c in range(kg.COLS):
                x = kg.column_x(c)
                texts += [T(str(1020 - 10 * (gi * 5 + c)), x + 58, y + 83, 50, 24),
                          T(f"Lv.5+x{c}/21", x - 6, y + 115, 120, 22)]
        located = kg.locate_rows(texts, image)
        out = kg.parse_tiles_at(texts, image, located)
        self.assertEqual(len(out["tiles"]), 5 * len(complete))
        self.assertEqual([t["lv5"] for t in out["tiles"][:5]], [0, 1, 2, 3, 4])
        self.assertEqual({t["knowhow_count"] for t in out["tiles"]}, {21})
        self.assertTrue(out["order_ok"])
        self.assertTrue(all(t["slot"] for t in out["tiles"]))
        fixed = kb.parse_list_tiles(texts, image)                        # the fixed grid misses the drifted pills
        self.assertLess(sum(t["lv5"] is not None for t in fixed["tiles"]), len(out["tiles"]))
        refused = kg.parse_tiles_at(texts, image, {**located, "ok": False})
        self.assertEqual(refused["tiles"], [])
        self.assertEqual(kg.parse_tiles_at(texts[2:], image, located)["tiles"], [])   # not the 一覧 page

    def test_readable_rows_bind_but_never_tap(self):
        image, tops = synthetic_grid(-60.0)
        texts = list_texts()
        for y in tops:
            for c in range(kg.COLS):
                x = kg.column_x(c)
                if y + 86 >= kg.VIEW_TOP and y + 136 <= kg.VIEW_BOTTOM:
                    texts += [T("900", x + 58, y + 86, 50, 22), T(f"Lv.5+x{c}/9", x - 6, y + 115, 120, 22)]
        located = kg.locate_rows(texts, image)
        readable = [r for r in located["rows"] if r["readable"] and not r["complete"]]
        self.assertEqual([r["y"] for r in readable], [120])                   # top clipped, value..pill visible
        plain = kg.parse_tiles_at(texts, image, located)
        both = kg.parse_tiles_at(texts, image, located, readable=True)
        self.assertEqual(len(both["tiles"]), len(plain["tiles"]) + 5)
        clipped = [t for t in both["tiles"] if not t["complete"]]
        self.assertEqual(len(clipped), 5)
        for t in clipped:
            self.assertEqual((t["y"], t["slot"], t["marked"], t["checked"]), (120, None, None, None))
            self.assertEqual((t["value"], t["knowhow_count"]), (900, 9))
        self.assertTrue(all(t["slot"] for t in both["tiles"] if t["complete"]))

    def test_slot_table(self):
        ids = [a["id"] for a in kg.SLOT_ACTIONS]
        self.assertEqual(len(ids), len(kg.SLOT_TOPS) * kg.COLS)
        self.assertEqual(len(set(ids)), len(ids))
        for a in kg.SLOT_ACTIONS:
            x, y, w, h = a["rect"]
            self.assertFalse(a["consuming"])
            self.assertFalse(a["enabled"])                                   # new live input starts disabled
            self.assertTrue(kg.VIEW_TOP <= y and y + h <= kg.VIEW_BOTTOM)
            self.assertEqual(kg.slot_rect(a["id"]), a["rect"])
        self.assertIsNone(kg.slot_rect("knowhow-tile-r0c0"))

    def test_every_complete_tile_gets_exactly_one_safe_slot(self):
        for t2 in range(2 * kg.VIEW_TOP, 2 * (kg.VIEW_BOTTOM - kg.TILE_EXTENT) + 1):
            top = t2 / 2
            for c in range(kg.COLS):
                box = (kg.column_x(c), round(top), kg.TILE, kg.TILE)
                slot = kg.slot_for(box)
                self.assertIsNotNone(slot, (top, c))
                x, y, w, h = kg.slot_rect(slot)
                self.assertTrue(slot.endswith(f"c{c}"))
                # true tile may be up to LOCATE_TOLERANCE px away from the located one
                for true_top in (box[1] - kg.LOCATE_TOLERANCE, box[1] + kg.LOCATE_TOLERANCE):
                    self.assertGreaterEqual(y, true_top + kg.SAFE_MARGIN)
                    self.assertLessEqual(y + h, true_top + kg.TILE - kg.SAFE_MARGIN)
                    self.assertLess(y + h, true_top + kg.PILL_TOP)            # never the pill
                self.assertGreaterEqual(x, box[0] + 31)                        # right of マーク (x+30) / lock (x+22)
                self.assertLessEqual(x + w, box[0] + 79)                       # left of the ✓ corner (x+80)
        self.assertIsNone(kg.slot_for((kg.column_x(0), 150, 108, 108)))         # cover cut by the header
        self.assertIsNone(kg.slot_for((kg.column_x(0), 560, 108, 108)))         # cover cut by the 所持数 bar
        self.assertIsNone(kg.slot_for((600, 300, 108, 108)))                    # not a column

    def test_settled(self):
        a = np.full((720, 1280, 3), 200, dtype=np.uint8)
        a[300:400, 600:700] = 30
        self.assertTrue(kg.settled(a, a.copy()))
        b = a.copy()
        b[0:150] = 0                                                           # outside the grid region (header)
        b[:, :500] = 0                                                         # left detail panel
        self.assertTrue(kg.settled(a, b))
        c = np.roll(a, 40, axis=0)
        self.assertFalse(kg.settled(a, c))
        self.assertFalse(kg.settled(None, a))
        self.assertFalse(kg.settled(a, a[:700]))


# ------------------------------------------------------------------------------------------------- live replay
@unittest.skipUnless(_frames_ready(), "需要 2026-10-12 实机校准私有帧（calibrate-20261012T022320Z）与记录")
class LiveGridDriftTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.maa_probe import rgb
        cls.images = {i: rgb(CAL / f"frame-{i:04d}.png") for i in range(1, 42)}
        cls.loc = {i: kg.locate_rows([], cls.images[i]) for i in GRID_FRAMES}
        cls.record = json.loads(RECORD.read_text(encoding="utf-8"))

    def test_pitch_and_offsets(self):
        for i, out in self.loc.items():
            with self.subTest(frame=i):
                self.assertTrue(out["ok"])
                self.assertEqual(out["source"], "pixels")
                self.assertIn(out["confidence"], ("high", "medium"))
                self.assertAlmostEqual(out["pitch_measured"], 157.5, delta=0.5)
                self.assertIn(len([r for r in out["rows"] if r["complete"]]), (2, 3))
        self.assertLessEqual(abs(self.loc[17]["offset_px"]), 1.0)               # aligned before the first wheel
        self.assertGreater(abs(self.loc[30]["offset_px"]), 25)                  # fixed grid is off by ~28 px here
        self.assertGreater(abs(self.loc[33]["offset_px"]), 50)                  # and by ~half a row after frame 33

    def test_offsets_track_recorded_steps(self):
        steps = [s for s in self.record["grid"]["steps"] if s["before"] in self.loc and s["after"] in self.loc]
        self.assertGreaterEqual(len(steps), 20)
        total = 0
        for s in steps:
            with self.subTest(step=(s["before"], s["after"], s["dy"])):
                pred = kg._wrap(self.loc[s["before"]]["offset_px"] - s["dy"])
                self.assertLessEqual(abs(kg._wrap(self.loc[s["after"]]["offset_px"] - pred)), 1.5)
            total += s["dy"]
        first, last = steps[0]["before"], steps[-1]["after"]
        cumulative = kg._wrap(self.loc[first]["offset_px"] - total)
        self.assertLessEqual(abs(kg._wrap(self.loc[last]["offset_px"] - cumulative)), 3.0)

    def test_settled_on_live_pairs(self):
        for i in range(2, 9):                                                  # detail-panel wheels: grid unchanged
            self.assertTrue(kg.settled(self.images[i], self.images[i + 1]), i)
        for s in self.record["grid"]["steps"]:
            if s["dy"] >= 100:
                self.assertFalse(kg.settled(self.images[s["before"]], self.images[s["after"]]), s)


@unittest.skipUnless(_ocr_ready(), "需要 2026-10-12 实机校准私有帧、ppocr 模型与 MaaFramework")
class LiveGridReadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.maa_probe import rgb
        cls.images = {i: rgb(CAL / f"frame-{i:04d}.png") for i in OCR_FRAMES}
        cls.texts = {i: _maa_ocr(cls.images[i]) for i in OCR_FRAMES}

    def test_ocr_agrees_with_pixels(self):
        for i in OCR_FRAMES:
            with self.subTest(frame=i):
                both = kg.locate_rows(self.texts[i], self.images[i])
                self.assertEqual((both["confidence"], both["reasons"]), ("high", []))
                ocr_only = kg.locate_rows(self.texts[i], None)
                self.assertTrue(ocr_only["ok"])
                self.assertLessEqual(abs(kg._wrap(ocr_only["offset_px"] - both["offset_px"])), 2.0)

    def test_tiles_readable_and_tappable_at_drifted_frames(self):
        for i in OCR_FRAMES:
            with self.subTest(frame=i):
                texts, image = self.texts[i], self.images[i]
                located = kg.locate_rows(texts, image)
                out = kg.parse_tiles_at(texts, image, located)
                complete = [r for r in located["rows"] if r["complete"]]
                self.assertEqual(len(out["tiles"]), 5 * len(complete))
                pills = [(kb._center(b), m) for t, b, _ in kb._tokens(texts) for m in [kb._LV5.fullmatch(t)]
                         if m and b[0] > 500]
                slots = set()
                for tile in out["tiles"]:
                    self.assertIsNotNone(tile["lv5"], tile)                   # every slot holds a book here
                    x, y = tile["box"][:2]
                    own = [(c, m) for c, m in pills if x - 10 <= c[0] <= x + 118 and y + 108 <= c[1] <= y + 142]
                    self.assertEqual(len(own), 1, tile)
                    (cx, cy), m = own[0]
                    self.assertEqual((tile["lv5"], tile["knowhow_count"]), (int(m.group(1)), int(m.group(2))))
                    true_top = cy - kg.OCR_PILL_CENTER                         # independent tile top from OCR
                    rx, ry, rw, rh = kg.slot_rect(tile["slot"])
                    self.assertGreaterEqual(ry, true_top + kg.SAFE_MARGIN - 0.5)
                    self.assertLessEqual(ry + rh, true_top + kg.TILE - kg.SAFE_MARGIN + 0.5)
                    self.assertTrue(x + 30 < rx and rx + rw < x + 80)
                    slots.add(tile["slot"])
                self.assertEqual(len(slots), len(out["tiles"]))                # one distinct slot per tile
        fixed = kb.parse_list_tiles(self.texts[30], self.images[30])
        self.assertEqual(sum(t["lv5"] is not None for t in fixed["tiles"]), 0)  # fixed LIST_GRID reads nothing here


if __name__ == "__main__":
    unittest.main()
