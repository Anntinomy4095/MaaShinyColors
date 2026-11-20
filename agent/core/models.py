from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any, Mapping, Sequence


ATTRIBUTES = ("vocal", "dance", "visual")


def _number(
    data: Mapping[str, Any],
    key: str,
    default: float = 0.0,
) -> float:
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{key} must be a number")
    try:
        result = float(value)
    except OverflowError as exc:
        raise ValueError(f"{key} must be finite") from exc
    if not math.isfinite(result):
        raise ValueError(f"{key} must be finite")
    return result


def _integer(data: Mapping[str, Any], key: str, default: int = 0) -> int:
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{key} must be a nonnegative integer")
    return value


def _boolean(data: Mapping[str, Any], key: str, default: bool) -> bool:
    value = data.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a boolean")
    return value


def _confidence(data: Mapping[str, Any]) -> float:
    value = _number(data, "confidence", 1.0)
    if not 0 <= value <= 1:
        raise ValueError("confidence must be within [0, 1]")
    return value


@dataclass(frozen=True)
class ProduceState:
    turn: int
    turns_to_exam: int
    stamina: float
    max_stamina: float
    stats: Mapping[str, float]
    targets: Mapping[str, float]
    mental: float = 0.0
    confidence: float = 1.0
    recognized: bool = True

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ProduceState":
        if not isinstance(data, Mapping):
            raise ValueError("state must be an object")
        stats = data.get("stats", {})
        targets = data.get("targets", {})
        if not isinstance(stats, Mapping) or not isinstance(targets, Mapping):
            raise ValueError("stats and targets must be objects")

        state = cls(
            turn=_integer(data, "turn"),
            turns_to_exam=_integer(data, "turns_to_exam", 99),
            stamina=_number(data, "stamina"),
            max_stamina=_number(data, "max_stamina"),
            stats={key: _number(stats, key) for key in ATTRIBUTES},
            targets={key: _number(targets, key) for key in ATTRIBUTES},
            mental=_number(data, "mental"),
            confidence=_confidence(data),
            recognized=_boolean(data, "recognized", True),
        )
        if state.max_stamina <= 0:
            raise ValueError("max_stamina must be positive")
        if not 0 <= state.stamina <= state.max_stamina:
            raise ValueError("stamina must be within [0, max_stamina]")
        if any(value < 0 for value in (*state.stats.values(), *state.targets.values(), state.mental)):
            raise ValueError("stats, targets and mental cannot be negative")
        return state


@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    label: str
    category: str
    gains: Mapping[str, float] = field(default_factory=dict)
    stamina_cost: float = 0.0
    stamina_recovery: float = 0.0
    mental_gain: float = 0.0
    risk: float = 0.0
    priority_bonus: float = 0.0
    available: bool = True
    confidence: float = 1.0

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Candidate":
        if not isinstance(data, Mapping):
            raise ValueError("candidate must be an object")
        gains = data.get("gains", {})
        if not isinstance(gains, Mapping):
            raise ValueError("candidate gains must be an object")
        candidate_id = data.get("id", "")
        if not isinstance(candidate_id, str):
            raise ValueError("candidate id must be a string")
        candidate = cls(
            candidate_id=candidate_id.strip(),
            label=str(data.get("label", data.get("id", ""))).strip(),
            category=str(data.get("category", "other")).strip(),
            gains={key: _number(gains, key) for key in ATTRIBUTES},
            stamina_cost=_number(data, "stamina_cost"),
            stamina_recovery=_number(data, "stamina_recovery"),
            mental_gain=_number(data, "mental_gain"),
            risk=_number(data, "risk"),
            priority_bonus=_number(data, "priority_bonus"),
            available=_boolean(data, "available", True),
            confidence=_confidence(data),
        )
        if not candidate.candidate_id:
            raise ValueError("candidate id is required")
        if candidate.stamina_cost < 0 or candidate.stamina_recovery < 0:
            raise ValueError("stamina values cannot be negative")
        if not 0 <= candidate.risk <= 1:
            raise ValueError("risk must be within [0, 1]")
        return candidate


@dataclass(frozen=True)
class CandidateScore:
    candidate: Candidate
    score: float
    contributions: Mapping[str, float]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.candidate.candidate_id,
            "label": self.candidate.label,
            "category": self.candidate.category,
            "score": round(self.score, 3),
            "contributions": {
                key: round(value, 3)
                for key, value in self.contributions.items()
            },
        }


@dataclass(frozen=True)
class Decision:
    profile: str
    chosen: CandidateScore
    ranking: Sequence[CandidateScore]
    rationale: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "chosen": self.chosen.to_dict(),
            "ranking": [item.to_dict() for item in self.ranking],
            "rationale": self.rationale,
        }


@dataclass(frozen=True)
class Scenario:
    state: ProduceState
    candidates: Sequence[Candidate]

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Scenario":
        if not isinstance(data, Mapping):
            raise ValueError("scenario must be an object")
        raw_candidates = data.get("candidates", [])
        if not isinstance(raw_candidates, Sequence) or isinstance(
            raw_candidates,
            (str, bytes),
        ):
            raise ValueError("candidates must be an array")
        candidates = tuple(Candidate.from_dict(item) for item in raw_candidates)
        if not candidates:
            raise ValueError("at least one candidate is required")
        ids = [item.candidate_id for item in candidates]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate candidate id")
        raw_state = data.get("state")
        if not isinstance(raw_state, Mapping):
            raise ValueError("state must be an object")
        return cls(
            state=ProduceState.from_dict(raw_state),
            candidates=candidates,
        )
