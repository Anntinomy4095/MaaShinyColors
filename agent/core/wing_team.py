"""W.I.N.G. auto-team selection v1: pure, deterministic, no input, no network, no wiki fetching.


Input: owned card records (mappings or ``CardRecord``); any feature may be unknown (None). Output: the best P card,
4 starters and a guest, with reasons, a confidence in 0..1 and a status:

* ``ok``: hard rules hold, enough data, clear margin over the runner-up;
* ``needs_confirmation``: hard rules hold but data is incomplete or the runner-up is within ``close_margin``;
* ``rejected``: no team satisfies the hard rules (the reasons say which).

Hard rules: ≥3 starters whose Live skill attribute is the primary; the P card has the TE flag (unknown is not
enough); every used card Lv ≥ 40 with a known level (user-locked cards excepted); **no idol twice in the whole team**
— P card, 4 starters and guest (; the guest is an owned card). The idol comes from ``idol`` or,
when absent, from digits 4–6 of a 10-digit wiki card id (type, rarity, idol number); an idol that cannot be derived is
treated as distinct and lowers confidence.
Soft rules: no stamina-recovery support −10 % (推测); SP mastery has diminishing returns (sqrt); duplicate Live skill
names among starters; ≥3 fully uncapped SSR
starters -> SP-budget warning only (C8).

Unknown fields count as 0 (no extrapolation) and lower the confidence by their rubric weight. A card with no rubric
data at all is never auto-picked as a starter; it may be the guest at the pool's 25th-percentile guest score.
"""
from __future__ import annotations

import dataclasses
import itertools
import math
from typing import Any, Iterable, Mapping

ATTRIBUTES = ("Vo", "Da", "Vi")
RARITIES = ("UR", "SSR", "SR", "R")

FIRE_WEIGHTS = {"live_mult": 3.0, "live_extra": 1.0, "passive": 2.0}
STAT_WEIGHTS = {"mastery_stamina": 3.0, "mastery_sp": 3.0, "mastery_cap": 1.5, "mastery_primary": 2.0,
                "audition_mastery": 1.5, "events_value": 1.0, "base_stat": 0.3}
# Normalisation upper bounds (推测: live ×5 = DIVE IN!+, mastery Lv ≈15, …)
NORM = {"live_mult": 5.0, "passive": 30.0, "mastery_stamina": 15, "mastery_sp": 15, "mastery_cap": 15,
        "mastery_primary": 16, "audition_mastery": 15, "events_value": 60, "base_stat": 300}
UNCAP_LIVE = (0.6, 0.75, 0.85, 0.95, 1.0)


@dataclasses.dataclass(frozen=True)
class TeamParams:
    w_fire_starter: float = 1.0
    w_stat_starter: float = 0.6
    w_fire_guest: float = 0.2
    w_stat_guest: float = 1.0
    min_primary_starters: int = 3
    min_level: int = 40
    require_te_flag: bool = True
    no_recovery_penalty: float = 0.10       # fraction of the team score (推测)
    sp_diminish: float = 0.6                # penalty weight on (S − sqrt S) of normalised SP mastery (推测)
    duplicate_skill_penalty: float = 0.5    # per duplicated Live skill name among starters (推测)
    unit_bonus: float = 0.1                 # per support in the P card's unit (C6, low confidence)
    close_margin: float = 0.03              # runner-up within 3 % -> ask the user
    min_confidence: float = 0.6             # below -> needs_confirmation
    pool_limit: int = 14                    # starter pool size for exhaustive search


@dataclasses.dataclass(frozen=True)
class CardRecord:
    id: str
    kind: str = "S"                          # "P" or "S"
    name: str | None = None
    idol: str | None = None
    unit: str | None = None
    rarity: str | None = None
    level: int | None = None
    uncap: int | None = None                 # 0..4
    te_flag: bool | None = None              # P card: True End achievable/marked (hard rule)
    live_skill_attr: str | None = None       # S: the card's own / 4-uncap panel Live skill attribute; P: best skill
    live_skill_mult: float | None = None
    live_skill_name: str | None = None
    live_skill_extra: bool | None = None
    passive_attr: str | None = None
    passive_pct: float | None = None         # e.g. 60 for "Vocal 60% UP"
    passive_rate: float | None = None        # trigger %, e.g. 30
    passive_cond: float | None = None        # condition width 0..1 (1 = always)
    mastery_stamina: int | None = None
    mastery_sp: int | None = None
    mastery_cap: int | None = None
    mastery_stats: Mapping[str, int] | None = None   # e.g. {"Vo": 14}
    audition_mastery: int | None = None
    events_value: int | None = None
    stats: Mapping[str, int] | None = None           # card stats at its level
    p_cap_total: int | None = None                   # P card: primary cap raises on its panel
    locked: bool = False                             # user-fixed: bypasses level / data eligibility only

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "CardRecord":
        """Lenient: unknown keys are ignored (the owned-card list format is still evolving)."""
        if isinstance(value, CardRecord):
            return value
        names = {f.name for f in dataclasses.fields(cls)}
        data = {k: v for k, v in value.items() if k in names}
        if not isinstance(data.get("id"), str) or not data["id"]:
            raise ValueError("card.id 必须是非空字符串")
        if data.get("kind", "S") not in ("P", "S"):
            raise ValueError("card.kind 必须是 P 或 S")
        return cls(**data)


