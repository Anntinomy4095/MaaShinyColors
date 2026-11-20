"""Grade Fes live screen reading (B5d): fixed boxes on the canonical 1280x720 canvas and pure parsers.

Boxes were measured on the native manual live capture 2026-10-03
(dev_assets/private/grade-live-manual-20261003-frames, 396 frames). Text is read with the
same det+rec OCR as the other tasks (``task_custom.Vision.text``: every line ≥ 0.95, inside the box); the card
attribute comes from the hue of its round appeal icon (only Vo. was sampled: Da./Vi. hue bands are assumptions).
``read_turn`` returns a ``fes_live_policy.TurnState`` or the reasons it could not.
"""
from __future__ import annotations

import colorsys
import re

from agent.core import fes_live_policy as policy

TURN_BOX = [640, 8, 130, 44]                       # 「1 / 7」 (OCR reads the slash as "/" or "1": "117")
# B5i: the TURN digits (dark outline, cyan fill) sit on a white / pink / grey / flashing banner where det+rec often finds
# nothing (manual 292–295, auto run 2026-10-03 frames 017/026). Fixed crop of the digits only, binarised three ways.
TURN_DIGITS = [648, 10, 122, 40]
TURN_PATTERN = r"[1-7][/1lI|]?7"
TURN_VARIANTS = ("fill", "outline", "otsu")
JUDGE_TEXT = {"Vo": [36, 116, 90, 34], "Da": [36, 283, 90, 34], "Vi": [36, 449, 90, 34]}   # 「0%」/「100%」/「審査終了」
MULTIPLIER_TEXT = {"Vo": [0, 47, 70, 26], "Da": [0, 215, 70, 26], "Vi": [0, 381, 70, 26]}  # 「★2倍」「★1.5倍」 or nothing
MEMORY_BOX = [1170, 650, 95, 48]                   # 「10%」 inside the memory appeal gauge
CARD_X = (300, 530, 760)                           # left edge of each card (portrait + appeal icon), 230 px apart
CARD_LABEL_H = (596, 36)                           # 「LINK✦」 / 「PLUS✦」 above the card
ICON_BOX = (140, 630, 75, 65)                      # appeal icon relative to the card x: dx, y, w, h
SWAP_HINT_X = tuple(x + 40 for x in CARD_X)        # 「スライドで交換」 under the selected card (measured on slot 3: x 800)
REPORT_BOXES = {"rank": [240, 138, 70, 36], "stars": [250, 175, 50, 28], "score": [370, 330, 185, 42],
                "money": [460, 398, 95, 36], "held": [400, 444, 155, 32]}  # held: 所持マニー (counts up last)
REHEARSAL_REPORT_BOXES = {"rank": [240, 223, 70, 38], "stars": [245, 267, 60, 36],
                          "score": [352, 430, 205, 49], "money": [453, 503, 102, 45],
                          "held": [398, 568, 162, 37]}
# B5k: second LIVE REPORT page (ranking list, auto run 2026-10-03 11:09, grade-live-auto-20261003-frames/report-ranking.png).
# Three rows visible, 155 px apart; the own row has a pink frame (right edge x 1212..1215). The 「1位」 laurel glyph is
# not read reliably by OCR (stylised 1), so the rank is the row position — trusted only while every readable 「N位」
# badge of the visible rows agrees with its position (the list is not scrolled).
RANKING_ROWS, RANKING_ROW_DY = 3, 155
RANKING_BADGE = [80, 140, 130, 100]   # 「N位」 laurel of row 0
RANKING_PT = [690, 198, 140, 46]      # 「20,751」 (pt label left out) of row 0
RANKING_EDGE = [1212, 160, 4, 50]     # right edge of the row frame: pink on the own row, light grey otherwise
RANKING_OWN_SHARE = 0.5
# Hue bands (degrees) of the appeal icon; Vo measured (median 331), Da measured on the dual Vo./Da. icon (200–220,
# auto run 2026-10-03), Vi not yet sampled.
HUE_BANDS = (("Vo", 300, 350), ("Da", 190, 250), ("Vi", 20, 65))
MIN_ICON_PIXELS = 120
# A dual-attribute icon (e.g. 「Vo./Da.」, Dance担当 card) shows a second band with >= this share and pixel count.
DUAL_SHARE, DUAL_MIN_PIXELS = 0.2, 60


# 2026-10-22: the official Grade live of the new season shows TURN x/6 (earlier seasons x/7; rehearsal x/10). The
# total is read from the screen among these candidates and then kept for the rest of the live.
GRADE_TURN_TOTALS = (6, 7)


