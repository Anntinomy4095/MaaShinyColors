"""W.I.N.G. audition / live play policy: pure, deterministic, no input, no screen reading.

Summary of the model:

* Three judges (Vo/Da/Vi) each have interest (興味値, an HP bar) shared by every unit. A hit depletes it; overkill is
  lost. Stars per judge by trend rank (流行 1/2/3): Top Appeal 20/15/10 (most appeal dealt to that judge),
  Last Appeal 8/6/4 (the finishing hit), bonus 4/3/2 each time *our own* cumulative appeal on that judge reaches
  30/60/90 % of its interest (personal contribution, not the shared total; wiki オーディションの基本).
  Percentage buffs add up before multiplying. Equal stars are not broken by
  damage: a star tie with the leader still counts as 1st (W.I.N.G. clears on a tie).
* Appeal = (2·P + 0.2·(1 + 0.1·W)·ΣS) × skill multiplier × buff × timing × Excellent (2.0 when the appeal attribute
  matches the judge) (wiki 仕様考察). We do not aim the timing bar, so timing is a random expectation.
* User preference: when stats suffice, ignore the own attribute and keep hitting the trend-#1 judge to
  secure its Top Appeal, which is what makes 1st place reliable. The game's Auto picks the largest single-target appeal
  and fires memory appeal as soon as the gauge is full, so it can scatter hits; ``should_use_game_auto`` is False
  whenever 1st place matters.

The policy values each option in *expected stars*:

* ``bonus``        30/60/90 % thresholds of *our own* contribution crossed by this hit (no race: each unit earns its own);
* ``last_appeal``  LA stars when the hit finishes the judge × first-mover probability (more when we are behind);
* ``top_appeal``   marginal Top Appeal value: P(TA | we commit to this judge) − P(TA | we never hit it again),
                   using a proportional-share model of the remaining interest (ours vs. the rivals' depletion rate);
* ``focus``        the user's trend-#1 rule: a bonus for hitting the trend-#1 judge when committing there gives
                   P(TA) ≥ ``focus_ta_threshold`` and its TA is not yet secured;
* ``overkill``     a small penalty for appeal wasted past the judge's remaining interest;
* ``mental``       when our mental is in danger, finishing a judge removes an attacker;
* ``hold_cost``    memory appeal only: the Last Appeal it could still steal later if kept.

Everything numeric lives in ``LiveParams``; values marked 推測 in the doc are guesses to fit from logged lives.
Unknown readings are never invented: an option whose damage cannot be estimated falls back to a relative proxy and
the decision's ``confidence`` drops to ``"low"``; nothing actionable -> ``action="none"`` with reasons.
"""
from __future__ import annotations

import dataclasses
import math
from typing import Any, Mapping

ATTRIBUTES = ("Vo", "Da", "Vi")
APPEAL_ATTRIBUTES = ATTRIBUTES + ("All",)
STARS = {1: {"ta": 20, "la": 8, "bonus": 4}, 2: {"ta": 15, "la": 6, "bonus": 3}, 3: {"ta": 10, "la": 4, "bonus": 2}}
BONUS_THRESHOLDS = (0.3, 0.6, 0.9)
EPS = 1e-9


