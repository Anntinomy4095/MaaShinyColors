"""Owned Lv / 凸 reader (agent/core/owned_levels.py, tools/owned_levels_scan.py).  Offline: synthetic star columns and
a fake OCR always; the user's private Lv-sorted capture when present (skipped otherwise).  No device, no input."""
import unittest
from pathlib import Path

from agent.core import owned_levels as ol

ROOT = Path(__file__).resolve().parent.parent
LV_FRAME = ROOT / "dev_assets" / "private" / "owned-levels-20261007" / "idol-list-lv-001.png"
MODEL = ROOT / "dev_assets" / "private" / "models" / "ppocr_v6_small"
ASSETS = ROOT / "ui" / "game-assets" / "manifest.json"

PURPLE, GOLD, RING, ART = (190, 160, 240), (250, 200, 40), (90, 90, 95), (200, 150, 230)
BOX = (100, 100, 108, 108)


def column(slots, *, ring=True, background=(245, 245, 250), dy=0):
    """1280x720 frame with a star column for a tile at BOX; ``slots`` top..bottom: 'p' / 'g' / 'a' (art blob) / None."""
    import numpy as np
    from PIL import Image

    arr = np.full((720, 1280, 3), background, dtype=np.uint8)
    mask = ol._mask()
    grown = ol._ring(mask) | mask
    for k, kind in enumerate(slots):
        if kind is None:
            continue
        x0, y0 = BOX[0] + ol.STAR_X, BOX[1] + ol.STAR_Y0 + ol.STAR_DY * k + dy
        patch = arr[y0:y0 + ol.STAR_SIZE, x0:x0 + ol.STAR_SIZE]
        if kind == "a":                                   # star-coloured art: whole area, no outline
            patch[grown] = ART
            continue
        if ring:
            patch[grown] = RING
        patch[mask] = PURPLE if kind == "p" else GOLD
    return Image.fromarray(arr)


def stars(slots, **kw):
    return ol.read_stars(ol._hsv(column(slots, **kw)), BOX)


class StarTests(unittest.TestCase):
    def test_counts_and_colours(self):
        for slots, count, colour in (((None,) * 4, 0, "none"), ((None, None, None, "g"), 1, "gold"),
                                     ((None, None, "g", "g"), 2, "gold"), ((None, "g", "g", "g"), 3, "gold"),
                                     (("p",) * 4, 4, "purple")):
            reading = stars(slots)
            self.assertEqual((reading.status, reading.count, reading.colour), ("accepted", count, colour), slots)

    def test_gold_stars_slightly_lower_still_read(self):
        self.assertEqual(stars((None, None, "g", "g"), dy=2).count, 2)

    def test_mixed_colours_refused(self):
        self.assertEqual(stars((None, "p", "g", "g")).reason, "mixed_star_colours")

    def test_unseen_count_colour_refused(self):
        # 1-3 purple or 4 gold was never observed: refused instead of interpreted
        self.assertEqual(stars((None, "p", "p", "p")).reason, "unseen_count_colour")
        self.assertEqual(stars(("g",) * 4).reason, "unseen_count_colour")

    def test_gap_in_column_refused(self):
        self.assertEqual(stars(("p", None, "p", "p")).reason, "stars_not_bottom_aligned")

    def test_weakly_star_coloured_art_is_not_a_star(self):
        import numpy as np

        frame = column((None, None, None, "g"))
        arr = np.asarray(frame).copy()
        x0, y0 = BOX[0] + ol.STAR_X, BOX[1] + ol.STAR_Y0
        arr[y0:y0 + 17:2, x0:x0 + 17] = ART               # striped purple art: about half the core
        reading = ol.read_stars(ol._hsv(__import__("PIL.Image").Image.fromarray(arr)), BOX)
        self.assertEqual((reading.status, reading.count), ("accepted", 1))

    def test_fully_star_coloured_art_is_refused(self):
        self.assertIn("ambiguous", stars(("a", None, None, "g")).reason)

    def test_star_without_outline_is_ambiguous(self):
        self.assertIn("ambiguous", stars((None, None, None, "p"), ring=False).reason)


class LevelTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(ol.parse_level("Lv.60"), 60)
        self.assertEqual(ol.parse_level("Lv80"), 80)
        self.assertIsNone(ol.parse_level("Ly.65"))
        self.assertIsNone(ol.parse_level("MLv.1"))

    def _bgr(self):
        import numpy as np

        return np.zeros((720, 1280, 3), dtype=np.uint8)

    def test_clean_read(self):
        level, score, reason = ol.read_level(lambda bgr, roi: [("Lv.57", 0.99)], self._bgr(), BOX)
        self.assertEqual((level, reason), (57, "ok"))

    def test_retry_must_agree_with_native_digits(self):
        calls = []

        def fake(answer):
            def run(bgr, roi):
                calls.append(roi)
                return [("Ly.65", 0.99)] if len(calls) % 2 == 1 else [(answer, 0.99)]
            return run

        self.assertEqual(ol.read_level(fake("Lv.65"), self._bgr(), BOX)[:2], (65, 0.99))
        self.assertIsNone(ol.read_level(fake("Lv.66"), self._bgr(), BOX)[0])

    def test_low_score_and_ambiguous_refused(self):
        self.assertEqual(ol.read_level(lambda b, r: [("Lv.5", 0.5)], self._bgr(), BOX)[2], "low_ocr_score")
        self.assertEqual(ol.read_level(lambda b, r: [("Lv.5", 0.99), ("Lv.6", 0.99)], self._bgr(), BOX)[2],
                         "ambiguous_text")

    def test_level_caps(self):
        self.assertEqual(ol.level_cap("SSR", 0), 60)
        self.assertEqual(ol.level_cap("S_SR", 2), 60)
        self.assertEqual(ol.level_cap("UR", 4), 90)
        self.assertIsNone(ol.level_cap("N", 0))
        self.assertIsNone(ol.level_cap("SSR", 5))


class MergeTests(unittest.TestCase):
    @staticmethod
    def rec(cid, level, lb, conf=0.9, frame="f1", status="accepted"):
        return {"status": status, "card_id": cid, "level": level, "limit_break": lb, "confidence": conf,
                "frame": frame, "slot": "r1c1", "stars": {"colour": "purple" if lb == 4 else "gold"}}

    def test_conflicting_frames_withheld(self):
        merged = ol.merge_readings([self.rec("2040010020", 80, 4), self.rec("2040010020", 80, 4, frame="f2"),
                                    self.rec("2040020020", 3, 1), self.rec("2040020020", 4, 1, frame="f2"),
                                    self.rec(None, None, None, status="rejected")])
        self.assertEqual(set(merged["cards"]), {"2040010020"})
        self.assertEqual(merged["cards"]["2040010020"]["frames"], 2)
        self.assertIn("2040020020", merged["conflicts"])
        self.assertEqual(merged["rejected"], 1)

    def test_details_shape(self):
        merged = ol.merge_readings([self.rec("2040010020", 80, 4), self.rec("1040010010", None, 2)])
        self.assertEqual(ol.details_for_import(merged),
                         {"2040010020": {"level": 80, "limit_break": 4}, "1040010010": {"limit_break": 2}})


class ConfidenceTests(unittest.TestCase):
    """Confidence = 1 - risk; each signal's risk is largest at its acceptance threshold."""
    COMFY = {"score": 0.95, "margin": 0.30, "verify": 0.85, "edge": 0.50, "variant_margin": 0.40}

    def rec(self, **kw):
        base = {"match_signals": dict(self.COMFY), "tier": {"expected": "rainbow", "observed": "rainbow"},
                "stars": {"fill": 1.0, "contrast": 0.30, "empty_peak": 0.0}, "limit_break": 4, "level": 80,
                "ocr_score": 0.99, "level_reason": "ok"}
        base.update(kw)
        return base

    def test_comfortable_reading_is_high(self):
        self.assertEqual(ol.match_confidence(self.COMFY), 1.0)
        self.assertGreaterEqual(ol.reading_confidence(self.rec()), ol.AUTO_ACCEPT_CONFIDENCE)

    def test_near_threshold_signals_are_low(self):
        edge = dict(self.COMFY, score=0.81, verify=0.61, variant_margin=0.03)   # just cleared three thresholds
        self.assertLess(ol.reading_confidence(self.rec(match_signals=edge)), 0.85)  # "near threshold" stays below the calibrated ~90%-correct band (user accepts auto >= 0.70)
        self.assertLess(ol.reading_confidence(self.rec(ocr_score=0.90, level_reason="ok_upscaled",
                                                       match_signals=dict(self.COMFY, score=0.83))), 0.85)

    def test_unclear_rarity_frame_and_missing_signals_cost(self):
        unclear = self.rec(tier={"expected": "rainbow", "observed": None})
        self.assertAlmostEqual(ol.reading_confidence(self.rec()) - ol.reading_confidence(unclear), ol.UNKNOWN_TIER_RISK)
        self.assertLess(ol.match_confidence({}), 0.6)          # no signals = no confidence

    def test_empty_slot_near_star_band_costs(self):
        calm = self.rec(limit_break=0, stars={"fill": 1.0, "contrast": 1.0, "empty_peak": 0.30})
        close = self.rec(limit_break=0, stars={"fill": 1.0, "contrast": 1.0, "empty_peak": 0.74})
        self.assertGreater(ol.reading_confidence(calm), ol.reading_confidence(close))

    def test_merge_keeps_best_tile_confidence_and_newest_frame(self):
        rec = MergeTests.rec
        merged = ol.merge_readings([rec("2040010020", 80, 4, conf=0.85, frame="rec-20261006-1/f1.png"),
                                    rec("2040010020", 80, 4, conf=0.95, frame="rec-20261006-1/f2.png"),
                                    rec("2040010020", 80, 4, conf=0.70, frame="live-20261008/f.png")])
        card = merged["cards"]["2040010020"]
        self.assertEqual(card["confidence"], 0.95)            # agreeing frames do not raise it (same art, same mistake)
        self.assertEqual(card["frame"], "rec-20261006-1/f2.png")
        self.assertEqual(card["latest_frame"], "live-20261008/f.png")
        self.assertEqual(card["frames_distinct"], 3)


