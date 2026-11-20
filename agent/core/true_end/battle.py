"""Offline W.I.N.G. battles; probabilities are conditional on explicit scenarios.

No input, screenshot, resource use, or connection to a live game occurs here.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR
from math import sqrt
from random import Random
from typing import Mapping

ATTRIBUTES = ("Vo", "Da", "Vi")
JUDGMENT = {"Bad": .5, "Normal": 1., "Good": 1.1, "Perfect": 1.5}
ORDER = {"Perfect": 0, "Good": 1, "Normal": 3, "Bad": 4}
TA_STARS = (20, 15, 10)
LA_STARS = (8, 6, 4)
BONUS_STARS = (4, 3, 2)
AUDITION_SOURCE = "https://wikiwiki.jp/shinycolors/各オーディション個別攻略"


def _d(value: float | int | Decimal) -> Decimal:
    try:
        number = Decimal(str(value))
    except (ValueError, ArithmeticError) as exc:
        raise ValueError("a finite numeric value is required") from exc
    if not number.is_finite():
        raise ValueError("NaN and infinity are not valid battle inputs")
    return number


def _finite(*values: object) -> None:
    for value in values:
        _d(value)


def _integer(*values: object) -> None:
    if any(not isinstance(value, int) or isinstance(value, bool) for value in values):
        raise ValueError("integer battle counts required")


def _floor(value: Decimal) -> int:
    return int(value.to_integral_value(rounding=ROUND_FLOOR))


def _appeal(base: Decimal, skill: float, judgment: str, buff: float,
            excellent: bool, interest: float, memory_correction: float = 1.) -> int:
    _finite(base, skill, buff, interest, memory_correction)
    if judgment not in JUDGMENT or min(skill, interest, memory_correction) < 0 or buff < -1:
        raise ValueError("invalid appeal parameters")
    first = _floor(base * (1 + _d(buff)) * _d(JUDGMENT[judgment]))
    second = _floor(_d(first) * _d(skill))
    third = _floor(_d(second) * _d(memory_correction))
    return _floor(_d(third) * (2 if excellent else 1) * _d(interest))


def calculate_p_appeal(p: int, support_total: int, week: int, skill: float,
                       judgment: str = "Perfect", *, buff: float = 0.,
                       excellent: bool = False, interest: float = 1.) -> int:
    """Apply INT at the documented stages, never to the initial base alone."""
    _finite(p, support_total, week)
    _integer(week)
    if min(p, support_total) < 0 or not 0 <= week <= 33:
        raise ValueError("invalid stats/week")
    base = 2 * _d(p) + _d(support_total) * _d(.2) * (1 + _d(.1) * week)
    return _appeal(base, skill, judgment, buff, excellent, interest)


def calculate_support_appeal(p: int, support_total: int, support_stat: int,
                             week: int, skill: float, judgment: str = "Perfect", *,
                             buff: float = 0., excellent: bool = False,
                             interest: float = 1.) -> int:
    _finite(p, support_total, support_stat, week)
    _integer(week)
    if min(p, support_total, support_stat) < 0 or support_stat > support_total or not 0 <= week <= 33:
        raise ValueError("invalid stats/week")
    base = _d(.5) * _d(p) + (_d(support_total) + 3 * _d(support_stat)) * _d(.2) * (1 + _d(.1) * week)
    return _appeal(base, skill, judgment, buff, excellent, interest)


@dataclass(frozen=True)
class BattleSkill:
    id: str
    source: str  # produce or support; panel origin does not change this
    components: tuple[tuple[str, float], ...]
    support_id: str | None = None
    buffs: tuple[tuple[str, float, int], ...] = ()  # attribute, additive fraction, turns
    heal_fraction: float = 0.
    unsupported: tuple[str, ...] = ()
    # Excellent appeal multiplier (0 = none): appeals on the selected judge's attribute, always Excellent.
    excellent: float = 0.


def excellent_components(skill: BattleSkill, target: str) -> tuple[tuple[str, float, bool], ...]:
    """(attribute, multiplier, forced_excellent) parts of one appeal at ``target``.

    Ordinary components are Excellent only when their attribute matches the judge.  An Excellent appeal
    ("Excellent N倍アピール", DB effect attribute ``excellent``) takes the judge's attribute, so it is always
    Excellent.  ASSUMPTION: its base uses the P/support stats of that attribute;
    the Wiki/DB text gives only the multiplier.
    """
    parts = tuple((a, m, a == target) for a, m in skill.components)
    if skill.excellent:
        parts += ((target, skill.excellent, True),)
    return parts


@dataclass(frozen=True)
class BattleSupport:
    id: str
    stats: Mapping[str, int]
    owned_skill: BattleSkill | None  # Guest is excluded from the battle deck.
    guest: bool = False


@dataclass(frozen=True)
class Passive:
    id: str
    attribute: str
    buff: float
    probability: float
    max_activations: int
    start_turn: int = 1
    end_turn: int = 999
    mental_ratio_max: float = 1.
    mental_ratio_min: float = 0.
    unsupported: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProducedIdol:
    stats: Mapping[str, int]
    mental: int
    learned_skills: tuple[BattleSkill, ...]
    week: int
    passives: tuple[Passive, ...] = ()
    memory_level: int = 0


@dataclass(frozen=True)
class Npc:
    id: str
    attribute: str
    base_appeal: int
    target_ranks: tuple[int, ...]  # 1-based; spear fallback or cycle order
    cycle: bool
    mental: int


@dataclass(frozen=True)
class Audition:
    id: str
    hp: Mapping[str, int]
    trend: tuple[str, ...]
    npcs: tuple[Npc, ...]  # A through E, top to bottom
    pass_rank: int
    mental_damage: int
    reaction_targets: int
    source: str = "explicit_fixture"
    checked_at: str = "unknown"
    mode: str = "Middle"


@dataclass(frozen=True)
class Scenario:
    """Required uncertain rules cannot silently become game facts.

    Weights may be any non-negative masses. Judgment probabilities, reaction
    sampling and memory turn scripts require calibration outside this module.
    """
    name: str
    player_judgments: tuple[tuple[str, float], ...]
    npc_judgments: tuple[tuple[str, float], ...]
    npc_late_multiplier: float
    reaction_mode: str  # uniform or player_always (stress test)
    fourth_turn_multiplier: float
    hand_recycle: str  # discard_shuffle or fifo
    ta_tie: str  # all or player_loses (conservative sensitivity)
    overkill_credit: bool
    assumptions: tuple[str, ...]
    player_buffs: tuple[tuple[str, float], ...] = ()
    player_low_mental_judgments: tuple[tuple[str, float], ...] = ()
    npc_low_mental_judgments: tuple[tuple[str, float], ...] = ()
    low_mental_ratio: float = .5
    player_memory_turns: tuple[int, ...] = ()
    npc_memory_turns: tuple[tuple[str, tuple[int, ...]], ...] = ()
    memory_good_probability: float = 1.
    max_turns: int = 50
    initial_hand_ids: tuple[str, ...] = ()
    interest: tuple[tuple[str, float], ...] = ()


@dataclass(frozen=True)
class BattleResult:
    won: bool
    rank: int
    stars: Mapping[str, int]
    turns: int
    mental: int
    complete: bool
    ta: tuple[str, ...]
    la: tuple[str, ...]
    contributions: Mapping[str, Mapping[str, int]]
    failure_reasons: tuple[str, ...]
    assumptions: tuple[str, ...]
    trace: tuple[dict, ...] = ()
    label: str = "CONDITIONAL_SIMULATION"


def build_deck(idol: ProducedIdol, supports: tuple[BattleSupport, ...]) -> tuple[BattleSkill, ...]:
    active = tuple(s for s in supports if not s.guest)
    if len(active) != 4 or sum(s.guest for s in supports) != 1 or len(idol.learned_skills) != 4:
        raise ValueError("require four equipped P skills, four Active and one Guest")
    if len({s.id for s in supports}) != 5:
        raise ValueError("duplicate support id")
    if any(s.source != "produce" for s in idol.learned_skills):
        raise ValueError("learned panel skills use the produce formula")
    for support in active:
        if (support.owned_skill is None or support.owned_skill.source != "support" or
                support.owned_skill.support_id != support.id):
            raise ValueError("owned support skill must bind to the active support")
    deck = idol.learned_skills + tuple(s.owned_skill for s in active)
    if len({s.id for s in deck}) != 8:
        raise ValueError("battle card ids must be unique (qualify source ids)")
    return deck


def award_bonus(contribution: int, maximum_hp: int, trend_rank: int) -> int:
    """Total earned bonus, based on this actor's contribution, not shared HP."""
    _finite(contribution, maximum_hp)
    if maximum_hp <= 0 or contribution < 0 or trend_rank not in (1, 2, 3):
        raise ValueError("invalid star inputs")
    return sum(contribution * 10 >= maximum_hp * level for level in (3, 6, 9)) * BONUS_STARS[trend_rank - 1]


