"""Card mechanics and ownership are separate; missing data is never strength zero."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
import math
import re
from typing import Any, Mapping
from ..support_effects import support_skill_expectation
from ..card_stats import resolve_level_caps

ATTRIBUTES = ("Vo", "Da", "Vi")
STATS = (*ATTRIBUTES, "Me")
ROOMS = (*ATTRIBUTES, "radio", "talk", "magazine")
PARSER_VERSION = "true-end-card-v2"


def number(value: Any, name: str, *, minimum: float = 0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name}: expected a number")
    result = float(value)
    if not math.isfinite(result) or result < minimum:
        raise ValueError(f"{name}: invalid finite value")
    return result


def integer(value: Any, name: str, *, minimum: int = 0) -> int:
    result = number(value, name, minimum=minimum)
    if not result.is_integer():
        raise ValueError(f"{name}: expected an integer")
    return int(result)


@dataclass(frozen=True)
class Ownership:
    card_id: str
    limit_break: int
    level: int
    ex_skills: tuple[Mapping[str, Any], ...] = ()

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Ownership:
        card_id = value.get("card_id")
        if not isinstance(card_id, str) or not card_id:
            raise ValueError("inventory.card_id is required")
        lb = integer(value.get("limit_break"), "limit_break")
        if lb > 4:
            raise ValueError("limit_break must be 0..4")
        return cls(card_id, lb, integer(value.get("level"), "level", minimum=1),
                   tuple(value.get("ex_skills", ())))


@dataclass(frozen=True)
class LiveSkill:
    id: str
    components: Mapping[str, float]
    unlock_level: int = 1
    buff_attribute: str | None = None
    buff_amount: float = 0
    buff_turns: int = 0
    heal_fraction: float = 0
    unsupported: tuple[str, ...] = ()
    # "Excellent N倍アピール" (DB effect attribute "excellent"): one appeal of N times on the targeted judge's
    # attribute that always counts as Excellent.  0 = none. (Excellent appeal, 2026-10-07).
    excellent: float = 0

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> LiveSkill:
        parts = value.get("components", {})
        heal = number(value.get("heal_fraction", 0), "heal_fraction")
        excellent = number(value.get("excellent", 0), "excellent")
        if (not isinstance(parts, Mapping) or any(a not in ATTRIBUTES for a in parts)
                or (not parts and heal <= 0 and excellent <= 0)):
            raise ValueError("live skill needs Vo/Da/Vi components")
        buff_attr = value.get("buff_attribute")
        if buff_attr is not None and buff_attr not in ATTRIBUTES:
            raise ValueError("unknown buff attribute")
        if heal > 1:
            raise ValueError("heal_fraction must be 0..1")
        return cls(str(value["id"]), {a: number(n, "multiplier") for a, n in parts.items()},
                   integer(value.get("unlock_level", 1), "unlock_level", minimum=1),
                   buff_attr, number(value.get("buff_amount", 0), "buff_amount"),
                   integer(value.get("buff_turns", 0), "buff_turns"), heal,
                   tuple(value.get("unsupported", ())), excellent)


@dataclass(frozen=True)
class SupportSkill:
    id: str
    kind: str
    levels: Mapping[int, float]
    attribute: str | None = None
    room: str | None = None
    required_lb: int = 0
    unsupported: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> SupportSkill:
        levels = {integer(int(k), "skill level", minimum=1): number(v, "skill amount")
                  for k, v in value.get("levels", {}).items()}
        attr, room = value.get("attribute"), value.get("room")
        if attr is not None and attr not in (*STATS, "SP"):
            raise ValueError("unknown mastery attribute")
        if room is not None and room not in ROOMS:
            raise ValueError("unknown mastery room")
        kind, unsupported = str(value["kind"]), tuple(value.get("unsupported", ()))
        if kind == "unit_mastery_stat" and attr is None and "体力" in str(value["id"]):
            # Libraries converted before 2026-10-25 stored ユニットマスタリー体力 as a stat mastery and flagged its
            # 「体力 Lv×3 回復」 text; read it as unit stamina when every flagged amount is exactly Lv×3.
            prefix = "description amount differs from skillLevel x1: "
            flagged = [u for u in unsupported if u.startswith(prefix)]
            amounts = [re.findall(r"(\d+)", u.removeprefix(prefix).replace("1人につき", "")) for u in flagged]
            if all(a and float(a[-1]) in {lv * 3 for lv in levels.values()} for a in amounts):
                kind, unsupported = "unit_mastery_stamina", tuple(u for u in unsupported if u not in flagged)
        return cls(str(value["id"]), kind, levels, attr, room,
                   integer(value.get("required_lb", 0), "required_lb"), unsupported)

    def amount(self, ownership: Ownership) -> float | None:
        if ownership.limit_break < self.required_lb:
            return 0.0  # Known locked effect, not missing data.
        unlocked = [lv for lv in self.levels if lv <= ownership.level]
        if unlocked:
            return self.levels[max(unlocked)]
        # Owned level is below the first unlock level of a known ladder: the skill is simply not acquired yet
        # (a known zero), not missing data. An empty ladder stays unknown.
        return 0.0 if self.levels else None


@dataclass(frozen=True)
class Panel:
    id: str
    kind: str
    sp_cost: int
    required_lb: int
    prerequisites: tuple[str, ...]
    prerequisites_known: bool
    effects: Mapping[str, Any]
    event: str | None = None
    unsupported: tuple[str, ...] = ()
    # Source coordinates only; this does not certify edges or variant selection.
    source_layout: Mapping[str, int] | None = None

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Panel:
        known = value.get("prerequisites_known", False)
        if not isinstance(known, bool):
            raise ValueError("prerequisites_known must be bool")
        lb = integer(value.get("required_lb", 0), "panel.required_lb")
        if lb > 4:
            raise ValueError("panel.required_lb must be 0..4")
        kind = str(value["kind"])
        if kind not in ("live", "cap", "passive", "stat", "unknown"):
            raise ValueError("unknown panel kind")
        effects = dict(value.get("effects", {}))
        if kind == "live":
            LiveSkill.from_dict(effects)
        elif kind in ("cap", "stat"):
            if not effects or any(k not in STATS for k in effects):
                raise ValueError("stat/cap needs named stats")
            effects = {k: number(v, f"panel.{k}") for k, v in effects.items()}
        layout = value.get("source_layout")
        if layout is not None:
            keys = ("table", "row", "column", "rowspan", "colspan")
            if not isinstance(layout, Mapping) or set(layout) != set(keys):
                raise ValueError("panel.source_layout needs table/cell coordinates")
            layout = {k: integer(layout[k], "panel.source_layout." + k, minimum=1) for k in keys}
        return cls(str(value["id"]), kind, integer(value.get("sp_cost"), "sp_cost"), lb,
                   tuple(value.get("prerequisites", ())), known, effects,
                   value.get("event"), tuple(value.get("unsupported", ())), layout)


@dataclass(frozen=True)
class CardData:
    card_id: str
    name: str
    idol: str
    kind: str
    status: str
    levels: Mapping[int, Mapping[str, float | None]]
    max_levels: Mapping[int, int]
    owned_live_skills: tuple[LiveSkill, ...]
    support_skills: tuple[SupportSkill, ...]
    panels: tuple[Panel, ...]
    events: tuple[Mapping[str, Any], ...]
    provenance: Mapping[str, Mapping[str, Any]]
    source_url: str
    rarity: str | None = None  # Also selects verified level limits; never a strength bonus.
    implementation_date: str | None = None
    initial_stats: Mapping[str, float] = field(default_factory=dict)
    unknown_fields: tuple[str, ...] = ()
    unsupported: tuple[str, ...] = ()
    unit: str | None = None
    te_eligible: bool | None = None
    panel_topology: Mapping[str, Any] | None = None
    te_condition: str | None = None

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> CardData:
        for key in ("card_id", "name", "idol", "kind", "status", "source_url"):
            if not isinstance(value.get(key), str) or not value[key]:
                raise ValueError(f"card.{key} is required")
        if value["kind"] not in ("P", "S"):
            raise ValueError("card.kind must be P/S")
        if value["status"] not in ("OK", "PARTIAL", "PARSE_ERROR"):
            raise ValueError("unknown parse status")
        levels = {integer(int(lv), "card.level", minimum=1): {
            a: None if n is None else number(n, f"stats.{a}") for a, n in stats.items()}
            for lv, stats in value.get("levels", {}).items()}
        if any(a not in STATS for stats in levels.values() for a in stats):
            raise ValueError("unknown card stat")
        max_levels = {integer(int(lb), "max_levels.lb"): integer(lv, "max_level", minimum=1)
                      for lb, lv in value.get("max_levels", {}).items()}
        if any(lb > 4 for lb in max_levels):
            raise ValueError("max_levels.lb must be 0..4")
        provenance = dict(value.get("provenance", {}))
        cap_rule = None
        if not max_levels:
            max_levels, cap_rule = resolve_level_caps(value)
            if cap_rule:
                provenance["max_levels"] = cap_rule
        panels = tuple(Panel.from_dict(v) for v in value.get("panels", ()))
        if len({p.id for p in panels}) != len(panels):
            raise ValueError("duplicate panel id")
        te_eligible = value.get("te_eligible")
        if te_eligible is not None and not isinstance(te_eligible, bool):
            raise ValueError("te_eligible must be bool or null")
        te_condition = value.get("te_condition")
        if te_condition is not None and (not isinstance(te_condition, str) or not te_condition.strip()):
            raise ValueError("te_condition must be nonempty text or null")
        topology = value.get("panel_topology")
        if topology is not None:
            if not isinstance(topology, Mapping) or not isinstance(topology.get("complete"), bool):
                raise ValueError("panel_topology needs an explicit completeness flag")
            if not all(isinstance(topology.get(k), str) and topology[k].strip()
                       for k in ("source_url", "locator")):
                raise ValueError("panel_topology needs source_url and locator")
            panel_ids = {p.id for p in panels}
            roots, edges = topology.get("roots"), topology.get("edges")
            if (not isinstance(roots, (list, tuple)) or not roots
                    or any(not isinstance(k, str) or k not in panel_ids for k in roots)
                    or len(set(roots)) != len(roots) or not isinstance(edges, (list, tuple))):
                raise ValueError("panel_topology has invalid roots or edges")
            links = set()
            for edge in edges:
                if (not isinstance(edge, (list, tuple)) or len(edge) != 2
                        or any(not isinstance(k, str) or k not in panel_ids for k in edge)
                        or edge[0] == edge[1]):
                    raise ValueError("panel_topology edge must name two different known panels")
                pair = tuple(sorted(edge))
                if pair in links:
                    raise ValueError("duplicate panel_topology edge")
                links.add(pair)
            if topology["complete"]:
                connected = set(roots)
                while True:
                    expanded = connected | {k for edge in links if set(edge) & connected for k in edge}
                    if expanded == connected:
                        break
                    connected = expanded
                if connected != panel_ids:
                    raise ValueError("complete panel_topology must connect every panel to a root")
            topology = {**topology, "roots": tuple(roots), "edges": tuple(sorted(links))}
        return cls(value["card_id"], value["name"], value["idol"], value["kind"], value["status"],
                   levels, max_levels,
                   tuple(LiveSkill.from_dict(v) for v in value.get("owned_live_skills", ())),
                   tuple(SupportSkill.from_dict(v) for v in value.get("support_skills", ())),
                   panels, tuple(value.get("events", ())), provenance,
                   value["source_url"], value.get("rarity"), value.get("implementation_date"),
                   {a: number(n, f"initial.{a}") for a, n in value.get("initial_stats", {}).items()},
                   tuple(g for g in value.get("unknown_fields", ()) if not (cap_rule and g == "max_levels")),
                   tuple(value.get("unsupported", ())),
                   value.get("unit"), te_eligible, topology, te_condition)


# Gaps that only an Active S needs (the owned Live skill is played only by Active supports).
ACTIVE_ONLY_GAPS = frozenset({"owned_live_skills"})


@dataclass(frozen=True)
class CardFeatures:
    card: CardData
    ownership: Ownership
    stats: Mapping[str, float]
    support_effects: tuple[tuple[SupportSkill, float], ...]
    owned_live: LiveSkill | None
    panels: tuple[Panel, ...]
    unknowns: tuple[str, ...]
    unsupported: tuple[str, ...]

    @property
    def ready(self) -> bool:
        """Ready for an Active slot (battle hand included)."""
        return not self.unknowns and self.card.status != "PARSE_ERROR"

    @property
    def guest_unknowns(self) -> tuple[str, ...]:
        """Gaps that matter for a Guest S: the Guest never enters the Live (battle skips it), so its own Live
        skill is not needed.  Stats stay required because growth's lesson correction reads them."""
        if self.card.kind != "S":
            return self.unknowns
        return tuple(u for u in self.unknowns if u not in ACTIVE_ONLY_GAPS)

    @property
    def ready_as_guest(self) -> bool:
        return self.card.kind == "S" and not self.guest_unknowns and self.card.status != "PARSE_ERROR"


