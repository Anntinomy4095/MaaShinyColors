"""Read-only owned Lv / limit-break (凸) reader for the in-game アイドル一覧 list. Offline only.

Builds on :mod:`agent.core.card_inventory` (same ``idol_list`` grid, DB thumbnails and rarity-frame check) and adds two
per-tile readings on a canonical 1280x720 frame:

* **Stars** (left edge of the tile, stacked from the bottom, 18 px apart, at most 4): the count is the limit-break
  count (特訓回数; the user's rule, and the サポートアイドル詳細 「特訓」 row shows the same stars).  The colour is read
  too: on every observed tile 1-3 stars are gold and 4 stars are purple/rainbow, i.e. the colour marks a completed
  4凸 card and carries no extra quantity.  A tile whose colours mix, whose stars are not one contiguous run
  from the bottom, or whose colour contradicts the count is refused.
* **Lv** (S tiles only; P cards have no level): MaaFramework OCR of the ``Lv.NN`` band, ``^Lv\\.?(\\d{1,3})$``.

Every accepted reading must also agree with the level cap of the matched card's rarity at the read limit break
(``LEVEL_CAPS``); anything below a threshold, ambiguous or contradictory is ``rejected`` with its reason.  The output is
a suggestion for ``prefs.owned_cards.details``: the resources page auto-applies readings whose ``confidence`` is at least
``AUTO_ACCEPT_CONFIDENCE`` (never over a user-set value) and asks the user about the rest;
this module never writes a profile.
"""
from __future__ import annotations

import dataclasses
import re
from typing import Any, Callable, Iterable, Mapping

from agent.core import card_inventory as ci
from agent.core.card_stats import LEVEL_CAPS, level_cap  # noqa: F401  (re-exported for callers)

# Star slots relative to the tile box (x, y): slot k (0 = top, 3 = bottom) covers
# x in [x + STAR_X, x + STAR_X + 17), y in [y + STAR_Y0 + 18k, +17).  Measured on the 2026-10-07 Lv-sorted list frame.
STAR_X, STAR_Y0, STAR_DY, STAR_SLOTS, STAR_SIZE = -3, 34, 18, 4, 17
STAR_SHIFT = range(-2, 4)          # gold stars sit ~2 px lower than purple ones; search a small window
# Star core (pixels inside the star outline on every measured purple star; a geometric shape, not a game image).
STAR_MASK = (
    ".................",
    ".................",
    ".................",
    ".................",
    "........#........",
    ".......##........",
    "......######.....",
    "...###########...",
    "...###########...",
    "....##########...",
    ".....########....",
    "......######.....",
    "......######.....",
    "......######.....",
    "......##...##....",
    ".................",
    ".................",
)
MIN_STAR_FILL = 0.85               # fraction of core pixels in one star colour
MAX_OTHER_FILL = 0.10              # ... while the other star colour stays below this
MIN_RING_CONTRAST = 0.20           # core brighter than its dark-grey outline ring (V, 0..1); art behind stars is not
# Ambiguity band: something star-coloured but not clearly a star -> the tile is refused, never counted either way.
AMBIGUOUS_FILL, AMBIGUOUS_CONTRAST = 0.75, 0.12
# Lv band (bottom-right of a tile) for OCR.
LEVEL_ROI = (40, 84, 68, 22)
LEVEL_PATTERN = r"^Lv\.?(\d{1,3})$"
MIN_OCR_SCORE = 0.90
UPSCALE = 3


def _mask():
    import numpy as np

    return np.array([[c == "#" for c in row] for row in STAR_MASK], dtype=bool)


def _ring(mask):
    """Two-pixel band around the star core (the outline), without SciPy."""
    import numpy as np

    grown = mask.copy()
    for _ in range(2):
        g = grown.copy()
        g[1:, :] |= grown[:-1, :]
        g[:-1, :] |= grown[1:, :]
        g[:, 1:] |= grown[:, :-1]
        g[:, :-1] |= grown[:, 1:]
        grown = g
    return grown & ~mask