def award_ta(contributions: Mapping[str, int], trend_rank: int, *, tie: str) -> dict[str, int]:
    _finite(*contributions.values())
    if tie not in ("all", "player_loses") or trend_rank not in (1, 2, 3) or not contributions:
        raise ValueError("invalid TA inputs")
    highest = max(contributions.values())
    winners = [actor for actor, total in contributions.items() if total == highest and total > 0]
    if tie == "player_loses" and len(winners) > 1:
        winners = [actor for actor in winners if actor != "player"]
    return {actor: TA_STARS[trend_rank - 1] for actor in winners}


def award_la(actor: str, trend_rank: int) -> dict[str, int]:
    if trend_rank not in (1, 2, 3):
        raise ValueError("invalid rank")
    return {actor: LA_STARS[trend_rank - 1]}


def placement(stars: Mapping[str, int], actor: str = "player") -> int:
    """Competition rank: WING ties qualify; damage is not a tiebreaker."""
    _finite(*stars.values())
    return 1 + sum(value > stars[actor] for value in stars.values())


def _safe_rank(audition_id: str, main: str, trend: tuple[str, ...]) -> int:
    """Conservative Middle baseline, not a claim of globally optimal play.

    The semifinal has Vi/Da rank-1 spears; Vo at rank 1 is the useful
    uncontested matchup. Final has a Vo rank-1 spear, so own Da/Vi rank 1
    can exploit Excellent. Otherwise use rank 2 TA plus opportunistic LA;
    never choose rank 3 solely because the main stat matches it.
    """
    main_rank = trend.index(main) + 1
    if audition_id == "semifinal":
        return 1 if main == "Vo" and main_rank == 1 else 2
    if audition_id == "final":
        return 1 if main in ("Da", "Vi") and main_rank == 1 else 2
    return main_rank


