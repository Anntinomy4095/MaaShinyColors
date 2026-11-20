"""Drift-tolerant row locator for the ノウハウブック一覧 grid (offline, pure Python + numpy; no input).

Why: one grid wheel step moves the content 159–160 px while rows are 157.5 px apart and the list never snaps, so
after a few steps the tiles drift off the fixed ``knowhow_books.LIST_GRID`` lattice (2026-10-12 live calibration,
``calibrate-20261012T022320Z``: half a row off by frame ~30). Fixed-grid reads and the fixed ``knowhow-tile-r*c*`` taps
both break. This module re-locates the rows on every frame and offers a fixed tap-slot table that covers any offset.

Measured on the 41 live frames (1280x720, 1:1 capture):
- Visible grid content: y 171..651 (``VIEW_TOP``..``VIEW_BOTTOM`` exclusive); above is the header rule (y 164/165),
  below the 所持数 bar.
- Row pitch: 157.5 px (pill tops alternate 157/158 px apart in every frame; least-squares slope 157.5 ± 0.3).
- The grey 「Lv.5↑xN/M」 pill is a flat (162,150,162) rounded bar 26 px tall at tile y+113..y+138 with text between
  y+119..y+134; its pure top (6 rows) and bottom (4 rows) bands and the white rows around it give an exact edge
  template (``locate_rows``), independent of the cover art.
- OCR pill token centre ≈ tile y + 124.5 (±3 px jitter): used as a cross-check and as the image-less fallback.

Tap slots (``SLOT_ACTIONS``): 6 y-slots × 5 columns, rect 44×12 at tile x+32..x+76. Slot tops are 70 px apart
(184, 254, …, 534). A tile at top T has a cover safe zone y∈[T+10, T+98] (≥10 px from the 108 px cover edges, far
above the pill at T+113); with ``LOCATE_TOLERANCE`` = 3 px of locator error a 12 px rect must start in
[T+13, T+83] — 71 integer positions, so a 70 px slot lattice always has one there for every complete row
(T∈[171, 513]). Horizontally x+32..x+76 avoids the マーク badge (x+4..x+30), the 保護 lock (x+2..x+22) and the ✓ corner
(x+80..x+110). ``slot_for`` returns the qualifying slot closest to the cover centre, so each tile maps to exactly one.
"""
from __future__ import annotations

import math
import re
from typing import Any, Mapping, Sequence

from agent.core import knowhow_books as kb

GRID_X = kb.LIST_GRID["x"]
GRID_Y = kb.LIST_GRID["y"]                  # aligned (offset 0) top of row 0
PITCH_X = kb.LIST_GRID["pitch_x"]
PITCH_Y = 157.5                             # measured 2026-10-12 (see module docstring); equals LIST_GRID pitch_y
TILE = kb.LIST_GRID["tile"]                 # 108 px white card / cover
COLS = kb.LIST_GRID["cols"]
VIEW_TOP, VIEW_BOTTOM = 171, 652            # visible content rows [171, 652)
PILL_RGB = (162, 150, 162)
PILL_COLOR_TOL = 8
PILL_TOP, PILL_HEIGHT = 113, 26             # pill rows tile+113 .. tile+138
PILL_BAND_X = (20, 90)                      # pill pixels sampled at tile x+20..x+90 (inside LIST_PILL_ROI)
TILE_EXTENT = PILL_TOP + PILL_HEIGHT        # a row is complete when tile top .. pill bottom are all visible
READ_TOP = kb.LIST_VALUE_ROI[1]             # a row is readable (評価値, lock, pill) when tile y+78 .. pill bottom are visible
OCR_PILL_CENTER = 124.5                     # OCR token centre y − tile top
EDGE_MIN = 1.5                              # per-column edge-template score for a pill (pure pill ≈ 2.0, side lobes ≤ 0.7)
INLIER_PX = 3                               # candidates within this many px of the lattice belong to it
OCR_AGREE_PX = 5
PITCH_CHECK_PX = 1.5                        # in-frame measured pitch must agree with PITCH_Y this closely

