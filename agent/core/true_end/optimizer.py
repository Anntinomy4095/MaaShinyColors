"""Bounded legal beam search, whole-team growth, conditional battle evaluation."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
import math
from itertools import permutations
from statistics import NormalDist
from typing import Any, Mapping

from .battle import (BattleSkill, BattleSupport, Passive, ProducedIdol, Scenario, calculate_p_appeal,
                     calculate_support_appeal, evaluate_scenarios, excellent_components, middle_audition)
from .features import GUEST_ROLES, ROLES, score_card_prior
from .growth import (UNIT_MEMBERS, GrowthConfig, GrowthProjection, estimate_partial_growth, estimate_team_growth,
                     on_stage, passive_buffs)
from .models import ATTRIBUTES, STATS, CardData, CardFeatures, LiveSkill, Ownership, integer, number, extract_card_features, validate_team

STRATEGIES = ("VO", "DA", "VI", "VO_DA", "VO_VI", "DA_VI")
CRITICAL = ("s3_odotte", "legend", "semifinal", "final")


@dataclass(frozen=True)
class SearchConfig:
    strategies: tuple[str, ...] = STRATEGIES
    candidate_per_role: int = 10
    beam_width: int = 200
    projection_budget: int = 12000
    evaluate_top: int = 6
    screening_trials: int = 16
    confirmation_trials: int = 512
    seed: int = 20261005
    difficulty: str = "Middle"
    force_threshold: float = .97
    legend_threshold: float = .95
    sensitivity: bool = True
    role_weights: Mapping[str, Mapping[str, float]] | None = None

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> SearchConfig:
        data = dict(value)
        if set(data) - set(cls.__dataclass_fields__):
            raise ValueError("unknown search settings: " + ", ".join(sorted(set(data) - set(cls.__dataclass_fields__))))
        if "strategies" in data:
            data["strategies"] = tuple(data["strategies"])
        cfg = cls(**data)
        if not cfg.strategies or len(set(cfg.strategies)) != len(cfg.strategies) or any(s not in STRATEGIES for s in cfg.strategies):
            raise ValueError("invalid search strategies")
        for key, maximum in (("candidate_per_role", 30), ("beam_width", 1000), ("projection_budget", 100000),
                             ("evaluate_top", 500), ("screening_trials", 5000), ("confirmation_trials", 5000)):
            if integer(getattr(cfg, key), key, minimum=1) > maximum:
                raise ValueError(f"{key} exceeds bound {maximum}")
        integer(cfg.seed, "seed")
        for key in ("force_threshold", "legend_threshold"):
            if number(getattr(cfg, key), key) > 1:
                raise ValueError("threshold must be 0..1")
        if cfg.difficulty != "Middle":
            raise ValueError("only verified Middle audition data is available")
        if not isinstance(cfg.sensitivity, bool):
            raise ValueError("sensitivity must be bool")
        if cfg.role_weights:
            for weights in cfg.role_weights.values():
                for k, v in weights.items():
                    number(v, f"role weight {k}")
        return cfg


def scenario_from_dict(value: Mapping[str, Any]) -> Scenario:
    data = dict(value)
    for key in ("player_judgments", "npc_judgments", "player_low_mental_judgments", "npc_low_mental_judgments",
                "player_buffs", "interest"):
        if key in data:
            raw = data[key]
            data[key] = tuple(raw.items()) if isinstance(raw, dict) else tuple(tuple(v) for v in raw)
    for key in ("assumptions", "player_memory_turns", "initial_hand_ids"):
        if key in data:
            data[key] = tuple(data[key])
    if "npc_memory_turns" in data:
        data["npc_memory_turns"] = tuple((k, tuple(v)) for k, v in data["npc_memory_turns"])
    for key in ("npc_late_multiplier", "fourth_turn_multiplier", "memory_good_probability", "low_mental_ratio"):
        if key in data:
            number(data[key], key)
    for key in ("max_turns",):
        if key in data:
            integer(data[key], key, minimum=1)
    if not isinstance(data.get("overkill_credit"), bool):
        raise ValueError("scenario.overkill_credit must be bool")
    if not data.get("assumptions"):
        raise ValueError("scenario must disclose its uncalibrated assumptions")
    return Scenario(**data)


def _attrs(strategy: str) -> tuple[str, ...]:
    return tuple(a.title() for a in strategy.split("_") if a.upper() in ("VO", "DA", "VI"))


# Cumulative SP a True End run reaches (S3 末 250–340, then S4 lessons and
# a ≥40 reserve for the final-week panels); an estimate, used only to saturate the SP term.
SP_LINE = 450
# 2026-10-27 (user): a card strong in Live panels but weak in growth (吼・吼・熱・々: rank 11-27 in every role) never
# reached the beam; this many of the best by Live passives and skills are kept on top of the role top-N (no measurable
# cost on the 2026-10-27 library: 93 s either way for one P card).
LIVE_RESERVE = 8
# Expected per-turn appeal buff of the learned passives (growth.passive_turn_buff) that counts as full marks.
PASSIVE_LINE = .3


# 2026-10-31 (user: a UR sat in the Guest while a 2.5x card played):
# only the four Active supports enter the audition. Each adds its own Live card to the eight-card deck
# (the P's four equipped skills + the four) and its stats to every appeal, while growth counts all five alike. The hand
# term below is that deck at the final (week 33, Perfect): half the expected best of a random three-card hand, half the
# deck mean (a turn plays one of three; a weak card still comes up as the deck cycles). Full marks = a hand that
# clears the final's 72000-HP judge in three appeals; an estimate that saturates the term, not a win line (the user's
# 2026-10-31 Vo team: 0.57).
HAND_WEEK = 33
HAND_LINE = 72000 / 3


def audition_hand(projection: GrowthProjection, active: tuple[CardFeatures, ...], strategy: str, *,
                  cap: bool = True) -> float:
    """0..1 audition deck strength of the P's equipped skills plus the Active supports' own Live, averaged over the
    build attributes (each as the targeted judge). ``cap=False`` keeps the value above 1: strong decks all reach the cap
    (2026-11-01: the user's Vo team), and the uncapped value still orders their Active/Guest splits."""
    attrs = _attrs(strategy)
    stats = {a: int(projection.effective_stats.get(a, 0)) for a in ATTRIBUTES}
    totals = {a: sum(int(c.stats.get(a, 0)) for c in active) for a in ATTRIBUTES}
    deck = [(_battle_skill(s, "P"), None) for s in projection.skill_plan.learned_skills]
    deck += [(_battle_skill(c.owned_live, c.card.card_id, support=True), c) for c in active if c.owned_live]
    if len(deck) < 3:
        return 0.
    scores = []
    for target in attrs:
        values = []
        for skill, card in deck:
            value = 0
            for attr, multiplier, excellent in excellent_components(skill, target):
                if card is None:
                    value += calculate_p_appeal(stats[attr], totals[attr], HAND_WEEK, multiplier, excellent=excellent)
                else:
                    value += calculate_support_appeal(stats[attr], totals[attr], int(card.stats.get(attr, 0)),
                                                      HAND_WEEK, multiplier, excellent=excellent)
            values.append(value)
        values.sort()
        n = len(values)
        # E[max of a random 3-subset]: the i-th smallest (0-based) is the maximum in C(i, 2) of the C(n, 3) hands.
        best = sum(v * math.comb(i, 2) for i, v in enumerate(values)) / math.comb(n, 3)
        score = (.5 * best + .5 * sum(values) / n) / HAND_LINE
        scores.append(min(1., score) if cap else score)
    return sum(scores) / len(scores)


def growth_readiness(projection: GrowthProjection, strategy: str,
                     active: tuple[CardFeatures, ...] | None = None) -> float:
    """Estimated readiness, not a probability; SP and unnecessary caps saturate. With the team's ``active`` supports
    half the skill term is the audition deck (:func:`audition_hand`), half the P's learned skills alone."""
    attrs = _attrs(strategy)
    attr_score = sum(min(1, projection.effective_stats[a] / (900 if len(attrs) == 1 else 650)) for a in attrs) / len(attrs)
    mental = min(1, projection.effective_stats["Me"] / 350)
    skills = projection.skill_plan.learned_skills
    power = sorted((max([s.components.get(a, 0) for a in attrs] + [s.excellent]) for s in skills), reverse=True)
    # 2026-10-14 (user: a 4x-best team is weak for W.I.N.G.): the strongest skill and the top three carry the score; the
    # old sum/12 saturated, so four 4x skills scored like 5x+4x+3.5x+3x.
    skill_score = .5 * min(1, (power[0] if power else 0) / 6) + .5 * min(1, sum(power[:3]) / 15)
    if active is not None:
        # Half each: the multipliers alone keep a strong skill's weight in a low-stat team (the hand scales with stats).
        skill_score = .5 * skill_score + .5 * audition_hand(projection, active, strategy)
    # 2026-10-25: earned SP against a fixed line. The old term divided by the team's own planned panel cost
    # (sp_readiness), so a team with more useful panels to buy scored lower; what the SP buys is already in the stats
    # and skills above, and spare SP still reaches panels the planner does not model.
    sp = min(1, projection.sp / SP_LINE)
    # 2026-10-27 (user): learned passives count by chance and count, as their own Live term (5 of the skills' 20). Folded
    # into the stats they multiply, the P card's own passives alone lifted most teams past the stat line, and a team
    # 120 Vo short scored first.
    buff = passive_buffs(projection.skill_plan.passives, attrs)
    passive = min(1, sum(buff.values()) / len(attrs) / PASSIVE_LINE)
    return (40 * attr_score + 15 * mental + 15 * sp + 15 * skill_score + 5 * passive
            + 10 * float(projection.route_feasible))


def prune_candidates(cards: tuple[CardFeatures, ...], strategy: str, slot: str, cfg: SearchConfig) -> tuple[CardFeatures, ...]:
    roles = GUEST_ROLES if slot == "guest" else ROLES
    union: dict[str, CardFeatures] = {}
    for role in roles:
        scored = [(score_card_prior(c, strategy, role, slot, weights=cfg.role_weights), c) for c in cards]
        # A zero prior is no evidence for the role.  Filling the top-N with zero-score ties ordered by card ID
        # admitted arbitrary low-ID cards (seven R cards in the 2026-10-07 audit) to roles they lack.
        ranked = sorted((v for v in scored if v[0] > 0), key=lambda v: (-v[0], v[1].card.card_id))
        for _, card in ranked[:cfg.candidate_per_role]:
            union[card.card.card_id] = card
    if slot == "active":
        live = sorted(((v, c) for c in cards
                       if (v := score_card_prior(c, strategy, "LIVE_RESERVE", slot, weights=cfg.role_weights)) > 0),
                      key=lambda v: (-v[0], v[1].card.card_id))
        for _, card in live[:LIVE_RESERVE]:
            union[card.card.card_id] = card
        # A narrow prior cannot eliminate every legal four-active-plus-guest
        # assignment when the inventory actually has five distinct identities.
        retained_idols = {c.card.idol for c in union.values()}
        for card in sorted(cards, key=lambda c: (-score_card_prior(c, strategy, "HYBRID", slot, weights=cfg.role_weights), c.card.card_id)):
            if len(retained_idols) >= 5:
                break
            if card.card.idol not in retained_idols:
                union[card.card.card_id] = card
                retained_idols.add(card.card.idol)
    # Mechanical hybrid prior affects traversal only, never final team rating.
    return tuple(sorted(union.values(), key=lambda c: (-score_card_prior(c, strategy, "HYBRID", slot, weights=cfg.role_weights), c.card.card_id)))


@dataclass(frozen=True)
class TeamCandidate:
    p: CardFeatures
    active: tuple[CardFeatures, ...]
    guest: CardFeatures
    strategy: str
    projection: GrowthProjection

    @property
    def key(self) -> tuple:
        return self.strategy, tuple(sorted(c.card.card_id for c in self.active)), self.guest.card.card_id


# Gaps that can make a projection too high: an incomplete panel graph may price a path too cheaply, and a support
# amount that disagrees with its text may be read too large. Every other gap leaves an effect at zero (an unmodelled
# support skill, optional panel, conditional event or Live extra), so the projection is a lower bound there; the beam
# used to charge a point for each, which pushed cards with strong unmodelled skills (パーフェクト / 滞在 / 施設
# マスタリー) out of the search (2026-10-25, user: value the stronger skills).
_OVERSTATING = ("panel adjacency graph is incomplete", "description amount differs")


def overstates(unknown: str) -> bool:
    return any(mark in unknown for mark in _OVERSTATING)


def beam_search(p: CardFeatures, supports: tuple[CardFeatures, ...], strategy: str,
                growth: GrowthConfig, cfg: SearchConfig) -> tuple[list[TeamCandidate], dict]:
    # Guest-only cards (e.g. owned Live unknown) may fill the Guest slot but never an Active slot.
    active_pool = prune_candidates(tuple(s for s in supports if s.ready), strategy, "active", cfg)
    guest_pool = prune_candidates(tuple(s for s in supports if s.ready_as_guest), strategy, "guest", cfg)
    states = [((), g, -1, 0.0) for g in guest_pool]
    spent, limited = 0, False
    budget = max(4, cfg.projection_budget // len(cfg.strategies))
    for depth in range(4):
        expanded, seen = [], set()
        # Reserve a share for every remaining depth, so even small limits reach complete teams.
        allowance = max(1, (budget - spent) // (4 - depth))
        stop = False
        for chosen, guest, start, _ in states:
            idols = {p.card.idol, guest.card.idol, *(c.card.idol for c in chosen)}
            for index in range(start + 1, len(active_pool)):
                card = active_pool[index]
                if card.card.idol in idols:
                    continue
                future_idols = {c.card.idol for c in active_pool[index + 1:]
                                if c.card.idol not in idols and c.card.idol != card.card.idol}
                if len(future_idols) < 3 - depth:
                    continue  # A partial state must have enough distinct identities left.
                team = (*chosen, card)
                key = tuple(c.card.card_id for c in team), guest.card.card_id
                if key in seen:
                    continue
                seen.add(key)
                projection = estimate_partial_growth(p, team, guest, strategy, growth)
                score = growth_readiness(projection, strategy, team)
                score -= min(20, sum(1 for u in projection.unknowns if overstates(u)))
                expanded.append((team, guest, index, score))
                spent += 1
                allowance -= 1
                if allowance <= 0:
                    limited, stop = True, True
                    break
            if stop:
                break
        expanded.sort(key=lambda v: (-v[3], tuple(c.card.card_id for c in v[0]), v[1].card.card_id))
        states = expanded[:cfg.beam_width]
        if not states:
            break
    candidates = []
    for active, guest, _, _ in states[:cfg.evaluate_top]:
        if len(active) != 4:
            continue
        validate_team(p, active, guest)
        candidates.append(best_slotting(p, active, guest, strategy, growth))
    return candidates, {"strategy": strategy, "active_pool": len(active_pool), "guest_pool": len(guest_pool),
                        "projections": spent, "budget_limited": limited, "beam_width": cfg.beam_width,
                        "complete_candidates": len(candidates), "optimality": "BOUNDED_SEARCH"}


def best_slotting(p: CardFeatures, active: tuple[CardFeatures, ...], guest: CardFeatures, strategy: str,
                  growth: GrowthConfig) -> TeamCandidate:
    """The five supports' best Active/Guest split (R3: the Guest is the card with the least audition value). Growth
    counts all five alike apart from on-stage passives, so each swap of the Guest with an Active card is re-projected
    and the highest readiness wins; equal readiness goes to the stronger uncapped deck (:func:`deck_order`), else the
    beam's split stays."""
    best = TeamCandidate(p, active, guest, strategy, estimate_team_growth(p, active, guest, strategy, growth))
    score = deck_order(best)
    for index in range(len(active)) if guest.ready else ():
        swapped = tuple(guest if i == index else c for i, c in enumerate(active))
        candidate = TeamCandidate(p, swapped, active[index], strategy,
                                  estimate_team_growth(p, swapped, active[index], strategy, growth))
        value = deck_order(candidate)
        if value > score:
            best, score = candidate, value
    return best


def deck_order(c: TeamCandidate) -> tuple[float, float]:
    """(readiness, uncapped deck) for ranking: readiness first, the deck only where readiness ties."""
    return (round(growth_readiness(c.projection, c.strategy, c.active), 9),
            audition_hand(c.projection, c.active, c.strategy, cap=False))


def _battle_skill(skill: LiveSkill, owner: str, *, support: bool = False) -> BattleSkill:
    buffs = ((skill.buff_attribute, skill.buff_amount, skill.buff_turns),) if skill.buff_attribute and skill.buff_turns else ()
    return BattleSkill(f"{owner}:{skill.id}", "support" if support else "produce", tuple(skill.components.items()),
                       owner if support else None, buffs, skill.heal_fraction, skill.unsupported, skill.excellent)


def _passive(value: Mapping[str, Any], index: int) -> Passive:
    probability = number(value.get("probability"), "passive.probability")
    if probability > 1:
        raise ValueError("passive probability must be 0..1")
    attribute = value.get("attribute")
    if attribute not in ATTRIBUTES:
        raise ValueError("passive attribute must be Vo, Da or Vi")
    unsupported = tuple(value.get("unsupported", ()))
    if value.get("condition"):
        unsupported += (f"unmapped passive condition: {value['condition']}",)
    return Passive(f"learned-{index}:{value.get('id', 'passive')}", attribute,
                   number(value.get("buff"), "passive.buff", minimum=-1), probability,
                   integer(value.get("max_activations"), "max_activations"),
                   integer(value.get("start_turn", 1), "start_turn", minimum=1),
                   integer(value.get("end_turn", 999), "end_turn", minimum=1),
                   number(value.get("mental_ratio_max", 1), "mental max"),
                   number(value.get("mental_ratio_min", 0), "mental min"), unsupported)


def _stage(candidate: TeamCandidate, audition: str) -> ProducedIdol:
    projection = candidate.projection
    # S3 and first LEGEND use the state BEFORE the audition, never final S4 skills/stats.
    planned = projection.timeline
    if audition == "s3_odotte":
        # Explicit route IDs are preferred; without them the earlier S3 state is conservative.
        target = next((i for i, row in enumerate(planned) if 16 <= i < 24 and row.get("audition_id") == "s3_odotte"),
                      next((i for i, row in enumerate(planned) if 16 <= i < 24 and row["planned_action"] == "audition"), 16))
    elif audition == "legend":
        target = next((i for i, row in enumerate(planned) if i >= 24 and row.get("audition_id") == "legend"),
                      next((i for i, row in enumerate(planned) if i >= 24 and row["planned_action"] == "audition"), 24))
    else:
        target = 32
    row = planned[max(0, target - 1)]
    stats = dict(row["stats"])
    if audition == "final":
        # Semifinal victory grants +20 per attribute before the final, capped normally.
        stats = {a: min(row["caps"][a], stats[a] + 20) for a in STATS}
    skills = tuple(row.get("learned_skills", ()))
    if not skills:
        raise ValueError("growth timeline lacks stage-specific equipped skills")
    passives = tuple(_passive(v, i) for i, v in enumerate(row.get("passives", ())))
    return ProducedIdol({a: int(stats[a]) for a in ATTRIBUTES}, int(stats["Me"]),
                        tuple(_battle_skill(s, "P") for s in skills),
                        33 if audition == "final" else 32 if audition == "semifinal" else target,
                        passives)


def _battle_supports(candidate: TeamCandidate) -> tuple[BattleSupport, ...]:
    result = []
    for card in candidate.active:
        if card.owned_live is None:
            raise ValueError("support owned live skill missing")
        result.append(BattleSupport(card.card.card_id, {a: int(card.stats[a]) for a in ATTRIBUTES},
                                    _battle_skill(card.owned_live, card.card.card_id, support=True)))
    guest = candidate.guest  # Excluded from the deck and stat totals; its own Live is never needed.
    result.append(BattleSupport(guest.card.card_id, {a: int(guest.stats[a]) for a in ATTRIBUTES if a in guest.stats},
                                _battle_skill(guest.owned_live, guest.card.card_id, support=True)
                                if guest.owned_live else None, True))
    return tuple(result)


def _bad_hand_ids(idol: ProducedIdol, supports: tuple[BattleSupport, ...], target: str,
                  scenario: Scenario) -> tuple[str, ...]:
    """Three lowest turn-one Perfect previews for one target, not all-hand worst case."""
    active = {s.id: s for s in supports if not s.guest}
    totals = {a: sum(s.stats[a] for s in active.values()) for a in ATTRIBUTES}
    buffs, interest = dict(scenario.player_buffs), dict(scenario.interest).get(target, 1.)
    deck = idol.learned_skills + tuple(s.owned_skill for s in active.values())

    def preview(skill: BattleSkill) -> int:
        damage = 0
        for attr, multiplier, excellent in excellent_components(skill, target):
            args = (idol.stats[attr], totals[attr])
            options = dict(buff=buffs.get(attr, 0.), excellent=excellent, interest=interest)
            if skill.source == "produce":
                damage += calculate_p_appeal(*args, idol.week, multiplier, **options)
            else:
                damage += calculate_support_appeal(*args, active[skill.support_id].stats[attr],
                                                   idol.week, multiplier, **options)
        return damage

    return tuple(s.id for s in sorted(deck, key=lambda s: (preview(s), s.id))[:3])


def evaluate_battle_profile(candidate: TeamCandidate, scenarios: tuple[Scenario, ...], cfg: SearchConfig,
                            *, trials: int, policies: tuple[str, ...] | None = None) -> dict:
    if not scenarios:
        return {"status": "NOT_SIMULATED", "reason": "需要显式情景与未校准假设", "true_end_probability": None}
    policy_names = policies or ((f"{candidate.strategy}_DUAL", f"{candidate.strategy}_FORCE")
                               if len(_attrs(candidate.strategy)) == 2 else
                               (f"{candidate.strategy}_SAFE", f"{candidate.strategy}_AGGRESSIVE", f"{candidate.strategy}_FORCE"))
    supports = _battle_supports(candidate)
    profiles = {}
    comparisons = len(CRITICAL) * 6 * len(scenarios) * (4 if cfg.sensitivity else 1) * len(policy_names)
    # Family confidence, not independent 95% intervals on a picked best scenario/policy.
    z = NormalDist().inv_cdf(1 - .05 / (2 * comparisons))
    for policy in policy_names:
        rows, assumptions = [], set()
        for audition_id in CRITICAL:
            idol = _stage(candidate, audition_id)
            variants = [("base", idol, scenarios)]
            if cfg.sensitivity:
                fraction = float(candidate.projection.range.get("fraction", .15))
                low = replace(idol, stats={a: int(v * (1 - fraction)) for a, v in idol.stats.items()},
                              mental=max(1, int(idol.mental * (1 - fraction))))
                variants.append(("lower_stats", low, scenarios))
                variants.append(("weak_buff", replace(idol, passives=()),
                                 tuple(replace(s, player_buffs=(), name=s.name + ":weak-buff") for s in scenarios)))
            for trend in permutations(ATTRIBUTES):
                trend_variants = list(variants)
                if cfg.sensitivity:
                    pressure = tuple(replace(s, initial_hand_ids=_bad_hand_ids(idol, supports, trend[0], s),
                        name=s.name + ":bad-hand", assumptions=s.assumptions + (
                        f"Turn-one low-appeal hand against {trend[0]}; no exhaustive multi-turn worst-hand proof",))
                        for s in scenarios)
                    trend_variants.append(("bad_hand", idol, pressure))
                for name, variant, selected_scenarios in trend_variants:
                    row = evaluate_scenarios(variant, supports, middle_audition(audition_id, trend), policy,
                                             selected_scenarios, trials=trials, seed=cfg.seed, confidence_z=z)
                    row["variant"] = name
                    rows.append(row)
                    for scenario in row["scenarios"]:
                        assumptions.update(scenario["assumptions"])
        points = [r["win_probability"] or 0.0 for row in rows for r in row["scenarios"]]
        turns = [r["mean_turns"] for row in rows for r in row["scenarios"]]
        profiles[policy] = {"rows": rows, "mean_probability": sum(points) / len(points),
                            "worst_probability": min(points), "worst_lower_bound": min(r["worst_lower_bound"] for r in rows),
                            "mean_turns": sum(turns) / len(turns), "assumptions": sorted(assumptions)}
    return {"status": "CONDITIONAL_SIMULATION", "policies": profiles, "confidence": "95% Bonferroni family bounds",
            "comparisons": comparisons, "trials_per_scenario": trials, "true_end_probability": None}


def select_strategy(profile: Mapping[str, Any], primary: str, *, cfg: SearchConfig,
                    force_blockers: tuple[str, ...] = ()) -> dict:
    policies = profile.get("policies", {})
    if not policies:
        return {"strategy": f"{primary}_SAFE", "force_candidate": False, "reason": "条件战斗未完成；保留基础攻略"}
    force_key = f"{primary}_FORCE"
    force = policies.get(force_key)
    gate = profile.get("status") == "CONDITIONAL_SIMULATION" and bool(force) and not force_blockers and cfg.sensitivity
    if force:
        for audition_id in CRITICAL:
            rows = [r for r in force["rows"] if r["audition"] == audition_id]
            expected = 6 * (4 if cfg.sensitivity else 1)
            variants = ("base", "lower_stats", "weak_buff", "bad_hand") if cfg.sensitivity else ("base",)
            coverage = {(tuple(r["trend"]), r.get("variant")) for r in rows}
            required = {(t, v) for t in permutations(ATTRIBUTES) for v in variants}
            if len(rows) != expected or coverage != required:
                gate = False
                continue
            threshold = cfg.legend_threshold if audition_id == "legend" else cfg.force_threshold
            if min(r["worst_lower_bound"] for r in rows) < threshold:
                gate = False
    if gate:
        return {"strategy": force_key, "force_candidate": True,
                "reason": "全部关键试镜、六种流行及敏感性情景的同时置信下界通过；结论只对列出假设成立"}
    permitted = {k: v for k, v in policies.items() if not k.endswith("_FORCE")}
    best = max(permitted or policies, key=lambda k: (policies[k]["mean_probability"], policies[k]["worst_probability"],
                                                     -policies[k]["mean_turns"], k))
    strength = policies[best]["worst_probability"]
    if primary.count("_"):
        label = f"{primary}_DUAL"
    elif strength < .7:
        label = f"{primary}_SAFE"
    elif strength < .9:
        label = f"{primary}_TREND_AWARE"
    else:
        label = f"{primary}_AGGRESSIVE"
    # The evidence tier and actual best tested policy are separate. Never report
    # the probability of SAFE while claiming it was measured under AGGRESSIVE.
    policy = best
    return {"strategy": label, "evaluated_policy": policy, "force_candidate": bool(force and force["mean_probability"] >= cfg.force_threshold),
            "force_blockers": list(force_blockers), "reason": "FORCE置信门槛未通过；按条件模拟选择基础/进阶打法，TREND_AWARE暂与SAFE共用规则"}


def evaluate_team(candidate: TeamCandidate, scenarios: tuple[Scenario, ...], cfg: SearchConfig, *, trials: int) -> dict:
    profile = evaluate_battle_profile(candidate, scenarios, cfg, trials=trials)
    blockers = tuple(candidate.projection.unknowns) + (() if candidate.projection.route_feasible else ("50万粉丝路线/季节门槛未成立",))
    blockers += tuple(sorted({a for p in profile.get("policies", {}).values()
                             for a in p["assumptions"] if a.startswith("unsupported:")}))
    strategy = select_strategy(profile, candidate.strategy, cfg=cfg, force_blockers=blockers)
    ready = growth_readiness(candidate.projection, candidate.strategy, candidate.active)
    if profile.get("policies"):
        policy = strategy.get("evaluated_policy", strategy["strategy"])
        result = profile["policies"][policy]
        speed = 1 / (1 + result["mean_turns"] / 10)
        rating = (70 * result["mean_probability"] + 15 * result["worst_probability"] + 10 * ready / 100 + 5 * speed)
        if not candidate.projection.route_feasible:
            rating *= .5
    else:
        rating = ready
    return {"candidate": candidate, "strategy": strategy, "rating": rating, "readiness": ready, "battle": profile,
            "deck": audition_hand(candidate.projection, candidate.active, candidate.strategy, cap=False)}


# 回体 = recovery in the build's own lesson room (or by unit members); 省体 = 体力サポート's random discount.
# おやすみブースト never earns a tag (no value).
_TAG_KINDS = (("mastery_sp", "SP"), ("unit_mastery_sp", "SP"), ("audition_mastery_sp", "SP"), ("mastery_cap", "上限"),
              ("unit_mastery_cap", "上限"), ("mastery_stamina", "回体"), ("unit_mastery_stamina", "回体"),
              ("stamina_support", "省体"))
_SHORT = {"Vo": "Vo", "Da": "Da", "Vi": "Vi", "Me": "Me", "Vocal": "Vo", "Dance": "Da", "Visual": "Vi", "Mental": "Me"}


def card_tags(f: CardFeatures, guest: bool = False, attrs: tuple[str, ...] = (), unit: bool = False) -> list[str]:
    """Short reasons shown beside an S card in 推荐编队 (2026-10-14 user): its owned Live skill (刀) and the support
    effects the growth model counts (主属性课 / SP / 上限 / 回体 / 组合 / 省体). ``attrs`` = the build's lesson rooms:
    another room's マスタリー体力 is not 回体 for this team; ``unit`` = it triggers a teammate's ユニットマスタリー.
    Display only; never a score."""
    tags = []
    live = None if guest else f.owned_live   # the Guest never enters the Live
    if live is not None and live.components:
        attr, mult = max(live.components.items(), key=lambda kv: kv[1])
        if mult > 0:
            tags.append(f"{_SHORT.get(attr, attr)} {mult:g}倍刀")
    kinds = [skill for skill, value in f.support_effects
             if value > 0 and not (skill.kind == "mastery_stamina" and attrs and skill.room not in attrs)]
    stat_attrs = sorted({_SHORT.get(sk.attribute, sk.attribute) for sk in kinds
                         if sk.kind in ("mastery_stat", "unit_mastery_stat") and sk.attribute})
    tags += [f"{a}课" for a in stat_attrs[:2]]
    for kind, label in _TAG_KINDS:
        if label == "省体" and unit:
            tags.append("组合")
        if label not in tags and any(sk.kind == kind for sk in kinds):
            tags.append(label)
    return tags[:4]


def _feeds_unit(card: CardFeatures, team: tuple[CardFeatures, ...]) -> bool:
    """A same-unit teammate holds an unlocked ユニットマスタリー, which this card's presence triggers."""
    return card.card.unit is not None and any(
        other is not card and other.card.unit == card.card.unit
        and any(s.kind.startswith("unit_mastery_") and v > 0 for s, v in other.support_effects) for other in team)


AUDITION_BUILD = 2   # オーディションマスタリー cards that make an オデマス build (1–3 such cards)


def team_style(c: TeamCandidate) -> dict:
    """The build's 流派 for 推荐编队: 组合 when a
    unit performs in full and a card's 「<unit>全員」 passive uses it, オデマス with :data:`AUDITION_BUILD` or more
    オーディションマスタリー cards, else 单属性 / 双属性 by the strategy. Display only; never a score."""
    attrs, team = _attrs(c.strategy), (c.p, *c.active, c.guest)
    stage = on_stage(c.p, c.active)
    for unit in UNIT_MEMBERS:
        if unit + "全員" in stage and any(f"{unit}全員がライブに参加" in text for f in team
                                          for panel in f.card.panels for text in panel.unsupported):
            return {"id": "unit", "label": f"组合 {unit}", "detail": f"{unit}全员上场，「{unit}全員」被动生效"}
    auditions = sum(1 for f in (*c.active, c.guest)
                    if any(s.kind.startswith("audition_mastery_") and v > 0 for s, v in f.support_effects))
    if auditions >= AUDITION_BUILD:
        return {"id": "audition", "label": "オデマス", "detail": f"オーディションマスタリー {auditions} 张：多打试镜，靠试镜涨 SP/属性"}
    if len(attrs) > 1:
        return {"id": "dual", "label": "双属性", "detail": f"{'/'.join(attrs)}：靠 Excellent 或双色技能"}
    return {"id": "mono", "label": "单属性", "detail": f"技能和支援集中在 {''.join(attrs) or '主属性'}"}


def _summary(value: dict, *, include_battle: bool = True) -> dict:
    c: TeamCandidate = value["candidate"]
    attrs, team = _attrs(c.strategy), (c.p, *c.active, c.guest)
    result = {"card_tags": {**{s.card.card_id: card_tags(s, attrs=attrs, unit=_feeds_unit(s, team)) for s in c.active},
                            c.guest.card.card_id: card_tags(c.guest, True, attrs, _feeds_unit(c.guest, team))},
              "style": team_style(c),
              "p_card": c.p.card.card_id, "active": [s.card.card_id for s in c.active], "guest": c.guest.card.card_id,
              "team_strategy": c.strategy,
              "strategy": value["strategy"], "ProxyTERating": round(value["rating"], 4), "rating_label": "ESTIMATED",
              "growth_readiness": round(value["readiness"], 4), "projection": asdict(c.projection),
              "ownership": [asdict(s.ownership) for s in (c.p, *c.active, c.guest)],
              "true_end_probability": None}
    if include_battle:
        result["battle"] = value["battle"]
    return result


def _card_set(candidate) -> tuple:
    return tuple(sorted(c.card.card_id for c in (*candidate.active, candidate.guest)))


def _distinct_alternatives(best: dict, evaluated: list[dict], battle: bool = True) -> list[dict]:
    """Retain actual team/growth choices, not equivalent Active/Guest permutations.

    Without a battle evaluation (``battle`` False) the Active/Guest placement of the same five S cards changes
    nothing that was scored (2026-10-14 live: three 「推荐」 were one team with the Guest swapped, all 73.8), so the
    card set alone identifies a team."""
    def signature(value: dict) -> tuple:
        candidate = value["candidate"]
        if not battle:
            return (candidate.strategy, _card_set(candidate))
        projection = candidate.projection
        active_combat = tuple(sorted((tuple(sorted(c.stats.items())), repr(c.owned_live))
                                     for c in candidate.active))
        return (candidate.strategy, tuple(sorted(c.card.card_id for c in (*candidate.active, candidate.guest))),
                active_combat,
                tuple(round(projection.effective_stats[a], 6) for a in STATS),
                round(projection.sp, 6), round(projection.stamina, 6),
                tuple(sorted(s.id for s in projection.skill_plan.learned_skills)))
    seen = {signature(best)}
    distinct = []
    for value in evaluated:
        key = signature(value)
        if key not in seen:
            seen.add(key)
            distinct.append(value)
    # 2026-11-01 (user: 流派): the best team of another 流派 takes the second place when the search found one
    style = team_style(best["candidate"])["id"]
    picked = distinct[:2]
    other = next((v for v in distinct if team_style(v["candidate"])["id"] != style), None)
    if other is not None and all(team_style(v["candidate"])["id"] == style for v in picked):
        picked = [*distinct[:1], other]
    alternatives = []
    for value in sorted(picked, key=lambda v: distinct.index(v)):
        alternative = _summary(value, include_battle=False)
        alternative["tradeoffs"] = explain_team(best, value)
        alternatives.append(alternative)
    return alternatives


def explain_team(baseline: dict, optimized: dict) -> dict:
    a, b = baseline["candidate"], optimized["candidate"]
    return {"removed": sorted({c.card.card_id for c in (*a.active, a.guest)} - {c.card.card_id for c in (*b.active, b.guest)}),
            "added": sorted({c.card.card_id for c in (*b.active, b.guest)} - {c.card.card_id for c in (*a.active, a.guest)}),
            "guest_before": a.guest.card.card_id, "guest_after": b.guest.card.card_id,
            "stat_delta": {k: b.projection.effective_stats[k] - a.projection.effective_stats[k] for k in STATS},
            "cap_delta": {k: b.projection.caps[k] - a.projection.caps[k] for k in STATS},
            "sp_delta": b.projection.sp - a.projection.sp, "sp_required_delta": b.projection.sp_required - a.projection.sp_required,
            "rating_delta": optimized["rating"] - baseline["rating"], "label": "MARGINAL_TEAM_ESTIMATE",
            "reason": "整队重算成长、SP路径和条件战斗；稀有度与系列标签没有直接分数"}


SWAP_POOL = 8   # cards tried per slot for growth-only alternatives


def _swap_alternatives(best: TeamCandidate, supports: tuple[CardFeatures, ...], growth: GrowthConfig,
                       cfg: SearchConfig, seen: set) -> list[dict]:
    """Growth-only alternatives (2026-10-14 user: 「其他推荐」 must be different teams): the best team with one slot
    replaced by another owned card of a different idol, ranked by the same rating. The beam's complete states were
    all Active/Guest permutations of one card set."""
    out = []
    for slot in (*range(4), "guest"):
        kind = "guest" if slot == "guest" else "active"
        pool = prune_candidates(tuple(c for c in supports if (c.ready_as_guest if kind == "guest" else c.ready)),
                                best.strategy, kind, cfg)
        current = best.guest if slot == "guest" else best.active[slot]
        others = {c.card.idol for c in (best.p, *best.active, best.guest)} - {current.card.idol}
        tried = 0
        for card in pool:
            if tried >= SWAP_POOL:
                break
            if card.card.idol in others or card.card.card_id == current.card.card_id:
                continue
            tried += 1
            active = list(best.active)
            guest = best.guest
            if slot == "guest":
                guest = card
            else:
                active[slot] = card
            try:
                validate_team(best.p, tuple(active), guest)
                candidate = TeamCandidate(best.p, tuple(active), guest, best.strategy,
                                          estimate_team_growth(best.p, tuple(active), guest, best.strategy, growth))
                if _card_set(candidate) in seen:
                    continue
                seen.add(_card_set(candidate))
                out.append(evaluate_team(candidate, (), cfg, trials=cfg.screening_trials))
            except ValueError:
                continue
    return out


def find_best_teams(cards: Mapping[str, CardData], inventory: Mapping[str, Ownership], p_card: str, *,
                    growth: GrowthConfig, config: SearchConfig | None = None, scenarios: tuple[Scenario, ...] = (),
                    baseline: Mapping[str, Any] | None = None) -> dict:
    cfg = config or SearchConfig()
    rejected, resolved, guest_only = {}, {}, {}
    for key, owned in inventory.items():
        card = cards.get(key)
        if card is None:
            rejected[key] = ["没有已解析卡页数据"]
            continue
        try:
            f = extract_card_features(card, owned)
        except ValueError as error:
            rejected[key] = [str(error)]
            continue
        if card.kind == "S" and f.ready_as_guest and not f.ready:
            resolved[key] = f  # Guest-only: active pool filters on f.ready.
            guest_only[key] = [u for u in f.unknowns if u not in f.guest_unknowns]
        elif not f.ready:
            rejected[key] = list(f.unknowns) or [card.status]
        else:
            resolved[key] = f
    p = resolved.get(p_card)
    if p is None or p.card.kind != "P":
        return {"status": "BLOCKED_DATA", "reason": "P卡未持有、TE资格未知或机制字段缺失", "rejected_cards": rejected,
                "true_end_probability": None, "input_budget": 0, "resource_budget": 0}
    supports = tuple(f for f in resolved.values() if f.card.kind == "S" and f.card.idol != p.card.idol)
    candidates, search = [], []
    for strategy in cfg.strategies:
        found, report = beam_search(p, supports, strategy, growth, cfg)
        candidates.extend(found)
        search.append(report)
    candidates.sort(key=lambda c: (*(-v for v in deck_order(c)), c.key))
    if not candidates:
        return {"status": "BLOCKED_DATA", "reason": "不足五名不同偶像的已知持有S卡，或搜索限额内无合法队伍",
                "rejected_cards": rejected, "search": search, "true_end_probability": None,
                "input_budget": 0, "resource_budget": 0}
    evaluated, errors = [], []
    for candidate in candidates[:cfg.evaluate_top]:
        try:
            evaluated.append(evaluate_team(candidate, scenarios, cfg, trials=cfg.screening_trials))
        except ValueError as error:
            errors.append({"team": list(candidate.key), "error": str(error)})
    if not evaluated:
        return {"status": "BLOCKED_DATA", "reason": "候选战斗数据不完整/不合法", "errors": errors, "rejected_cards": rejected,
                "search": search, "true_end_probability": None, "input_budget": 0, "resource_budget": 0}
    evaluated.sort(key=lambda e: (-e["rating"], -e["deck"], e["candidate"].key))
    best = evaluated[0]
    if scenarios:
        # Fresh disjoint confirmation seeds avoid selecting on the same scouting sample.
        confirmed_cfg = replace(cfg, seed=cfg.seed + cfg.screening_trials + 100000)
        best = evaluate_team(best["candidate"], scenarios, confirmed_cfg, trials=cfg.confirmation_trials)
    pool = list(evaluated)
    if not scenarios:
        pool += _swap_alternatives(best["candidate"], supports, growth, cfg, {_card_set(v["candidate"]) for v in evaluated})
        pool.sort(key=lambda e: (-e["rating"], -e["deck"], e["candidate"].key))
        best = pool[0]
    output = {"status": "NEEDS_REVIEW", "recommendation": _summary(best), "search": search,
              "rejected_cards": rejected, "guest_only_cards": guest_only, "errors": errors,
              "alternatives": _distinct_alternatives(best, pool, battle=bool(scenarios)),
              "true_end_probability": None, "input_budget": 0, "resource_budget": 0,
              "limitations": ["32周期望投影与条件战斗不等于整局True End成功率", "搜索有界，未证明全局最优",
                              "数据/情景假设需审核；本结果不执行编队、学习或任何游戏输入"]}
    output["recommendation"]["evaluation"] = "INDEPENDENT_CONFIRMATION" if scenarios else "NO_BATTLE"
    for alternative in output["alternatives"]:
        alternative["evaluation"] = "SCOUTING_ONLY" if scenarios else "NO_BATTLE"
    if baseline:
        active = tuple(resolved[k] for k in baseline["active"])
        guest = resolved[baseline["guest"]]
        validate_team(p, active, guest)
        candidate = TeamCandidate(p, active, guest, best["candidate"].strategy,
                                  estimate_team_growth(p, active, guest, best["candidate"].strategy, growth))
        reference = evaluate_team(candidate, scenarios, replace(cfg, seed=cfg.seed + cfg.screening_trials + 100000),
                                  trials=cfg.confirmation_trials)
        output["replacement_explanation"] = explain_team(reference, best)
    return output


def training_hints(cards: Mapping[str, CardData], inventory: Mapping[str, Ownership], recommendation: Mapping[str, Any],
                   upgrades: Mapping[str, tuple[CardData, Ownership]], *, growth: GrowthConfig, config: SearchConfig,
                   scenarios: tuple[Scenario, ...] = (), limit: int = 3, min_gain: float = .3,
                   screen: int = 15) -> list[dict]:
    """推荐编队的练级提示: owned S cards below their current 凸's level cap, levelled to that cap and put
    into the recommended team one slot at a time. Only levelling, which the player can do directly; never 凸.

    ``upgrades`` = ``{card_id: (card data with the cap-level row, ownership at the cap)}``. Members of the team are always
    tried in their own slot; other cards are screened by their levelled HYBRID prior (``screen``). A card of an idol
    already in the team may only replace that idol's card; the P idol is never used. Returns at most ``limit`` hints
    whose rating gain is at least ``min_gain``, best first."""
    p = extract_card_features(cards[recommendation["p_card"]], inventory[recommendation["p_card"]])
    active = tuple(extract_card_features(cards[k], inventory[k]) for k in recommendation["active"])
    guest = extract_card_features(cards[recommendation["guest"]], inventory[recommendation["guest"]])
    strategy = recommendation["team_strategy"]
    team = (*active, guest)
    def rating(act: tuple[CardFeatures, ...], g: CardFeatures) -> float:
        candidate = TeamCandidate(p, act, g, strategy, estimate_team_growth(p, act, g, strategy, growth))
        return evaluate_team(candidate, scenarios, config, trials=config.confirmation_trials)["rating"]
    base = rating(active, guest)
    levelled = {}
    for key, (card, own) in upgrades.items():
        try:
            f = extract_card_features(card, own)
        except ValueError:
            continue
        if f.card.kind == "S" and f.ready_as_guest and f.card.idol != p.card.idol:
            levelled[key] = f
    in_team = {c.card.card_id for c in team}
    members = [f for f in levelled.values() if f.card.card_id in in_team]
    others = sorted((f for f in levelled.values() if f.card.card_id not in in_team),
                    key=lambda f: (-score_card_prior(f, strategy, "HYBRID", "active" if f.ready else "guest",
                                                     weights=config.role_weights), f.card.card_id))
    hints = []
    for f in (*members, *others[:screen]):
        same = [i for i, c in enumerate(team) if c.card.idol == f.card.idol]
        best = None
        for slot in same or range(5):
            if slot < 4 and not f.ready:
                continue
            act, g = list(active), guest
            if slot < 4:
                act[slot] = f
            else:
                g = f
            try:
                validate_team(p, tuple(act), g)
                gain = rating(tuple(act), g) - base
            except ValueError:
                continue
            if best is None or gain > best[0]:
                best = gain, slot
        if best is not None and best[0] >= min_gain:
            own = upgrades[f.card.card_id][1]
            hints.append({"card_id": f.card.card_id, "level": inventory[f.card.card_id].level, "target_level": own.level,
                          "limit_break": own.limit_break, "slot": "guest" if best[1] == 4 else f"S{best[1] + 1}",
                          "replaces": team[best[1]].card.card_id, "gain": round(best[0], 2)})
    hints.sort(key=lambda h: (-h["gain"], h["card_id"]))
    return hints[:limit]


def calculate_marginal_card_value(candidate: TeamCandidate, replacement: CardFeatures, slot: int | str,
                                  growth: GrowthConfig, cfg: SearchConfig, scenarios: tuple[Scenario, ...]) -> dict:
    """Recompute replacing one actual slot; no independent permanent card score."""
    before = evaluate_team(candidate, scenarios, cfg, trials=cfg.confirmation_trials)
    active, guest = list(candidate.active), candidate.guest
    if slot == "guest":
        guest = replacement
    elif isinstance(slot, int) and not isinstance(slot, bool) and 0 <= slot < 4:
        active[slot] = replacement
    else:
        raise ValueError("slot must be active 0..3 or guest")
    validate_team(candidate.p, tuple(active), guest)
    after_candidate = TeamCandidate(candidate.p, tuple(active), guest, candidate.strategy,
                                    estimate_team_growth(candidate.p, tuple(active), guest, candidate.strategy, growth))
    return explain_team(before, evaluate_team(after_candidate, scenarios, cfg, trials=cfg.confirmation_trials))
