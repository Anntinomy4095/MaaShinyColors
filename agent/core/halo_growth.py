"""say "Halo" 成長 (H2c batch 3): readers and the per-visit plan.

ステータス tab (H1 frames 2026-11-01): four rows (Vocal / Dance / Visual / メンタル), each a stat card (left, +10) and a
cap card (right, 上限+10) with − / 「N回」 / + / >>; one step costs the number under the row's colour (stat 10, 15 after
many; cap 10 → 15 → 20). Top bar: the five points left after what is queued (歌唱力 安定感 表現力 集中力 団結力). Bottom
bar: the projected stat / cap per colour (current values until something is queued). その他 tab: テンション+10 and
親愛度+10 (団結力 30; in H1 a second + after the first did nothing). Queued steps are learned with 習得する → 成長確認
習得する.

Pure: OCR boxes in, readings out; the plan is a function of one reading plus the visit state.
"""
from __future__ import annotations

import re
from typing import Iterable, Mapping, Sequence

Texts = Iterable[tuple]
COLOURS = ("vo", "da", "vi", "me")
POINTS = ("vo", "da", "vi", "me", "unity")
POINT_X = (710, 831, 953, 1075, 1201)        # value centres of the top bar (y ~57); icons sit ~60 px left
POINT_TOL = 30
ROW_Y = {"vo": 210, "da": 337, "vi": 464, "me": 591}     # centre of a row's button line
ROW_LABEL = {"vo": r"^Vocal\+10$", "da": r"^Dance\+10$", "vi": r"^Visual\+10$", "me": r"^メンタル\+10$"}
COUNT_X = {"stat": 470, "cap": 1050}
COST_X = {"vo": 134, "da": 248, "vi": 353}    # cost under the colour's icon in the stat card; the cap card is +582
CAP_DX = 582
COST_DY = 54                                  # cost line below the button line (メンタル's is below the fold)
BOTTOM_X = {"vo": 240, "da": 390, "vi": 537, "me": 690}
BOTTOM_Y = (646, 677)                         # projected stat, cap
STAT_STEP = 10
AFFECTION_COST = 30                           # 団結力 per 親愛度+10
MAX_TAPS = 120                                # taps per 成長 visit: H1 S3W8 had ~580-910 points a colour, ~20-26
                                              # cap steps each at the rule below


def _clean(text) -> str:
    return re.sub(r"\s+", "", str(text))


def _cx(box: Sequence[int]) -> float:
    return box[0] + box[2] / 2


def _cy(box: Sequence[int]) -> float:
    return box[1] + box[3] / 2


NUMBER = r"^\d{1,3}(,?\d{3})?$"               # 「1,020」 on the bottom bar once a cap passes 1000 (H1 S3W8)


def _near(texts: Sequence[tuple], x: float, y: float, dx: float, dy: float, pattern: str = NUMBER) -> list:
    return [(t, b) for t, b in texts if re.search(pattern, _clean(t)) and abs(_cx(b) - x) <= dx and abs(_cy(b) - y) <= dy]


def _one_int(texts, x, y, dx, dy, pattern=NUMBER) -> int | None:
    hits = _near(texts, x, y, dx, dy, pattern)
    if len(hits) != 1:
        return None
    m = re.search(r"\d[\d,]*", _clean(hits[0][0]))
    return int(m.group(0).replace(",", "")) if m else None


def parse_points(texts: Texts) -> dict:
    """Top bar: {"vo", "da", "vi", "me", "unity": points left or None}."""
    texts = list(texts)
    return {key: _one_int(texts, x, 57, POINT_TOL, 20) for key, x in zip(POINTS, POINT_X)}


def parse_tab(texts: Texts) -> str | None:
    """"status" (row labels Vocal+10 …), "other" (親愛度+10) or None."""
    joined = {_clean(t) for t, _ in texts}
    if any(re.search(ROW_LABEL["vo"], t) for t in joined):
        return "status"
    if any("親愛度+10" in t for t in joined):
        return "other"
    return None


def parse_rows(texts: Texts) -> dict:
    """ステータス rows: {colour: {"label", "stat_count", "cap_count", "stat_cost", "cap_cost"}} (メンタル: no costs)."""
    texts = list(texts)
    rows = {}
    for colour in COLOURS:
        y = ROW_Y[colour]
        label = any(re.search(ROW_LABEL[colour], _clean(t)) and abs(_cy(b) - y) <= 22 for t, b in texts)
        row = {"label": label,
               "stat_count": _one_int(texts, COUNT_X["stat"], y, 40, 20, r"^\d{1,3}回$"),
               "cap_count": _one_int(texts, COUNT_X["cap"], y, 40, 20, r"^\d{1,3}回$"),
               "stat_cost": None, "cap_cost": None}
        if colour in COST_X:
            row["stat_cost"] = _one_int(texts, COST_X[colour], y + COST_DY, 30, 22)
            row["cap_cost"] = _one_int(texts, COST_X[colour] + CAP_DX, y + COST_DY, 30, 22)
        rows[colour] = row
    return rows


