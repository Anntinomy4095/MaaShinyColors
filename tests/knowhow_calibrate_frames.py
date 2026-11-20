"""Native-derived calibration sequence for tests/test_knowhow_calibrate.py (private 2026-10-08 frames; no input).

The 2026-10-08 capture has no wheel frames. The detail pages are the real scrollbar-drag pages of
the 1020 book (real pixel shifts); the grid wheel frames are windows of a virtual grid strip built from two real grid
crops (``knowhow_1020_detail_bottom`` above ``knowhow_default_grid``), so a "wheel" of ``dy`` px is an exact content
shift with real tiles on both sides (no wrap-around). OCR tokens of the source frame are kept (the calibration reads
only the page meta from them on grid frames).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from agent.core import knowhow_calibrate as kc

ROOT = Path(__file__).resolve().parents[1]
D = ROOT / "dev_assets" / "private" / "live-capture-20261008-knowhow"
GRID_START = "knowhow_rating_grid"
DETAIL = (["knowhow_1020_detail_top"] + [f"knowhow_1020_detail_{i:02d}" for i in range(1, 10)]
          + ["knowhow_1020_detail_bottom"])
STRIP_SOURCES = ("knowhow_1020_detail_bottom", "knowhow_default_grid")


def ready() -> bool:
    try:
        from tests.test_knowhow_native_20261008 import _ready
    except Exception:
        return False
    return _ready() and all((D / f"{n}.png").is_file() for n in (GRID_START, *DETAIL, *STRIP_SOURCES))


def load(name):
    from tests.maa_probe import rgb

    return rgb(D / f"{name}.png")


def grid_window(base, strip, offset):
    """``base`` with its grid ROI replaced by rows ``offset``.. of ``strip`` (content scrolled down by ``offset``)."""
    x, y, w, h = kc.GRID_ROI
    out = base.copy()
    out[y:y + h, x:x + w] = strip[offset:offset + h]
    return out


def grid_strip():
    x, y, w, h = kc.GRID_ROI
    return np.concatenate([load(n)[y:y + h, x:x + w] for n in STRIP_SOURCES], axis=0)
