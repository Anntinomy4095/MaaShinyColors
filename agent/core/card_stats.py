"""Support-card stats at an arbitrary owned level (pure, offline).

Model (two linear segments, values truncated toward zero):

* segment 1, ``1 <= Lv <= cap0`` (cap0 = the 0凸 level cap):  ``floor(L1 + (Lcap0 - L1) * (Lv - 1) / (cap0 - 1))``
* segment 2, ``cap0 <= Lv <= top`` (top = highest known row, normally the 4凸 cap):
  ``floor(Lcap0 + (Ltop - Lcap0) * (Lv - cap0) / (top - cap0))``

The limit break itself adds nothing; it only raises the level cap (``LEVEL_CAPS``).  Foil (フォイル化) bonuses are
separate and not included (the detail page shows them as their own +N row).

Evidence:

* Segment 2: every intermediate Wiki row (Lv65/70/75 of SSR, 55/60/65 of SR, 75/80/85 of UR) of the 30 merged
  candidate cards: 356 of 360 values equal the formula from the cap0 and top rows; the 4 misses sit on 2 cards whose
  other rows prove a Wiki typo (one Lv60 Me cell, one Lv65 Me cell).  -> ``VERIFIED_WIKI_ROWS``.
* Segment 1: in-game training results of UR【くっつく、くっつかない】芹沢あさひ 2050170010, Lv1->2 and Lv3->4
  (Lv1-4 = 4 rows x 4 stats).  With its Lv70 row taken from the identical-profile UR【HoP PoP らびっつ】(Lv1 and
  Lv90 match it attribute for attribute), all 12 derived values match with floor, rounding would miss 2.  One card
  only -> ``IN_GAME_ONE_CARD``.  Also consistent: Lv1 = 0.4 x Lcap0 on 113/120 Wiki values (two event SSRs differ).
* Max row: ShinyColorsDB ``cardStatus`` = Wiki top row = the in-game detail page (Lv 80/80, foil +0) for
  【お揃いスナップ】櫻木真乃 2040010020 (206/206/188/150).

Nothing here is a guess presented as data: a value is ``known`` (a stored row), ``derived`` (the formula between two
stored rows, with the evidence label above), ``estimated`` (only via ``estimate_from_max``, never used by default)
or ``unknown``.
"""
from __future__ import annotations

import dataclasses
import math
from typing import Any, Mapping

STATS = ("Vo", "Da", "Vi", "Me")
# Level cap per rarity at limit break 0..4.  SSR/SR/UR: Wiki max_levels of the merged candidates and the in-game
# detail page; R: inferred from the 2026-10-07 list (R tiles reach Lv.60 only with 4 stars).  N: unknown.
LEVEL_CAPS = {"UR": (70, 75, 80, 85, 90), "SSR": (60, 65, 70, 75, 80), "SR": (50, 55, 60, 65, 70),
              "R": (40, 45, 50, 55, 60)}
LEVEL_CAP_EVIDENCE = {"UR": "wiki", "SSR": "wiki+in-game", "SR": "wiki", "R": "inferred"}
VERIFIED_CAP_RARITIES = frozenset({"UR", "SSR", "SR"})
SEGMENT_EVIDENCE = {1: "IN_GAME_ONE_CARD (UR 2050170010 Lv1-4, 12/12 values, floor)",
                    2: "VERIFIED_WIKI_ROWS (356/360 intermediate Wiki values; 4 misses = 2 Wiki typos)"}
# ratio ranges seen on the 30 Wiki cards: Lv1 / Lcap0 and Ltop / Lcap0 (only for estimate_from_max)
LV1_RATIO = 0.4
TOP_RATIO_RANGE = (1.25, 1.305)
_EPS = 1e-9


@dataclasses.dataclass(frozen=True)
class StatValue:
    level: int
    stats: dict | None           # {"Vo": int, ...} (low end for estimates)
    status: str                  # known | derived | estimated | unknown
    method: str
    anchors: tuple = ()
    evidence: str = ""
    high: dict | None = None     # estimate upper bound

    def to_dict(self) -> dict:
        return dataclasses.asdict(self) | {"anchors": list(self.anchors)}


def level_cap(rarity: str | None, limit_break: int | None) -> int | None:
    caps = LEVEL_CAPS.get(str(rarity or "").replace("S_", "").replace("P_", ""))
    if caps is None or limit_break is None or not 0 <= int(limit_break) <= 4:
        return None
    return caps[int(limit_break)]


def resolve_level_caps(card: Mapping[str, Any]) -> tuple[dict[int, int], dict[str, Any] | None]:
    """Explicit card caps first; otherwise the already verified S-card rarity rule, with its evidence.

    This resolves level *limits*, never stat rows or Live skills. R's inferred rule and N are deliberately excluded.
    An unresolved disagreement is not an absent field and cannot be replaced by a general rule.
    """
    caps = {int(lb): int(lv) for lb, lv in (card.get("max_levels") or {}).items()}
    if caps or card.get("kind") != "S":
        return caps, None
    if "unresolved:max_levels" in (card.get("unknown_fields") or ()):
        return {}, None
    if any(item.get("field") == "max_levels" and item.get("critical")
           and item.get("status") != "non_blocking" for item in (card.get("merge") or {}).get("unresolved", ())):
        return {}, None
    rarity = str(card.get("rarity") or "").removeprefix("S_")
    if rarity not in VERIFIED_CAP_RARITIES:
        return {}, None
    return dict(enumerate(LEVEL_CAPS[rarity])), {
        "source": "verified_rarity_rule", "status": "DERIVED_RULE", "rule_id": "support-level-caps-20261007",
        "rarity": rarity, "locator": f"card_stats.LEVEL_CAPS.{rarity}",
        "evidence": LEVEL_CAP_EVIDENCE[rarity],
    }