# --- tap slots ---
SAFE_MARGIN = 10                            # px kept from the cover's top/bottom edges
LOCATE_TOLERANCE = 3                        # locator error budget added to the margin
SLOT_X, SLOT_W, SLOT_H = 32, 44, 12         # rect relative to the column's tile x
SLOT_TOPS = (184, 254, 324, 394, 464, 534)  # 70 px lattice covering complete rows T∈[171, 513]
SETTLE_ROI = (526, 171, 690, 481)           # grid content left of the scrollbar
SETTLE_MAD = 0.5                            # live: static pairs 0.09–0.17, tile-selection change 0.92–1.8, scroll ≥ 11


def column_x(col: int) -> int:
    return round(GRID_X + col * PITCH_X)


def _wrap(value: float, period: float = PITCH_Y) -> float:
    """Wrap into (-period/2, period/2]."""
    w = math.fmod(value + period / 2, period)
    if w <= 0:
        w += period
    return w - period / 2


SLOT_ACTIONS: tuple[dict, ...] = tuple(
    {"id": f"knowhow-slot-y{k}c{c}", "family": "knowhow-scan", "consuming": False, "enabled": False,
     "rect": [column_x(c) + SLOT_X, top, SLOT_W, SLOT_H],
     "source": (f"knowhow_grid y-slot {k} (top {top}) x column {c}: cover middle x+{SLOT_X}..x+{SLOT_X + SLOT_W} of the "
                f"drifting 一覧 grid; any complete row (tile top 171..513, pitch 157.5) has one slot inside "
                f"[T+{SAFE_MARGIN + LOCATE_TOLERANCE}, T+{TILE - SAFE_MARGIN - LOCATE_TOLERANCE}] "
                "(calibrate-20261012T022320Z frames, 2026-10-12); selecting a tile only fills the left detail panel")}
    for k, top in enumerate(SLOT_TOPS) for c in range(COLS))
_SLOT_BY_ID = {a["id"]: a for a in SLOT_ACTIONS}


# ------------------------------------------------------------------------------------------------- locating
def pill_profile(image) -> list[list[float]]:
    """Per column, per y: fraction of pill-coloured pixels in the pill band (x+20..x+90)."""
    import numpy as np
    a = np.asarray(image, dtype=np.int16)
    out = []
    for c in range(COLS):
        x = column_x(c)
        band = a[:, x + PILL_BAND_X[0]:x + PILL_BAND_X[1], :3]
        hit = np.abs(band - np.asarray(PILL_RGB, dtype=np.int16)).max(axis=2) <= PILL_COLOR_TOL
        out.append(hit.mean(axis=1))
    return out


def pixel_pill_rows(image) -> list[dict]:
    """Pill top candidates ``[{"y": pill_top, "tile_y": .., "columns": [..], "score": ..}]`` from the edge template:
    pure top band [Y, Y+6) + bottom band [Y+22, Y+26) minus the white rows [Y-5, Y-1) and [Y+27, Y+31)."""
    import numpy as np
    if image is None:
        return []
    h = np.asarray(image).shape[0]
    lo, hi = VIEW_TOP, min(VIEW_BOTTOM, h) - PILL_HEIGHT       # the whole pill must lie in the visible area
    if hi <= lo:
        return []
    ys = np.arange(lo, hi + 1)
    scores = []
    for prof in pill_profile(image):
        cs = np.concatenate(([0.0], np.cumsum(prof)))

        def band(a, b):
            return (cs[ys + b] - cs[ys + a]) / (b - a)
        scores.append(band(0, 6) + band(22, 26) - band(-5, -1) - band(27, 31))
    scores = np.asarray(scores)                                 # (cols, len(ys))
    strong = scores >= EDGE_MIN
    strength = np.where(strong, scores, 0.0).sum(axis=0)
    out = []
    for i, y in enumerate(ys):
        if strength[i] <= 0:
            continue
        window = strength[max(0, i - 8):i + 9]
        if strength[i] < window.max() or (strength[i] == window.max() and np.argmax(window) + max(0, i - 8) != i):
            continue
        cols = [c for c in range(COLS) if strong[c, i]]
        out.append({"y": int(y), "tile_y": int(y) - PILL_TOP, "columns": cols, "score": round(float(strength[i]), 3)})
    return out


