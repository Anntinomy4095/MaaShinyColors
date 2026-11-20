"""W.I.N.G. audition play: adapter over the Grade Fes live policy/reader (pure, no input, no screen boxes yet).

Reuses ``fes_live_policy`` (Judge/Card/TurnState, ``card_judge_value``, ``memory_rule``, ``choose_target``) and the
``fes_live_reader`` text parsers (``parse_percent``, ``parse_multiplier``, ``ranks_from_multipliers``).
Differences from Grade Fes handled here:

| aspect | Grade Fes (renewed) | W.I.N.G. audition (wiki, unverified) | adapter |
| --- | --- | --- | --- |
| judge meter | per-unit interest 0 -> 100 % | shared 興味値 (HP) falls to 0, NPCs hit the same judges | ``remaining`` HP % -> progress = 100 - remaining |
| stars | TA/LA/Bonus × ★N倍 | TA 20–45, LA 8–18 by trend, +4 at 30/60/90 % | ``last_appeal_stars`` {1:18, 2:13, 3:8} |
| competitors | own judges, no stealing | NPCs can take TA/LA (×1.25 from turn 3) | ``contested`` judge -> value × (1 - penalty) |
| focus | none | win = 1st place; aim at primary attribute on trend 1 | ``primary_focus_bonus`` |
| memory appeal | Center gauge, judgement Good/Bad | gauge per turn, strength by 思い出 Lv; Lv2+ only | ``memory_min_level`` = 2, ``memory_min_turn`` = 4 |
| turns | TURN n / 7 | not sampled | ``max_turns`` None -> no last-turn rule |
| reroll | 再抽選 costs a Fes right | none; no participation cost | nothing to guard |

All values here are starting heuristics to tune from logged auditions, not verified mechanics. Screen boxes for the
audition screen are not defined until native samples exist; ``state_from_texts`` takes already-OCR'd texts.
"""
from __future__ import annotations

import dataclasses
import re

from agent.core import fes_live_policy as fes
from agent.core import fes_live_reader as reader

NO_TURN_LIMIT = 99   # max_turns unknown: keeps fes.validate happy and disables the last-turn memory rule

WING_PARAMS = {
    **fes.PARAMS,
    "last_appeal_stars": {1: 18, 2: 13, 3: 8},
    "memory_min_turn": 4,
    "memory_min_level": 2,
    "primary_focus_bonus": 0.25,
    "contested_penalty": 0.3,
}


@dataclasses.dataclass(frozen=True)
class AuditionJudge:
    attribute: str                 # Vo / Da / Vi
    remaining: float | None        # 興味値 remaining %, None = left / unread (see ``left``)
    rank: int                      # trend rank 1..3
    multiplier: float = 0.0        # star multiplier if shown, else 0 -> trend fallback
    left: bool = False             # judge already left (興味値 0)
    contested: bool = False        # an NPC is close to taking this judge's TA/LA (caller's estimate)


@dataclasses.dataclass(frozen=True)
class AuditionState:
    turn: int
    judges: tuple                  # three AuditionJudge, Vo/Da/Vi order
    cards: tuple                   # three fes.Card
    memory_gauge: int | None
    memory_level: int | None
    primary: str | None            # unit primary attribute (from the strategy)
    max_turns: int | None = None


def to_turn_state(state: AuditionState) -> fes.TurnState:
    judges = tuple(
        fes.Judge(j.attribute, not j.left, None if j.left or j.remaining is None else round(100.0 - j.remaining, 1),
                  j.rank, j.multiplier)
        for j in state.judges)
    return fes.TurnState(state.turn, judges, tuple(state.cards), state.memory_gauge,
                         state.max_turns or NO_TURN_LIMIT)


def validate(state: AuditionState) -> list[str]:
    reasons = []
    if any((not j.left) and (j.remaining is None or not 0 < j.remaining <= 100) for j in state.judges):
        reasons.append("alive_judge_remaining_unreadable")
    reasons += [r for r in fes.validate(to_turn_state(state)) if r != "alive_judge_interest_unreadable"]
    if state.primary is not None and state.primary not in fes.ATTRIBUTES:
        reasons.append("primary_unknown")
    return reasons


