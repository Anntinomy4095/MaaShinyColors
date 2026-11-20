"""Shared offline WING profile and complete observed resume snapshot."""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import hashlib
import json
from typing import Mapping

from .growth import GrowthConfig, _attributes, _passive_supported
from .models import CardFeatures, LiveSkill, integer, number
from .sp_value import PanelValueState


def fingerprint(value) -> str:
    def canonical(item):
        if isinstance(item, Mapping):
            return {str(k): canonical(v) for k, v in item.items()}
        if isinstance(item, (list, tuple)):
            return [canonical(v) for v in item]
        if isinstance(item, float) and item.is_integer():
            return int(item)
        return item
    return hashlib.sha256(json.dumps(canonical(value), sort_keys=True, ensure_ascii=False,
                                     allow_nan=False).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RunProfile:
    p: CardFeatures
    active: tuple[CardFeatures, ...]
    guest: CardFeatures
    strategy: str
    mode: str = "WING"
    difficulty: str = "Middle"
    goal: str = "TrueEnd"

    def __post_init__(self):
        if (len(self.active) != 4 or self.p.card.kind != "P" or
                any(c.card.kind != "S" for c in (*self.active, self.guest))):
            raise ValueError("profile needs P, four Active S and one Guest S")
        if (len({c.card.card_id for c in self.cards}) != 6 or
                len({c.card.idol for c in self.cards}) != 6):
            raise ValueError("profile has duplicate card or idol")
        _attributes(self.strategy)
        if self.strategy.upper().split("_")[-1] not in ("SAFE", "AWARE", "AGGRESSIVE", "FORCE", "DUAL"):
            raise ValueError("profile needs an explicit battle strategy")
        if (self.mode, self.difficulty, self.goal) != ("WING", "Middle", "TrueEnd"):
            raise ValueError("shared terminal model currently supports WING Middle TrueEnd")

    @property
    def cards(self) -> tuple[CardFeatures, ...]:
        return (self.p, *self.active, self.guest)

    @property
    def id(self) -> str:
        # Includes card mechanics/coverage, ownership, role order and settings.
        return fingerprint(asdict(self))

    @property
    def primary(self) -> str:
        return _attributes(self.strategy)[0]

    def bind(self, state: "RunState") -> None:
        if state.profile_id != self.id:
            raise ValueError("run snapshot belongs to a different card/Lv/LB/role/model profile")
        if set(state.bonds) != {c.card.card_id for c in self.cards[1:]}:
            raise ValueError("run snapshot needs current bonds for all five selected supports")
        known_panels = {f"{c.card.card_id}:{p.id}" for c in self.cards for p in c.panels}
        if set(state.panels.learned_panel_ids) - known_panels:
            raise ValueError("learned panel does not belong to this owned profile")
        # Current cumulative fans cannot contradict an already passed season gate.
        gate = (0, 1000, 10000, 50000, 100000)[state.next_week // 8]
        if state.route_feasible and state.fans < gate:
            raise ValueError("current fans contradict the completed season gate")


@dataclass(frozen=True)
class RunState:
    profile_id: str
    next_week: int  # absolute zero-based; an observed failure has already used its week
    panels: PanelValueState  # effective stats/caps; purchased effects already applied
    sp: float  # unspent, never historical total SP
    stamina: float
    fans: float
    bonds: Mapping[str, float]
    events_completed: tuple[str, ...] = ()
    route_feasible: bool = True
    phase: str = "week"
    memory_level: int = 0

    def __post_init__(self):
        if type(self.next_week) is not int or type(self.memory_level) is not int:
            raise ValueError("next_week and memory_level must be integers")
        week = integer(self.next_week, "next_week")
        if (week > 32 or self.phase not in ("week", "semifinal", "final") or
                (self.phase == "week") != (week < 32)):
            raise ValueError("run phase must match the absolute next week")
        if not isinstance(self.profile_id, str) or not self.profile_id:
            raise ValueError("profile_id required")
        if len(self.panels.live_skills) != 4:
            raise ValueError("run snapshot needs four actual equipped P skills")
        for name in ("sp", "stamina", "fans"):
            number(getattr(self, name), name)
        if self.stamina > 100 or not isinstance(self.route_feasible, bool):
            raise ValueError("invalid run stamina or route status")
        if any(not isinstance(k, str) or not k or number(v, "bond") > 100 for k, v in self.bonds.items()):
            raise ValueError("current bond must be in 0..100")
        if (len(set(self.events_completed)) != len(self.events_completed) or
                any(not isinstance(k, str) or ":" not in k for k in self.events_completed)):
            raise ValueError("events_completed needs unique qualified IDs")
        if any(not _passive_supported(p) for p in self.panels.passives):
            raise ValueError("run snapshot contains an unsupported actual passive")
        if integer(self.memory_level, "memory_level") > 5:
            raise ValueError("memory_level must be in 0..5")

    @property
    def id(self) -> str:
        return fingerprint(asdict(self))

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping) -> "RunState":
        fields = dict(value)
        panels = dict(fields["panels"])
        panels["live_skills"] = tuple(LiveSkill.from_dict(s) for s in panels["live_skills"])
        panels["passives"] = tuple(panels.get("passives", ()))
        panels["learned_panel_ids"] = tuple(panels.get("learned_panel_ids", ()))
        fields["panels"] = PanelValueState(**panels)
        fields["events_completed"] = tuple(fields.get("events_completed", ()))
        return cls(**fields)

    @classmethod
    def from_week_state(cls, profile: RunProfile, observed, *, panels: PanelValueState,
                        bonds: Mapping[str, float], memory_level: int,
                        events_completed: tuple[str, ...] = (), route_feasible: bool = True) -> "RunState":
        """Reader facts plus caller-verified equipment/bonds; missing facts cannot become defaults."""
        if dict(observed.stats) != dict(panels.stats) or observed.stat_caps != panels.caps:
            raise ValueError("observed stats/caps differ from the panel snapshot")
        if observed.memory_level is not None and observed.memory_level != memory_level:
            raise ValueError("observed memory level differs from the snapshot")
        if observed.phase == "week":
            if type(observed.season) is not int or type(observed.week) is not int:
                raise ValueError("observed absolute week is unknown")
            week = (observed.season - 1) * 8 + observed.week - 1
        else:
            week = 32
        result = cls(profile.id, week, panels, observed.sp, observed.stamina, observed.fans,
                     bonds, events_completed, route_feasible, observed.phase, memory_level)
        profile.bind(result)
        return result

    def growth_config(self, assumptions: GrowthConfig) -> GrowthConfig:
        """Keep future priors/actions, replace every starting fact with the snapshot."""
        return replace(assumptions, start_week=self.next_week,
                       starting_stats=self.panels.stats, starting_caps=self.panels.caps,
                       starting_live_skills=self.panels.live_skills,
                       starting_panel_ids=self.panels.learned_panel_ids,
                       starting_passives=self.panels.passives, starting_bonds=self.bonds,
                       starting_sp=self.sp, starting_stamina=self.stamina, starting_fans=self.fans,
                       starting_route_feasible=self.route_feasible,
                       events_completed=self.events_completed)
