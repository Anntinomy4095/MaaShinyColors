"""Default 训练卡 when the option is empty.

1. N/R cards and cards at their level cap are never picked.
2. S cards of the user's Produce/W.I.N.G. team that are not maxed come first, lowest level first.
3. Otherwise the highest-rarity non-max S card (UR > SSR > SR), highest level first.

Every id comes from the scanned owned cards; a card without a read level or cap is skipped. None when nothing fits
(the training task then keeps its in-game default mode).
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from agent.core.card_stats import level_cap

RARITY_ORDER = {"UR": 3, "SSR": 2, "SR": 1}


def _rarity(card: Mapping[str, Any]) -> str:
    return str(card.get("rarity") or "").rsplit("_", 1)[-1]


def candidates(owned: Mapping[str, Any], cards: Mapping[str, Mapping[str, Any]],
               readings: Mapping[str, Mapping[str, Any]] | None = None) -> list[dict]:
    """[{"id", "rarity", "level", "cap"}] of owned S cards (SR or higher) below their level cap. ``readings`` (the
    passive training-list store, newer than the owned-card scan; 2026-11-21 live: UR 2050070010 scanned Lv66, read
    Lv70 = its cap) override the scanned level and limit break per card."""
    details = owned.get("details") or {}
    readings = readings or {}
    out = []
    for card_id in owned.get("s") or ():
        card = cards.get(str(card_id)) or {}
        rarity = _rarity(card)
        if card.get("kind") != "S" or rarity not in RARITY_ORDER:
            continue
        detail = {**(details.get(str(card_id)) or {}),
                  **{k: v for k, v in (readings.get(str(card_id)) or {}).items() if k in ("level", "limit_break")}}
        level, lb = detail.get("level"), detail.get("limit_break")
        levels = card.get("max_levels") or {}
        cap = levels.get(str(lb)) if lb is not None else None
        cap = int(cap) if cap else level_cap(rarity, lb)
        if type(level) is not int or not cap or level >= cap:
            continue
        out.append({"id": str(card_id), "rarity": rarity, "level": level, "cap": cap})
    return out


def pick(owned: Mapping[str, Any], cards: Mapping[str, Mapping[str, Any]], team: Iterable[str] = (),
         readings: Mapping[str, Mapping[str, Any]] | None = None, skip: Iterable[str] = ()) -> dict | None:
    """The chosen candidate plus ``reason`` ("team" or "rarity"), or None. ``skip``: ids never picked (card DBs that
    disagree on the card's name, training_task.swapped_names)."""
    skip = {str(c) for c in skip}
    rows = [r for r in candidates(owned, cards, readings) if r["id"] not in skip]
    team = {str(c) for c in team if c}
    in_team = [r for r in rows if r["id"] in team]
    if in_team:
        best = min(in_team, key=lambda r: (r["level"], -RARITY_ORDER[r["rarity"]], r["id"]))
        return {**best, "reason": "team"}
    if rows:
        # Within the rarity the highest non-max level -- a Lv1 card sits among ~230 Lv1 cards
        # (10 scrolls did not reach it); near-cap cards are a few scrolls from the Lv順 end
        best = min(rows, key=lambda r: (-RARITY_ORDER[r["rarity"]], -r["level"], r["id"]))
        return {**best, "reason": "rarity"}
    return None