def ocr_pill_rows(texts) -> list[dict]:
    """Pill OCR tokens on the grid (x ≥ 500, kb._LV5 full match) → ``[{"tile_y": float, "col": int|None, "text"}]``."""
    out = []
    for t, b, _ in kb._tokens(texts):
        if not kb._LV5.fullmatch(t):
            continue
        cx, cy = kb._center(b)
        if cx < GRID_X - 20 or not (VIEW_TOP <= cy < VIEW_BOTTOM):
            continue
        col = round((cx - (GRID_X + TILE / 2)) / PITCH_X)
        out.append({"tile_y": cy - OCR_PILL_CENTER, "col": col if 0 <= col < COLS else None, "text": t})
    return out


def _fit(tops: Sequence[tuple[float, float]]) -> tuple[float | None, list[int], float | None]:
    """Lattice fit of (tile_y, weight) candidates → (offset, inlier indexes, measured in-frame pitch)."""
    if not tops:
        return None, [], None
    ref = max(range(len(tops)), key=lambda i: tops[i][1])
    o_ref = _wrap(tops[ref][0] - GRID_Y)
    res = [_wrap(_wrap(t - GRID_Y) - o_ref) for t, _ in tops]
    inl = [i for i, r in enumerate(res) if abs(r) <= INLIER_PX]
    wsum = sum(tops[i][1] for i in inl)
    offset = _wrap(o_ref + sum(res[i] * tops[i][1] for i in inl) / wsum)
    pitch = None
    pts = sorted(tops[i][0] for i in inl)
    if len(pts) >= 2:
        ks = [round((p - pts[0]) / PITCH_Y) for p in pts]
        if len(set(ks)) >= 2:
            mk, mp = sum(ks) / len(ks), sum(pts) / len(pts)
            pitch = sum((k - mk) * (p - mp) for k, p in zip(ks, pts)) / sum((k - mk) ** 2 for k in ks)
    return offset, inl, pitch


def rows_for_offset(offset: float) -> list[dict]:
    """Every lattice row overlapping the visible area for ``offset``: {index, y, y_exact, complete, readable,
    cover_visible}. ``readable``: the signature fields (評価値 y+78.., 保護 lock, pill ..y+139) are visible, though the
    tile top (マーク, ✓) may be clipped — enough to bind a row across a scroll, not to tap or read マーク / ✓."""
    base = GRID_Y + offset
    k = math.floor((VIEW_TOP - TILE_EXTENT - base) / PITCH_Y)
    rows = []
    while True:
        top = base + k * PITCH_Y
        if top >= VIEW_BOTTOM:
            break
        if top + TILE_EXTENT > VIEW_TOP:
            rows.append({"index": len(rows), "y": round(top), "y_exact": round(top, 2),
                         "complete": round(top) >= VIEW_TOP and round(top) + TILE_EXTENT <= VIEW_BOTTOM,
                         "readable": round(top) + READ_TOP >= VIEW_TOP and round(top) + TILE_EXTENT <= VIEW_BOTTOM,
                         "cover_visible": round(top) + SAFE_MARGIN >= VIEW_TOP
                         and round(top) + TILE - SAFE_MARGIN <= VIEW_BOTTOM})
        k += 1
    return rows