def memory_rule(state: AuditionState, params=WING_PARAMS):
    if state.memory_level is None or state.memory_level < params["memory_min_level"]:
        return False, "memory_level_too_low"
    return fes.memory_rule(to_turn_state(state), params)


def card_value(card: fes.Card, judge: AuditionJudge, state: AuditionState, params=WING_PARAMS) -> float:
    """Fes value × primary focus (primary attribute judge) × contest penalty."""
    base = fes.card_judge_value(card, to_turn_state(state).judges[fes.ATTRIBUTES.index(judge.attribute)], params)
    if judge.attribute == state.primary:
        base *= 1.0 + params["primary_focus_bonus"]
    if judge.contested:
        base *= 1.0 - params["contested_penalty"]
    return round(base, 4)


def decide(state: AuditionState, params=WING_PARAMS) -> fes.Decision:
    reasons = validate(state)
    if reasons:
        return fes.Decision("none", None, None, {}, tuple(reasons))
    use, why = memory_rule(state, params)
    scores = {"memory": why}
    alive = [j for j in state.judges if not j.left]
    if use:
        top = min(alive, key=lambda j: j.rank)
        return fes.Decision("memory", None, top.attribute, scores, ("memory:" + why,))
    best = None
    for card in state.cards:
        values = {j.attribute: card_value(card, j, state, params) for j in alive}
        scores[f"card{card.slot}"] = values
        for index, judge in enumerate(state.judges):
            if judge.left:
                continue
            key = (values[judge.attribute], -judge.rank, -card.slot, -index)
            if best is None or key > best[0]:
                best = (key, card, judge)
    (value, *_), card, judge = best
    return fes.Decision("card", card.slot, judge.attribute, scores,
                        (f"card{card.slot}_{card.appeal}_to_{judge.attribute}_value_{value}",))


def choose_target(state: AuditionState, decision: fes.Decision, predicted_remaining: dict, params=WING_PARAMS):
    """Predicted remaining HP % per judge -> predicted progress, then the Fes target rule."""
    predicted = {a: None if r is None else round(100.0 - r, 1) for a, r in predicted_remaining.items()}
    return fes.choose_target(to_turn_state(state), decision, predicted, params)


def parse_turn(text):
    """'TURN 3' / '3/5' / '3' -> 3 (format of the audition turn counter not sampled yet)."""
    if not isinstance(text, str):
        return None
    match = re.fullmatch(r"(?:TURN\s*)?([1-9])(?:\s*/\s*[1-9])?", text.strip(), re.IGNORECASE)
    return int(match.group(1)) if match else None


def state_from_texts(texts: dict, cards: tuple, *, memory_level: int | None, primary: str | None,
                     max_turns: int | None = None):
    """Build an AuditionState from OCR texts: {"turn", "judges": {attr: "45%" | "0%"}, "multipliers": {attr},
    "memory": "80%"}. Returns (state | None, reasons). A judge reading 0 % has left."""
    turn = parse_turn(texts.get("turn"))
    ranks = reader.ranks_from_multipliers(
        {a: reader.parse_multiplier((texts.get("multipliers") or {}).get(a)) for a in fes.ATTRIBUTES})
    reasons = [] if turn else ["turn_unreadable"]
    if not ranks:
        reasons.append("trend_multipliers_unreadable")
    judges = []
    for attribute in fes.ATTRIBUTES:
        value = reader.parse_percent((texts.get("judges") or {}).get(attribute))
        rank, mult = ranks[attribute] if ranks else (0, 0.0)
        if value is None:
            reasons.append(f"judge_{attribute}_unreadable")
        judges.append(AuditionJudge(attribute, value if value else None, rank, mult, left=value == 0))
    if reasons:
        return None, reasons
    state = AuditionState(turn, tuple(judges), tuple(cards), reader.parse_percent(texts.get("memory")),
                          memory_level, primary, max_turns)
    reasons = validate(state)
    return (None, reasons) if reasons else (state, [])


def explain(state: AuditionState, decision: fes.Decision) -> str:
    return "W.I.N.G. " + fes.explain(to_turn_state(state), decision) + f" [primary {state.primary}, Lv{state.memory_level}]"