@unittest.skipUnless(LV_FRAME.is_file() and MODEL.is_dir() and ASSETS.is_file(), "private Lv-sorted capture not present")
class PrivateCaptureTests(unittest.TestCase):
    def test_lv_sorted_frame(self):
        from PIL import Image

        from agent.core import card_inventory as ci

        index = ci.CardIndex.from_assets()
        with Image.open(LV_FRAME) as image:
            recs = ol.read_frame(image, index, ol.maa_ocr(MODEL), frame_name="lv-001")
        got = {r["slot"]: (r["level"], r["limit_break"]) for r in recs if r["status"] == "accepted"}
        # checked by eye on the capture (r1c1 SR: Lv60 with 2 gold stars; r2c6 SSR: Lv59, 1 gold; r3c2 SSR: Lv58, none)
        self.assertEqual(got["r1c1"], (60, 2))
        self.assertEqual(got["r2c6"], (59, 1))
        self.assertEqual(got["r3c2"], (58, 0))
        self.assertEqual(got["r3c7"], (53, 4))
        self.assertGreaterEqual(len(got), 18)
        self.assertTrue(all(r["status"] == "rejected" for r in recs if r["card_id"] is None))
        self.assertTrue(all(0 < r["confidence"] <= 1 for r in recs if r["status"] == "accepted"))

    def test_leave_one_out_wrong_matches_stay_below_auto_threshold(self):
        """Remove the true card from the reference set: whatever is still accepted is wrong, and must not be auto-applied."""
        from PIL import Image

        from agent.core import card_inventory as ci

        index = ci.CardIndex.from_assets()
        pos = {cid: i for i, cid in enumerate(index.ids)}
        with Image.open(LV_FRAME) as image:
            frame = image.convert("RGB")
            recs = [r for r in ol.read_frame(frame, index, ol.maa_ocr(MODEL), frame_name="lv-001") if r["status"] == "accepted"]
            for r in recs:
                x, y, w, h = r["box"]
                tile = frame.crop((x, y, x + w, y + h))
                row = index.coarse_scores([tile])[0].copy()
                row[pos[r["card_id"]]] = -2.0
                tier = ci.frame_tier(frame, tuple(r["box"]))
                m = ci._rarity_check(index.match(tile, r["slot"], tuple(r["box"]), None, row, tier), frame, index)
                if m.status != "accepted":
                    continue
                rarity = index.meta.get(m.card_id, {}).get("rarity")
                wrong = dict(r, match_signals={k: getattr(m, k) for k in ("score", "margin", "verify", "edge", "variant_margin")},
                             tier={"expected": ci.FRAME_TIERS.get(str(rarity or "")), "observed": tier})
                self.assertLess(ol.reading_confidence(wrong), ol.AUTO_ACCEPT_CONFIDENCE, (r["slot"], m.card_id))


if __name__ == "__main__":
    unittest.main()