def _star_colours(hsv):
    """(purple, gold) boolean maps of an HSV float array (h in degrees, s/v in 0..1)."""
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    purple = (h >= 170) & (h <= 330) & (s >= 0.12) & (v >= 0.70)
    gold = (h >= 30) & (h <= 65) & (s >= 0.45) & (v >= 0.75)
    return purple, gold


def _hsv(frame):
    import numpy as np

    arr = np.asarray(frame.convert("HSV"), dtype=np.float32) / 255.0
    arr[..., 0] *= 360.0
    return arr


@dataclasses.dataclass(frozen=True)
class StarReading:
    status: str                  # accepted | rejected
    count: int | None
    colour: str | None           # purple | gold | none
    slots: tuple                 # per slot (top..bottom): "purple" | "gold" | None
    fill: float                  # weakest accepted star's core fill (1.0 when no star)
    reason: str
    contrast: float = 1.0        # weakest accepted star's core-vs-outline contrast (1.0 when no star)
    empty_peak: float = 0.0      # strongest star-colour fill seen in a slot read as empty (0.0 when all four are stars)

    def to_dict(self) -> dict:
        return dataclasses.asdict(self) | {"slots": list(self.slots)}


def read_stars(hsv, box) -> StarReading:
    """Read the star column of one tile from an HSV array (``_hsv(frame)``)."""
    mask = _mask()
    ring = _ring(mask)
    n = int(mask.sum())
    x, y = box[0], box[1]
    slots, fills, contrasts, empties = [], [], [], []
    for k in range(STAR_SLOTS):
        best = ("", 0.0, 0.0, 0.0)
        for dy in STAR_SHIFT:
            for dx in (-1, 0, 1):
                x0, y0 = x + STAR_X + dx, y + STAR_Y0 + STAR_DY * k + dy
                patch = hsv[y0:y0 + STAR_SIZE, x0:x0 + STAR_SIZE]
                if patch.shape[:2] != mask.shape:
                    continue
                purple, gold = _star_colours(patch)
                p, g = float(purple[mask].sum()) / n, float(gold[mask].sum()) / n
                contrast = float(patch[..., 2][mask].mean() - patch[..., 2][ring].mean())
                for name, fill, other in (("purple", p, g), ("gold", g, p)):
                    if fill + contrast > best[1] + best[2]:       # best alignment: filled core, dark outline
                        best = (name, fill, contrast, other)
        name, fill, contrast, other = best
        if fill >= MIN_STAR_FILL and contrast >= MIN_RING_CONTRAST and other <= MAX_OTHER_FILL:
            slots.append(name)
            fills.append(fill)
            contrasts.append(contrast)
        elif fill >= MIN_STAR_FILL or (fill >= AMBIGUOUS_FILL and contrast >= AMBIGUOUS_CONTRAST):
            # star-coloured core without a clear outline (or a weak star): refuse rather than count either way
            return StarReading("rejected", None, None, tuple(slots) + (None,) * (STAR_SLOTS - len(slots)), round(fill, 3),
                               f"slot{k}_ambiguous_star")
        else:
            slots.append(None)
            empties.append(fill)
    present = [i for i, s in enumerate(slots) if s]
    count = len(present)
    if present and present != list(range(STAR_SLOTS - count, STAR_SLOTS)):
        return StarReading("rejected", None, None, tuple(slots), 0.0, "stars_not_bottom_aligned")
    colours = {s for s in slots if s}
    if len(colours) > 1:
        return StarReading("rejected", count, None, tuple(slots), round(min(fills), 3), "mixed_star_colours")
    colour = next(iter(colours), "none")
    # Observed semantics: 4 stars purple, 1-3 gold.  Anything else is a new state we have not seen.
    if (count == 4 and colour != "purple") or (0 < count < 4 and colour != "gold"):
        return StarReading("rejected", count, colour, tuple(slots), round(min(fills), 3), "unseen_count_colour")
    return StarReading("accepted", count, colour, tuple(slots), round(min(fills), 3) if fills else 1.0, "ok",
                       round(min(contrasts), 3) if contrasts else 1.0, round(max(empties), 3) if empties else 0.0)


