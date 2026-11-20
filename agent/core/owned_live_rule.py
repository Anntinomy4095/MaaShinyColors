"""S 卡自带 Live 技能（所持ライブスキル）的规则推算：ShinyColorsDB 没有这项，Wiki 只覆盖约 160 张 S 卡。

2026-10-31 (user: 「自带技能应该是有个规律」): over the 159 S cards whose Wiki page gives the skill, it follows the card's
rarity, how it is obtained (``scdb.get_method``) and its release date:

- R「アピールⅡ」1.5x; SR「アピールⅢ」2.0x (a Present SR from 2024 on: 「アピールⅣ」2.5x); none level up.
- SSR permanent / event cards, and limited cards released before 2020-01-31:「アピールⅣ」2.5x -> 3.0x.
- SSR limited / Fes reward / paid / collaboration / situation-drama cards from 2020-01-31 on:「アピールⅤ」3.0x -> 3.5x.
- Twilight SSR:「アピールⅥ」3.0x -> 3.5x, each step also Vocal/Dance/Visual +5% for 3 turns.
- UR:「アピールⅦ」3.5x -> 4.0x at Lv70–90, with the same buff steps.
- SSR steps +0.1x at Lv60/65/70/75/80; UR at Lv70/75/80/85/90.

The attribute is the card's highest Vo/Da/Vi stat; three equal stats mean an Excellent appeal. A two-way tie (old
two-attribute cards), an N card or an SSR source with no sample keeps the skill unknown (SSR from an unsampled source
take the lower 2.5x tier). Every derived skill is labelled with :data:`RULE`; a Wiki value always wins.
"""
from __future__ import annotations

from typing import Any, Mapping

RULE = "owned-live-rule-20261031"
LIMITED_FROM = "2020-01-31"
# SSR sources whose own skill is the 3.0x tier from LIMITED_FROM on (Wiki samples: Limited 23, Fes 7, Paid / Collabo /
# SituationDrama 1-2 each).
UPPER_TIER = frozenset({"LimitedGasha", "FesReward", "PaidGasha", "CollaboGasha", "SituationDrama"})
LOWER_TIER = frozenset({"GeneralGasha", "Events"})
NAMES = {"Vo": "ボーカル", "Da": "ダンス", "Vi": "ビジュアル"}
ROMAN = {1.5: "Ⅱ", 2.0: "Ⅲ", 2.5: "Ⅳ", 3.0: "Ⅴ"}
SSR_STEPS = (60, 65, 70, 75, 80)
UR_STEPS = (70, 75, 80, 85, 90)


def _attribute(card: Mapping[str, Any]) -> str | None:
    """Vo / Da / Vi of the highest stat at the card's top recorded level, "EX" when all three are equal, None on a
    two-way tie or without stats."""
    levels = card.get("levels") or {}
    try:
        top = levels[max(levels, key=int)] if levels else None
    except (TypeError, ValueError):
        return None
    if not isinstance(top, Mapping):
        return None
    try:
        values = {a: float(top[a]) for a in ("Vo", "Da", "Vi")}
    except (KeyError, TypeError, ValueError):
        return None
    best = max(values.values())
    leaders = [a for a, v in values.items() if v == best]
    return leaders[0] if len(leaders) == 1 else "EX" if len(leaders) == 3 else None


def _skill(attribute: str, numeral: str, value: float, level: int, step: int, buff: bool) -> dict[str, Any]:
    excellent = attribute == "EX"
    name = ("エクセレント" if excellent else NAMES[attribute]) + "アピール" + numeral + "+" * step
    return {"id": name, "components": {} if excellent else {attribute: round(value, 2)},
            "excellent": round(value, 2) if excellent else 0.0, "unlock_level": level,
            "buff_attribute": None if excellent or not (buff and step) else attribute,
            "buff_amount": round(.05 * step, 2) if buff and step and not excellent else 0.0,
            "buff_turns": 3 if buff and step and not excellent else 0, "heal_fraction": 0.0, "unsupported": []}


def derive(card: Mapping[str, Any]) -> list[dict[str, Any]] | None:
    """The S card's own Live ladder (``owned_live_skills`` rows) by the rule, or None when it cannot be told."""
    if card.get("kind") != "S":
        return None
    rarity = str(card.get("rarity") or "").removeprefix("S_")
    method = str((card.get("scdb") or {}).get("get_method") or "")
    date = str(card.get("implementation_date") or (card.get("scdb") or {}).get("last_modified") or "")
    attribute = _attribute(card)
    if attribute is None:
        return None
    if rarity == "R":
        return [_skill(attribute, "Ⅱ", 1.5, 1, 0, False)]
    if rarity == "SR":
        value = 2.5 if method == "Present" and date >= "2024-01-01" else 2.0
        return [_skill(attribute, ROMAN[value], value, 1, 0, False)]
    if rarity == "UR":
        steps, base, numeral, buff = UR_STEPS, 3.5, "Ⅶ", True
    elif rarity == "SSR" and method == "TwilightCollection":
        steps, base, numeral, buff = SSR_STEPS, 3.0, "Ⅵ", True
    elif rarity == "SSR":
        upper = method in UPPER_TIER and date >= LIMITED_FROM
        steps, base, buff = SSR_STEPS, 3.0 if upper else 2.5, False
        numeral = ROMAN[base]
    else:
        return None
    return [_skill(attribute, numeral, base + .1 * i, level, i, buff) for i, level in enumerate((1, *steps))]


def with_derived_owned_live(cards: Mapping[str, dict]) -> tuple[dict[str, dict], list[str]]:
    """``cards`` with the rule's skills on S cards that have none (labelled in ``provenance`` and ``derived_fields``),
    plus the derived card IDs. Cards are copied, never changed in place."""
    out, derived = dict(cards), []
    for key, card in cards.items():
        if card.get("kind") != "S" or card.get("owned_live_skills"):
            continue
        skills = derive(card)
        if not skills:
            continue
        provenance = {**(card.get("provenance") or {}),
                      "owned_live_skills": {"source": RULE, "basis": "rarity/get_method/implementation_date/stats"}}
        out[key] = {**card, "owned_live_skills": skills, "provenance": provenance,
                    "unknown_fields": [u for u in card.get("unknown_fields") or () if u != "owned_live_skills"]}
        derived.append(key)
    return out, derived
