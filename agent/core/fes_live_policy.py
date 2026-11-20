"""Grade Fes live play: pure, deterministic turn policy (no Maa import, no input, no screen reading).

Evidence (manual live 2026-10-03): each turn the player taps one of three skill
cards (or the memory appeal), then a judge (Vo./Da./Vi.), then taps once on the moving APPEAL timing bar
(white = Perfect, orange = Good, cyan = Normal). Judges' interest is per unit; a judge reaches 100 % -> 審査終了 and
the unit gets Last Appeal / Top Appeal / Bonus stars weighted by the judge's trend (流行 1 ★2倍, 2 ★1.5倍, 3 ×1).
While a card is selected the judges show the predicted interest after the appeal (e.g. 「100%」 = would finish).

The policy is a transparent heuristic, not a simulator. Every weight lives in ``PARAMS`` so it can be tuned from
logged turns; ``decide`` / ``choose_target`` return the scores they used so the run log explains each choice.
Ties are broken deterministically (higher trend, then lower judge index / card slot).
"""
from __future__ import annotations

import dataclasses

ATTRIBUTES = ("Vo", "Da", "Vi")
APPEALS = ATTRIBUTES + ("All",)
MAX_TURNS = 7

# Tunable parameters (keep in one table; for the source of each value).
PARAMS = {
    "attribute_match": 2.0,       # Excellent!! when the appeal attribute matches the judge (wiki: ×2.0)
    "attribute_mismatch": 1.0,    # other judges still take the appeal (observed: Vo card finished the Vi judge)
    "unknown_appeal": 1.0,        # card attribute not classified: no attribute bonus anywhere
    "trend_multiplier": {1: 2.0, 2: 1.5, 3: 1.0},   # fallback when the ★N倍 badge was not read
    "last_appeal_stars": {1: 12, 2: 9, 3: 6},       # wiki: LA stars by trend rank (relative value only)
    "remaining_weight": 0.5,      # prefer judges already closer to 100 % (last hit): × (1 + w × interest/100)
    "link_bonus": 0.25,           # card shows LINK✦ (extra +500 行動評価 and a follow-up appeal when it triggers)
    "plus_bonus": 0.15,           # card shows PLUS✦
    "finish_weight": 1.0,         # target stage: value of a predicted finish = weight × LA stars of the judge
    "gain_weight": 0.05,          # target stage: value per predicted interest point when nothing finishes
    "memory_min_gauge": 100,      # memory appeal only with a full gauge
    "memory_finish_interest": 50,  # memory when the top-trend alive judge (or two alive judges) reached this
    "memory_min_turn": 3,         # ... and not before this turn unless it is the last turn
}


@dataclasses.dataclass(frozen=True)
class Judge:
    attribute: str              # Vo / Da / Vi (fixed screen order: top, middle, bottom)
    alive: bool                 # False: 審査終了
    interest: float | None      # current interest % of this unit, e.g. 69.2 (None when finished: 審査終了)
    rank: int                   # 流行 rank 1..3
    multiplier: float           # star multiplier (★2倍 -> 2.0, ★1.5倍 -> 1.5, none -> 1.0)


@dataclasses.dataclass(frozen=True)
class Card:
    slot: int                   # 1..3 from the left
    appeal: str | None          # Vo / Da / Vi / All or a dual "Vo/Da" icon, None when not classified
    link: bool = False
    plus: bool = False


@dataclasses.dataclass(frozen=True)
class TurnState:
    turn: int
    judges: tuple               # three Judge, Vo/Da/Vi order
    cards: tuple                # three Card
    memory_gauge: int | None    # memory appeal gauge %, None when unreadable
    max_turns: int = MAX_TURNS

    def alive(self):
        return [j for j in self.judges if j.alive]

    def signature(self):
        return (self.turn, tuple(dataclasses.astuple(j) for j in self.judges),
                tuple(dataclasses.astuple(c) for c in self.cards), self.memory_gauge)


@dataclasses.dataclass(frozen=True)
class Decision:
    kind: str                   # "card" | "memory" | "none"
    slot: int | None            # card slot (None for memory / none)
    target: str | None          # planned judge attribute
    scores: dict                # explanation: per card per judge, memory rule
    reasons: tuple

    def to_dict(self):
        return {"kind": self.kind, "slot": self.slot, "target": self.target, "scores": self.scores,
                "reasons": list(self.reasons)}


def validate(state):
    """Reasons the state is not usable (empty list: usable)."""
    reasons = []
    if not (isinstance(state.turn, int) and 1 <= state.turn <= state.max_turns):
        reasons.append("turn_out_of_range")
    if len(state.judges) != 3 or [j.attribute for j in state.judges] != list(ATTRIBUTES):
        reasons.append("judges_incomplete")
    elif sorted(j.rank for j in state.judges) != [1, 2, 3]:
        reasons.append("trend_ranks_not_a_permutation")
    if any(j.alive and (j.interest is None or not 0 <= j.interest < 100) for j in state.judges):
        reasons.append("alive_judge_interest_unreadable")
    if len(state.cards) != 3 or [c.slot for c in state.cards] != [1, 2, 3]:
        reasons.append("cards_incomplete")
    if not state.alive():
        reasons.append("no_alive_judge")
    return reasons


def _multiplier(judge, params):
    return judge.multiplier if judge.multiplier else params["trend_multiplier"][judge.rank]