def turn_pattern(max_turns, capture=True):
    """Regex of 「turn/total」 OCR text for one total (2–10); the slash may be read as 1/l/I/| or dropped."""
    group = "(" if capture else "(?:"
    if max_turns == 10:
        return group + r"10|[1-9])[/1lI|]?10"
    if isinstance(max_turns, int) and 2 <= max_turns <= 9:
        return group + rf"[1-{max_turns}])[/1lI|]?{max_turns}"
    return None


def parse_turn(text, max_turns=7):
    """'1/7', '117' (slash read as 1), '1l7' -> 1; None otherwise."""
    if not isinstance(text, str):
        return None
    pattern = turn_pattern(max_turns)
    match = re.fullmatch(pattern, text) if pattern else None
    return int(match.group(1)) if match else None


def parse_percent(text):
    """'0%' -> 0, '69.2%' -> 69.2 (judge interest has one decimal once partial), '100%' -> 100; None otherwise."""
    if not isinstance(text, str):
        return None
    match = re.fullmatch(r"([0-9]{1,3})(?:\.([0-9]))?%", text)
    if not match:
        return None
    return int(match.group(1)) if match.group(2) is None else float(f"{match.group(1)}.{match.group(2)}")


def turn_pixels(image, variant):
    """Black-on-white pixels of the TURN digits for only_rec OCR (None on a blank crop)."""
    import numpy as np
    from agent.core import task_common as tc
    x, y, w, h = TURN_DIGITS
    patch = image[y:y + h, x:x + w].astype(int)  # BGR
    b, g, r = patch[:, :, 0], patch[:, :, 1], patch[:, :, 2]
    dark = patch.max(axis=2) < 120                                 # the digits' dark outline
    if variant == "fill":
        ink = dark | ((b - r > 40) & (g - r > 20))                 # outline + cyan fill (backgrounds have r >= b)
    elif variant == "outline":
        ink = dark
    else:
        gray = patch.mean(axis=2)
        if float(gray.max() - gray.min()) < 20:
            return None
        norm = (gray - gray.min()) * 255 / (gray.max() - gray.min())
        ink = norm <= tc.otsu_cutoff(norm)
    if ink.sum() < 30:
        return None
    binary = np.where(ink, 0, 255).astype(np.uint8)
    padded = np.pad(tc.resize_numeric(binary), ((tc.OCR_PADDING,) * 2,) * 2, constant_values=255)
    return np.ascontiguousarray(np.repeat(padded[:, :, None], 3, axis=2).astype(np.uint8))


def read_turn_number(vision, image, max_turns=7):
    """TURN from det+rec on the box and only_rec on the binarised digits: every reading that parses must agree
    (one reading suffices). Returns (turn | None, readings)."""
    readings = {"det": vision.text(image, TURN_BOX)}
    rec = getattr(vision, "rec", None)
    for variant in TURN_VARIANTS if rec is not None else ():
        pixels = turn_pixels(image, variant)
        readings[variant] = None if pixels is None else rec(pixels, TURN_PATTERN if max_turns == 7 else turn_pattern(max_turns, capture=False))
    values = {parse_turn(t, max_turns) for t in readings.values()} - {None}
    return (values.pop() if len(values) == 1 else None), readings


def read_turn_total(vision, image, totals):
    """TURN when the total is one of several candidates: (turn | None, total | None, readings). Exactly one candidate
    total must give a consistent turn; none or more than one -> unreadable."""
    found, readings = [], {}
    for total in totals:
        turn, raw = read_turn_number(vision, image, total)
        readings[str(total)] = raw
        if turn is not None:
            found.append((turn, total))
    return (found[0] + (readings,)) if len(found) == 1 else (None, None, readings)


def is_finished(text):
    return isinstance(text, str) and text in ("審査終了", "審查終了")


def parse_multiplier(text):
    """'★2倍' -> 2.0, '★1.5倍' -> 1.5, None / other -> None."""
    if not isinstance(text, str):
        return None
    match = re.fullmatch(r"[★☆*]?([0-9](?:\.[0-9])?)倍", text)
    return float(match.group(1)) if match else None


def ranks_from_multipliers(multipliers):
    """{attr: 2.0 | 1.5 | None} -> {attr: (rank, multiplier)} when it is exactly one ★2倍, one ★1.5倍 and one
    unmarked judge (the observed trend layout); None otherwise (incomplete evidence)."""
    values = sorted((m or 1.0) for m in multipliers.values())
    if values != [1.0, 1.5, 2.0]:
        return None
    rank = {2.0: 1, 1.5: 2, 1.0: 3}
    return {a: (rank[m or 1.0], m or 1.0) for a, m in multipliers.items()}