@dataclasses.dataclass(frozen=True)
class CardScore:
    card: CardRecord
    fire: float
    stat: float
    level_factor: float | None
    known_share: float                       # rubric weight share of known fields
    no_data: bool

    def starter(self, p: TeamParams) -> float:
        return (self.level_factor or 0.0) * (p.w_fire_starter * self.fire + p.w_stat_starter * self.stat)

    def guest(self, p: TeamParams) -> float:
        return (self.level_factor or 0.0) * (p.w_fire_guest * self.fire + p.w_stat_guest * self.stat)


@dataclasses.dataclass(frozen=True)
class TeamSelection:
    status: str                              # ok | needs_confirmation | rejected
    p_card: str | None
    starters: tuple
    guest: str | None
    score: float | None
    confidence: float
    reasons: tuple
    runner_up: Mapping[str, Any] | None = None
    rejected_cards: Mapping[str, str] = dataclasses.field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"status": self.status, "p_card": self.p_card, "starters": list(self.starters), "guest": self.guest,
                "score": None if self.score is None else round(self.score, 4),
                "confidence": round(self.confidence, 3), "reasons": list(self.reasons),
                "runner_up": None if self.runner_up is None else dict(self.runner_up),
                "rejected_cards": dict(self.rejected_cards)}


def idol_key(card: "CardRecord") -> str:
    """Idol identity: explicit ``idol``, else digits 4–6 of a 10-digit wiki card id, else a per-card placeholder."""
    if card.idol:
        return card.idol
    if len(card.id) == 10 and card.id.isdigit():
        return f"idol#{card.id[3:6]}"
    return f"?{card.id}"


def level_factor(level: int | None, min_level: int = 40) -> float | None:
    """≥70 1.0, 60–69 0.9, 50–59 0.75, 40–49 0.5, below min -> 0 (reject); unknown -> None."""
    if level is None:
        return None
    if level >= 70:
        return 1.0
    if level >= 60:
        return 0.9
    if level >= 50:
        return 0.75
    if level >= min_level:
        return 0.5
    return 0.0


def score_card(card: CardRecord, primary: str, params: TeamParams | None = None) -> CardScore:
    """Per-card fire/stat scores for ``primary``; unknown fields count 0 and reduce ``known_share``."""
    p = params or TeamParams()
    known, total = 0.0, 0.0

    def feat(name: str, value: float | None, weight: float, bucket: list) -> None:
        nonlocal known, total
        total += weight
        if value is None:
            return
        known += weight
        bucket.append(weight * min(1.0, max(0.0, value / NORM.get(name, 1.0))))

    fire_parts: list = []
    if card.live_skill_attr is None or card.live_skill_mult is None:
        feat("live_mult", None, FIRE_WEIGHTS["live_mult"], fire_parts)
    else:
        uncap = UNCAP_LIVE[card.uncap] if card.uncap is not None and 0 <= card.uncap <= 4 else UNCAP_LIVE[0]
        mult = card.live_skill_mult * uncap if card.live_skill_attr == primary else 0.0
        feat("live_mult", mult, FIRE_WEIGHTS["live_mult"], fire_parts)
    feat("live_extra", None if card.live_skill_extra is None else float(card.live_skill_extra and
                                                                         card.live_skill_attr == primary),
         FIRE_WEIGHTS["live_extra"], fire_parts)
    if card.passive_attr is None or card.passive_pct is None or card.passive_rate is None:
        feat("passive", None, FIRE_WEIGHTS["passive"], fire_parts)
    else:
        cond = 1.0 if card.passive_cond is None else card.passive_cond
        eff = card.passive_pct * card.passive_rate / 100 * cond if card.passive_attr == primary else 0.0
        feat("passive", eff, FIRE_WEIGHTS["passive"], fire_parts)

    stat_parts: list = []
    feat("mastery_stamina", card.mastery_stamina, STAT_WEIGHTS["mastery_stamina"], stat_parts)
    feat("mastery_sp", card.mastery_sp, STAT_WEIGHTS["mastery_sp"], stat_parts)
    feat("mastery_cap", card.mastery_cap, STAT_WEIGHTS["mastery_cap"], stat_parts)
    feat("mastery_primary", None if card.mastery_stats is None else card.mastery_stats.get(primary, 0),
         STAT_WEIGHTS["mastery_primary"], stat_parts)
    feat("audition_mastery", card.audition_mastery, STAT_WEIGHTS["audition_mastery"], stat_parts)
    feat("events_value", card.events_value, STAT_WEIGHTS["events_value"], stat_parts)
    feat("base_stat", None if card.stats is None else card.stats.get(primary), STAT_WEIGHTS["base_stat"],
         stat_parts)
    return CardScore(card, sum(fire_parts), sum(stat_parts), level_factor(card.level, p.min_level),
                     known / total if total else 0.0, known == 0)