def attribute_factor(appeal, judge, params=PARAMS):
    if appeal is None:
        return params["unknown_appeal"]
    if appeal == "All" or judge.attribute in appeal.split("/"):  # "Vo/Da": dual-attribute icon
        return params["attribute_match"]
    return params["attribute_mismatch"]


def card_judge_value(card, judge, params=PARAMS):
    """Expected value of appealing ``card`` to ``judge`` (0 for a finished judge)."""
    if not judge.alive:
        return 0.0
    value = attribute_factor(card.appeal, judge, params) * _multiplier(judge, params)
    value *= 1.0 + params["remaining_weight"] * (judge.interest or 0) / 100.0
    value *= 1.0 + (params["link_bonus"] if card.link else 0.0) + (params["plus_bonus"] if card.plus else 0.0)
    return round(value, 4)


def _best_target(card, state, params):
    """(value, judge) with deterministic tie-break: value, then higher multiplier, then screen order."""
    ranked = sorted(((card_judge_value(card, j, params), _multiplier(j, params), -i, j)
                     for i, j in enumerate(state.judges) if j.alive), key=lambda t: t[:3], reverse=True)
    value, _, _, judge = ranked[0]
    return value, judge


def memory_rule(state, params=PARAMS):
    """(use, reason). Memory appeal reaches every judge (伝説の一瞬 = all Last Appeals at once): use a full gauge when
    it can finish judges, or on the last turn so the gauge is not wasted."""
    gauge = state.memory_gauge
    if gauge is None or gauge < params["memory_min_gauge"]:
        return False, "gauge_not_full"
    alive = state.alive()
    if not alive:
        return False, "no_alive_judge"
    if state.turn >= state.max_turns:
        return True, "last_turn"
    if state.turn < params["memory_min_turn"]:
        return False, "too_early"
    top = min(alive, key=lambda j: j.rank)
    threshold = params["memory_finish_interest"]
    if (top.interest or 0) >= threshold:
        return True, f"top_trend_{top.attribute}_at_{top.interest}"
    if sum((j.interest or 0) >= threshold for j in alive) >= 2:
        return True, "two_judges_near_finish"
    return False, "no_judge_near_finish"


def decide(state, params=PARAMS):
    """Waiting stage: choose the memory appeal or one card and the planned target judge."""
    reasons = validate(state)
    if reasons:
        return Decision("none", None, None, {}, tuple(reasons))
    use_memory, why = memory_rule(state, params)
    scores = {"memory": why}
    if use_memory:
        top = min(state.alive(), key=lambda j: j.rank)
        return Decision("memory", None, top.attribute, scores, ("memory:" + why,))
    best = None
    for card in state.cards:
        value, judge = _best_target(card, state, params)
        scores[f"card{card.slot}"] = {j.attribute: card_judge_value(card, j, params) for j in state.judges}
        key = (value, _multiplier(judge, params), -card.slot)
        if best is None or key > best[0]:
            best = (key, card, judge)
    _, card, judge = best
    return Decision("card", card.slot, judge.attribute, scores,
                    (f"card{card.slot}_{card.appeal}_to_{judge.attribute}_value_{best[0][0]}",))


def choose_target(state, decision, predicted, params=PARAMS):
    """Target stage (card or memory selected): pick the judge with the predicted interest shown on screen.

    ``predicted``: {attribute: predicted % or None}. A predicted finish (>= 100) is worth the judge's Last Appeal
    stars × trend; otherwise the predicted gain × trend. Unreadable predictions keep the planned target.
    Returns (attribute, explanation dict)."""
    values = {}
    for index, judge in enumerate(state.judges):
        if not judge.alive:
            continue
        p = predicted.get(judge.attribute)
        if p is None:
            continue
        mult = _multiplier(judge, params)
        if p >= 100:
            value = params["finish_weight"] * params["last_appeal_stars"][judge.rank] * mult
        else:
            value = params["gain_weight"] * max(0, p - (judge.interest or 0)) * mult
        values[judge.attribute] = (round(value, 4), mult, -index)
    if not values:
        return decision.target, {"predicted": predicted, "values": {}, "rule": "planned_target_kept"}
    best = max(values, key=lambda a: values[a])
    if values[best][0] <= 0 and decision.target in [j.attribute for j in state.alive()]:
        best = decision.target
        rule = "no_predicted_gain_planned_target_kept"
    else:
        rule = "predicted_finish" if predicted.get(best, 0) >= 100 else "predicted_gain"
    return best, {"predicted": predicted, "values": {a: v[0] for a, v in values.items()}, "rule": rule}


def explain(state, decision):
    """One-line human-readable explanation for the run log."""
    judges = ", ".join(f"{j.attribute}(流行{j.rank} ×{j.multiplier:g} "
                       + (f"{j.interest}%" if j.alive else "審査終了") + ")" for j in state.judges)
    cards = ", ".join(f"{c.slot}:{c.appeal or '?'}" + ("+LINK" if c.link else "") + ("+PLUS" if c.plus else "")
                      for c in state.cards)
    return (f"TURN {state.turn}/{state.max_turns}; judges {judges}; cards {cards}; memory {state.memory_gauge}% -> "
            f"{decision.kind} {decision.slot or ''} to {decision.target} ({'; '.join(decision.reasons)})")
