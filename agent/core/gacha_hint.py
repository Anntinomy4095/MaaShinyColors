"""Read-only 「ガシャ」 free-draw hint on the Home screen. Never taps, never draws.

The Home ガシャ button carries an orange tag above its label when the game offers a free draw today (native frame
``manual-capture-kept-20261002/home-20261003-nobadge.png`` and recordings of 2026-10-06: 「1日1回無料」). Without an
offer the plate shows the plain blue gacha pictogram instead. The same orange tag style is used for other notices
(「超成功発生中」 on アイドル), so the colour alone is never a positive: a free read needs the tag fill AND OCR text that
contains 「無料」 with a clean score.

Kinds (display only; the tool never draws):

* ``free_ten``    -- 「無料10連」 / 「10連無料」 (no sample yet; documented look, same tag),
* ``free_single`` -- 「単発無料」 / 「1連無料」 (no sample yet),
* ``free_daily``  -- 「1日1回無料」 (positive samples; the tag does not say whether it is a single or a ten-pull),
* ``free``        -- 「無料」 with nothing else readable (a white shimmer can cut the text: 「回無料」).

States: ``free`` (positive), ``no_tag`` (plain plate: the user drew already or there is no offer), ``tag_other`` (an
orange tag without 無料), ``unread`` (tag present but OCR not clean / not run) and ``unknown`` (fill between the
thresholds). Only ``free`` is shown to the user; everything else hides the reminder.
"""
from __future__ import annotations

import re

# (x, y, w, h) on the canonical 1280x720 canvas, measured on the native home frames (2026-10-02/03/06).
TAG_ROI = (390, 630, 112, 38)        # the orange tag band above the label (plain plate: white/blue pictogram)
OCR_ROI = (386, 626, 122, 46)        # OCR box: the tag plus a small margin
TAG_FILL_POSITIVE = 0.20            # positive frames: 0.29 (white shimmer sweeping the tag) .. ~0.75
TAG_FILL_NEGATIVE = 0.05            # plain plate: <= 0.004 on 568 home frames
MIN_OCR = 0.80

FREE = re.compile(r"無料|无料")
TEN = re.compile(r"10\s*[連连回]|十[連连]")
SINGLE = re.compile(r"単発|单发|1\s*連")      # never bare 「1回」: a shimmer-cut 「1日1回無料」 reads 「1回無料」
DAILY = re.compile(r"1\s*日\s*1\s*回|毎日|每日")


def _pixels(image, roi):
    """RGB rows of ``roi`` from a PIL image or an HxWx3 BGR numpy array (Maa frame)."""
    x, y, w, h = roi
    if hasattr(image, "crop"):
        crop = image.convert("RGB").crop((x, y, x + w, y + h))
        return [list(crop.get_flattened_data())]
    region = image[y:y + h, x:x + w, :3]
    return [[(int(p[2]), int(p[1]), int(p[0])) for p in row] for row in region]


def tag_fill(image, roi=TAG_ROI):
    """Fraction of saturated orange/red tag pixels in the tag band (0..1)."""
    total = hit = 0
    for row in _pixels(image, roi):
        for r, g, b in row:
            total += 1
            if r >= 200 and b <= 70 and 35 <= g <= 225 and r - b >= 150:
                hit += 1
    return hit / total if total else 0.0


def kind_of(text):
    text = (text or "").replace(" ", "")
    if not FREE.search(text):
        return None
    if TEN.search(text):
        return "free_ten"
    if DAILY.search(text):
        return "free_daily"
    if SINGLE.search(text):
        return "free_single"
    return "free"


def classify(fill, texts=None, min_ocr=MIN_OCR):
    """``fill`` from :func:`tag_fill`; ``texts`` = [(text, score, ...)] OCR rows of :data:`OCR_ROI` (None = not run)."""
    out = {"state": "unknown", "kind": None, "text": "", "fill_pm": int(round(max(0.0, min(1.0, fill)) * 1000))}
    if fill <= TAG_FILL_NEGATIVE:
        out["state"] = "no_tag"
        return out
    if fill < TAG_FILL_POSITIVE:
        return out
    clean = [row for row in texts or [] if isinstance(row[0], str) and float(row[1]) >= min_ocr]
    text = "".join(row[0] for row in clean)[:30]
    out["text"] = text
    if texts is None or not clean:
        out["state"] = "unread"
        return out
    kind = kind_of(text)
    if kind is None:
        out["state"] = "tag_other"
        return out
    out.update(state="free", kind=kind)
    return out


def read(image, ocr=None, min_ocr=MIN_OCR):
    """Pixel gate first; OCR (``ocr(image, roi) -> rows``) only when the tag is there. Pure reading, no input."""
    fill = tag_fill(image)
    texts = None
    if fill >= TAG_FILL_POSITIVE and ocr is not None:
        texts = list(ocr(image, OCR_ROI))
    return classify(fill, texts, min_ocr)
