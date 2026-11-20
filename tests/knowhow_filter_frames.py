"""Synthetic 絞り込み popup scroll frames for offline replay (built from private 2026-10-12 native frames; no input).

The native samples (dev_assets/private/knowhow-sample-20261012) only show the popup at the top (#131) and part-way
down (#122). A virtual content strip = #131 content over #122 content is windowed at each scroll offset, and the
popup scrollbar thumb is repainted at the matching position, so the session's shift/thumb logic runs on real pixels.
"""
from __future__ import annotations

import glob
from pathlib import Path

import numpy as np

from agent.core import knowhow_scan as ks

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "dev_assets" / "private" / "knowhow-sample-20261012"
X, Y, W, H = ks.FILTER_POPUP_CONTENT
TRACK = (104, 570)
THUMB = 64


def path(n: int) -> Path | None:
    hits = glob.glob(str(SAMPLE / f"{n:03d}-*.png"))
    return Path(hits[0]) if hits else None


def ready() -> bool:
    return all(path(n) is not None for n in (1, 122, 131))


def load(n: int) -> np.ndarray:
    from PIL import Image

    with Image.open(path(n)) as image:
        return np.asarray(image.convert("RGB")).copy()


def strip() -> np.ndarray:
    return np.vstack([load(131)[Y:Y + H, X:X + W], load(122)[Y:Y + H, X:X + W]])


def popup_window(base: np.ndarray, content: np.ndarray, offset: int) -> np.ndarray:
    """``base`` popup frame with the content window at ``offset`` and the thumb at the matching track position."""
    out = base.copy()
    out[Y:Y + H, X:X + W] = content[offset:offset + H]
    span = content.shape[0] - H
    top = TRACK[0] + 2 + round((TRACK[1] - TRACK[0] - 4 - THUMB) * offset / span)
    out[TRACK[0]:TRACK[1] + 1, 1113:1126] = 160
    out[top:top + THUMB, 1113:1126] = 250
    return out


def offsets(step: int = 160) -> list[int]:
    span = strip().shape[0] - H
    values = list(range(0, span, step))
    return values + [span] if values[-1] != span else values
