from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Sequence

from agent.core.models import (
    ATTRIBUTES,
    Candidate,
    CandidateScore,
    Decision,
    ProduceState,
)


@dataclass(frozen=True)
class PolicyProfile:
    name: str
    gap_weight: float
    gain_weight: float
    stamina_penalty: float
    reserve_ratio: float
    risk_aversion: float
    mental_weight: float
    recovery_weight: float


PROFILES = {
    "balanced": PolicyProfile(
        name="balanced",
        gap_weight=1.25,
        gain_weight=1.0,
        stamina_penalty=1.0,
        reserve_ratio=0.30,
        risk_aversion=0.65,
        mental_weight=0.55,
        recovery_weight=0.75,
    ),
    "score": PolicyProfile(
        name="score",
        gap_weight=1.75,
        gain_weight=1.15,
        stamina_penalty=0.65,
        reserve_ratio=0.20,
        risk_aversion=0.40,
        mental_weight=0.45,
        recovery_weight=0.45,
    ),
    "safe": PolicyProfile(
        name="safe",
        gap_weight=1.0,
        gain_weight=0.85,
        stamina_penalty=1.55,
        reserve_ratio=0.42,
        risk_aversion=1.20,
        mental_weight=0.65,
        recovery_weight=1.30,
    ),
}


class ProducePolicy:
    """Deterministic, explainable baseline policy.

    This is intentionally game-data agnostic. Recognition converts a scene into
    ProduceState/Candidate values; this class only ranks those candidates.
    """

    def __init__(
        self,
        profile: str = "balanced",
        *,
        low_stamina_threshold: float | None = None,
        min_confidence: float = 0.95,
    ) -> None:
        for name, value in (("low_stamina_threshold", low_stamina_threshold), ("min_confidence", min_confidence)):
            if value is None and name == "low_stamina_threshold":
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{name} must be a finite number")
            try:
                finite = math.isfinite(value)
            except OverflowError:
                finite = False
            if not finite:
                raise ValueError(f"{name} must be a finite number")
        if low_stamina_threshold is not None and low_stamina_threshold < 0:
            raise ValueError("low_stamina_threshold cannot be negative")
        if not 0 <= min_confidence <= 1:
            raise ValueError("min_confidence must be within [0, 1]")
        self.low_stamina_threshold = low_stamina_threshold
        self.min_confidence = min_confidence
        try:
            self.profile = PROFILES[profile]
        except KeyError as exc:
            supported = ", ".join(sorted(PROFILES))
            raise ValueError(
                f"unknown profile {profile!r}; choose one of: {supported}"
            ) from exc

    def decide(
        self,
        state: ProduceState,
        candidates: Sequence[Candidate],
    ) -> Decision:
        if not candidates:
            raise ValueError("cannot decide without candidates")
        if not state.recognized or state.confidence < self.min_confidence:
            raise ValueError("state is unknown or low confidence")
        ids = [candidate.candidate_id for candidate in candidates]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate candidate id")
        available = [candidate for candidate in candidates if candidate.available]
        if not available:
            raise ValueError("cannot decide without available candidates")
        if any(candidate.confidence < self.min_confidence for candidate in available):
            raise ValueError("candidate is low confidence")

        scored = [
            self._score_candidate(state, candidate)
            for candidate in available
        ]
        # Python sorting is stable: equal scores keep the user-visible order.
        ranking = tuple(sorted(scored, key=lambda item: item.score, reverse=True))
        chosen = ranking[0]
        rationale = self._rationale(state, chosen)
        return Decision(
            profile=self.profile.name,
            chosen=chosen,
            ranking=ranking,
            rationale=rationale,
        )

    def _score_candidate(
        self,
        state: ProduceState,
        candidate: Candidate,
    ) -> CandidateScore:
        contributions: dict[str, float] = {}

        for attribute in ATTRIBUTES:
            current = float(state.stats.get(attribute, 0.0))
            target = max(float(state.targets.get(attribute, 0.0)), 1.0)
            gap_ratio = max(target - current, 0.0) / target
            gain = float(candidate.gains.get(attribute, 0.0))
            weight = self.profile.gain_weight * (
                1.0 + self.profile.gap_weight * gap_ratio
            )
            contributions[f"gain_{attribute}"] = gain * weight

        reserve = (
            self.profile.reserve_ratio * state.max_stamina
            if self.low_stamina_threshold is None
            else self.low_stamina_threshold
        )
        after_stamina = (
            state.stamina
            - candidate.stamina_cost
            + candidate.stamina_recovery
        )
        low_stamina_severity = max(reserve - state.stamina, 0.0) / max(reserve, 0.01)
        reserve_shortfall = max(reserve - after_stamina, 0.0)

        contributions["mental"] = (
            candidate.mental_gain * self.profile.mental_weight
        )
        contributions["recovery"] = (
            candidate.stamina_recovery
            * self.profile.recovery_weight
            * (1.0 + low_stamina_severity)
        )
        contributions["stamina_cost"] = -(
            candidate.stamina_cost * self.profile.stamina_penalty
            + reserve_shortfall * self.profile.stamina_penalty * 1.5
        )

        exam_multiplier = 1.5 if state.turns_to_exam <= 2 else 1.0
        contributions["risk"] = -(
            candidate.risk
            * 100.0
            * self.profile.risk_aversion
            * exam_multiplier
        )
        contributions["priority_bonus"] = candidate.priority_bonus

        if not all(math.isfinite(value) for value in contributions.values()) or not math.isfinite(sum(contributions.values())):
            raise ValueError("candidate score must be finite")
        return CandidateScore(
            candidate=candidate,
            score=sum(contributions.values()),
            contributions=contributions,
        )

    def _rationale(
        self,
        state: ProduceState,
        chosen: CandidateScore,
    ) -> str:
        positive = sorted(
            (
                (key, value)
                for key, value in chosen.contributions.items()
                if value > 0
            ),
            key=lambda item: item[1],
            reverse=True,
        )
        negative = sorted(
            (
                (key, value)
                for key, value in chosen.contributions.items()
                if value < 0
            ),
            key=lambda item: item[1],
        )
        best_gain = positive[0][0] if positive else "none"
        biggest_cost = negative[0][0] if negative else "none"
        return (
            f"选择 {chosen.candidate.label}；主要收益={best_gain}，"
            f"主要代价={biggest_cost}，体力={state.stamina:.0f}/"
            f"{state.max_stamina:.0f}，距考核={state.turns_to_exam} 回合。"
            + (
                f"低体力阈值={self.low_stamina_threshold:g}（绝对体力值）；"
                "低于阈值增强恢复收益，并按行动后体力缺口扣分。"
                if self.low_stamina_threshold is not None
                else ""
            )
        )