def extract_card_features(card: CardData, ownership: Ownership) -> CardFeatures:
    """Resolve actual Lv/LB only; no rarity bonus or interpolated level curve."""
    if card.card_id != ownership.card_id:
        raise ValueError("ownership does not match card")
    unknowns = list(card.unknown_fields)
    if not card.idol.strip():
        unknowns.append("idol")
    if card.kind == "P" and card.te_eligible is not True:
        unknowns.append("te_eligible")
    max_level = card.max_levels.get(ownership.limit_break)
    if card.kind == "S" and max_level is None:
        unknowns.append("max_levels")
    elif max_level is not None and ownership.level > max_level:
        raise ValueError(f"{card.card_id}: level exceeds owned limit-break cap")
    raw = card.levels.get(ownership.level)
    stats: dict[str, float] = {}
    if card.kind == "S":
        for a in STATS:
            if raw is None or raw.get(a) is None:
                unknowns.append(f"levels.{ownership.level}.{a}")
            else:
                stats[a] = float(raw[a])
    unsupported = list(card.unsupported)
    effects = []
    for skill in card.support_skills:
        amount = skill.amount(ownership)
        if amount is None:
            unknowns.append(f"support_skills.{skill.id}.level")
        else:
            # おやすみブースト is valued at zero (growth.ZERO_VALUE_SUPPORT), so its text is no data gap.
            if skill.kind == "rest_support" or support_skill_expectation(skill, amount, card.rarity) is not None:
                skill = replace(skill, unsupported=())
            effects.append((skill, amount))
        unsupported.extend(skill.unsupported)
    available = [s for s in card.owned_live_skills if s.unlock_level <= ownership.level]
    owned_live = max(available, key=lambda s: s.unlock_level) if available else None
    if card.kind == "S" and owned_live is None:
        unknowns.append("owned_live_skills")
    if owned_live:
        unsupported.extend(owned_live.unsupported)
    if ownership.ex_skills:
        unsupported.append("EX skills require a reviewed mechanics override")
    panels = tuple(p for p in card.panels if p.required_lb <= ownership.limit_break)
    # Unselected panels do not participate in this team's mechanics. The growth
    # planner rejects unsupported closures and reports their exclusion separately.
    return CardFeatures(card, ownership, stats, tuple(effects), owned_live, panels,
                        tuple(sorted(set(unknowns))), tuple(sorted(set(unsupported))))


def load_inventory(values: list[Mapping[str, Any]]) -> dict[str, Ownership]:
    owned = [Ownership.from_dict(v) for v in values]
    if len({v.card_id for v in owned}) != len(owned):
        raise ValueError("duplicate owned card id")
    return {v.card_id: v for v in owned}


def validate_team(p: CardFeatures, active: tuple[CardFeatures, ...], guest: CardFeatures) -> None:
    if p.card.kind != "P" or len(active) != 4 or any(s.card.kind != "S" for s in (*active, guest)):
        raise ValueError("team must contain one P, four active S, one guest S")
    cards = (p, *active, guest)
    if len({c.card.card_id for c in cards}) != 6 or len({c.card.idol for c in cards}) != 6:
        raise ValueError("duplicate card or idol across P/active/guest")
    if any(not c.ready for c in (p, *active)) or not guest.ready_as_guest:
        raise ValueError("team contains unknown or unparsed mechanics")
