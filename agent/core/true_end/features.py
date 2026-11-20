"""Mechanical vectors and configurable role priors, exclusively for pruning."""
from __future__ import annotations

from typing import Mapping
from ..support_effects import support_skill_expectation

from .growth import SINGLE_GENRE, idol_key, passive_turn_buff, stage_passive
from .models import ATTRIBUTES, CardFeatures

ROLES = ("MAIN_HITTER", "SECONDARY_HITTER", "ATTRIBUTE_GROWTH", "SP_ENGINE",
         "STAMINA_ENGINE", "CAP_ENGINE", "AUDITION_MASTERY", "UTILITY", "HYBRID")
GUEST_ROLES = ("GUEST_GROWTH", "GUEST_SP", "GUEST_CAP", "GUEST_STAMINA", "HYBRID")

# Initial search heuristics, not card ratings or team performance.
DEFAULT_ROLE_WEIGHTS: dict[str, dict[str, float]] = {
    "MAIN_HITTER": {"own_appeal": 3, "panel_live": 1, "combat_stat": 1},
    "SECONDARY_HITTER": {"own_appeal": 2, "panel_live": 1},
    "ATTRIBUTE_GROWTH": {"mastery": 2, "growth_stat": 1, "event_growth": 1},
    "SP_ENGINE": {"sp": 2, "audition_sp": 1},
    # 2026-10-25 (user): stamina = the build room's マスタリー体力 (no rest weeks); 体力サポート is a random discount;
    # おやすみブースト counts nothing.
    "STAMINA_ENGINE": {"stamina": 2, "stamina_reduction": 1},
    "CAP_ENGINE": {"cap": 2, "mastery_cap": 1},
    "AUDITION_MASTERY": {"audition_growth": 2, "audition_sp": 1},
    "UTILITY": {"bond": 1, "sp": 1, "stamina": 1},
    "GUEST_GROWTH": {"mastery": 2, "growth_stat": 1, "event_growth": 1},
    "GUEST_SP": {"sp": 2, "audition_sp": 1},
    "GUEST_CAP": {"cap": 2, "mastery_cap": 1},
    "GUEST_STAMINA": {"stamina": 2, "stamina_reduction": 1},
    # Not a role: optimizer.prune_candidates keeps the few best Live-panel cards (LIVE_RESERVE) for the beam to judge.
    "LIVE_RESERVE": {"panel_passive": 2, "panel_live": 1},
}
NORMALIZERS = {"own_appeal": 4, "panel_live": 5, "panel_passive": .1, "combat_stat": 300, "growth_stat": 300,
               "mastery": 15, "sp": 15, "stamina": 15, "cap": 300, "mastery_cap": 10,
               "audition_growth": 15, "audition_sp": 15, "bond": 50, "event_growth": 100,
               "stamina_reduction": 1}


def mechanical_vector(features: CardFeatures, primary: str, *, slot: str = "active") -> dict[str, float]:
    if primary not in ATTRIBUTES or slot not in ("active", "guest"):
        raise ValueError("unknown primary/slot")
    values = dict.fromkeys(NORMALIZERS, 0.0)
    # This vector is computed only from resolved fields; unknown paths remain on features.
    values["growth_stat"] = features.stats.get(primary, 0)
    if slot == "active":
        values["combat_stat"] = features.stats.get(primary, 0)
        if features.owned_live:
            # An Excellent appeal hits any judge as Excellent, i.e. at least the primary-attribute multiplier.
            values["own_appeal"] = max(features.owned_live.components.get(primary, 0), features.owned_live.excellent)
    for skill, lv in features.support_effects:
        if skill.unsupported:
            continue
        expected = support_skill_expectation(skill, lv, features.card.rarity)
        if expected is not None:
            values["stamina_reduction"] += expected.get("expected_stamina_reduction", 0)
        if skill.kind in ("mastery_stat", "unit_mastery_stat") and skill.attribute == primary:
            values["mastery"] += lv
        elif skill.kind in ("mastery_sp", "unit_mastery_sp"):
            values["sp"] += lv
        elif skill.kind == "unit_mastery_stamina" or (skill.kind == "mastery_stamina" and skill.room == primary):
            values["stamina"] += lv   # another room's recovery rarely fires: the build trains in its own room
        elif skill.kind in ("mastery_cap", "unit_mastery_cap") and skill.attribute == primary:
            values["mastery_cap"] += lv
        elif skill.kind == "audition_mastery_stat" and skill.attribute == primary:
            values["audition_growth"] += lv
        elif skill.kind == "audition_mastery_sp":
            values["audition_sp"] += lv
        elif skill.kind == "bond":
            values["bond"] += 5 * lv
    # an active support performs, so its own 「<idol>がライブに参加」 passives hold (growth.stage_passive); a
    # 「履歴に1ジャンル以下」 passive is the P's and holds in a single-genre build, whatever the slot (pruning prior only)
    own = frozenset({idol_key(features.card.idol)} if slot == "active" else ()) | {SINGLE_GENRE}
    for panel in features.panels:
        if panel.kind == "passive":
            passive = stage_passive(panel, own) or (panel.effects if not panel.unsupported else {})
            if passive.get("attribute") == primary and not passive.get("condition") and not passive.get("unsupported"):
                values["panel_passive"] += passive_turn_buff(passive)
        elif panel.kind == "cap":
            values["cap"] += float(panel.effects.get(primary, 0))
        elif panel.kind == "live":
            values["panel_live"] = max(values["panel_live"],
                                        float(panel.effects.get("components", {}).get(primary, 0)),
                                        float(panel.effects.get("excellent", 0)))
    for event in features.card.events:
        options = event.get("options", ())
        gains = event.get("gains", event.get("stats", {}))
        values["event_growth"] += max([float(gains.get(primary, 0))] +
                                      [float(o.get("gains", o.get("stats", {})).get(primary, 0)) for o in options])
    return values


def normalized_vector(features: CardFeatures, primary: str, *, slot: str = "active") -> dict[str, float]:
    return {key: value / NORMALIZERS[key] for key, value in mechanical_vector(features, primary, slot=slot).items()}


def score_card_prior(features: CardFeatures, strategy: str, role: str, slot: str = "active", *,
                     weights: Mapping[str, Mapping[str, float]] | None = None) -> float:
    """Relative role prior; never aggregate this into the final recommendation score."""
    primaries = strategy.split("_")
    attrs = [a.title() for a in primaries if a.upper() in ("VO", "DA", "VI")]
    if not attrs:
        raise ValueError("strategy must name Vo/Da/Vi")
    vectors = [normalized_vector(features, a, slot=slot) for a in attrs]
    table = DEFAULT_ROLE_WEIGHTS | dict(weights or {})
    vector = {key: max(v[key] for v in vectors) for key in NORMALIZERS}
    if role == "HYBRID":
        # Preserve cards compressing multiple roles even if each scalar prior is modest.
        roles = GUEST_ROLES[:-1] if slot == "guest" else ROLES[:-1]
        scores = sorted((sum(vector.get(k, 0) * w for k, w in table[r].items())
                         for r in roles), reverse=True)
        return sum(scores[:4])
    if role not in table:
        raise ValueError("unknown role prior")
    return sum(vector.get(k, 0) * float(w) for k, w in table[role].items())


def effective_cap_contribution(raw: float, current_cap: float, extra_cap: float) -> float:
    return max(0.0, min(extra_cap, raw - current_cap))


def sp_readiness(sp: float, required_sp: float) -> float:
    return 1.0 if required_sp <= 0 else min(1.0, max(0.0, sp / required_sp))