def _sample(weights: tuple[tuple[str, float], ...], rng: Random) -> str:
    return rng.choices([name for name, _ in weights], [weight for _, weight in weights], k=1)[0]


def npc_target(npc: Npc, turn: int, trend: tuple[str, ...], hp: Mapping[str, int]) -> str | None:
    ranks = npc.target_ranks
    if npc.cycle:
        start = (turn - 1) % len(ranks)
        ranks = ranks[start:] + ranks[:start]
    for rank in ranks + (1, 2, 3):
        if hp[trend[rank - 1]] > 0:
            return trend[rank - 1]
    return None


def _validate(idol: ProducedIdol, supports: tuple[BattleSupport, ...], audition: Audition,
              scenario: Scenario, deck: tuple[BattleSkill, ...]) -> None:
    if set(audition.trend) != set(ATTRIBUTES) or len(audition.trend) != 3:
        raise ValueError("trend must be a permutation of Vo/Da/Vi")
    for stats in (idol.stats,) + tuple(s.stats for s in supports if not s.guest):
        _finite(*stats.values())
        if any(a not in stats or stats[a] < 0 for a in ATTRIBUTES):
            raise ValueError("complete nonnegative stats required")
    _finite(*audition.hp.values(), idol.week, idol.mental, idol.memory_level, audition.pass_rank,
            audition.mental_damage, audition.reaction_targets, scenario.max_turns,
            scenario.npc_late_multiplier, scenario.fourth_turn_multiplier,
            scenario.memory_good_probability, scenario.low_mental_ratio)
    _integer(idol.week, idol.memory_level, audition.pass_rank, audition.reaction_targets, scenario.max_turns)
    if any(audition.hp.get(a, 0) <= 0 for a in ATTRIBUTES) or not 0 <= idol.week <= 33 or idol.mental <= 0:
        raise ValueError("invalid HP/week/mental")
    if not 0 <= idol.memory_level <= 5 or not 1 <= audition.pass_rank <= len(audition.npcs) + 1:
        raise ValueError("invalid memory/pass rank")
    if scenario.player_memory_turns and idol.memory_level == 0:
        raise ValueError("cannot script unavailable level-zero memory")
    if scenario.hand_recycle not in ("discard_shuffle", "fifo") or scenario.ta_tie not in ("all", "player_loses"):
        raise ValueError("unsupported hand/tie scenario")
    if scenario.reaction_mode not in ("uniform", "player_always") or scenario.max_turns < 1:
        raise ValueError("invalid reaction/turn scenario")
    if min(scenario.npc_late_multiplier, scenario.fourth_turn_multiplier) < 0:
        raise ValueError("negative scenario multiplier")
    if not 0 <= scenario.memory_good_probability <= 1 or not 0 <= scenario.low_mental_ratio <= 1:
        raise ValueError("invalid probability/mental threshold")
    if audition.mental_damage < 0 or audition.reaction_targets < 0:
        raise ValueError("invalid reaction")
    _finite(*(b for _, b in scenario.player_buffs), *(i for _, i in scenario.interest))
    if any(a not in ATTRIBUTES or b < -1 for a, b in scenario.player_buffs):
        raise ValueError("invalid scenario buff")
    if any(a not in ATTRIBUTES or i < 0 for a, i in scenario.interest):
        raise ValueError("invalid scenario interest")
    if not scenario.name or not scenario.assumptions:
        raise ValueError("scenario requires a name and explicit assumptions")
    for weights in (scenario.player_judgments, scenario.npc_judgments,
                    scenario.player_low_mental_judgments, scenario.npc_low_mental_judgments):
        _finite(*(weight for _, weight in weights), sum(weight for _, weight in weights))
        if weights and (any(name not in JUDGMENT or weight < 0 for name, weight in weights) or sum(w for _, w in weights) <= 0):
            raise ValueError("invalid judgment distribution")
    if not scenario.player_judgments or not scenario.npc_judgments:
        raise ValueError("judgment distributions are required")
    if len({n.id for n in audition.npcs}) != len(audition.npcs) or any(n.id == "player" for n in audition.npcs):
        raise ValueError("NPC ids must be unique and not player")
    for npc in audition.npcs:
        _finite(npc.mental, npc.base_appeal, *npc.target_ranks)
        _integer(*npc.target_ranks)
        if npc.attribute not in ATTRIBUTES or not npc.target_ranks or any(r not in (1, 2, 3) for r in npc.target_ranks):
            raise ValueError("invalid NPC definition")
        if npc.mental <= 0 or npc.base_appeal < 0:
            raise ValueError("invalid NPC stats")
    for skill in deck:
        _finite(*(m for _, m in skill.components), skill.heal_fraction, skill.excellent,
                *(b for _, b, _ in skill.buffs), *(t for _, _, t in skill.buffs))
        _integer(*(t for _, _, t in skill.buffs))
        if any(a not in ATTRIBUTES or m < 0 for a, m in skill.components) or skill.excellent < 0:
            raise ValueError("invalid skill component")
        if any(a not in ATTRIBUTES or b < -1 or t < 1 for a, b, t in skill.buffs) or not 0 <= skill.heal_fraction <= 1:
            raise ValueError("invalid skill effect")
    for passive in idol.passives:
        _finite(passive.buff, passive.probability, passive.max_activations, passive.start_turn,
                passive.end_turn, passive.mental_ratio_min, passive.mental_ratio_max)
        _integer(passive.max_activations, passive.start_turn, passive.end_turn)
        if passive.attribute not in ATTRIBUTES or not 0 <= passive.probability <= 1 or passive.max_activations < 0:
            raise ValueError("invalid passive")
        if not 0 <= passive.mental_ratio_min <= passive.mental_ratio_max <= 1 or passive.buff < -1:
            raise ValueError("invalid passive condition")
    if len({p.id for p in idol.passives}) != len(idol.passives):
        raise ValueError("passive ids must be unique")
    memory_ids = [npc_id for npc_id, _ in scenario.npc_memory_turns]
    if len(set(memory_ids)) != len(memory_ids) or not set(memory_ids) <= {n.id for n in audition.npcs}:
        raise ValueError("memory scripts must bind unique present NPCs")
    for turns in (scenario.player_memory_turns,) + tuple(turns for _, turns in scenario.npc_memory_turns):
        _integer(*turns)
        if any(turn < 1 for turn in turns) or len(set(turns)) != len(turns):
            raise ValueError("memory turns must be positive and unique")


