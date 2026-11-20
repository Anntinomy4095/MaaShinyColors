"""Private replay derivatives of native training frames for the 2026-10-07 list adaptation (tests only).

Every composite starts from a native 1280x720 capture of this account and moves *real* tiles: the configured あさひ tile
of the 2026-10-01 default-order list is pasted into the 2026-10-07 Lv順/▼降順 list (after_training_r1, which does not
show her) at other slots; button labels are redrawn with Yu Gothic Bold on the button's own background. They are
replay inputs, never evidence of the live state, and stay under dev_assets/private/ (not committed).
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
P = "dev_assets/private/"
# Native frames.
LIST_DEFAULT = P + "training-selection-execute-20261001f-frames/frame-001.png"   # デフォルト/▼降順, あさひ Lv2 at r1c2
LIST_LV_DESC = P + "supervised-tasks-20261007/after_training_r1.png"            # Lv順/▼降順 (live failure), no あさひ
ZERO = P + "training-selection-execute-20261001f-frames/frame-023.png"
SYN = P + "b5-replay/training-list/"
LV_DESC_R1C2 = SYN + "lv-desc-target-r1c2.png"
LV_DESC_R2C5 = SYN + "lv-desc-target-r2c5.png"
LV_DESC_R3C7 = SYN + "lv-desc-target-r3c7.png"
LV_ASC_R1C2 = SYN + "lv-asc-target-r1c2.png"
LV_ASC_R1C1 = SYN + "lv-asc-target-r1c1.png"
SORT_UNKNOWN = SYN + "sort-unknown.png"
FILTER_ON = SYN + "filter-on.png"
LOOKALIKE_GOLD = SYN + "lookalike-gold-r1c2.png"
WRONG_ZERO = SYN + "detail-zero-other-card.png"
NATIVE = (LIST_DEFAULT, LIST_LV_DESC, ZERO)
TILE = (405, 184, 513, 292)        # あさひ tile box in LIST_DEFAULT (grid offset 184)
FONT = "C:/Windows/Fonts/YuGothB.ttc"


def present():
    return all((ROOT / f).is_file() for f in NATIVE) and Path(FONT).is_file()


def slot_xy(row, col):
    return 267 + 138 * col, 184 + 154 * row


def _relabel(image, box, text, size=22):
    """Replace a button label: fill the text box with the button colour sampled at its left edge, draw ``text``."""
    from PIL import ImageDraw, ImageFont

    x, y, w, h = box
    colour = image.getpixel((x - 3, y + h // 2))
    draw = ImageDraw.Draw(image)
    draw.rectangle([x, y, x + w, y + h], fill=colour)
    draw.text((x + w / 2, y + h / 2), text, font=ImageFont.truetype(FONT, size), fill=(70, 62, 92), anchor="mm")
    return image


def _gold(tile):
    import numpy as np
    from PIL import Image

    a = np.asarray(tile).copy()
    gold = (214, 170, 40)
    a[:6, :] = gold
    a[-6:, :] = gold
    a[:, :4] = gold
    a[:, -6:] = gold
    return Image.fromarray(a)


def build():
    from PIL import Image

    out = ROOT / SYN
    out.mkdir(parents=True, exist_ok=True)
    default = Image.open(ROOT / LIST_DEFAULT).convert("RGB")
    lv_desc = Image.open(ROOT / LIST_LV_DESC).convert("RGB")
    tile = default.crop(TILE)

    def put(base, row, col, t=tile):
        image = base.copy()
        image.paste(t, slot_xy(row, col))
        return image

    put(lv_desc, 0, 1).save(ROOT / LV_DESC_R1C2)
    put(lv_desc, 1, 4).save(ROOT / LV_DESC_R2C5)
    put(lv_desc, 2, 6).save(ROOT / LV_DESC_R3C7)
    direction = (1151, 111, 82, 32)   # ▼降順 text box [1153,112,77,30] plus 2 px
    _relabel(put(lv_desc, 0, 1), direction, "▲昇順").save(ROOT / LV_ASC_R1C2)
    _relabel(put(lv_desc, 0, 0), direction, "▲昇順").save(ROOT / LV_ASC_R1C1)
    _relabel(put(lv_desc, 0, 1), (996, 111, 108, 32), "レア度順").save(ROOT / SORT_UNKNOWN)
    _relabel(put(lv_desc, 0, 1), (788, 110, 110, 33), "絞り込みON").save(ROOT / FILTER_ON)
    put(lv_desc, 0, 1, _gold(tile)).save(ROOT / LOOKALIKE_GOLD)
    zero = Image.open(ROOT / ZERO).convert("RGB")
    _relabel(zero, (162, 95, 416, 30), "【ひだまりの中で】櫻木真乃", size=22).save(ROOT / WRONG_ZERO)