def locate_rows(texts=None, image=None) -> dict:
    """Vertical row offset of the 一覧 grid on one frame.

    Returns ``{ok, offset_px, pitch, pitch_measured, source, confidence, rows, reasons, pixel_rows, ocr_rows}``:
    ``offset_px`` ∈ (−pitch/2, pitch/2] relative to the aligned ``LIST_GRID`` (row tops at 180 + offset + k·pitch);
    ``rows`` lists every row overlapping y 171..651 with ``complete`` (tile top .. pill bottom visible) — only complete
    rows may be read or tapped. Pixels (pill edge template) are primary; OCR pill tokens cross-check them or stand in
    when no image is given. ``ok`` needs high/medium confidence; disagreement → low, no offset trust."""
    reasons: list[str] = []
    px = pixel_pill_rows(image) if image is not None else []
    ocr = ocr_pill_rows(texts) if texts else []
    out: dict[str, Any] = {"ok": False, "offset_px": None, "pitch": PITCH_Y, "pitch_measured": None, "source": None,
                           "confidence": "none", "rows": [], "reasons": reasons,
                           "pixel_rows": px, "ocr_rows": [round(o["tile_y"], 1) for o in ocr]}
    offset = None
    if px:
        offset, inl, pitch = _fit([(p["tile_y"], len(p["columns"])) for p in px])
        out["source"], out["pitch_measured"] = "pixels", None if pitch is None else round(pitch, 2)
        outliers = len(px) - len(inl)
        if outliers:
            reasons.append(f"pixel_outliers:{outliers}")
        if pitch is not None and abs(pitch - PITCH_Y) > PITCH_CHECK_PX:
            reasons.append(f"pitch_mismatch:{pitch:.2f}")
        n_rows = len(inl)
        if ocr:
            res = [_wrap(_wrap(o["tile_y"] - GRID_Y) - offset) for o in ocr]
            bad = sum(abs(r) > OCR_AGREE_PX for r in res)
            if bad:
                reasons.append(f"ocr_disagrees:{bad}/{len(ocr)}")
        else:
            reasons.append("no_ocr_check")
        strong = n_rows >= 2 or any(len(px[i]["columns"]) >= 3 for i in inl)
        if outliers or any(r.startswith(("pitch_mismatch", "ocr_disagrees")) for r in reasons):
            out["confidence"] = "low"
        else:
            out["confidence"] = "high" if strong else "medium"
    elif ocr:
        offset, inl, pitch = _fit([(o["tile_y"], 1.0) for o in ocr])
        out["source"], out["pitch_measured"] = "ocr", None if pitch is None else round(pitch, 2)
        reasons.append("no_pixel_pills" if image is not None else "no_image")
        outliers = len(ocr) - len(inl)
        if outliers:
            reasons.append(f"ocr_outliers:{outliers}")
        out["confidence"] = "medium" if len(inl) >= 2 and outliers == 0 else "low"
    else:
        reasons.append("no_pills")
    if offset is not None:
        out["offset_px"] = round(offset, 2)
        out["rows"] = rows_for_offset(offset)
        seen = {round(p["tile_y"]) for p in px}
        for row in out["rows"]:
            row["pill_seen"] = any(abs(row["y"] - s) <= INLIER_PX for s in seen)
    out["ok"] = out["confidence"] in ("high", "medium")
    return out


# ------------------------------------------------------------------------------------------------- reading
def tile_box_at(row_y: int, col: int) -> tuple[int, int, int, int]:
    return (column_x(col), int(row_y), TILE, TILE)


def _rows_of(rows, readable: bool = False) -> list[dict]:
    if isinstance(rows, Mapping):
        if not rows.get("ok", True):
            return []
        rows = rows.get("rows") or []
    return [r for r in rows if r.get("complete") or (readable and r.get("readable"))]


def parse_tiles_at(texts, image=None, rows=None, *, readable: bool = False) -> dict:
    """``kb.parse_list_tiles`` for a drifted grid: same per-tile fields, tiles only for complete rows of ``rows``
    (a ``locate_rows`` result or its row list; a not-ok locate gives no tiles). Adds ``y`` (row top), ``y_exact``,
    ``grid_row`` (visible row order), ``complete`` and ``slot`` (tap action id). ``readable=True`` also returns the
    rows whose top is clipped but whose 評価値 / lock / pill are visible (``complete`` False): their マーク / ✓ are
    None and they have no slot. Same screen refusals as ``parse_list_tiles``."""
    if rows is None:
        rows = locate_rows(texts, image)
    screen = kb.classify_knowhow_screen(texts, image)
    out = {**screen, "tiles": [], "order_ok": None, "calibrated": kb.CALIBRATION["list_tiles"],
           "offset_px": rows.get("offset_px") if isinstance(rows, Mapping) else None}
    if screen["screen"] != "list" or not screen["ok"]:
        return out
    toks = kb._tokens(texts)
    for gi, row in enumerate(_rows_of(rows, readable)):
        complete = bool(row.get("complete"))
        for col in range(COLS):
            tile = tile_box_at(row["y"], col)
            values = [t for t, b, _ in toks if kb._inside(b, kb._off(tile, kb.LIST_VALUE_ROI)) and re.fullmatch(r"\d{2,4}", t)]
            pills = [m for t, b, _ in toks if kb._inside(b, kb._off(tile, kb.LIST_LV5_ROI)) for m in [kb._LV5.fullmatch(t)] if m]
            names = [t for t, b, _ in toks if kb._inside(b, kb._off(tile, kb.LIST_NAME_ROI))
                     and re.fullmatch(r"[぀-ヿ一-鿿]{1,4}", t)]
            lv5 = count = None
            if len(pills) == 1:
                n, m = int(pills[0].group(1)), int(pills[0].group(2))
                if n <= m <= kb.MAX_ROWS:
                    lv5, count = n, m
            overlay = kb._tile_overlay(toks, tile, kb.SORT_OVERLAY.get(screen["sort"]), count)
            pixels = kb.list_tile_pixels(image, tile)
            if not complete:                    # マーク / ✓ ROIs lie in the clipped top part
                pixels.update(marked=None, checked=None)
            out["tiles"].append({"row": gi, "grid_row": gi, "col": col, "y": row["y"],
                                 "y_exact": row.get("y_exact", row["y"]), "complete": complete, "box": list(tile),
                                 "overlay": overlay,
                                 "value": int(values[0]) if len(values) == 1 and screen["eval_sorted"] else None,
                                 "lv5": lv5, "knowhow_count": count, "name": names[0] if len(names) == 1 else None,
                                 "slot": slot_for(tile) if complete else None, **pixels})
    vals = [t["value"] for t in out["tiles"] if t["value"] is not None]
    if screen["eval_sorted"] and vals:
        pairs = list(zip(vals, vals[1:]))
        if screen["direction"] == "desc":
            out["order_ok"] = all(a >= b for a, b in pairs)
        elif screen["direction"] == "asc":
            out["order_ok"] = all(a <= b for a, b in pairs)
    return out


