"""Bounded conditional routes: replace declared future audition slots in place."""
from __future__ import annotations

from dataclasses import dataclass, replace
from itertools import combinations
from typing import Any, Mapping

from .growth import GrowthConfig
from .models import ROOMS, integer, number
from .run_state import fingerprint


@dataclass(frozen=True)
class FanRoutePlan:
    actions: tuple[Mapping[str, Any], ...]
    replaced_weeks: tuple[int, ...]
    remaining_audition_weeks: tuple[int, ...]

    @property
    def id(self) -> str:
        return fingerprint({"actions": self.actions, "replaced_weeks": self.replaced_weeks})


@dataclass(frozen=True)
class FanRoutePlans:
    plans: tuple[FanRoutePlan, ...]
    total_possible: int
    search_complete: bool


@dataclass(frozen=True)
class FanRoutePolicy:
    """One explicit room/rest fallback per editable absolute week, not all actions.

    Every retained audition still assumes the declared future win/reward/unlock.
    Actual growth handles fan gates, fatigue and SP separately for every route.
    No relocation, historical edits, invented auditions or future observations.
    """
    fallbacks: Mapping[int, Mapping[str, Any]]
    assumptions: tuple[str, ...]
    max_plans: int = 64

    def __post_init__(self):
        if not self.assumptions or any(not isinstance(s, str) or not s for s in self.assumptions):
            raise ValueError("fan route assumptions must be explicit")
        if integer(self.max_plans, "max_plans", minimum=2) > 64:
            raise ValueError("max_plans must be 2..64")
        for week, fallback in self.fallbacks.items():
            if type(week) is not int or not 0 <= week < 32:
                raise ValueError("fallback week must be an absolute integer in 0..31")
            if (fallback.get("action") not in (*ROOMS, "rest") or
                    set(fallback) - {"action", "facility_level", "source"}):
                raise ValueError("fallback must declare a room/rest, not audition rewards")
            if "facility_level" in fallback and (type(fallback["facility_level"]) is not int or
                                                  not 1 <= fallback["facility_level"] <= 5):
                raise ValueError("fallback facility_level must be 1..5")

    def candidates(self, config: GrowthConfig) -> FanRoutePlans:
        if len(config.actions) != 32:
            raise ValueError("fan replanning requires an explicit 32-week action table")
        remaining = tuple(w for w in range(config.start_week, 32) if config.actions[w]["action"] == "audition")
        # Validate the ORIGINAL route too: GrowthConfig's legacy defaults cannot
        # silently turn an unread fan gate or reward into a successful route.
        for week in remaining:
            action = config.actions[week]
            number(action.get("fans"), f"week {week} future audition fans", minimum=1)
            number(action.get("required_fans"), f"week {week} future audition fan gate")
        editable = tuple(w for w in remaining if w in self.fallbacks) if config.starting_route_feasible else ()
        total = 2 ** len(editable)
        plans, seen = [], set()

        def add(removed):
            if removed in seen:
                return
            seen.add(removed)
            actions = tuple(dict(self.fallbacks[w] if w in removed else row) for w, row in enumerate(config.actions))
            plans.append(FanRoutePlan(actions, removed, tuple(w for w in remaining if w not in removed)))

        add(())  # original always occupies the first slot
        add(editable)  # all declared replacements always get evaluated, even under a small budget
        for count in range(len(editable) - 1, 0, -1):
            for removed in combinations(editable, count):
                if len(plans) == self.max_plans:
                    return FanRoutePlans(tuple(plans), total, len(plans) == total)
                add(removed)
        return FanRoutePlans(tuple(plans), total, len(plans) == total)

    @staticmethod
    def apply(config: GrowthConfig, plan: FanRoutePlan) -> GrowthConfig:
        return replace(config, actions=plan.actions)