_FRAGMENT = r"^,\d{3}$"                       # 「1,474」 read without its leading digit (live 2026-11-15 S3)


def _bottom_int(texts, x, y) -> tuple[int | None, int | None]:
    """(value, fragment) of one bottom-bar slot. OCR may add the tail of the number as a second box (「1,474」 + 「474」,
    live 2026-11-15): a value that is only the tail of a longer one is dropped. fragment = the last three digits of a
    「,474」 reading whose leading digit was lost."""
    values = set()
    for t, _ in _near(texts, x, y, 32, 14):
        values.add(int(re.sub(r"\D", "", _clean(t))))
    values = {v for v in values if not any(w != v and str(w).endswith(str(v)) for w in values)}
    frags = {int(_clean(t)[1:]) for t, _ in _near(texts, x, y, 32, 14, _FRAGMENT)}
    return (values.pop() if len(values) == 1 else None), (frags.pop() if len(frags) == 1 and not values else None)


def parse_bottom(texts: Texts) -> dict:
    """Bottom bar: {colour: [projected stat, cap] or None}."""
    texts = list(texts)
    out = {}
    for colour, x in BOTTOM_X.items():
        stat, frag = _bottom_int(texts, x, BOTTOM_Y[0])
        cap, _ = _bottom_int(texts, x, BOTTOM_Y[1])
        if stat is None and frag is not None and cap is not None and cap >= 1000:
            # 「,474」 is at least 1,000 and at most the cap: the cap's thousands, else one thousand less
            stat = next((v for v in (cap // 1000 * 1000 + frag, (cap // 1000 - 1) * 1000 + frag) if 1000 <= v <= cap), None)
        out[colour] = [stat, cap] if stat is not None and cap is not None and stat <= cap else None
    return out


def _mean(image, rect) -> tuple | None:
    if image is None:
        return None
    import numpy as np
    x, y, w, h = rect
    patch = np.asarray(image[y:y + h, x:x + w], dtype=float)
    return tuple(float(v) for v in patch.reshape(-1, patch.shape[-1])[:, :3].mean(0)) if patch.size else None


# Button faces (H1 frames): an enabled + is white with a pink cross (mean ~217), a disabled one grey (~92-134); 習得する
# lights pink once something is queued and stays dim purple otherwise.
AFFECTION_PLUS = (1144, 200, 28, 24)
AFFECTION_CARD = (630, 183, 570, 112)         # その他 tab, 親愛度+10 card (H2d live 2026-11-06: 「習得済み」 replaces the
                                              # +/− there once bought, and the white pill reads as a lit button)
LEARN_FACE = (1120, 640, 115, 40)


def button_enabled(image, rect, threshold: float = 170.0) -> bool | None:
    mean = _mean(image, rect)
    return None if mean is None else bool(sum(mean) / 3 >= threshold)


def learn_enabled(image) -> bool | None:
    mean = _mean(image, LEARN_FACE)
    if mean is None:
        return None
    return bool(mean[0] >= 200)       # H1: lit (244,146,193), dim (146,87,116)


def read(texts: Texts, image=None) -> dict:
    texts = list(texts)
    tab = parse_tab(texts)
    out = {"tab": tab, "points": parse_points(texts), "learn": learn_enabled(image)}
    if tab == "status":
        out["rows"] = parse_rows(texts)
        out["bottom"] = parse_bottom(texts)
    if tab == "other":
        x0, y0, w, h = AFFECTION_CARD
        learned = any("習得済" in _clean(t) and x0 <= b[0] + b[2] / 2 <= x0 + w and y0 <= b[1] + b[3] / 2 <= y0 + h
                      for t, b in texts)
        out["affection_learned"] = learned
        out["affection_plus"] = False if learned else button_enabled(image, AFFECTION_PLUS)
    return out


# ---- plan ----------------------------------------------------------------------------------------------------------
def stat_step(colour: str, read: Mapping) -> str | None:
    """The next ステータス tap for one colour, or None when this colour is done.

    Fill the stat with >> while it is below its cap; raise the cap one step (+) only when the points left also pay for
    filling the stat up to the raised cap (so a cap is never bought that the stat cannot use)."""
    points = (read.get("points") or {}).get(colour)
    row = (read.get("rows") or {}).get(colour) or {}
    bottom = (read.get("bottom") or {}).get(colour)
    if not isinstance(points, int) or not bottom or not row.get("label"):
        return None
    stat, cap = bottom
    stat_cost = row.get("stat_cost") or STAT_STEP
    gap = (cap - stat) // STAT_STEP
    cap_cost = row.get("cap_cost")
    if isinstance(cap_cost, int) and points >= cap_cost + stat_cost * (gap + 1):
        return "cap_plus"
    if gap >= 1 and points >= stat_cost:
        return "stat_max"
    return None