def parse_level(text: str | None) -> int | None:
    match = re.fullmatch(LEVEL_PATTERN, (text or "").strip())
    return int(match.group(1)) if match else None


# OCR callable: (bgr_array, roi[x, y, w, h]) -> list of (text, score).
OcrFn = Callable[[Any, list], list]


def maa_ocr(model_dir) -> OcrFn:
    """MaaFramework OCR (offline replay recogniser; never sends input)."""
    from tools.replay_ocr import MaaOcrRecognizer

    recognizer = MaaOcrRecognizer(model_dir)

    def run(bgr, roi):
        field = {"name": "SC.OwnedLevels.Lv", "roi": list(roi), "expected": "^.*$", "threshold": 0.0}
        result = recognizer.recognize(field, bgr)
        if recognizer.input_attempts:
            raise RuntimeError("OCR recogniser attempted input")
        return [(item["text"], float(item["score"])) for item in result["results"]]

    return run


def _upscaled(bgr, roi, scale=UPSCALE):
    """The ROI enlarged ``scale`` times on a black 1280x720 canvas (second OCR attempt) and its new ROI."""
    import numpy as np
    from PIL import Image

    x, y, w, h = roi
    crop = Image.fromarray(np.ascontiguousarray(bgr[y:y + h, x:x + w, ::-1])).resize((w * scale, h * scale), Image.BICUBIC)
    canvas = np.zeros((720, 1280, 3), dtype=np.uint8)
    canvas[100:100 + h * scale, 100:100 + w * scale] = np.asarray(crop)[:, :, ::-1]
    return canvas, [90, 90, w * scale + 20, h * scale + 20]


def _one_read(ocr: OcrFn, bgr, roi) -> tuple[int | None, float, str]:
    results = ocr(bgr, roi)
    if len(results) != 1:
        return None, 0.0, "no_text" if not results else "ambiguous_text"
    text, score = results[0]
    level = parse_level(text)
    if level is None:
        return None, float(score), f"unexpected_text:{text}"
    if score < MIN_OCR_SCORE:
        return None, float(score), "low_ocr_score"
    return level, float(score), "ok"


def read_level(ocr: OcrFn, bgr, box) -> tuple[int | None, float, str]:
    """OCR of the tile's ``Lv.NN`` band at native size; if that is not a clean ``Lv.NN`` read, one more attempt on the
    same pixels enlarged 3x.  Only a clean read is accepted; the first failure reason is kept otherwise."""
    x, y = box[0], box[1]
    roi = [x + LEVEL_ROI[0], y + LEVEL_ROI[1], LEVEL_ROI[2], LEVEL_ROI[3]]
    level, score, reason = _one_read(ocr, bgr, roi)
    if level is not None:
        return level, score, reason
    canvas, big_roi = _upscaled(bgr, roi)
    level2, score2, _ = _one_read(ocr, canvas, big_roi)
    digits = re.findall(r"\d+", reason.split(":", 1)[1]) if reason.startswith("unexpected_text:") else []
    if level2 is not None and (not digits or digits == [str(level2)]):   # the native digits must agree
        return level2, score2, "ok_upscaled"
    return None, score, reason


# ------------------------------------------------------------------------------------------------ confidence
# Every accepted reading already cleared hard thresholds; ``confidence`` says how far clear of them it is.  Each signal
# adds a risk that is largest exactly at its acceptance threshold and falls linearly to 0 at ``threshold + span``
# (spans ~ the 25th percentile of accepted tiles in the 2026-10-06/07/08 evidence): confidence = 1 - sum(risk).
# Calibrated on native frames incl. a leave-one-out test that removes the true card from
# the reference set: the wrong matches that would still be accepted land below AUTO_ACCEPT_CONFIDENCE.
#            signal            threshold  span   risk at threshold
MATCH_RISK = (("score",          0.80,     0.07,  0.10),
              ("margin",         0.06,     0.08,  0.12),
              ("verify",         0.60,     0.08,  0.08),
              ("edge",           0.30,     0.08,  0.06),
              ("variant_margin", 0.02,     0.12,  0.10))
