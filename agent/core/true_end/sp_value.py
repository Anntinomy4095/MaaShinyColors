"""Bounded, offline SP marginal value using the growth model's reviewed panel paths.

The caller supplies one pure terminal-value function in PEP units. It sees actual
purchased effects, never unspent SP. This is an adapter, not a calibrated TE probability
or a model of unknown future rooms/events. No Maa or live-input dependency.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
import heapq
import json
from typing import Callable, Mapping

from .growth import _equip, _signature, panel_closure
from .models import ATTRIBUTES, STATS, CardFeatures, LiveSkill, integer, number
from .skill_identity import mastered_identities, panel_skill_identity


@dataclass(frozen=True)
class PanelValueState:
    """Observed stats/caps, equipped P skills, owned passives and qualified panel IDs.

    Stats are effective values: raising a cap never recovers previously lost overflow.
    Already-purchased effects must already be included in this snapshot.
    """
    stats: Mapping[str, float]
    caps: Mapping[str, float]
    live_skills: tuple[LiveSkill, ...]
    passives: tuple[Mapping, ...] = ()
    learned_panel_ids: tuple[str, ...] = ()
    mastered_skills: tuple[str, ...] = ()

    def __post_init__(self):
        if set(self.stats) != set(STATS) or set(self.caps) != set(STATS):
            raise ValueError("SP value state needs all four observed stats and caps")
        for a in STATS:
            if number(self.stats[a], a) > number(self.caps[a], f"cap.{a}"):
                raise ValueError("effective stat exceeds cap")
        if (len(self.live_skills) > 4 or
                len({_signature(s) for s in self.live_skills}) != len(self.live_skills)):
            raise ValueError("SP value state needs at most four distinct equipped skills")
        if (any(not isinstance(k, str) or not k for k in self.learned_panel_ids) or
                len(set(self.learned_panel_ids)) != len(self.learned_panel_ids)):
            raise ValueError("learned panel IDs must be unique qualified strings")
        if any(not isinstance(k, str) or not k for k in self.mastered_skills):
            raise ValueError("mastered skills must contain strict identity keys")


@dataclass(frozen=True)
class PanelBudgetPlan:
    state: PanelValueState
    value_pep: float
    spent: int
    purchases: tuple[str, ...]
    evaluated_states: int
    search_complete: bool
    unknowns: tuple[str, ...] = ()

    def summary(self) -> dict:
        return {"value_pep": self.value_pep, "spent": self.spent,
                "purchases": list(self.purchases), "evaluated_states": self.evaluated_states,
                "search_complete": self.search_complete, "unknowns": list(self.unknowns)}


@dataclass(frozen=True)
class SPValueContext:
    """One bound panel snapshot and terminal evaluator, shared by every action branch.

    ``value_pep`` must value terminal stats/actual skills/caps/passives in the scorer's
    PEP units, excluding unspent SP. A ProxyTERating is a different scale.
    Growth after acquisition, including whether a cap can still help, is the caller's
    explicit projection. The evaluator must be deterministic (reuse seeds for battles).
    Equipment selection reuses growth's four-slot heuristic; it is not exhaustive.
    """
    cards: tuple[CardFeatures, ...]
    state: PanelValueState
    primary: str
    value_pep: Callable[[PanelValueState], float] = field(repr=False, compare=False)
    events_completed: tuple[str, ...] = ()
    max_states: int = 128

    def __post_init__(self):
        if (self.primary not in ATTRIBUTES or not callable(self.value_pep) or
                type(self.max_states) is not int or not 1 <= self.max_states <= 4096):
            raise ValueError("invalid SP value primary, evaluator or state budget")
        if (len(self.cards) > 6 or len({c.card.card_id for c in self.cards}) != len(self.cards) or
                len({c.card.idol for c in self.cards}) != len(self.cards)):
            raise ValueError("SP value cards must have distinct card and idol identities")
        known = {f"{c.card.card_id}:{p.id}" for c in self.cards for p in c.panels}
        if set(self.state.learned_panel_ids) - known:
            raise ValueError("learned panel does not belong to this owned card profile")


def _state_key(state: PanelValueState) -> str:
    return json.dumps(asdict(state), ensure_ascii=False, sort_keys=True, allow_nan=False)


def _apply_path(state: PanelValueState, feature: CardFeatures, path, primary: str, cards=()):
    learned = set(state.learned_panel_ids)
    stats, caps = dict(state.stats), dict(state.caps)
    skills, passives = state.live_skills, list(state.passives)
    mastered = set(state.mastered_skills) | mastered_identities(cards or (feature,), learned, skills)
    purchases, spent = [], 0
    for panel in path:
        key = f"{feature.card.card_id}:{panel.id}"
        if key in learned:
            continue
        identity = panel_skill_identity(panel)
        duplicate = identity is not None and identity in mastered
        if panel.kind == "live" and not duplicate:
            skills = _equip(skills, LiveSkill.from_dict(panel.effects), (primary,))
        elif panel.kind == "cap":
            for a, amount in panel.effects.items():
                caps[a] += amount
        elif panel.kind == "stat":
            for a, amount in panel.effects.items():
                stats[a] = min(caps[a], stats[a] + amount)
        elif panel.kind == "passive":
            passives.append({**panel.effects, "id": key})
        learned.add(key)
        purchases.append(key)
        spent += 0 if duplicate else panel.sp_cost
        if identity is not None:
            mastered.add(identity)
    result = PanelValueState(stats, caps, skills, tuple(passives), tuple(sorted(learned)), tuple(sorted(mastered)))
    return result, spent, tuple(purchases)


def best_panel_plan(context: SPValueContext, budget: int, *,
                    incumbent: PanelBudgetPlan | None = None) -> PanelBudgetPlan:
    """Best evaluated affordable panel portfolio, including choosing no purchase.

    Full-path effects/costs are applied in legal order, sharing paid predecessors.
    The optional incumbent is a feasible plan returned for this exact context at a
    smaller budget; retaining it avoids artificial negative marginals on truncation.
    """
    budget = integer(budget, "SP budget")
    value = number(context.value_pep(context.state), "terminal value PEP", minimum=-float("inf"))
    unknowns = {f"{c.card.card_id}:{u}" for c in context.cards
                for u in (*c.unknowns, *c.unsupported)}
    targets = [(c, p) for c in context.cards for p in c.panels]
    for c, p in targets:
        paid = tuple(q.id for q in c.panels if f"{c.card.card_id}:{q.id}" in context.state.learned_panel_ids)
        mastered = set(context.state.mastered_skills) | mastered_identities(context.cards, context.state.learned_panel_ids,
                                                                           context.state.live_skills)
        if panel_closure(c, p.id, context.events_completed, paid, mastered) is None:
            unknowns.add(f"{c.card.card_id}:{p.id}:path_or_effect_unavailable")
    if any(c.card.panel_topology is not None and not c.card.panel_topology["complete"] for c in context.cards):
        unknowns.add("partial_panel_topology")
    best = PanelBudgetPlan(context.state, value, 0, (), 1, True, tuple(sorted(unknowns)))
    evaluated = 1
    if incumbent is not None:
        if incumbent.spent > budget:
            raise ValueError("SP incumbent exceeds budget")
        # Replay against this profile; an old team's plan cannot seed a new search.
        current, spent = context.state, 0
        for key in incumbent.purchases:
            found = next(((c, p) for c, p in targets if f"{c.card.card_id}:{p.id}" == key), None)
            if found is None:
                raise ValueError("SP incumbent belongs to another profile")
            c, p = found
            paid = tuple(q.id for q in c.panels if f"{c.card.card_id}:{q.id}" in current.learned_panel_ids)
            mastered = set(current.mastered_skills) | mastered_identities(context.cards, current.learned_panel_ids,
                                                                         current.live_skills)
            path = panel_closure(c, p.id, context.events_completed, paid, mastered)
            if path is None:
                raise ValueError("SP incumbent path is unavailable")
            current, cost, _ = _apply_path(current, c, path, context.primary, context.cards)
            spent += cost
        if spent != incumbent.spent or _state_key(current) != _state_key(incumbent.state):
            raise ValueError("SP incumbent does not match current effects")
        if incumbent.purchases:
            if evaluated >= context.max_states:
                raise ValueError("SP state budget cannot re-evaluate a purchased incumbent")
            evaluated += 1
            incumbent_value = number(context.value_pep(current), "terminal value PEP", minimum=-float("inf"))
        else:
            incumbent_value = value
        if incumbent_value > best.value_pep:
            best = replace(best, state=current, value_pep=incumbent_value,
                           spent=spent, purchases=incumbent.purchases)

    serial = 0
    frontier = [(-value, 0, serial, context.state, ())]
    visited = {_state_key(context.state)}
    complete = True
    while frontier:
        _, spent, _, current, purchases = heapq.heappop(frontier)
        for feature, panel in targets:
            key = f"{feature.card.card_id}:{panel.id}"
            if key in current.learned_panel_ids:
                continue
            paid = tuple(p.id for p in feature.panels
                         if f"{feature.card.card_id}:{p.id}" in current.learned_panel_ids)
            mastered = set(current.mastered_skills) | mastered_identities(context.cards, current.learned_panel_ids,
                                                                         current.live_skills)
            path = panel_closure(feature, panel.id, context.events_completed, paid, mastered)
            if path is None:
                continue
            after, cost, extra = _apply_path(current, feature, path, context.primary, context.cards)
            if spent + cost > budget:
                continue
            state_key = _state_key(after)
            if state_key in visited:
                continue
            if evaluated >= context.max_states:
                complete = False
                frontier.clear()
                break
            visited.add(state_key)
            evaluated += 1
            after_value = number(context.value_pep(after), "terminal value PEP", minimum=-float("inf"))
            total, sequence = spent + cost, (*purchases, *extra)
            if (after_value, -total, sequence) > (best.value_pep, -best.spent, best.purchases):
                best = replace(best, state=after, value_pep=after_value, spent=total, purchases=sequence)
            serial += 1
            heapq.heappush(frontier, (-after_value, total, serial, after, sequence))
    return replace(best, evaluated_states=evaluated, search_complete=complete)


def marginal_sp_value(context: SPValueContext, sp_before: int, sp_gain: int) -> dict:
    """Acquired-effect value at two budgets; excess/unusable SP gets no bonus."""
    sp_before, sp_gain = integer(sp_before, "SP before"), integer(sp_gain, "SP gain")
    before = best_panel_plan(context, sp_before)
    after = before if sp_gain == 0 else best_panel_plan(context, sp_before + sp_gain, incumbent=before)
    return {"marginal_pep": after.value_pep - before.value_pep,
            "before": before.summary(), "after": after.summary(),
            "search_complete": before.search_complete and after.search_complete,
            "unknowns": sorted(set(before.unknowns) | set(after.unknowns))}