# ------------------------------------------------------------------------------------------------- tapping
def safe_zone(tile_box) -> tuple[int, int]:
    """[top, bottom] y range a tap rect must stay inside for this tile (margin + locator tolerance)."""
    y = tile_box[1]
    return y + SAFE_MARGIN + LOCATE_TOLERANCE, y + TILE - SAFE_MARGIN - LOCATE_TOLERANCE


def slot_for(tile_box) -> str | None:
    """Tap action id for a located tile: the slot in the tile's column whose whole rect lies in the cover safe zone,
    closest to the cover centre. None when the column is unknown, the cover is not fully visible or no slot fits."""
    x, y = int(tile_box[0]), int(tile_box[1])
    col = next((c for c in range(COLS) if abs(column_x(c) - x) <= 4), None)
    if col is None or y + SAFE_MARGIN < VIEW_TOP or y + TILE - SAFE_MARGIN > VIEW_BOTTOM:
        return None
    lo, hi = safe_zone(tile_box)
    fits = [k for k, top in enumerate(SLOT_TOPS) if top >= lo and top + SLOT_H <= hi]
    if not fits:
        return None
    centre = y + TILE / 2
    k = min(fits, key=lambda k: (abs(SLOT_TOPS[k] + SLOT_H / 2 - centre), k))
    return f"knowhow-slot-y{k}c{col}"


def slot_rect(action_id: str) -> list[int] | None:
    a = _SLOT_BY_ID.get(action_id)
    return None if a is None else list(a["rect"])


# ------------------------------------------------------------------------------------------------- settling
def grid_mad(prev_image, image, roi=SETTLE_ROI) -> float | None:
    import numpy as np
    if prev_image is None or image is None:
        return None
    a, b = np.asarray(prev_image), np.asarray(image)
    if a.shape != b.shape:
        return None
    x, y, w, h = roi
    pa = a[y:y + h, x:x + w, :3].astype(np.int16)
    pb = b[y:y + h, x:x + w, :3].astype(np.int16)
    return float(np.abs(pa - pb).mean()) if pa.size else None


def settled(prev_image, image, threshold: float = SETTLE_MAD, roi=SETTLE_ROI) -> bool:
    """True when the grid region (or ``roi``) is (near-)identical to the previous capture: read only settled frames."""
    mad = grid_mad(prev_image, image, roi)
    return mad is not None and mad <= threshold


__all__ = ["PITCH_Y", "VIEW_TOP", "VIEW_BOTTOM", "SLOT_ACTIONS", "SLOT_TOPS", "locate_rows", "rows_for_offset",
           "parse_tiles_at", "slot_for", "slot_rect", "safe_zone", "settled", "grid_mad", "pixel_pill_rows",
           "ocr_pill_rows", "tile_box_at", "column_x"]