@dataclasses.dataclass(frozen=True)
class LiveParams:
    excellent: float = 2.0                  # wiki 仕様考察
    timing_expectation: float = 1.0         # 推測: un-aimed timing bar ≈ Normal on average
    memory_level_multiplier: Mapping[int, float] = dataclasses.field(
        default_factory=lambda: {0: 0.0, 1: 0.8, 2: 1.0, 3: 1.2, 4: 1.4, 5: 2.0})   # wiki 仕様考察
    memory_base_multiplier: float = 2.5     # 推測: memory appeal ≈ a 2.5× skill on every judge at Lv2
    memory_good_rate: float = 0.7           # 推測: memory judgement is Good 1.5 / Bad 0.5
    memory_min_level: int = 1               # Lv0 cannot fire (wiki)
    memory_hold_fraction: float = 0.5       # 推測: chance a kept memory appeal later steals the best LA
    rival_top_share: float = 0.6            # 推測: the strongest rival's share of the rivals' damage on a judge
    default_rival_rate: Mapping[int, float] = dataclasses.field(
        default_factory=lambda: {1: 0.25, 2: 0.15, 3: 0.10})  # 推測: rivals' depletion per turn by trend rank
    ta_scale: float = 0.08                  # 推測: logistic width (fraction of judge HP) for the TA race
    la_first_mover: float = 0.7             # 推測: P(our finishing hit lands before a rival's this turn)
    bonus_first_mover: float = 0.8          # unused since bonus stars are personal (kept for override compatibility)
    behind_la_weight: float = 1.3           # LA stealing is worth more when visible rivals lead us in stars
    focus_ta_threshold: float = 0.75        # "数值够": committing to the trend-#1 judge gives P(TA) at least this
    focus_secured: float = 0.95             # TA already this likely without further hits -> no more focus bonus
    focus_min_gain: float = 0.02            # ...and this hit still raises P(TA) by at least this much
    focus_bonus: float = 3.0                # stars
    overkill_penalty: float = 2.0           # stars per 100 % of judge HP wasted
    mental_danger_ratio: float = 0.3
    mental_finish_bonus: float = 2.0        # stars-equivalent for removing an attacker while in danger
    proxy_scale: float = 0.1                # damage-unknown proxy: stars per (multiplier × Excellent) unit
    # audition_win_estimate (推測 logistic; fit from logged auditions)
    win_c0: float = 0.5
    win_c_ratio: float = 3.0
    win_c_star: float = 0.5                 # per 5 TA stars below trend #1
    win_c_memory: float = 0.15              # per memory level
    win_mental_ratio: float = 0.65          # Me below this × recommended Me -> penalty
    win_mental_penalty: float = 1.5
    win_pass_margin: float = 1.5            # regular audition: 2nd place also passes
    win_need_first_penalty: float = 0.5     # semifinal / final: stronger rivals than the recommended value suggests
    # should_use_game_auto
    auto_min_pass: float = 0.97
    # 「アイドルの応援」(after a 大富豪 game; wikiwiki.jp/shinycolors/プロデュース#assistance and the in-game help, second
    # demo #16/#200): the whole W.I.N.G. Produce gets Vocal/Dance/Visual appeal +20 % and mental damage −30 %. Applied
    # only when the run knows the cheer is active (``cheer`` True); None/False keep the plain model.
    cheer_appeal: float = 1.2
    cheer_mental_cut: float = 0.3

    @classmethod
    def from_overrides(cls, overrides: Mapping[str, Any] | None) -> "LiveParams":
        if not overrides:
            return cls()
        names = {f.name for f in dataclasses.fields(cls)}
        unknown = sorted(set(overrides) - names)
        if unknown:
            raise ValueError(f"unknown LiveParams: {unknown}")
        return dataclasses.replace(cls(), **dict(overrides))


DEFAULT_PARAMS = LiveParams()


# ---------------------------------------------------------------- tolerant readers (unknown -> None, never invented)

def _num(value, lo=None, hi=None):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, str):
        try:
            value = float(value.strip().rstrip("%"))
        except ValueError:
            return None
    if not isinstance(value, (int, float)) or math.isnan(value) or math.isinf(value):
        return None
    if (lo is not None and value < lo) or (hi is not None and value > hi):
        return None
    return value


def _int(value, lo=None, hi=None):
    v = _num(value, lo, hi)
    return None if v is None or int(v) != v else int(v)


def _attr(value, allowed=ATTRIBUTES):
    return value if isinstance(value, str) and value in allowed else None


def _fraction_map(value) -> dict | None:
    if not isinstance(value, Mapping):
        return None
    out = {}
    for key, v in value.items():
        a = _attr(key)
        f = _num(v, 0.0)
        if a is not None and f is not None:
            out[a] = float(f)
    return out or None


# ---------------------------------------------------------------- observed state

@dataclasses.dataclass(frozen=True)
class JudgeView:
    """One judge. Fractions are of the judge's full interest (HP): ``remaining`` 1.0 = untouched, 0 = left."""
    attribute: str
    trend_rank: int | None = None
    remaining: float | None = None
    max_hp: float | None = None             # absolute interest, e.g. 75,000 (audition table) when known
    left: bool = False
    our_dealt: float | None = None          # agent-tracked fraction we dealt so far
    rival_top_dealt: float | None = None    # strongest rival's dealt fraction, if visible
    rival_rate: float | None = None         # rivals' combined depletion per turn (fraction), if estimated
    attack: float | None = None             # this judge's next mental damage to us, if known

    @property
    def alive(self) -> bool:
        return not self.left and (self.remaining is None or self.remaining > EPS)

    @classmethod
    def from_dict(cls, value: Mapping) -> "JudgeView | None":
        if not isinstance(value, Mapping):
            return None
        attribute = _attr(value.get("attribute"))
        if attribute is None:
            return None
        remaining = _num(value.get("remaining"), 0.0, 1.0)
        return cls(attribute, _int(value.get("trend_rank"), 1, 3), remaining, _num(value.get("max_hp"), EPS),
                   bool(value.get("left")) is True or remaining == 0, _num(value.get("our_dealt"), 0.0, 1.0),
                   _num(value.get("rival_top_dealt"), 0.0, 1.0), _num(value.get("rival_rate"), 0.0, 1.0),
                   _num(value.get("attack"), 0.0))