STAR_RISK = (("fill", MIN_STAR_FILL, 0.10, 0.06), ("contrast", MIN_RING_CONTRAST, 0.06, 0.06))
EMPTY_RISK = (AMBIGUOUS_FILL, 0.40, 0.05)       # an empty slot whose star-colour fill nears the ambiguity band
OCR_RISK = (MIN_OCR_SCORE, 0.08, 0.08)
UPSCALED_RISK = 0.03                            # Lv only read cleanly on the 3x retry
UNKNOWN_TIER_RISK = 0.05                        # rarity frame colour unclear (no frame agreement)
AUTO_ACCEPT_CONFIDENCE = 0.70                   # UI: >= this is applied without per-card confirmation (resource-cards.js)


def _ramp(value, threshold, span, risk):
    if value is None:
        return risk
    return risk * max(0.0, min(1.0, 1.0 - (float(value) - threshold) / span))


def match_confidence(signals: Mapping[str, Any], tier: Mapping[str, Any] | None = None) -> float:
    """Confidence of a card-thumbnail match alone (also used for ownership-only scans)."""
    risk = sum(_ramp(signals.get(name), thr, span, r) for name, thr, span, r in MATCH_RISK)
    if tier is not None and (tier.get("observed") is None or tier.get("observed") != tier.get("expected")):
        risk += UNKNOWN_TIER_RISK
    return round(max(0.0, 1.0 - risk), 3)


def reading_confidence(record: Mapping[str, Any]) -> float:
    """Confidence of one accepted tile reading: card match + rarity frame + stars + Lv OCR (see the table above)."""
    risk = 1.0 - match_confidence(record.get("match_signals") or {"verify": record.get("verify")}, record.get("tier"))
    stars = record.get("stars") or {}
    if (record.get("limit_break") or 0) > 0:
        risk += sum(_ramp(stars.get(name, 1.0), thr, span, r) for name, thr, span, r in STAR_RISK)
    if (record.get("limit_break") or 0) < STAR_SLOTS:
        thr, span, r = EMPTY_RISK
        risk += r * max(0.0, min(1.0, 1.0 - (thr - float(stars.get("empty_peak", 0.0))) / span))
    if record.get("level") is not None:
        risk += _ramp(record.get("ocr_score"), *OCR_RISK)
        if record.get("level_reason") == "ok_upscaled":
            risk += UPSCALED_RISK
    return round(max(0.0, 1.0 - risk), 3)


def read_frame(frame, index: "ci.CardIndex", ocr: OcrFn | None, *, frame_name: str = "",
               layout: str = "idol_list") -> list[dict[str, Any]]:
    """Per-tile records for one 1280x720 PIL frame: card match, stars, Lv and the combined decision."""
    import numpy as np

    frame = ci._check_frame(frame)
    hsv = _hsv(frame)
    bgr = np.ascontiguousarray(np.asarray(frame)[:, :, ::-1])
    scan = ci.recognise(frame, index, layout)
    records = []
    for tile in scan["tiles"]:
        box = tuple(tile["box"])
        record: dict[str, Any] = {"frame": frame_name, "slot": tile["slot"], "box": list(box),
                                  "card_id": tile["card_id"], "match": tile["status"], "match_reason": tile["reason"],
                                  "verify": tile["verify"],
                                  "match_signals": {k: tile[k] for k in ("score", "margin", "verify", "edge", "variant_margin")}}
        stars = read_stars(hsv, box)
        record["stars"] = stars.to_dict()
        card_id = tile["card_id"]
        reasons = []
        if tile["status"] != "accepted":
            reasons.append(f"tile:{tile['reason']}")
        if stars.status != "accepted":
            reasons.append(f"stars:{stars.reason}")
        level, ocr_score, level_reason = None, None, "not_read"
        kind = card_id[0] if card_id else None
        if kind == "2" and ocr is not None:
            level, ocr_score, level_reason = read_level(ocr, bgr, box)
            if level is None:
                reasons.append(f"level:{level_reason}")
        elif kind == "2":
            reasons.append("level:no_ocr")
        record.update(level=level, ocr_score=ocr_score, level_reason=level_reason)
        rarity = index.meta.get(card_id, {}).get("rarity") if card_id else None
        record["rarity"] = rarity
        if card_id:
            record["tier"] = {"expected": ci.FRAME_TIERS.get(str(rarity or "")), "observed": ci.frame_tier(frame, box)}
        if not reasons and kind == "2":
            cap = level_cap(rarity, stars.count)
            if cap is None:
                reasons.append(f"cap:unknown_for_{rarity}")
            elif not 1 <= level <= cap:
                reasons.append(f"cap:level_{level}_exceeds_LB{stars.count}_cap_{cap}")
            record["cap"] = cap
        record["status"] = "accepted" if not reasons else "rejected"
        record["reasons"] = reasons
        if not reasons:
            record["limit_break"] = stars.count
            record["confidence"] = reading_confidence(record)
        records.append(record)
    return records