def _p_card_score(card: CardRecord, primary: str) -> float:
    """P cards are mostly pass/fail; small bonus for a primary Live skill and cap raises."""
    lf = level_factor(card.level) or 0.5
    mult = (card.live_skill_mult or 0.0) if card.live_skill_attr == primary else 0.0
    return lf * (1.0 + mult / NORM["live_mult"] + min(1.0, (card.p_cap_total or 0) / 500))


def select_team(cards: Iterable[Mapping[str, Any] | CardRecord], primary: str = "Vo", *,
                params: TeamParams | Mapping[str, Any] | None = None) -> TeamSelection:
    """Pick the P card, 4 starters and a guest for a single-attribute W.I.N.G. True End team."""
    if primary not in ATTRIBUTES:
        raise ValueError("primary 必须是 Vo/Da/Vi")
    p = params if isinstance(params, TeamParams) else dataclasses.replace(TeamParams(), **dict(params or {}))
    records = [CardRecord.from_mapping(c) for c in cards]
    ids = [c.id for c in records]
    if len(ids) != len(set(ids)):
        raise ValueError("card id 重复")
    rejected: dict[str, str] = {}
    reasons: list[str] = []
    by_id = {c.id: c for c in records}

    # ---- P card ----
    p_pool = []
    for c in sorted((c for c in records if c.kind == "P"), key=lambda c: c.id):
        if p.require_te_flag and c.te_flag is not True:
            rejected[c.id] = "p_te_flag_false" if c.te_flag is False else "p_te_flag_unknown"
        elif not c.locked and (c.level is None or c.level < p.min_level):
            rejected[c.id] = "level_unknown" if c.level is None else "level_below_min"
        else:
            p_pool.append(c)
    locked_p = [c for c in p_pool if c.locked]
    if locked_p:
        p_pool = locked_p
    if not p_pool:
        return TeamSelection("rejected", None, (), None, None, 0.0, ("no_eligible_p_card",), None, rejected)
    best_p = max(p_pool, key=lambda c: (_p_card_score(c, primary), c.id))     # reported when no team is found

    # ---- S cards ----
    scored = {}
    for c in sorted((c for c in records if c.kind == "S"), key=lambda c: c.id):
        cs = score_card(c, primary, p)
        if not c.locked:
            if cs.level_factor is None:
                rejected[c.id] = "level_unknown"
                continue
            if cs.level_factor == 0.0:
                rejected[c.id] = "level_below_min"
                continue
        if c.locked and cs.level_factor is None:
            cs = dataclasses.replace(cs, level_factor=0.5)       # user pick, level unread: lowest valid factor
        scored[c.id] = cs
    known_cards = [s for s in scored.values() if not s.no_data]
    guest_scores = sorted(s.guest(p) for s in known_cards)
    q25 = guest_scores[(len(guest_scores) - 1) // 4] if guest_scores else 0.0
    starter_pool = sorted((s for s in scored.values() if not s.no_data or s.card.locked),
                          key=lambda s: (-s.starter(p), s.card.id))
    locked_s = [s for s in starter_pool if s.card.locked]
    pool = locked_s + [s for s in starter_pool if not s.card.locked][:max(0, p.pool_limit - len(locked_s))]
    for s in scored.values():
        if s.no_data and not s.card.locked:
            rejected.setdefault(s.card.id, "no_data_not_starter")
    if len(scored) < 5:
        return TeamSelection("rejected", best_p.id, (), None, None, 0.0, ("fewer_than_5_eligible_s_cards",), None,
                             rejected)

    locked_ids = {s.card.id for s in scored.values() if s.card.locked}
    if len(locked_ids) > 5:
        return TeamSelection("rejected", best_p.id, (), None, None, 0.0, ("more_than_5_locked_s_cards",), None,
                             rejected)

    def guest_value(s: CardScore) -> float:
        return q25 if s.no_data else s.guest(p)

    def evaluate(p_card: CardRecord, starters: tuple, guest: CardScore):
        notes = []
        if sum(1 for s in starters if s.card.live_skill_attr == primary) < p.min_primary_starters:
            return None, ("hard:primary_starters_lt_3",)
        if not locked_ids <= {x.card.id for x in starters} | {guest.card.id}:
            return None, ("hard:locked_card_missing",)
        idols = [idol_key(p_card)] + [idol_key(m.card) for m in starters + (guest,)]
        if len(set(idols)) != len(idols):
            return None, ("hard:duplicate_idol",)
        base = sum(s.starter(p) for s in starters) + guest_value(guest) + _p_card_score(p_card, primary)
        members = starters + (guest,)
        adj = 0.0
        stamina = [m.card.mastery_stamina for m in members]
        if all(v is not None for v in stamina) and not any(stamina):
            adj -= p.no_recovery_penalty * base
            notes.append("soft:no_stamina_recovery")
        elif not any(stamina):
            notes.append("soft:stamina_recovery_unknown")
        sp_total = sum(min(1.0, (m.card.mastery_sp or 0) / NORM["mastery_sp"]) for m in members)
        if sp_total > 1:
            adj -= p.sp_diminish * STAT_WEIGHTS["mastery_sp"] * (sp_total - math.sqrt(sp_total))
        if all(m.card.mastery_sp is not None for m in members) and not any(m.card.mastery_sp for m in members):
            notes.append("soft:no_sp_mastery")
        names = [s.card.live_skill_name for s in starters if s.card.live_skill_name]
        dups = len(names) - len(set(names))
        if dups:
            adj -= p.duplicate_skill_penalty * dups
            notes.append(f"soft:duplicate_live_skill_{dups}")
        if p_card.unit:
            same = sum(1 for m in members if m.card.unit == p_card.unit)
            adj += p.unit_bonus * same
        maxed = sum(1 for s in starters if s.card.rarity in ("SSR", "UR") and s.card.uncap == 4)
        if maxed >= 3:
            notes.append("warn:3plus_maxed_ssr_sp_budget")
        return base + adj, tuple(notes)

    results = []
    all_s = sorted(scored.values(), key=lambda s: s.card.id)
    for p_card in p_pool:
        for combo in itertools.combinations(pool, 4):
            combo = tuple(sorted(combo, key=lambda s: s.card.id))
            chosen = {s.card.id for s in combo}
            for g in all_s:
                if g.card.id in chosen:
                    continue
                value, notes = evaluate(p_card, combo, g)
                if value is None:
                    continue
                results.append((round(value, 9), tuple(s.card.id for s in combo), g.card.id, notes, p_card.id))
    if not results:
        return TeamSelection("rejected", best_p.id, (), None, None, 0.0,
                             ("no_team_satisfies_hard_rules", f"need_{p.min_primary_starters}_{primary}_starters"),
                             None, rejected)
    results.sort(key=lambda r: (-r[0], r[4], r[1], r[2]))
    best = results[0]
    p_card = by_id[best[4]]
    runner = next((r for r in results[1:] if (r[1], r[4]) != (best[1], best[4])), None)
    members = [scored[i] for i in best[1]] + [scored[best[2]]]
    confidence = sum(m.known_share for m in members) / len(members)
    if idol_key(p_card).startswith("?"):
        reasons.append(f"idol_unknown:{p_card.id}")
        confidence *= 0.95
    reasons.extend(best[3])
    reasons.append(f"p_card:{p_card.id}")
    for m in members:
        if m.no_data:
            reasons.append(f"data_incomplete:{m.card.id}")
        elif m.known_share < 1:
            reasons.append(f"partial_data:{m.card.id}:{m.known_share:.2f}")
        if idol_key(m.card).startswith("?"):
            reasons.append(f"idol_unknown:{m.card.id}")
            confidence *= 0.95
    status = "ok"
    if confidence < p.min_confidence:
        status = "needs_confirmation"
        reasons.append("low_confidence")
    if runner is not None and best[0] > 0 and (best[0] - runner[0]) / best[0] < p.close_margin:
        status = "needs_confirmation"
        reasons.append("close_runner_up")
    runner_info = None if runner is None else {"p_card": runner[4], "starters": list(runner[1]), "guest": runner[2],
                                               "score": round(runner[0], 4)}
    return TeamSelection(status, p_card.id, best[1], best[2], best[0], confidence, tuple(reasons), runner_info,
                         rejected)