@dataclasses.dataclass(frozen=True)
class AppealOption:
    """A skill card in hand. ``predicted`` = per-judge depletion fraction shown/derived for this card (preferred over
    the formula); ``multiplier`` = the skill's appeal multiplier (e.g. 2.5 for 「Vocal2.5倍アピール」)."""
    id: str
    attribute: str | None = None
    multiplier: float | None = None
    buff: float = 1.0                       # total buff factor = 1 + Σ percentage buffs (additive, never multiplied)
    predicted: Mapping[str, float] | None = None

    @classmethod
    def from_dict(cls, value: Mapping) -> "AppealOption | None":
        if not isinstance(value, Mapping) or not isinstance(value.get("id"), str) or not value["id"]:
            return None
        buffs = value.get("buffs")
        if isinstance(buffs, (list, tuple)):
            # Percentage buffs add up (wiki 仕様考察): +20 % and +30 % -> ×1.5, not ×1.56. Unreadable entries -> no buff.
            parts = [_num(b, -1.0 + EPS) for b in buffs]
            buff = None if any(b is None for b in parts) else additive_buff(parts)
        else:
            buff = _num(value.get("buff"), EPS)
        return cls(value["id"], _attr(value.get("attribute"), APPEAL_ATTRIBUTES), _num(value.get("multiplier"), EPS),
                   1.0 if buff is None else float(buff), _fraction_map(value.get("predicted")))


@dataclasses.dataclass(frozen=True)
class LiveState:
    turn: int | None
    judges: tuple                           # JudgeView, any order (normally Vo/Da/Vi)
    options: tuple                          # AppealOption (the hand)
    stats: Mapping[str, int | None] = dataclasses.field(default_factory=dict)   # Vo/Da/Vi/Me of our idol
    support_sum: Mapping[str, int] | None = None    # ΣS per attribute of the 4 active supports
    week_index: int | None = None           # W in the appeal formula (elapsed weeks)
    memory_gauge: float | None = None       # %
    memory_level: int | None = None
    memory_predicted: Mapping[str, float] | None = None
    mental: float | None = None
    mental_max: float | None = None
    our_stars: int | None = None
    rival_stars: tuple | None = None        # visible rival star counts
    need_first: bool = True                 # W.I.N.G. semifinal/final: only 1st passes (safe default)
    max_turns: int | None = None
    cheer: bool | None = None               # アイドルの応援 active this Produce (appeal ×cheer_appeal)

    @classmethod
    def from_dict(cls, value: Mapping) -> "LiveState":
        """Tolerant: unknown keys are ignored, unreadable values become None (see ``decide`` unknowns)."""
        value = value if isinstance(value, Mapping) else {}
        judges = tuple(j for j in (JudgeView.from_dict(v) for v in (value.get("judges") or ())) if j is not None)
        options = tuple(o for o in (AppealOption.from_dict(v) for v in (value.get("options") or ())) if o is not None)
        stats_in = value.get("stats") if isinstance(value.get("stats"), Mapping) else {}
        stats = {k: _int(stats_in.get(k), 0) for k in ATTRIBUTES + ("Me",)}
        support = value.get("support_sum")
        support = {k: _int(support.get(k), 0) for k in ATTRIBUTES} if isinstance(support, Mapping) else None
        rivals = value.get("rival_stars")
        rivals = tuple(r for r in (_int(x, 0) for x in rivals) if r is not None) \
            if isinstance(rivals, (list, tuple)) else None
        need_first = value.get("need_first")
        return cls(_int(value.get("turn"), 1), judges, options, stats, support, _int(value.get("week_index"), 0),
                   _num(value.get("memory_gauge"), 0.0, 100.0), _int(value.get("memory_level"), 0, 5),
                   _fraction_map(value.get("memory_predicted")), _num(value.get("mental"), 0.0),
                   _num(value.get("mental_max"), EPS), _int(value.get("our_stars"), 0), rivals or None,
                   need_first if isinstance(need_first, bool) else True, _int(value.get("max_turns"), 1),
                   value.get("cheer") if isinstance(value.get("cheer"), bool) else None)


# ---------------------------------------------------------------- output

@dataclasses.dataclass(frozen=True)
class OptionScore:
    option_id: str                          # card id or "memory"
    kind: str                               # "skill" / "memory"
    target: str                             # judge attribute or "all"
    ev: float
    terms: Mapping[str, float]
    unknown: tuple = ()

    def to_dict(self) -> dict:
        return {"option_id": self.option_id, "kind": self.kind, "target": self.target, "ev": self.ev,
                "terms": dict(self.terms), "unknown": list(self.unknown)}


@dataclasses.dataclass(frozen=True)
class Decision:
    action: str                             # "skill" / "memory" / "none"
    option_id: str | None
    target: str | None
    confidence: str                         # "ok" / "low" / "none"
    rows: tuple                             # OptionScore, best first
    reasons: tuple
    focus: Mapping[str, Any]
    unknown: tuple = ()

    def to_dict(self) -> dict:
        return {"action": self.action, "option_id": self.option_id, "target": self.target,
                "confidence": self.confidence, "rows": [r.to_dict() for r in self.rows],
                "reasons": list(self.reasons), "focus": dict(self.focus), "unknown": list(self.unknown)}