def icon_attribute(image, slot):
    """Appeal attribute of a card from the hue of its icon (BGR image); None when unclassified."""
    dx, y, w, h = ICON_BOX
    x = CARD_X[slot - 1] + dx
    patch = image[y:y + h, x:x + w]
    hues = []
    for b, g, r in patch.reshape(-1, 3)[::5]:
        hue, sat, val = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
        if sat > 0.45 and val > 0.5:
            hues.append(hue * 360.0)
    if len(hues) < MIN_ICON_PIXELS:
        return None, len(hues), None
    hues.sort()
    median = hues[len(hues) // 2]
    counts = {name: sum(low <= h <= high for h in hues) for name, low, high in HUE_BANDS}
    for name, low, high in HUE_BANDS:
        if low <= median <= high:
            second = [n for n in counts if n != name and counts[n] >= DUAL_MIN_PIXELS
                      and counts[n] >= DUAL_SHARE * len(hues)]
            if len(second) == 1:  # 「Vo./Da.」 style icon: both attributes, in fixed Vo/Da/Vi order
                name = "/".join(a for a in policy.ATTRIBUTES if a in (name, second[0]))
            return name, len(hues), round(median)
    return None, len(hues), round(median)


def card_label(text):
    text = text or ""
    return "LINK" in text.upper(), "PLUS" in text.upper()


def swap_hint_slot(box_x):
    """Slot under which 「スライドで交換」 was found (the selected card), None when ambiguous."""
    if box_x is None:
        return None
    distances = [abs(box_x - x) for x in SWAP_HINT_X]
    best = min(range(3), key=lambda i: distances[i])
    return best + 1 if distances[best] <= 40 else None


# 2026-10-24 思い出アピール film (QTE): after the judge a strip of Good (pink) / Bad (blue-grey) memories scrolls right
# to left (~1500 px/s, tile pitch ~260 px, ~2.5 s) under a fixed cyan frame (x 508..771, y 262..457); a tap stops it,
# no tap ends in 「miss」 (2026-10-22 video maa-20261022T112112372Z, frames 142.9-145.6 s; native frame-049).
FILM_EDGES = {"top": ((515, 258, 250, 18), 1800), "bottom": ((515, 444, 250, 18), 1800),
              "left": ((504, 275, 20, 170), 1200), "right": ((756, 275, 20, 170), 1200)}
FILM_TILES = {"left": 20, "center": 530, "right1": 790, "right2": 1050}  # tile interiors x .. x+220, y 290..400
FILM_MIN_LUMA = 150   # the strip under 「Ready!!」 and the strip's end are darker (51 / 88-125 on the samples)


def film_tile(image, x):
    """good | bad | edge for the tile interior at x (BGR image)."""
    import numpy as np
    patch = image[290:400, x:x + 220].reshape(-1, 3).astype(int)
    b, g, r = patch[:, 0], patch[:, 1], patch[:, 2]
    pink = float(((r > 190) & (r - g > 45) & (b > g)).mean())
    blue = float(((b > r + 8) & (b > 120)).mean())
    if pink >= 0.4 and blue < 0.1:
        return "good"
    if blue >= 0.5 and pink < 0.1:
        return "bad"
    return "edge"


def read_film(image):
    """The memory film with a lit tile under its cyan frame: {tile position: good|bad|edge}; None otherwise."""
    import numpy as np
    if image is None or getattr(image, "shape", None) is None or image.shape[:2] != (720, 1280):
        return None
    for (x, y, w, h), minimum in FILM_EDGES.values():
        patch = image[y:y + h, x:x + w]
        cyan = (patch[:, :, 2] < 140) & (patch[:, :, 1] > 190) & (patch[:, :, 0] > 200)
        if int(np.count_nonzero(cyan)) < minimum:
            return None
    if float(image[290:400, 530:750].mean()) < FILM_MIN_LUMA:
        return None
    return {name: film_tile(image, x) for name, x in FILM_TILES.items()}


def read_judges(texts, multipliers):
    """Judges from the OCR texts; (judges, reasons)."""
    ranks = ranks_from_multipliers({a: parse_multiplier(multipliers.get(a)) for a in policy.ATTRIBUTES})
    reasons = [] if ranks else ["trend_multipliers_unreadable"]
    judges = []
    for attribute in policy.ATTRIBUTES:
        text = texts.get(attribute)
        rank, mult = ranks[attribute] if ranks else (0, 0.0)
        if is_finished(text):
            judges.append(policy.Judge(attribute, False, None, rank, mult))
            continue
        value = parse_percent(text)
        if value is None or value >= 100:
            reasons.append(f"judge_{attribute}_unreadable")
        judges.append(policy.Judge(attribute, True, value, rank, mult))
    return tuple(judges), reasons


def read_turn(vision, image, max_turns=7):
    """Waiting-stage state from one frame: (TurnState | None, raw readings, reasons). ``max_turns`` is the known total
    or a tuple of candidate totals (the state then carries the total read from the screen)."""
    if isinstance(max_turns, tuple):
        turn, total, raw = read_turn_total(vision, image, max_turns)
        max_turns = total if total is not None else max(max_turns)
    else:
        turn, raw = read_turn_number(vision, image, max_turns)
    raw = {"turn": raw}
    raw["judges"] = {a: vision.text(image, box) for a, box in JUDGE_TEXT.items()}
    raw["multipliers"] = {a: vision.text(image, box) for a, box in MULTIPLIER_TEXT.items()}
    raw["memory"] = vision.text(image, MEMORY_BOX)
    cards, raw["cards"] = [], []
    for slot in (1, 2, 3):
        x = CARD_X[slot - 1]
        label = vision.text(image, [x, CARD_LABEL_H[0], 110, CARD_LABEL_H[1]])
        appeal, pixels, hue = icon_attribute(image, slot)
        link, plus = card_label(label)
        cards.append(policy.Card(slot, appeal, link, plus))
        raw["cards"].append({"slot": slot, "label": label, "appeal": appeal, "icon_pixels": pixels, "hue": hue})
    reasons = [] if turn else ["turn_unreadable"]
    judges, judge_reasons = read_judges(raw["judges"], raw["multipliers"])
    reasons += judge_reasons
    if not reasons:
        state = policy.TurnState(turn, judges, tuple(cards), parse_percent(raw["memory"]), max_turns=max_turns)
        reasons = policy.validate(state)
        if not reasons:
            return state, raw, []
    return None, raw, reasons


def read_predictions(vision, image):
    """Target stage: predicted interest per judge ({attr: % or None}; finished judges -> None)."""
    out = {}
    for attribute, box in JUDGE_TEXT.items():
        text = vision.text(image, box)
        out[attribute] = None if is_finished(text) else parse_percent(text)
    return out


def parse_int(text):
    if not isinstance(text, str):
        return None
    match = re.fullmatch(r"([0-9]{1,3}(?:,[0-9]{3})*|[0-9]+)(?:pt|位)?", text)
    return int(match.group(1).replace(",", "")) if match else None


def read_report(vision, image, mode="grade"):
    """LIVE REPORT: {rank, stars, score, money, held} (ints or None) and the raw texts."""
    boxes = REHEARSAL_REPORT_BOXES if mode == "rehearsal" else REPORT_BOXES
    raw = {name: vision.text(image, box) for name, box in boxes.items()}
    return {name: parse_int(text) for name, text in raw.items()}, raw


def _row_box(box, row):
    x, y, w, h = box
    return [x, y + RANKING_ROW_DY * row, w, h]


def ranking_own_rows(image):
    """Indexes of the visible ranking rows whose frame edge is pink (the own row)."""
    rows = []
    for row in range(RANKING_ROWS):
        x, y, w, h = _row_box(RANKING_EDGE, row)
        pixels = image[y:y + h, x:x + w].astype(int)
        if pixels.size == 0:
            continue
        red, green, blue = pixels[..., 2], pixels[..., 1], pixels[..., 0]  # BGR (Maa/OpenCV)
        if (((red - green) > 60) & (blue > 150)).mean() >= RANKING_OWN_SHARE:
            rows.append(row)
    return rows


def read_ranking(vision, image):
    """Ranking page: {rank, pt, row} of the own (pink-framed) row (ints or None) and the raw texts.

    ``rank`` is the row position + 1 when exactly one row is the own row and no readable badge contradicts the
    positions; otherwise None (record only — the OK does not depend on it)."""
    own = ranking_own_rows(image)
    badges = [vision.text(image, _row_box(RANKING_BADGE, row)) for row in range(RANKING_ROWS)]
    ranks = [parse_int(text) for text in badges]
    ordered = all(rank is None or rank == row + 1 for row, rank in enumerate(ranks))
    raw = {"own_rows": own, "badges": badges}
    if len(own) != 1:
        return {"rank": None, "pt": None, "row": None}, raw
    row = own[0]
    raw["pt"] = vision.text(image, _row_box(RANKING_PT, row))
    return {"rank": row + 1 if ordered else None, "pt": parse_int(raw["pt"]), "row": row}, raw