def simulate_audition(produced_idol: ProducedIdol, supports: tuple[BattleSupport, ...],
                       audition: Audition, policy: str, rng: Random, scenario: Scenario,
                       *, keep_trace: bool = False) -> BattleResult:
    """Simulate actual turns. Inputs remain unchanged; Random is caller-owned.

    Memory scripts explicitly assume availability, rather than inventing gauge
    calibration. A selected target leaving before action consumes that action.
    """
    idol = produced_idol
    deck = build_deck(idol, supports)
    _validate(idol, supports, audition, scenario, deck)
    mode = policy.upper().split("_")[-1]
    if mode not in ("SAFE", "AWARE", "AGGRESSIVE", "FORCE", "DUAL"):
        raise ValueError("unknown battle policy")
    main = max(ATTRIBUTES, key=lambda a: idol.stats[a])
    prefix = policy.upper().split("_")[0]
    if prefix in ("VO", "DA", "VI"):
        main = {"VO": "Vo", "DA": "Da", "VI": "Vi"}[prefix]
    active = {s.id: s for s in supports if not s.guest}
    totals = {a: sum(s.stats[a] for s in active.values()) for a in ATTRIBUTES}
    hp = dict(audition.hp)
    ids = [n.id for n in audition.npcs] + ["player"]
    mental = {n.id: n.mental for n in audition.npcs} | {"player": idol.mental}
    stars = dict.fromkeys(ids, 0)
    damage = {a: dict.fromkeys(ids, 0) for a in ATTRIBUTES}
    ta, la, trace = [], [], []
    assumptions = list(scenario.assumptions)
    assumptions += ["换牌循环/退场后NPC循环相位由scenario假设", "已选目标提前退场时本回合不重选目标",
                    "排名排除Me归零对手；其已造成的贡献仍参与TA结算", "未模拟完整32周育成与50万粉丝路线"]
    if mode in ("SAFE", "AWARE"):
        assumptions.append("SAFE采用Middle低配流2TA+流1LA基线；不宣称逐属性全局最优，未优化延迟判定抢LA")
        if audition.mode != "Middle":
            assumptions.append("SAFE目标分支仅为Middle基线；该模式未验证")
    if mode == "AWARE":
        assumptions.append("TREND_AWARE当前是SAFE的显式别名，不是独立优化策略")
    for skill in deck:
        assumptions += [f"unsupported:{skill.id}:{effect}" for effect in skill.unsupported]
        if skill.excellent:
            assumptions.append("Excellentアピール按所选审查员属性的P/支援属性计算且必定Excellent（倍率来自Wiki/DB，基础属性为推断）")
    for passive in idol.passives:
        assumptions += [f"unsupported:{passive.id}:{effect}" for effect in passive.unsupported]
    if idol.passives:
        assumptions.append("被动独立抽样；未校准同回合发动数量限制/选择顺序")
    if scenario.player_memory_turns or scenario.npc_memory_turns:
        assumptions.append("思い出可用回合由显式脚本提供，未模拟槽增长/触发分布")
    else:
        assumptions.append("本情景未使用思い出；不可解释为其在真实对局不会发动")
    pile = list(deck)
    rng.shuffle(pile)
    if scenario.initial_hand_ids:
        if len(set(scenario.initial_hand_ids)) != 3 or not set(scenario.initial_hand_ids) <= {s.id for s in deck}:
            raise ValueError("initial hand must name three unique deck cards")
        by_id = {s.id: s for s in pile}
        hand = [by_id[i] for i in scenario.initial_hand_ids]
        pile = [s for s in pile if s.id not in scenario.initial_hand_ids]
    else:
        hand, pile = pile[:3], pile[3:]
    discard: list[BattleSkill] = []
    ongoing: list[tuple[str, float, int]] = []
    passive_uses = dict.fromkeys((p.id for p in idol.passives), 0)
    interest = {a: 1. for a in ATTRIBUTES} | dict(scenario.interest)
    memory_script = dict(scenario.npc_memory_turns)

    def emit(event: str, **values: object) -> None:
        if keep_trace:
            trace.append({"turn": turn, "event": event, **values})

    def hit(actor: str, target: str, value: int) -> None:
        if hp[target] <= 0 or mental[actor] <= 0:
            return
        credited = value if scenario.overkill_credit else min(hp[target], value)
        rank = audition.trend.index(target) + 1
        previous = award_bonus(damage[target][actor], audition.hp[target], rank)
        damage[target][actor] += credited
        bonus = award_bonus(damage[target][actor], audition.hp[target], rank) - previous
        stars[actor] += bonus
        hp[target] = max(0, hp[target] - value)
        emit("appeal", actor=actor, target=target, value=value, credited=credited, hp=hp[target], bonus=bonus)
        if hp[target] == 0:
            awarded = award_ta(damage[target], rank, tie=scenario.ta_tie)
            for winner, count in awarded.items():
                stars[winner] += count
            stars[actor] += award_la(actor, rank)[actor]
            if "player" in awarded:
                ta.append(target)
            if actor == "player":
                la.append(target)
            emit("judge_exit", target=target, ta=awarded, la=actor)

    def skill_damage(skill: BattleSkill, target: str, judgment: str, buffs: Mapping[str, float]) -> int:
        value = 0
        for attribute, multiplier, excellent in excellent_components(skill, target):
            kw = dict(buff=buffs.get(attribute, 0.), excellent=excellent, interest=interest[target])
            if skill.source == "produce":
                value += calculate_p_appeal(idol.stats[attribute], totals[attribute], idol.week, multiplier, judgment, **kw)
            else:
                value += calculate_support_appeal(idol.stats[attribute], totals[attribute],
                    active[skill.support_id].stats[attribute], idol.week, multiplier, judgment, **kw)
        return value

    def focus_target() -> str:
        alive = [a for a in audition.trend if hp[a] > 0]
        if mode in ("FORCE", "AGGRESSIVE"):
            return alive[0]
        if mode == "DUAL":
            return max(alive[:2], key=lambda a: idol.stats[a])
        target = audition.trend[_safe_rank(audition.id, main, audition.trend) - 1]
        if target not in alive:
            target = alive[0]
        return target

    def opponents_retired() -> bool:
        return bool(audition.npcs) and all(mental[npc.id] <= 0 for npc in audition.npcs)

    for turn in range(1, scenario.max_turns + 1):
        buffs = dict(scenario.player_buffs)
        ongoing = [(a, b, end) for a, b, end in ongoing if end >= turn]
        for attribute, amount, _ in ongoing:
            buffs[attribute] = buffs.get(attribute, 0.) + amount
        for passive in idol.passives:
            ratio = mental["player"] / idol.mental
            if (not passive.unsupported and passive.start_turn <= turn <= passive.end_turn
                    and passive.mental_ratio_min <= ratio <= passive.mental_ratio_max
                    and passive_uses[passive.id] < passive.max_activations and rng.random() < passive.probability):
                buffs[passive.attribute] = buffs.get(passive.attribute, 0.) + passive.buff
                passive_uses[passive.id] += 1
                emit("passive", id=passive.id, amount=passive.buff)
        player_dist = scenario.player_judgments
        if mental["player"] / idol.mental <= scenario.low_mental_ratio and scenario.player_low_mental_judgments:
            player_dist = scenario.player_low_mental_judgments
        memory = turn in scenario.player_memory_turns
        target = focus_target()
        if memory:
            multiplier = (0., .8, 1., 1.2, 1.4, 2.)[idol.memory_level]
            selected = BattleSkill("memory", "produce", tuple((a, multiplier) for a in ATTRIBUTES))
        else:
            if mode != "FORCE":
                # Immediate attainable LA is visible information, never future RNG.
                candidates = [(a, s) for a in audition.trend if hp[a] > 0 for s in hand
                              if skill_damage(s, a, "Perfect", buffs) >= hp[a]]
                if candidates:
                    target = max(candidates, key=lambda pair: LA_STARS[audition.trend.index(pair[0])])[0]
            # Select using the intended Perfect preview, before the random timing
            # outcome. Never grant policy foresight of the sampled judgment.
            selected = max(hand, key=lambda s: (min(hp[target], skill_damage(s, target, "Perfect", buffs)),
                                                skill_damage(s, target, "Perfect", buffs)))
        # Timing outcome is sampled only after committing the card and target.
        judgment = ("Perfect" if rng.random() < scenario.memory_good_probability else "Bad") if memory else _sample(player_dist, rng)
        emit("selection", hand=[s.id for s in hand], skill=selected.id, target=target, judgment=judgment, buffs=dict(buffs))
        actions = [(ORDER[judgment], -len(audition.npcs), "player", selected, target, judgment, memory)]
        for index, npc in enumerate(audition.npcs):
            if mental[npc.id] <= 0:
                continue
            dist = scenario.npc_judgments
            if mental[npc.id] / npc.mental <= scenario.low_mental_ratio and scenario.npc_low_mental_judgments:
                dist = scenario.npc_low_mental_judgments
            npc_memory = turn in memory_script.get(npc.id, ())
            grade = ("Perfect" if rng.random() < scenario.memory_good_probability else "Bad") if npc_memory else _sample(dist, rng)
            actions.append((ORDER[grade], -index, npc.id, npc, npc_target(npc, turn, audition.trend, hp), grade, npc_memory))
        # Vi -> Da -> Vo at the reaction phase, before Normal and Bad.
        for index, attribute in enumerate(ATTRIBUTES):
            actions.append((2, -index, "judge", None, attribute, "", False))
        for _, _, actor, item, destination, grade, is_memory in sorted(actions, key=lambda a: a[:2]):
            if not any(hp.values()) or mental["player"] <= 0 or opponents_retired():
                break
            if actor == "judge":
                if hp[destination] <= 0:
                    continue
                alive = [i for i in ids if mental[i] > 0]
                count = min(audition.reaction_targets, len(alive))
                if scenario.reaction_mode == "player_always" and count:
                    victims = ["player"] + rng.sample([i for i in alive if i != "player"], count - 1)
                else:
                    victims = rng.sample(alive, count)
                amount = _floor(_d(audition.mental_damage) * _d(scenario.fourth_turn_multiplier if turn % 4 == 0 else 1))
                for victim in victims:
                    mental[victim] = max(0, mental[victim] - amount)
                emit("reaction", judge=destination, victims=victims, amount=amount)
            elif mental[actor] > 0:
                destinations = [a for a in ATTRIBUTES if hp[a] > 0] if is_memory else [destination]
                for dest in destinations:
                    if actor == "player":
                        hit(actor, dest, skill_damage(item, dest, grade, buffs))
                    else:
                        excellent = is_memory or item.attribute == dest
                        amount = _floor(_d(item.base_appeal) * _d(JUDGMENT[grade]) * (2 if excellent else 1)
                                        * _d(scenario.npc_late_multiplier if turn >= 3 else 1) * _d(interest[dest]))
                        hit(actor, dest, amount)
                if actor == "player":
                    mental[actor] = min(idol.mental, mental[actor] + _floor(_d(idol.mental) * _d(item.heal_fraction)))
                    ongoing.extend((a, b, turn + duration - 1) for a, b, duration in item.buffs)
        if not memory:
            hand.remove(selected)
            discard.append(selected)
            if not pile:
                pile, discard = discard, []
                if scenario.hand_recycle == "discard_shuffle":
                    rng.shuffle(pile)
            hand.append(pile.pop(0))
        if not any(hp.values()) or mental["player"] <= 0 or opponents_retired():
            break
    complete = not any(hp.values()) or mental["player"] <= 0 or opponents_retired()
    rank = placement({i: stars[i] for i in ids if mental[i] > 0 or i == "player"})
    won = complete and mental["player"] > 0 and rank <= audition.pass_rank
    reasons = []
    if not complete:
        reasons.append("simulation_turn_limit")
    elif mental["player"] <= 0:
        reasons.append("mental_failure")
    elif not won:
        reasons.append("insufficient_stars")
        if audition.trend[0] not in ta:
            reasons.append("lost_trend1_ta")
        if audition.trend[0] not in la:
            reasons.append("lost_trend1_la")
    return BattleResult(won, rank, stars, turn, mental["player"], complete, tuple(ta), tuple(la),
                        damage, tuple(reasons), tuple(dict.fromkeys(assumptions)), tuple(trace))


