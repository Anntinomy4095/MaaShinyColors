"""Verified WING support formulas; probabilities are not activation observations."""
from __future__ import annotations

import math
import re
from itertools import product
from typing import Any, Mapping, Sequence

SOURCE = "https://note.com/ze3rx/n/ndc0cfb2da6ba"


def support_skill_expectation(skill: Any, level: float, rarity: str | None) -> dict[str, float] | None:
    """Resolve SupportSkill + actual unlocked amount, refusing unknown/conflicting text.

    Old parsers stored the final ladder description as unsupported. Only an exact
    standard Japanese description, validated against that ladder, may be cleared.
    """
    # ShinyColorsDB keeps the S_ card-type prefix; Wiki records use the bare rarity.
    rarity = str(rarity or "").removeprefix("S_")
    if skill.kind not in ("stamina_support", "rest_support"):
        return None
    if not math.isfinite(level) or not 0 <= level <= 100 / 3:
        return None
    amount = ({"SSR": .5, "SR": .3, "R": .15, "UR": .5} if skill.kind == "stamina_support"
              else {"SSR": 15., "SR": 10., "R": 5.}).get(rarity)
    if amount is None:
        return None
    # All six cached UR cards agree: skill Lv3 -> 9%, Lv5 -> 15%, reduction 50%.
    # The existing unlock ladder determines the current rank; no UR rest/rank rule is inferred.
    if rarity == "UR" and (not skill.levels or set(skill.levels.values()) - {0, 3, 5} or level not in (0, 3, 5)):
        return None
    probability_pattern = r"(\d+(?:\.\d+)?|<Lv\*3>)"
    pattern = (r"レッスンかお仕事を選択する時に" + probability_pattern + r"%の確率で体力消費量-(\d+(?:\.\d+)?)%"
               if skill.kind == "stamina_support" else
               r"「休む」を選択時に" + probability_pattern + r"%の確率で体力回復量\+(\d+(?:\.\d+)?)")
    for text in skill.unsupported:
        match = re.fullmatch(pattern, str(text).strip())
        if (not match or not skill.levels or
                (match[1] != "<Lv*3>" and not math.isclose(float(match[1]), max(skill.levels.values()) * 3)) or
                not math.isclose(float(match[2]), amount * (100 if skill.kind == "stamina_support" else 1))):
            return None
    probability = level * .03
    if skill.kind == "stamina_support":
        return {"activation_probability": probability, "stamina_reduction": amount,
                "expected_stamina_reduction": probability * amount}
    return {"activation_probability": probability, "recovery": amount,
            "expected_recovery": probability * amount}


def expected_stamina_multiplier(effects: Sequence[Mapping[str, float]]) -> float:
    """Independent activation expectation with additive reduction and a zero floor."""
    result = 0.
    for flags in product((False, True), repeat=len(effects)):
        probability, reduction = 1., 0.
        for active, effect in zip(flags, effects):
            p = effect["activation_probability"]
            if not 0 <= p <= 1 or not 0 <= effect["stamina_reduction"] <= 1:
                raise ValueError("support effect probability/reduction must be in 0..1")
            probability *= p if active else 1 - p
            reduction += effect["stamina_reduction"] if active else 0
        result += probability * max(0., 1 - reduction)
    return result