def _rows(levels: Mapping[Any, Mapping[str, Any]] | None) -> dict[int, dict[str, float]]:
    out = {}
    for key, row in (levels or {}).items():
        if row and all(isinstance(row.get(s), (int, float)) for s in STATS):
            out[int(key)] = {s: float(row[s]) for s in STATS}
    return out


def interpolate(lo: int, lo_row: Mapping[str, float], hi: int, hi_row: Mapping[str, float], level: int) -> dict[str, int]:
    """``floor`` of the straight line through (lo, lo_row) and (hi, hi_row) at ``level`` (lo <= level <= hi)."""
    if not lo <= level <= hi or hi <= lo:
        raise ValueError(f"level {level} outside [{lo}, {hi}]")
    t = (level - lo) / (hi - lo)
    return {s: int(math.floor(lo_row[s] + (hi_row[s] - lo_row[s]) * t + _EPS)) for s in STATS}


def _cap0(rarity: str | None, max_levels: Mapping[Any, Any] | None) -> int | None:
    caps = {int(k): int(v) for k, v in (max_levels or {}).items()}
    if 0 in caps:
        return caps[0]
    return level_cap(rarity, 0)


def stats_at_level(levels: Mapping[Any, Mapping[str, Any]] | None, level: int, *, rarity: str | None = None,
                   max_levels: Mapping[Any, Any] | None = None) -> StatValue:
    """Stats of one S card at ``level`` from its stored rows (``CardData.levels``).

    A stored row is returned as ``known``; otherwise the segment's two anchors must both be stored (Lv1 and cap0 for
    segment 1; cap0 and the highest stored row for segment 2), else ``unknown``."""
    level = int(level)
    rows = _rows(levels)
    if level in rows:
        return StatValue(level, {s: int(v) for s, v in rows[level].items()}, "known", "stored_row", (level,))
    cap0 = _cap0(rarity, max_levels)
    if cap0 is None or level < 1:
        return StatValue(level, None, "unknown", "no_level_cap")
    if level <= cap0:
        if 1 in rows and cap0 in rows:
            return StatValue(level, interpolate(1, rows[1], cap0, rows[cap0], level), "derived", "linear_floor_segment1",
                             (1, cap0), SEGMENT_EVIDENCE[1])
        return StatValue(level, None, "unknown", "segment1_anchor_missing", tuple(k for k in (1, cap0) if k in rows))
    top = max(rows) if rows else None
    if cap0 in rows and top is not None and top >= level:
        return StatValue(level, interpolate(cap0, rows[cap0], top, rows[top], level), "derived", "linear_floor_segment2",
                         (cap0, top), SEGMENT_EVIDENCE[2])
    return StatValue(level, None, "unknown", "segment2_anchor_missing", tuple(k for k in (cap0, top) if k in rows))


def formula_mismatches(levels: Mapping[Any, Mapping[str, Any]] | None, *, rarity: str | None = None,
                       max_levels: Mapping[Any, Any] | None = None) -> list[dict[str, Any]]:
    """Stored intermediate rows that disagree with the formula from their segment anchors (evidence of a source typo
    or of a card that does not follow the model).  Values are never changed here."""
    rows = _rows(levels)
    cap0 = _cap0(rarity, max_levels)
    if cap0 is None or cap0 not in rows:
        return []
    top = max(rows)
    out = []
    for lv in sorted(rows):
        if lv in (1, cap0, top):
            continue
        if lv < cap0 and 1 in rows:
            pred = interpolate(1, rows[1], cap0, rows[cap0], lv)
        elif cap0 < lv < top:
            pred = interpolate(cap0, rows[cap0], top, rows[top], lv)
        else:
            continue
        for s in STATS:
            if int(rows[lv][s]) != pred[s]:
                out.append({"field": f"levels.{lv}.{s}", "stored": rows[lv][s], "formula": pred[s]})
    return out


def estimate_from_max(max_level: int, max_row: Mapping[str, Any], level: int, *, rarity: str | None) -> StatValue:
    """Rough range when only the top row (ShinyColorsDB ``cardStatus``) is known: cap0 = top / [1.25, 1.305], Lv1 =
    0.4 x cap0.  The ratios vary per card (release era), so this is ``estimated`` and never fed to the model unless a
    caller explicitly opts in."""
    level = int(level)
    cap0 = level_cap(rarity, 0)
    if cap0 is None or not all(isinstance(max_row.get(s), (int, float)) for s in STATS) or not 1 <= level <= max_level:
        return StatValue(level, None, "unknown", "estimate_inputs_missing")
    bounds = []
    for ratio in TOP_RATIO_RANGE[::-1]:      # larger top ratio -> smaller cap0 -> lower bound
        c0 = {s: float(max_row[s]) / ratio for s in STATS}
        if level == max_level:
            vals = {s: int(max_row[s]) for s in STATS}
        elif level <= cap0:
            vals = interpolate(1, {s: LV1_RATIO * c0[s] for s in STATS}, cap0, c0, level)
        else:
            vals = interpolate(cap0, c0, max_level, {s: float(max_row[s]) for s in STATS}, level)
        bounds.append(vals)
    return StatValue(level, bounds[0], "estimated", "ratio_estimate_from_max", (max_level,),
                     f"ESTIMATE: top/cap0 in {TOP_RATIO_RANGE}, Lv1/cap0 = {LV1_RATIO}", high=bounds[1])