# ---------------------------------------------------------------- model pieces

def _sigmoid(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


def _rank(judge: JudgeView) -> int:
    return judge.trend_rank if judge.trend_rank in STARS else 3     # unknown rank: lowest star value (conservative)


def _remaining(judge: JudgeView) -> float | None:
    if not judge.alive:
        return 0.0
    return judge.remaining


def additive_buff(percentages) -> float:
    """Total buff factor from percentage buffs given as fractions (0.2 = +20 %): 1 + Σ, floored at 0."""
    return max(0.0, 1.0 + sum(float(p) for p in percentages))


def star_placement(our_stars: int, rival_stars) -> int:
    """Our place by stars. Ties are not broken by damage: place = 1 + number of rivals with strictly more stars."""
    return 1 + sum(1 for r in (rival_stars or ()) if r is not None and r > our_stars)


def _stat(state: LiveState, attribute: str) -> int | None:
    return state.stats.get(attribute) if isinstance(state.stats, Mapping) else None


def skill_damage(state: LiveState, option: AppealOption, judge: JudgeView, params: LiveParams) -> tuple[float | None, str]:
    """Expected depletion fraction of ``judge`` by ``option``, and how it was obtained ("predicted"/"formula"/"")."""
    if option.predicted and judge.attribute in option.predicted:
        return option.predicted[judge.attribute], "predicted"
    if option.attribute is None or option.multiplier is None or judge.max_hp is None:
        return None, ""
    stat_attr = judge.attribute if option.attribute == "All" else option.attribute
    p = _stat(state, stat_attr)
    if p is None:
        return None, ""
    base = 2.0 * p
    if state.support_sum and state.support_sum.get(stat_attr) is not None:
        w = state.week_index if state.week_index is not None else 0
        base += 0.2 * (1 + 0.1 * w) * state.support_sum[stat_attr]
    match = option.attribute == "All" or option.attribute == judge.attribute
    appeal = base * option.multiplier * option.buff * params.timing_expectation * (params.excellent if match else 1.0)
    if state.cheer:
        appeal *= params.cheer_appeal
    return appeal / judge.max_hp, "formula"


def memory_damage(state: LiveState, judge: JudgeView, params: LiveParams) -> tuple[float | None, str]:
    if state.memory_predicted and judge.attribute in state.memory_predicted:
        return state.memory_predicted[judge.attribute], "predicted"
    level = state.memory_level
    p = _stat(state, judge.attribute)
    if level is None or p is None or judge.max_hp is None:
        return None, ""
    judgement = params.memory_good_rate * 1.5 + (1 - params.memory_good_rate) * 0.5
    appeal = 2.0 * p * params.memory_base_multiplier * params.memory_level_multiplier.get(level, 0.0) * judgement
    return appeal / judge.max_hp, "formula"


def _rival_rate(state: LiveState, judge: JudgeView, params: LiveParams) -> float:
    if judge.rival_rate is not None:
        return judge.rival_rate
    rem = _remaining(judge)
    if rem is not None and state.turn and state.turn > 1:
        rival_dealt = max(0.0, 1.0 - rem - (judge.our_dealt or 0.0))
        if rival_dealt > EPS:
            return rival_dealt / (state.turn - 1)
    return params.default_rival_rate[_rank(judge)]


def _rival_top(judge: JudgeView, rem: float, params: LiveParams) -> float:
    if judge.rival_top_dealt is not None:
        return judge.rival_top_dealt
    return max(0.0, 1.0 - rem - (judge.our_dealt or 0.0)) * params.rival_top_share


def ta_probability(state: LiveState, judge: JudgeView, hit: float, future_hit: float, params: LiveParams) -> float:
    """P(we hold Top Appeal on ``judge`` when it leaves) if we hit it now for ``hit`` and keep hitting it with
    ``future_hit`` per turn (0 = never again). Proportional share of the remaining interest."""
    rem = _remaining(judge) or 0.0
    ours = judge.our_dealt or 0.0
    rival_top = _rival_top(judge, rem, params)
    eff = min(hit, rem)
    after = rem - eff
    rate = _rival_rate(state, judge, params)
    share = future_hit / (future_hit + rate) if future_hit + rate > EPS else 0.0
    ours_final = ours + eff + after * share
    rival_final = rival_top + after * (1 - share) * params.rival_top_share
    return _sigmoid((ours_final - rival_final) / params.ta_scale)


def _behind(state: LiveState) -> bool:
    return state.our_stars is not None and star_placement(state.our_stars, state.rival_stars) > 1


def _mental_danger(state: LiveState, params: LiveParams) -> tuple[bool, bool]:
    """(danger, lethal_next): lethal when a known judge attack would take us to 0 next turn."""
    if state.mental is None:
        return False, False
    attack = sum(j.attack for j in state.judges if j.alive and j.attack is not None)
    lethal = attack > 0 and state.mental <= attack
    danger = lethal or (state.mental_max is not None and state.mental / state.mental_max < params.mental_danger_ratio)
    return danger, lethal


def _hit_terms(state: LiveState, judge: JudgeView, d: float, future_hit: float, params: LiveParams,
               danger: bool) -> dict:
    rank = _rank(judge)
    stars = STARS[rank]
    rem = _remaining(judge) or 0.0
    eff = min(d, rem)
    # Bonus stars follow our own cumulative contribution to this judge (fraction of its max interest), so they are
    # not contested; overkill beyond the remaining interest does not count.
    own0 = judge.our_dealt or 0.0
    own1 = own0 + eff
    crossed = sum(1 for t in BONUS_THRESHOLDS if own0 < t - EPS and own1 >= t - EPS)
    finishes = rem > EPS and d >= rem - EPS
    la_weight = params.behind_la_weight if _behind(state) else 1.0
    ta_now = ta_probability(state, judge, d, future_hit, params)
    ta_base = ta_probability(state, judge, 0.0, 0.0, params)
    return {
        "bonus": round(crossed * stars["bonus"], 4),
        "last_appeal": round(stars["la"] * params.la_first_mover * la_weight, 4) if finishes else 0.0,
        "top_appeal": round(stars["ta"] * (ta_now - ta_base), 4),
        "overkill": round(-params.overkill_penalty * max(0.0, d - rem), 4),
        "mental": params.mental_finish_bonus if (finishes and danger) else 0.0,
    }


def _focus(state: LiveState, params: LiveParams) -> dict:
    """User rule: commit to the trend-#1 judge when our best hit there wins its TA race with ≥ threshold."""
    top = [j for j in state.judges if j.alive and j.trend_rank == 1]
    if not top:
        return {"judge": None, "reason": "trend1_unknown_or_left"}
    judge = top[0]
    hits = [d for d, _ in (skill_damage(state, o, judge, params) for o in state.options) if d is not None]
    if not hits:
        return {"judge": judge.attribute, "active": False, "reason": "trend1_damage_unknown"}
    best = max(hits)
    p_commit = ta_probability(state, judge, best, best, params)
    p_base = ta_probability(state, judge, 0.0, 0.0, params)
    secured = p_base >= params.focus_secured or (p_base >= params.focus_ta_threshold
                                                  and p_commit - p_base < params.focus_min_gain)
    active = p_commit >= params.focus_ta_threshold and not secured
    reason = ("trend1_ta_secured" if secured else
              "stats_sufficient" if active else "stats_insufficient")
    return {"judge": judge.attribute, "active": active, "p_ta_commit": round(p_commit, 4),
            "p_ta_now": round(p_base, 4), "reason": reason}


# ---------------------------------------------------------------- decision

def score_options(state: LiveState, params: LiveParams = DEFAULT_PARAMS) -> tuple[list, dict, list]:
    """All (option, judge) skill rows and the memory row (if usable). Returns (rows, focus, unknowns)."""
    unknown = []
    if state.turn is None:
        unknown.append("turn")
    alive = [j for j in state.judges if j.alive]
    if any(j.trend_rank is None for j in alive):
        unknown.append("trend_rank")
    if any(j.remaining is None for j in alive):
        unknown.append("judge_remaining")
    danger, lethal = _mental_danger(state, params)
    if state.mental is None:
        unknown.append("mental")
    focus = _focus(state, params)
    rows = []
    for index, option in enumerate(state.options):
        for judge in alive:
            d, source = skill_damage(state, option, judge, params)
            if d is None or judge.remaining is None:
                match = option.attribute in ("All", judge.attribute)
                proxy = ((option.multiplier or 1.0) * (params.excellent if match else 1.0) * params.proxy_scale
                         * STARS[_rank(judge)]["ta"])
                terms = {"proxy": round(proxy, 4)}
                rows.append(OptionScore(option.id, "skill", judge.attribute, round(proxy, 4), terms,
                                        ("damage",)))
                continue
            terms = _hit_terms(state, judge, d, d, params, danger)
            terms["focus"] = params.focus_bonus if (focus.get("active") and focus["judge"] == judge.attribute) else 0.0
            rows.append(OptionScore(option.id, "skill", judge.attribute, round(sum(terms.values()), 4), terms,
                                    () if source == "predicted" else ("damage_formula",)))
    memory_row, memory_reason = _memory_row(state, alive, params, danger, lethal)
    if memory_row is not None:
        rows.append(memory_row)
    elif memory_reason.endswith("unknown"):
        unknown.append(memory_reason)
    order = {"skill": 0, "memory": 1}
    attr_index = {a: i for i, a in enumerate(ATTRIBUTES)}
    opt_index = {o.id: i for i, o in enumerate(state.options)}
    judge_rank = {j.attribute: _rank(j) for j in state.judges}
    rows.sort(key=lambda r: (-r.ev, order[r.kind], judge_rank.get(r.target, 0), opt_index.get(r.option_id, 99),
                             attr_index.get(r.target, 9)))
    focus = dict(focus, mental_danger=danger, lethal_next=lethal, behind=_behind(state))
    return rows, focus, sorted(set(unknown))


def _memory_row(state: LiveState, alive: list, params: LiveParams, danger: bool, lethal: bool):
    if state.memory_gauge is None:
        return None, "memory_gauge_unknown"
    if state.memory_gauge < 100 - EPS:
        return None, "memory_gauge_not_full"
    if state.memory_level is None:
        return None, "memory_level_unknown"
    if state.memory_level < params.memory_min_level:
        return None, "memory_level_too_low"
    totals = {"bonus": 0.0, "last_appeal": 0.0, "top_appeal": 0.0, "overkill": 0.0, "mental": 0.0}
    finishes = 0
    for judge in alive:
        d, _ = memory_damage(state, judge, params)
        if d is None or judge.remaining is None:
            return None, "memory_damage_unknown"
        terms = _hit_terms(state, judge, d, 0.0, params, danger)    # one-shot: no commitment afterwards
        finishes += terms["last_appeal"] > 0
        for k, v in terms.items():
            totals[k] += v
    last_chance = lethal or danger or (state.max_turns is not None and state.turn is not None
                                       and state.turn >= state.max_turns) or (len(alive) <= 1 and finishes > 0)
    hold = 0.0 if last_chance else -params.memory_hold_fraction * max(
        (STARS[_rank(j)]["la"] for j in alive), default=0)
    totals = {k: round(v, 4) for k, v in totals.items()}
    totals["hold_cost"] = round(hold, 4)
    return OptionScore("memory", "memory", "all", round(sum(totals.values()), 4), totals, ()), ""


def decide(state: LiveState, params: LiveParams | Mapping | None = None) -> Decision:
    """Next appeal: the highest expected-star row (deterministic tie-breaks: skill before memory, higher trend,
    hand order, Vo/Da/Vi order). Never invents a reading; low-confidence rows are flagged."""
    params = params if isinstance(params, LiveParams) else LiveParams.from_overrides(params)
    reasons = []
    if len(state.judges) != 3 or sorted(j.attribute for j in state.judges) != sorted(ATTRIBUTES):
        reasons.append("judges_not_three_distinct")
    if not any(j.alive for j in state.judges):
        reasons.append("no_alive_judge")
    if not state.options and not (state.memory_gauge is not None and state.memory_gauge >= 100):
        reasons.append("no_options")
    if reasons:
        return Decision("none", None, None, "none", (), tuple(reasons), {}, ())
    rows, focus, unknown = score_options(state, params)
    if not rows:
        return Decision("none", None, None, "none", (), ("no_scorable_option",), focus, tuple(unknown))
    best = rows[0]
    confidence = "low" if "damage" in best.unknown or "trend_rank" in unknown else "ok"
    why = [f"{best.kind}:{best.option_id}->{best.target} ev={best.ev}"]
    if focus.get("active"):
        why.append(f"focus_trend1_{focus['judge']}")
    if best.kind == "memory":
        why.append("memory_now" if best.terms.get("hold_cost", 0) == 0 else "memory_beats_hold")
    return Decision(best.kind, best.option_id, best.target, confidence, tuple(rows), tuple(why), focus,
                    tuple(unknown))


# ---------------------------------------------------------------- pre-audition estimate (for wing_score)

@dataclasses.dataclass(frozen=True)
class AuditionContext:
    primary: str | None
    stats: Mapping[str, int | None]
    recommended: Mapping[str, int] = dataclasses.field(default_factory=dict)   # 推奨能力値, e.g. {"Vo": 450, "Me": 450}
    trend: Mapping[str, int] | None = None   # {"Vo": 1, "Da": 2, "Vi": 3}
    memory_level: int | None = None
    need_first: bool = False                 # semifinal / final
    rival_base_attack: float | None = None   # wiki 対手基礎攻撃 of the audition, when known
    best_multiplier: float | None = None     # best Live Skill multiplier in the deck
    support_sum: Mapping[str, int] | None = None
    week_index: int | None = None
    cheer: bool | None = None                # アイドルの応援 active (大富豪 played before this Produce)


def context_from_wing(state, audition, primary: str, *, need_first: bool = False, **extra) -> AuditionContext:
    """Adapter from ``wing_state.WingState`` / ``wing_state.Audition`` (duck-typed; missing attributes -> None)."""
    if "cheer" not in extra and isinstance(getattr(state, "cheer", None), bool):
        extra["cheer"] = state.cheer
    return AuditionContext(primary, dict(getattr(state, "stats", {}) or {}),
                           dict(getattr(audition, "recommended", {}) or {}), getattr(state, "trend", None),
                           getattr(state, "memory_level", None), need_first, **extra)


def audition_win_estimate(ctx: AuditionContext, params: LiveParams | Mapping | None = None) -> dict:
    """P(1st) and P(pass) before entering an audition (推測 logistic).

    Two plans are compared and the better is reported: hit our own-attribute judge (Excellent, star value by its
    trend rank) or, when different, focus the trend-#1 judge off-attribute (TA 20, half the appeal). Firepower ratio
    is our appeal vs. a rival's Perfect+Excellent hit when ``rival_base_attack`` and ``best_multiplier`` are known,
    else primary / recommended. Returns None probabilities with ``unknown`` when neither is available."""
    params = params if isinstance(params, LiveParams) else LiveParams.from_overrides(params)
    unknown = []
    primary = _attr(ctx.primary)
    stat = _num((ctx.stats or {}).get(primary)) if primary else None
    if primary is None or stat is None:
        return {"p_first": None, "p_pass": None, "plan": None, "unknown": ["primary_stat"], "terms": {}}
    trend = ctx.trend if isinstance(ctx.trend, Mapping) and sorted(ctx.trend.values()) == [1, 2, 3] else None
    if trend is None:
        unknown.append("trend")
    own_rank = trend[primary] if trend else 3
    if ctx.rival_base_attack and ctx.best_multiplier:
        base = 2.0 * stat
        if ctx.support_sum and ctx.support_sum.get(primary) is not None:
            base += 0.2 * (1 + 0.1 * (ctx.week_index or 0)) * ctx.support_sum[primary]
        own_ratio = base * ctx.best_multiplier * params.excellent * params.timing_expectation / (
            ctx.rival_base_attack * 1.5 * params.excellent)
        source = "firepower"
    else:
        rec = _num((ctx.recommended or {}).get(primary), EPS)
        if rec is None:
            return {"p_first": None, "p_pass": None, "plan": None,
                    "unknown": sorted(set(unknown + ["recommended_primary", "firepower"])), "terms": {}}
        own_ratio = stat / rec
        source = "recommended"
        unknown.append("firepower")
    if ctx.cheer:
        own_ratio *= params.cheer_appeal
    plans = {"own_attribute": (own_ratio, own_rank)}
    if trend and own_rank != 1:
        plans["trend1_focus"] = (own_ratio / params.excellent, 1)
    shared = 0.0
    if ctx.memory_level is not None:
        shared += params.win_c_memory * ctx.memory_level
    else:
        unknown.append("memory_level")
    me, me_rec = _num((ctx.stats or {}).get("Me")), _num((ctx.recommended or {}).get("Me"), EPS)
    if me is not None and me_rec is not None:
        if ctx.cheer:
            me = me / max(EPS, 1.0 - params.cheer_mental_cut)     # 30 % less damage = 1/0.7 effective mental
        if me < params.win_mental_ratio * me_rec:
            shared -= params.win_mental_penalty
    else:
        unknown.append("mental")
    if ctx.need_first:
        shared -= params.win_need_first_penalty
    logits = {}
    for name, (ratio, rank) in plans.items():
        logits[name] = (params.win_c0 + params.win_c_ratio * math.log(max(ratio, EPS))
                        - params.win_c_star * (STARS[1]["ta"] - STARS[rank]["ta"]) / 5 + shared)
    plan = max(sorted(logits), key=lambda k: logits[k])
    p_first = _sigmoid(logits[plan])
    p_pass = p_first if ctx.need_first else _sigmoid(logits[plan] + params.win_pass_margin)
    return {"p_first": round(p_first, 4), "p_pass": round(p_pass, 4), "plan": plan, "source": source,
            "terms": {k: round(v, 4) for k, v in logits.items()}, "unknown": sorted(set(unknown))}


def should_use_game_auto(*, need_first: bool = True, estimate: Mapping | None = None, user_opt_in: bool = False,
                         params: LiveParams | Mapping | None = None) -> tuple[bool, str]:
    """Whether handing the live to the game's own Auto is acceptable. False whenever 1st place matters (W.I.N.G.
    semifinal/final, or the caller cares about 1st, e.g. オーディションマスタリー). Otherwise only with the user's opt-in
    and an estimated pass probability ≥ ``auto_min_pass``. Auto picks the biggest single-target appeal regardless of
    trend/Excellent/remaining interest and fires memory at 100 %, so it cannot be trusted to keep 1st."""
    params = params if isinstance(params, LiveParams) else LiveParams.from_overrides(params)
    if need_first:
        return False, "first_place_required"
    if not user_opt_in:
        return False, "auto_not_opted_in"
    p_pass = None if not isinstance(estimate, Mapping) else _num(estimate.get("p_pass"), 0.0, 1.0)
    if p_pass is None:
        return False, "pass_estimate_unknown"
    if p_pass < params.auto_min_pass:
        return False, f"pass_estimate_{p_pass}_below_{params.auto_min_pass}"
    return True, "safe_margin_opted_in"


# ---------------------------------------------------------------- navigation adapters (wing_nav SCORER_HOOKS)
# Thin, pure adapters with the names agent/core/wing_nav.py resolves ("audition" / "live" hooks). They never mutate
# ``memory`` (the navigator owns its RunMemory bookkeeping) and only return what to do next.

# Pages of the audition tab. Demo #763 (S4) shows 6 page dots, 4th highlighted (the tab does not open on page 1);
# earlier seasons were assumed to list 4. 6 = the observed maximum (2026-10-05 integration).
AUDITION_MAX_PAGES = 6


def choose_audition(readings: Mapping | None, memory: Any) -> dict:
    """Audition-tab page decision for the week plan.

    ``readings``: the audition page reading (``reward`` = fan reward of the shown page; optional ``locked``).
    ``memory``: the navigator's RunMemory (``pending``, ``pending_audition`` = fan reward chosen by the week
    decision, ``audition_pages`` = pages already turned). Returns ``{"action": ...}`` with ``schedule_decide``
    (enter this audition), ``audition_next`` (turn the page) or ``decline`` (the navigator stops), plus a reason."""
    r = readings if isinstance(readings, Mapping) else {}
    if getattr(memory, "pending", None) != "audition":
        return {"action": "decline", "reason": "no_audition_plan"}
    wanted = getattr(memory, "pending_audition", None)
    if wanted is None:
        return {"action": "decline", "reason": "week_plan_has_no_reward"}
    reward = _int(r.get("reward"), 1)
    pages = _int(getattr(memory, "audition_pages", 0), 0) or 0
    special = getattr(memory, "pending_special", None)
    seen = getattr(memory, "auditions_seen", None)
    special_week = isinstance(seen, Mapping) and bool(seen.get("special_week"))
    if reward is None and not special_week:
        return {"action": "decline", "reason": "audition_reward_unread"}
    if reward == wanted and r.get("special") == special and r.get("locked") is not True:
        return {"action": "schedule_decide", "reason": "matches_week_plan", "reward": reward, "special": special}
    if reward == wanted and r.get("special") == special:
        return {"action": "decline", "reason": "planned_audition_locked", "reward": reward}
    if pages + 1 >= AUDITION_MAX_PAGES:
        return {"action": "decline", "reason": "planned_audition_not_found", "wanted": wanted, "pages": pages}
    return {"action": "audition_next", "reason": "audition_page", "reward": reward, "wanted": wanted}


def live_plan(readings: Mapping | None, memory: Any, params: LiveParams | Mapping | None = None) -> dict:
    """Live-screen decision for the navigator.

    The game's Auto is used only through ``should_use_game_auto``: ``memory.live_mode == "game_auto"`` is the user's
    explicit opt-in, ``readings["need_first"]`` (default True = safe) and ``readings["estimate"]`` (an
    ``audition_win_estimate`` result) gate it. Otherwise our own policy: ``readings["live_state"]`` (LiveState dict)
    -> ``decide``; the returned ``play`` decision must be executed by the caller (appeal-card intents are not wired
    in the navigator yet). Actions: ``live_auto`` (toggle Auto on), ``wait``, ``play``, ``stop``."""
    r = readings if isinstance(readings, Mapping) else {}
    need_first = r.get("need_first") if isinstance(r.get("need_first"), bool) else True
    opt_in = getattr(memory, "live_mode", None) == "game_auto"
    use_auto, reason = should_use_game_auto(need_first=need_first, estimate=r.get("estimate"), user_opt_in=opt_in,
                                            params=params)
    auto = r.get("auto")
    if use_auto:
        if auto == "OFF":
            return {"action": "live_auto", "reason": reason}
        if auto == "ON":
            return {"action": "wait", "reason": "game_auto_running"}
        return {"action": "stop", "reason": "auto_state_unread"}
    if auto == "ON":
        return {"action": "stop", "reason": "game_auto_on_without_permission", "auto_reason": reason}
    live_state = r.get("live_state")
    if not isinstance(live_state, Mapping):
        return {"action": "stop", "reason": "live_state_unread", "auto_reason": reason}
    decision = decide(LiveState.from_dict(live_state), params)
    if decision.action == "none":
        return {"action": "stop", "reason": "policy_no_action", "decision": decision.to_dict()}
    return {"action": "play", "reason": "own_policy", "auto_reason": reason, "decision": decision.to_dict()}
