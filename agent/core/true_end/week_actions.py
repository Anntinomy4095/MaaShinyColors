"""Offline observed-week branches for the shared terminal objective.

The caller declares preview semantics and supplies uncertain distributions. This
is not a native reader or a calibrated transition model; no live input is issued.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from typing import Mapping

from ..wing_state import RANKS, SEASON_FAN_GATES, WingState
from ..wing_score import work_type
from .models import ATTRIBUTES, STATS, integer, number
from .run_state import RunProfile, RunState, fingerprint

MODEL_VERSION = "wing-observed-week-v1"


def _probability(value: float) -> float:
    result = number(value, "probability")
    if result > 1:
        raise ValueError("probability must be in 0..1")
    return result


def _stats(values: Mapping, *, losses: bool = False) -> None:
    if set(values) - set(STATS):
        raise ValueError("effect stats must use Vo/Da/Vi/Me")
    for value in values.values():
        number(value, "stat effect", minimum=float("-inf") if losses else 0)


@dataclass(frozen=True)
class Reward:
    """Declared total stat/SP/fan gains; omitted stat keys explicitly mean zero."""
    stats: Mapping[str, float]
    sp: float
    fans: float

    def __post_init__(self):
        _stats(self.stats)
        number(self.sp, "reward SP")
        number(self.fans, "reward fans")


@dataclass(frozen=True)
class FailureOutcome:
    probability: float  # conditional on trouble, not a whole-week probability
    losses: Mapping[str, float]
    catastrophe: bool = False

    def __post_init__(self):
        _probability(self.probability)
        _stats(self.losses, losses=True)
        if any(v > 0 for v in self.losses.values()) or type(self.catastrophe) is not bool:
            raise ValueError("failure needs nonpositive losses and a catastrophe flag")


@dataclass(frozen=True)
class TrainingModel:
    perfect_given_success: float
    preview_is_total: bool  # including stat/SP mastery and Excellent; absent stat keys = zero
    preview_is_uncapped: bool  # doubling a clipped preview loses the original reward
    perfect_reward: Reward | None = None  # exact total when known; otherwise 2*integer preview
    failures: tuple[FailureOutcome, ...] = ()

    def __post_init__(self):
        _probability(self.perfect_given_success)
        if type(self.preview_is_total) is not bool or type(self.preview_is_uncapped) is not bool:
            raise ValueError("preview semantics must be explicit booleans")
        if self.failures and abs(sum(f.probability for f in self.failures) - 1) > 1e-9:
            raise ValueError("conditional failure probabilities must sum to one")


@dataclass(frozen=True)
class RestOutcome:
    probability: float
    recovery: float

    def __post_init__(self):
        _probability(self.probability)
        number(self.recovery, "rest recovery")


@dataclass(frozen=True)
class AuditionModel:
    pass_probability: float
    first_given_pass: float
    win_stats: Mapping[str, float]  # ordinary appeal-stat reward, excluding audition mastery
    loss_stats: Mapping[str, float]  # explicit losses, including an explicit empty mapping

    def __post_init__(self):
        _probability(self.pass_probability)
        _probability(self.first_given_pass)
        _stats(self.win_stats)
        _stats(self.loss_stats, losses=True)
        if set(self.win_stats) - set(ATTRIBUTES) or any(v > 0 for v in self.loss_stats.values()):
            raise ValueError("ordinary audition reward is appeal stats only; losses must be nonpositive")


@dataclass(frozen=True)
class EventOutcome:
    probability: float  # conditional on the named action outcome
    event_id: str | None
    reward: Reward
    cap_gain: Mapping[str, float] = field(default_factory=dict)
    stamina_gain: float = 0
    memory_level_after: int | None = None

    def __post_init__(self):
        _probability(self.probability)
        _stats(self.cap_gain)
        number(self.stamina_gain, "event stamina")
        if self.event_id is not None and (not isinstance(self.event_id, str) or ":" not in self.event_id):
            raise ValueError("event needs a qualified ID")
        if self.event_id is None and (self.reward != Reward({}, 0, 0) or self.cap_gain or
                                      self.stamina_gain or self.memory_level_after is not None):
            raise ValueError("a no-event branch cannot grant event effects")
        if self.memory_level_after is not None and integer(self.memory_level_after, "memory level") > 5:
            raise ValueError("memory level must be in 0..5")


@dataclass(frozen=True)
class WeekActionModel:
    assumptions: tuple[str, ...]  # data/probability provenance and preview interpretation
    training: Mapping[str, TrainingModel] = field(default_factory=dict)
    rest: Mapping[str, tuple[RestOutcome, ...]] = field(default_factory=dict)
    auditions: Mapping[str, AuditionModel] = field(default_factory=dict)
    events: Mapping[str, Mapping[str, tuple[EventOutcome, ...]]] = field(default_factory=dict)

    def __post_init__(self):
        if not self.assumptions or any(not isinstance(s, str) or not s for s in self.assumptions):
            raise ValueError("transition assumptions/provenance must be explicit")

    @property
    def id(self) -> str:
        return fingerprint({"version": MODEL_VERSION, **asdict(self)})


@dataclass(frozen=True)
class ActionBranch:
    label: str
    probability: float
    state: RunState


@dataclass(frozen=True)
class WeekSuccessors:
    profile_id: str
    state_id: str
    observation_id: str
    transition_model_id: str
    actions: Mapping[str, tuple[ActionBranch, ...]]
    excluded: Mapping[str, str]
    unknowns: tuple[str, ...]
    assumptions: tuple[str, ...]

    def score(self, evaluator, before: RunState) -> dict:
        if before.id != self.state_id or evaluator.profile.id != self.profile_id:
            raise ValueError("successor report belongs to a different snapshot/profile")
        result = evaluator.score_successors(before, {
            key: tuple((b.probability, b.state) for b in branches) for key, branches in self.actions.items()})
        for row in result["ranked"]:
            labels = [b.label for b in self.actions[row["action"]] if b.probability > 0]
            for branch, label in zip(row["branches"], labels):
                branch["outcome"] = label
        return {**result, "observation_id": self.observation_id,
                "transition_model_id": self.transition_model_id, "excluded": dict(self.excluded),
                "comparison_scope": "WEEK_ACTIONS_WITH_CURRENT_PANELS",
                "transition_assumptions": [*self.assumptions,
                    "本周固定传入的面板/四装备；周前学习需先更新快照，再比较行动。后继SP供后续学习，不能恢复本周溢出。",
                    "delta基线沿用终局评估器的成长计划；当前行动集合不包含周前购置，不能称全操作最优。"],
                "unknowns": sorted(set(result["unknowns"]) | set(self.unknowns))}


def _settle(state: RunState, reward: Reward, *, caps=None, stamina=None, bonds=None) -> RunState:
    new_caps = {a: state.panels.caps[a] + (caps or {}).get(a, 0) for a in STATS}
    stats = {a: min(new_caps[a], max(0, state.panels.stats[a] + reward.stats.get(a, 0))) for a in STATS}
    return replace(state, panels=replace(state.panels, stats=stats, caps=new_caps),
                   sp=state.sp + reward.sp, fans=state.fans + reward.fans,
                   stamina=state.stamina if stamina is None else min(100, max(0, stamina)),
                   bonds=dict(state.bonds if bonds is None else bonds))


def _loss(state: RunState, losses: Mapping, stamina: float) -> RunState:
    stats = {a: max(0, state.panels.stats[a] + losses.get(a, 0)) for a in STATS}
    return replace(state, panels=replace(state.panels, stats=stats), stamina=max(0, stamina))


def _room(option) -> str:
    if option.kind == "lesson" and option.attribute in ATTRIBUTES:
        return option.attribute
    if option.kind == "work" and work_type(option) is not None:
        return work_type(option)
    raise ValueError("room_type_unknown")


def _room_effects(profile: RunProfile, option, unknowns: set) -> tuple[dict, float]:
    training = {c.card.card_id: c for c in profile.cards[1:]}
    ids = option.support_ids
    if len(set(ids)) != len(ids) or set(ids) - set(training):
        raise ValueError("room_support_identity_invalid")
    if option.supports is None or len(ids) != option.supports:
        raise ValueError("room_support_identity_unread")
    room = _room(option)
    caps, recovery = dict.fromkeys(STATS, 0), 0
    for key in ids:
        card = training[key]
        unknowns.update(f"{key}:{u}" for u in (*card.unknowns, *card.unsupported))
        for skill, amount in card.support_effects:
            if skill.unsupported:
                unknowns.add(f"{key}:{skill.id}:unsupported_room_effect")
                continue
            if skill.kind.startswith(("mastery_", "unit_mastery_")):
                if skill.kind.startswith("mastery_") and skill.room is None:
                    unknowns.add(f"{key}:{skill.id}:mastery_room_unread")
                    continue
                if skill.room is not None and skill.room != room:
                    continue
            multiplier = 1
            if skill.kind.startswith("unit_mastery_"):
                if card.card.unit is None:
                    unknowns.add(f"{key}:{skill.id}:unit_unread")
                    continue
                multiplier = sum(training[k].card.unit == card.card.unit for k in ids if k != key)
            if skill.kind in ("mastery_cap", "unit_mastery_cap") and skill.attribute in STATS:
                caps[skill.attribute] += amount * multiplier
            elif skill.kind == "mastery_stamina":
                recovery += amount * 3
            elif skill.kind not in ("mastery_stat", "mastery_sp", "unit_mastery_stat", "unit_mastery_sp",
                                    "bond", "excellent_boost", "audition_mastery_stat", "audition_mastery_sp"):
                unknowns.add(f"{key}:{skill.id}:room_effect_not_modeled")
    if option.stamina_gain is not None:
        recovery = option.stamina_gain  # an observed total overrides, never adds to derived mastery
    elif option.stamina_recovery and recovery == 0:
        raise ValueError("room_stamina_recovery_unread")
    return caps, recovery


def _training(profile, before, option, model, unknowns):
    if not model.preview_is_total or not model.preview_is_uncapped:
        raise ValueError("normal_preview_must_be_total_uncapped")
    if not option.preview or any(getattr(option, k) is None for k in ("sp_gain", "fan_gain", "stamina_cost", "trouble_rate")):
        raise ValueError("training_reward_cost_or_trouble_unread")
    normal = Reward(dict(option.preview), option.sp_gain, option.fan_gain)
    caps, recovery = _room_effects(profile, option, unknowns)
    trouble = _probability(option.trouble_rate / 100)
    if trouble and not model.failures:
        raise ValueError("conditional_failure_losses_unread")
    branches = []
    success = 1 - trouble
    bonds = {k: min(100, v + (8 if k in option.support_ids else 0)) for k, v in before.bonds.items()}
    for label, probability, reward, factor in (
            ("normal", success * (1 - model.perfect_given_success), normal, 1),
            ("perfect", success * model.perfect_given_success, model.perfect_reward, 2)):
        if not probability:
            continue
        if reward is None:
            reward = Reward({a: 2 * g for a, g in normal.stats.items()}, 2 * normal.sp, 2 * normal.fans)
            unknowns.add(f"{option.id}:perfect_integer_preview_doubling_approximation")
        after = _settle(before, reward, caps={a: g * factor for a, g in caps.items()},
                        stamina=before.stamina - option.stamina_cost + recovery, bonds=bonds)
        branches.append(ActionBranch(label, probability, after))
    for i, failure in enumerate(model.failures):
        probability = trouble * failure.probability
        if probability:
            after = _loss(before, failure.losses, 0 if failure.catastrophe else before.stamina - option.stamina_cost)
            branches.append(ActionBranch(f"{'catastrophe' if failure.catastrophe else 'failure'}{i}", probability, after))
    return branches


def _audition(profile, before, audition, model, unknowns):
    mastery, sp = dict.fromkeys(STATS, 0), 0
    for card in profile.cards[1:]:  # Guest also contributes audition mastery, never its battle skill here
        unknowns.update(f"{card.card.card_id}:{u}" for u in (*card.unknowns, *card.unsupported))
        for skill, amount in card.support_effects:
            if skill.kind not in ("audition_mastery_stat", "audition_mastery_sp"):
                continue
            if skill.unsupported:
                unknowns.add(f"{card.card.card_id}:{skill.id}:unsupported_audition_mastery")
            elif skill.kind == "audition_mastery_sp":
                sp += amount
            elif skill.attribute in STATS:
                mastery[skill.attribute] += amount
    passed, first = model.pass_probability, model.first_given_pass
    branches = []
    for label, probability, is_first in (("first", passed * first, True), ("second", passed * (1 - first), False)):
        if probability:
            reward = Reward({a: model.win_stats.get(a, 0) + (mastery[a] if is_first else 0) for a in STATS},
                            sp if is_first else 0, audition.fan_reward)
            branches.append(ActionBranch(label, probability, _settle(before, reward)))
    if passed < 1:
        branches.append(ActionBranch("loss", 1 - passed, _loss(before, model.loss_stats, before.stamina)))
    return branches


def _distribution(outcomes) -> None:
    if not outcomes or abs(sum(o.probability for o in outcomes) - 1) > 1e-9:
        raise ValueError("outcome_probabilities_must_sum_to_one")


def _events(profile, branches, distributions):
    if set(distributions) - {b.label for b in branches}:
        raise ValueError("event_outcome_not_in_action")
    known = {f"{c.card.card_id}:{event['id']}" for c in profile.cards for event in c.card.events}
    result = []
    for branch in branches:
        events = distributions.get(branch.label)
        if events is None:
            result.append(branch)
            continue
        _distribution(events)
        for i, event in enumerate(events):
            if event.event_id is not None:
                if event.event_id not in known:
                    raise ValueError("event_not_in_profile")
                if event.event_id in branch.state.events_completed:
                    raise ValueError("event_already_completed")
            if not event.probability:
                continue
            after = _settle(branch.state, event.reward, caps=event.cap_gain,
                            stamina=branch.state.stamina + event.stamina_gain)
            after = replace(after, events_completed=(*after.events_completed, event.event_id) if event.event_id else after.events_completed,
                            memory_level=after.memory_level if event.memory_level_after is None else event.memory_level_after)
            result.append(ActionBranch(f"{branch.label}/event{i}", branch.probability * event.probability, after))
    return result


def build_week_successors(profile: RunProfile, before: RunState, observed: WingState,
                          model: WeekActionModel) -> WeekSuccessors:
    """Consume exactly one week; incomplete actions are excluded with concrete reasons.

    This action set holds the current panels/equipment fixed. Complete any desired
    pre-week purchases first and pass the updated snapshot; do not interpret this
    ranking as optimization over purchases plus schedule actions.

    Preview total/uncapped semantics and every probability come from the caller's
    explicit assumptions. Tension, affection, facility trajectories and unspecified
    events remain unmodeled. All branches are estimates, never execution authority.
    """
    profile.bind(before)
    if not before.route_feasible or before.phase != "week" or observed.phase != "week" or observed.validate():
        raise ValueError("successors need a recognized active WING week")
    if (observed.season not in range(1, 5) or observed.week not in range(1, 9) or
            observed.max_stamina not in (None, 100)):
        raise ValueError("observed WING week/stamina scale is unsupported")
    rebound = RunState.from_week_state(profile, observed, panels=before.panels, bonds=before.bonds,
                                      memory_level=before.memory_level, events_completed=before.events_completed)
    if rebound.id != before.id:
        raise ValueError("observed week/resources differ from the run snapshot")
    ids = {o.id for o in (*observed.options, *observed.auditions)}
    if (set(model.training) | set(model.rest) | set(model.auditions) | set(model.events)) - ids:
        raise ValueError("transition model contains an action absent from this observation")
    actions, excluded = {}, {}
    unknowns = {"tension_affection_facility_trajectories_not_modeled", "unspecified_post_action_events_not_modeled",
                "rest_conditional_support_effects_not_modeled"}
    if observed.promise:
        unknowns.add("promise_tension_affection_changes_not_modeled")
    for option in (*observed.options, *observed.auditions):
        key, local_unknowns = option.id, set()
        try:
            if not option.available:
                raise ValueError("unavailable")
            if option in observed.auditions:
                if option.min_fans is None:
                    raise ValueError("audition_fan_gate_unread")
                if before.fans < option.min_fans:
                    raise ValueError("audition_fan_gate")
                if option.required_rank is not None:
                    if observed.rank not in RANKS:
                        raise ValueError("audition_rank_unread")
                    if RANKS.index(observed.rank) < RANKS.index(option.required_rank):
                        raise ValueError("audition_rank_gate")
                if key not in model.auditions:
                    raise ValueError("audition_outcomes_unread")
                branches = _audition(profile, before, option, model.auditions[key], local_unknowns)
            elif option.kind in ("lesson", "work"):
                if key not in model.training:
                    raise ValueError("training_model_unread")
                branches = _training(profile, before, option, model.training[key], local_unknowns)
            elif option.kind == "rest":
                outcomes = model.rest.get(key, ())
                _distribution(outcomes)
                branches = [ActionBranch(f"rest{i}", r.probability,
                                         _settle(before, Reward({}, 0, 0), stamina=before.stamina + r.recovery))
                            for i, r in enumerate(outcomes) if r.probability]
            else:
                raise ValueError("action_kind_not_modeled")
            branches = _events(profile, branches, model.events.get(key, {}))
            if not 1 <= len(branches) <= 8:
                raise ValueError("action_exceeds_eight_branch_budget")
            settled = []
            next_week = before.next_week + 1
            for branch in branches:
                route = before.route_feasible
                if next_week % 8 == 0:
                    route = route and branch.state.fans >= SEASON_FAN_GATES[next_week // 8]
                after = replace(branch.state, next_week=next_week, route_feasible=route,
                                phase="semifinal" if next_week == 32 else "week")
                profile.bind(after)
                settled.append(replace(branch, state=after))
            actions[key] = tuple(settled)
            unknowns.update(local_unknowns)
        except ValueError as error:
            excluded[key] = str(error)
    if len(actions) > 16:
        raise ValueError("week exceeds sixteen action budget")
    return WeekSuccessors(profile.id, before.id, fingerprint(observed.to_dict()), model.id, actions, excluded,
                          tuple(sorted(unknowns)), model.assumptions)