def wilson_interval(wins: int, trials: int, *, z: float = 1.959963984540054) -> tuple[float, float]:
    _finite(wins, trials, z)
    _integer(wins, trials)
    if trials < 1 or not 0 <= wins <= trials or z <= 0:
        raise ValueError("valid nonempty sample and positive z required")
    p = wins / trials
    denominator = 1 + z * z / trials
    center = (p + z * z / (2 * trials)) / denominator
    half = z * sqrt(p * (1 - p) / trials + z * z / (4 * trials * trials)) / denominator
    _finite(center, half)
    return max(0., center - half), min(1., center + half)


def evaluate_scenarios(idol: ProducedIdol, supports: tuple[BattleSupport, ...], audition: Audition,
                       policy: str, scenarios: tuple[Scenario, ...], *, trials: int, seed: int,
                       confidence_z: float = 1.959963984540054) -> dict:
    """No scenario mixing: return each conditional rate, bounds and incompletes."""
    _finite(trials, seed, confidence_z)
    _integer(trials, seed)
    if confidence_z <= 0:
        raise ValueError("positive confidence z required")
    if trials < 1 or not scenarios or len({s.name for s in scenarios}) != len(scenarios):
        raise ValueError("positive trials and unique named scenarios required")
    rows = []
    for scenario in scenarios:
        runs = [simulate_audition(idol, supports, audition, policy, Random(seed + n), scenario) for n in range(trials)]
        completed = [r for r in runs if r.complete]
        wins = sum(r.won for r in completed)
        incomplete = trials - len(completed)
        lower, upper = wilson_interval(wins, len(completed), z=confidence_z) if completed else (0., 1.)
        failure_counts: dict[str, int] = {}
        for run in runs:
            for reason in run.failure_reasons:
                failure_counts[reason] = failure_counts.get(reason, 0) + 1
        rows.append({"scenario": scenario.name, "trials": trials, "completed": len(completed), "wins": wins,
                     "win_probability": wins / len(completed) if completed else None,
                     "interval": [lower, upper], "incomplete": incomplete,
                     "mean_turns": sum(r.turns for r in runs) / trials,
                     "ta_probability": {a: sum(a in r.ta for r in runs) / trials for a in audition.trend},
                     "failures": failure_counts, "assumptions": list(runs[0].assumptions)})
    return {"label": "CONDITIONAL_SIMULATION", "audition": audition.id, "trend": list(audition.trend),
            "policy": policy, "seed": seed, "scenarios": rows,
            "worst_lower_bound": min(row["interval"][0] if not row["incomplete"] else 0. for row in rows),
            "true_end_probability": None, "source": audition.source, "checked_at": audition.checked_at}