def _frame_date(name: str) -> str:
    """YYYYMMDD a frame path names (…-20261006-… / …20261008-…/…), else "" (same rule as team-levels-import.js)."""
    match = re.search(r"(?:^|\D)(20\d{6})(?!\d)", name or "")
    return match.group(1) if match else ""


def merge_readings(records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Combine tile records of many frames: ``{cards: {card_id: {level, limit_break, confidence, frame, latest_frame,
    frames, frames_distinct}}, conflicts: {card_id: [...]}, rejected: n}``.  ``confidence`` is the best tile's and is **not** raised by
    agreeing frames: a wrong same-idol variant is read identically in every frame (leave-one-out test), so
    agreement only rules out value conflicts, not identity errors.  A card read with different values in two frames is a conflict and
    is withheld (the list may have changed between captures; re-capture instead of guessing)."""
    cards: dict[str, dict[str, Any]] = {}
    seen: dict[str, set] = {}
    frames_of: dict[str, set] = {}
    rejected = 0
    for rec in records:
        if rec.get("status") != "accepted":
            rejected += 1
            continue
        cid = rec["card_id"]
        value = (rec.get("level"), rec["limit_break"])
        seen.setdefault(cid, set()).add(value)
        frames_of.setdefault(cid, set()).add(rec.get("frame"))
        cur = cards.get(cid)
        if cur is None or rec["confidence"] > cur["confidence"]:
            cards[cid] = {"level": rec.get("level"), "limit_break": rec["limit_break"], "confidence": rec["confidence"],
                          "frame": rec["frame"], "slot": rec["slot"], "star_colour": rec["stars"]["colour"],
                          "frames": (cur or {}).get("frames", 0)}
        cards[cid]["frames"] = cards[cid].get("frames", 0) + 1
    conflicts = {cid: sorted([list(v) for v in values], key=str) for cid, values in seen.items() if len(values) > 1}
    for cid in conflicts:
        cards.pop(cid, None)
    for cid, card in cards.items():
        names = frames_of.get(cid, set())
        card["frames_distinct"] = len(names)
        # newest recording that read this same value (the UI dates the reading by it; older -> 旧录制)
        dated = sorted((_frame_date(n), n) for n in names if n)
        card["latest_frame"] = dated[-1][1] if dated and dated[-1][0] else card["frame"]
    return {"cards": dict(sorted(cards.items())), "conflicts": conflicts, "rejected": rejected}


def details_for_import(merged: Mapping[str, Any]) -> dict[str, dict[str, int]]:
    """``prefs.owned_cards.details``-shaped suggestions ({id: {level?, limit_break}}); P cards carry no level."""
    out = {}
    for cid, card in merged.get("cards", {}).items():
        entry = {"limit_break": int(card["limit_break"])}
        if card.get("level") is not None:
            entry["level"] = int(card["level"])
        out[cid] = entry
    return out