def middle_audition(audition_id: str, trend: tuple[str, ...]) -> Audition:
    """Small, source-backed Middle-only database; no unverified mode scaling."""
    # id -> hp, base, mental, reaction damage, pass rank, (attribute,ranks,cycle)
    data = {
        "s3_odotte": (52000, 3050, 350, 26, 2, (("Da", (3, 1, 2), True), ("Vo", (1, 2, 3), True),
            ("Vi", (2, 1, 3), True), ("Vo", (2, 3, 1), False), ("Da", (3, 2, 1), True))),
        "legend": (75000, 4250, 470, 30, 2, (("Vi", (2, 3, 1), False), ("Da", (3, 2, 1), True),
            ("Vo", (3, 1, 2), False), ("Vo", (1, 2, 3), False), ("Da", (1, 2, 3), False))),
        "semifinal": (66000, 3980, 450, 25, 1, (("Vi", (1, 2, 3), False), ("Vi", (1, 3, 2), True),
            ("Vo", (3, 1, 2), True), ("Vo", (1, 2, 3), True), ("Da", (1, 3, 2), False))),
        "final": (72000, 4636, 540, 15, 1, (("Vi", (3, 2, 1), True), ("Da", (3, 1, 2), False),
            ("Vo", (1, 2, 3), False), ("Vo", (2, 3, 1), True), ("Da", (3, 2, 1), False))),
    }
    if audition_id not in data:
        raise ValueError("audition not in verified Middle database")
    hp, base, mental, reaction, pass_rank, patterns = data[audition_id]
    npcs = tuple(Npc(chr(65 + i), attr, base, ranks, cycle, mental)
                 for i, (attr, ranks, cycle) in enumerate(patterns))
    return Audition(audition_id, dict.fromkeys(ATTRIBUTES, hp), trend, npcs, pass_rank, reaction, 3,
                    AUDITION_SOURCE, "2026-10-05", "Middle")
