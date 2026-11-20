"""Expected 32-week WING growth and budgeted panel paths, never P(TrueEnd)."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
import heapq
import math
import re
from typing import Any, Mapping
from ..support_effects import expected_stamina_multiplier, support_skill_expectation

from .models import ATTRIBUTES, STATS, ROOMS, CardFeatures, LiveSkill, Panel, integer, number
from .skill_identity import mastered_identities, panel_skill_identity, priced_panel_sequence

BASE = {
    "lesson": ((19, 4, 30), (22, 4, 120), (26, 5, 270), (30, 6, 480), (34, 7, 960)),
    "radio": ((4, 15, 4, 150), (4, 18, 4, 300), (5, 21, 5, 600), (6, 24, 6, 1000), (7, 27, 7, 1500)),
    "talk": ((4, 4, 15, 150), (4, 4, 18, 300), (5, 5, 21, 600), (6, 6, 24, 1000), (7, 7, 27, 1500)),
    "magazine": ((4, 4, 4, 250), (4, 4, 4, 500), (5, 5, 5, 900), (6, 6, 6, 1400), (7, 7, 7, 2000)),
}
SUPPORTED = {"mastery_stat", "mastery_sp", "mastery_stamina", "mastery_cap",
             "unit_mastery_stat", "unit_mastery_sp", "unit_mastery_cap", "unit_mastery_stamina",
             "audition_mastery_stat", "audition_mastery_sp", "bond", "excellent_boost"}
CONDITIONAL_SUPPORT = {"stamina_support"}
# おやすみブースト only adds Lv×3% × 15 on a 休む week, and a good team avoids those weeks; it is
# known and worth nothing here (not a data gap). The live week scorer (wing_score) still reads it on a real rest.
ZERO_VALUE_SUPPORT = {"rest_support"}


@dataclass(frozen=True)
class GrowthConfig:
    starting_stats: Mapping[str, float] | None = None
    starting_live_skills: tuple[LiveSkill, ...] = ()
    starting_caps: Mapping[str, float] = field(default_factory=lambda: dict.fromkeys(STATS, 500.0))
    starting_sp: float = 0
    starting_stamina: float = 100
    starting_fans: float = 0
    actions: tuple[Mapping[str, Any], ...] = ()
    facility_levels: tuple[int, ...] = (1, 2, 3, 4)
    placement_probability: float = 1 / 6
    perfect_probability: float = .1
    excellent_probability: float = .5
    event_completion_probability: float = .75
    first_place_probability: float = .5
    rest_recovery: float = 50
    rest_threshold: float = 50
    tension: float = 1.05
    events_completed: tuple[str, ...] = ()
    sensitivity_fraction: float = .15
    # Resume snapshots already include all purchased effects. SP is unspent here.
    start_week: int = 0  # absolute, zero-based next training week; 32 = before WING
    starting_panel_ids: tuple[str, ...] = ()
    starting_passives: tuple[Mapping[str, Any], ...] = ()
    starting_bonds: Mapping[str, float] | None = None
    starting_route_feasible: bool = True

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "GrowthConfig":
        fields = dict(value)
        if "starting_stats" in fields:
            if set(fields["starting_stats"]) != set(STATS):
                raise ValueError("starting_stats needs explicit Vo/Da/Vi/Me")
            fields["starting_stats"] = {a: number(n, a) for a, n in fields["starting_stats"].items()}
        if "starting_caps" in fields:
            if set(fields["starting_caps"]) != set(STATS):
                raise ValueError("starting_caps needs Vo/Da/Vi/Me")
            fields["starting_caps"] = {a: number(n, a) for a, n in fields["starting_caps"].items()}
        if "starting_live_skills" in fields:
            skills = tuple(LiveSkill.from_dict(s) for s in fields["starting_live_skills"])
            if len(skills) != 4 or len({_signature(s) for s in skills}) != 4:
                raise ValueError("starting_live_skills must contain four distinct equipped skills")
            fields["starting_live_skills"] = skills
        if "actions" in fields:
            actions = tuple({"action": a} if isinstance(a, str) else dict(a) for a in fields["actions"])
            if len(actions) != 32:
                raise ValueError("actions must contain exactly 32 weeks")
            for a in actions:
                if a.get("action") not in (*ROOMS, "rest", "audition", "flex"):
                    raise ValueError("unknown weekly action")
                if a["action"] == "audition":
                    number(a.get("fans", 0), "audition.fans")
                    number(a.get("required_fans", 0), "audition.required_fans")
                    if a.get("sp", 0):
                        raise ValueError("ordinary season auditions do not reward SP")
                    if any(stat not in STATS for stat in a.get("stats", {})):
                        raise ValueError("audition stats needs named Vo/Da/Vi/Me")
                    for stat, gain in a.get("stats", {}).items():
                        number(gain, f"audition.stats.{stat}")
            fields["actions"] = actions
        if "facility_levels" in fields:
            levels = tuple(fields["facility_levels"])
            if len(levels) != 4 or any(isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= 5 for n in levels):
                raise ValueError("facility_levels needs four season levels in 1..5")
            fields["facility_levels"] = levels
        for key in ("placement_probability", "perfect_probability", "excellent_probability",
                    "event_completion_probability", "first_place_probability", "sensitivity_fraction"):
            if key in fields:
                fields[key] = number(fields[key], key)
                if fields[key] > 1:
                    raise ValueError(f"{key} must be in 0..1")
        for key in ("starting_sp", "starting_stamina", "starting_fans", "rest_recovery", "rest_threshold", "tension"):
            if key in fields:
                fields[key] = number(fields[key], key)
        if fields.get("starting_stamina", 100) > 100 or fields.get("rest_threshold", 50) > 100:
            raise ValueError("stamina must be at most 100")
        fields["events_completed"] = tuple(fields.get("events_completed", ()))
        fields["start_week"] = integer(fields.get("start_week", 0), "start_week")
        if fields["start_week"] > 32:
            raise ValueError("start_week must be in 0..32")
        for key in ("starting_panel_ids", "events_completed"):
            values = tuple(fields.get(key, ()))
            if any(not isinstance(k, str) or not k for k in values) or len(set(values)) != len(values):
                raise ValueError(f"{key} needs unique qualified IDs")
            fields[key] = values
        passives = tuple(fields.get("starting_passives", ()))
        if any(not _passive_supported(p) for p in passives):
            raise ValueError("starting_passives contains an unsupported effect")
        fields["starting_passives"] = passives
        if fields.get("starting_bonds") is not None:
            fields["starting_bonds"] = {k: number(v, "bond") for k, v in fields["starting_bonds"].items()}
            if any(v > 100 for v in fields["starting_bonds"].values()):
                raise ValueError("bond must be in 0..100")
        if not isinstance(fields.get("starting_route_feasible", True), bool):
            raise ValueError("starting_route_feasible must be bool")
        return cls(**fields)


@dataclass(frozen=True)
class SkillPlan:
    learned_skills: tuple[LiveSkill, ...]
    passives: tuple[Mapping[str, Any], ...]
    unlocked_panel_ids: tuple[str, ...]
    cost: float
    caps: Mapping[str, float]


@dataclass(frozen=True)
class GrowthProjection:
    raw_stats: Mapping[str, float]
    effective_stats: Mapping[str, float]
    caps: Mapping[str, float]
    sp: float
    sp_required: float
    sp_readiness: float
    stamina: float
    fans_by_season: tuple[float, ...]
    route_feasible: bool
    skill_plan: SkillPlan
    assumptions: tuple[str, ...]
    unknowns: tuple[str, ...]
    range: Mapping[str, Any]
    timeline: tuple[Mapping[str, Any], ...]
    warnings: tuple[str, ...] = ()
    final_fans: float = 0
    final_bonds: Mapping[str, float] = field(default_factory=dict)

    @property
    def remaining_sp(self) -> float:
        return self.sp - self.skill_plan.cost


def bind_p_initial_state(p: CardFeatures, config: GrowthConfig) -> GrowthConfig:
    """Bind fresh-run stats and standard equipped skills; explicit snapshots win."""
    if config.start_week:
        return config
    changes = {}
    if config.starting_stats is None and set(p.card.initial_stats) == set(STATS):
        changes["starting_stats"] = dict(p.card.initial_stats)
    if not config.starting_live_skills:
        # Every P starts with these four skills, not its card-specific panel Live.
        # https://wikiwiki.jp/shinycolors/オーディションの基本#situation01
        changes["starting_live_skills"] = (
            LiveSkill("ボーカルアピールI", {"Vo": 1.0}),
            LiveSkill("ダンスアピールI", {"Da": 1.0}),
            LiveSkill("ビジュアルアピールI", {"Vi": 1.0}),
            LiveSkill("メンタルキュアI", {}, heal_fraction=.05),
        )
    return replace(config, **changes) if changes else config


def _signature(skill: LiveSkill) -> tuple:
    # Shared skill identities cannot be equipped twice, even from different cards.
    return (skill.id, tuple(sorted(skill.components.items())), skill.buff_attribute,
            skill.buff_amount, skill.buff_turns, skill.heal_fraction, skill.excellent)


def _attributes(strategy: str) -> tuple[str, ...]:
    attrs = tuple(a.title() for a in strategy.split("_") if a.upper() in ("VO", "DA", "VI"))
    if not attrs:
        raise ValueError("strategy must name Vo/Da/Vi")
    return tuple(dict.fromkeys(attrs))


# Season-end メンタル line for flexible weeks (S1 末 150–200, S2 末
# 200–240, S3 末 250–330, 准决赛前 ≥300). 2026-10-25: the old table fixed 4 ラジオ/トーク weeks in S1 and 2 トーク weeks in
# S2 and allowed only 14 lessons, so stamina and cap skills had little to act on; a flexible week now trains メンタル on
# ラジオ while the run is behind this line and the build's lesson otherwise (rest still replaces it on low stamina).
FLEX_ME_TARGETS = (150., 220., 290., 330.)


def flex_me_target(week: int) -> float:
    season, step = divmod(week, 8)
    start = FLEX_ME_TARGETS[season - 1] if season else 0.
    return start + (FLEX_ME_TARGETS[season] - start) * (step + 1) / 8


def _default_actions(attrs: tuple[str, ...]) -> tuple[Mapping[str, Any], ...]:
    def lesson() -> Mapping[str, Any]:
        return {"action": "flex"}
    def audition(key: str, fans: int, required: int, appeal: int) -> Mapping[str, Any]:
        return {"action": "audition", "audition_id": key, "fans": fans, "required_fans": required,
                "stats": {**dict.fromkeys(ATTRIBUTES, appeal), "Me": 0},
                "unbound_attribute_bonus": appeal, "reward_bound": "conservative_lower_bound",
                "source": "https://wikiwiki.jp/shinycolors/各オーディション個別攻略"}
    return tuple([{"action": "magazine"}] * 4 + [lesson() for _ in range(4)]
                 + [lesson() for _ in range(7)] + [audition("s2_evening", 10000, 1000, 5)]
                 + [lesson() for _ in range(6)]
                 + [audition("s3_spotlight", 40000, 10000, 6), audition("s3_odotte", 50000, 50000, 7)]
                 + [lesson() for _ in range(3)] + [audition("legend", 100000, 100000, 8)] * 4
                 + [audition("s4_festival", 50000, 50000, 7)])


def _passive_supported(effects: Mapping[str, Any]) -> bool:
    """Only battle's explicit probability/count/turn/Me-ratio contract is actionable."""
    allowed = {"id", "attribute", "buff", "probability", "max_activations", "start_turn", "end_turn",
               "mental_ratio_max", "mental_ratio_min", "unsupported", "condition"}
    if (set(effects) - allowed or effects.get("unsupported") or effects.get("condition")
            or effects.get("attribute") not in ATTRIBUTES):
        return False
    try:
        number(effects.get("buff"), "passive.buff", minimum=-1)
        if number(effects.get("probability"), "passive.probability") > 1:
            return False
        count = number(effects.get("max_activations"), "passive.max_activations")
        if not count.is_integer():
            return False
        first, last = (number(effects.get(k, default), k, minimum=1)
                       for k, default in (("start_turn", 1), ("end_turn", 999)))
        minimum, maximum = (number(effects.get(k, default), k)
                            for k, default in (("mental_ratio_min", 0), ("mental_ratio_max", 1)))
        return first.is_integer() and last.is_integer() and first <= last and minimum <= maximum <= 1
    except (ValueError, TypeError):
        return False


# 2026-10-27 (user): 「<idol>がライブに参加している場合」 holds when that idol performs: the P idol and the four active
# supports are on stage, the guest is not. The card library keeps such a passive as unparsed text; it is resolved per team.
_STAGE_PASSIVE = re.compile(r"passive requires battle interpreter: (Vocal|Dance|Visual)(\d+)%UP / "
                            r"\[条件:(?:(\d+)ターン以降)?(.+?)がライブに参加している場合\] \[確率:(\d+)%\] \[最大:(\d+)回\]")
_STAGE_ATTRIBUTE = {"Vocal": "Vo", "Dance": "Da", "Visual": "Vi"}
# 2026-11-01 (user asked to verify): 「<unit>全員がライブに参加」
# holds when every member performs, the P idol included (a 5-member unit can only fill P + 4 actives). Members are the
# ShinyColorsDB unitInfo groups of the local library, keyed by the short names the condition text uses.
UNIT_MEMBERS = {
    "イルミネ": ("櫻木真乃", "風野灯織", "八宮めぐる"),
    "アンティーカ": ("月岡恋鐘", "田中摩美々", "白瀬咲耶", "三峰結華", "幽谷霧子"),
    "放クラ": ("小宮果穂", "園田智代子", "西城樹里", "杜野凛世", "有栖川夏葉"),
    "アルスト": ("大崎甘奈", "大崎甜花", "桑山千雪"),
    "ストレイ": ("芹沢あさひ", "黛冬優子", "和泉愛依"),
    "ノクチル": ("浅倉透", "樋口円香", "福丸小糸", "市川雛菜"),
    "シーズ": ("七草にちか", "緋田美琴"),
    "コメティック": ("斑鳩ルカ", "鈴木羽那", "郁田はるき"),
}
# Readiness and the pruning prior value a passive over an assumed audition length; no audition turn count is recorded
# yet, and the battle simulation (scenarios) uses its own turns.
PASSIVE_REFERENCE_TURNS = 6


def idol_key(name: str | None) -> str:
    return re.sub(r"\s+", "", name or "")


def on_stage(p: CardFeatures | None, supports: tuple[CardFeatures, ...]) -> frozenset[str]:
    """The performing idols plus 「<unit>全員」 for each unit whose members all perform (:data:`UNIT_MEMBERS`)."""
    idols = {idol_key(c.card.idol) for c in (*((p,) if p else ()), *supports)}
    return frozenset(idols | {unit + "全員" for unit, members in UNIT_MEMBERS.items() if idols.issuperset(members)})


# 2026-10-31: 「履歴に1ジャンル以下」 counts the Vo/Da/Vi genres
# of the last skills used and also holds before any skill; a single-attribute build appeals in one genre, so it holds
# there (inference: the P's off-attribute default cards are overwritten or not played). 「又は3ジャンル以上」 also
# holds for one genre. A dual build mixes two genres and fails both. This token in the live context marks such a build.
SINGLE_GENRE = "#single-genre"
_HISTORY_PASSIVE = re.compile(r"passive requires battle interpreter: (Vocal|Dance|Visual)(\d+)%UP / "
                              r"\[条件:(?:(\d+)ターン以降)?履歴に1ジャンル以下(?:又は3ジャンル以上)?\] "
                              r"\[確率:(\d+)%\] \[最大:(\d+)回\]")


def live_context(p: CardFeatures | None, supports: tuple[CardFeatures, ...], attrs: tuple[str, ...]) -> frozenset[str]:
    """The performing idols (:func:`on_stage`) plus :data:`SINGLE_GENRE` for a single-attribute build."""
    return on_stage(p, supports) | ({SINGLE_GENRE} if len(attrs) == 1 else set())


def stage_passive(panel: Panel, stage: frozenset[str]) -> dict[str, Any] | None:
    """A passive whose only condition is an idol taking part in the Live, as the battle contract when that idol is on
    stage, or a 「履歴に1ジャンル以下」 passive in a single-genre build (``stage`` holds :data:`SINGLE_GENRE`), else None.
    The text must be exactly one attribute buff with probability and count, and agree with effects."""
    if panel.kind != "passive" or len(panel.unsupported) != 1:
        return None
    m = _STAGE_PASSIVE.fullmatch(panel.unsupported[0])
    h = None if m else _HISTORY_PASSIVE.fullmatch(panel.unsupported[0])
    if m and idol_key(m[4]) in stage:
        attribute, buff, chance, count = _STAGE_ATTRIBUTE[m[1]], int(m[2]) / 100, int(m[5]), int(m[6])
        first = int(m[3]) if m[3] else None
    elif h and SINGLE_GENRE in stage:
        attribute, buff, chance, count = _STAGE_ATTRIBUTE[h[1]], int(h[2]) / 100, int(h[4]), int(h[5])
        first = int(h[3]) if h[3] else None
    else:
        return None
    if (panel.effects.get("buffs") != {attribute: buff} or panel.effects.get("probability") != chance / 100
            or panel.effects.get("max_activations") != count):
        return None
    resolved = {"id": panel.id, "attribute": attribute, "buff": buff, "probability": chance / 100,
                "max_activations": count, **({"start_turn": first} if first else {})}
    return resolved if _passive_supported(resolved) else None


def passive_turn_buff(passive: Mapping[str, Any], turns: int = PASSIVE_REFERENCE_TURNS) -> float:
    """Expected buff per turn over ``turns``: buff x E[min(max, Binomial(eligible turns, p))] / turns. A Me-ratio window
    is taken to hold for half the Live."""
    first, last = max(1, int(passive.get("start_turn", 1))), min(turns, int(passive.get("end_turn", 999)))
    n, count = max(0, last - first + 1), int(passive.get("max_activations", 0))
    chance = float(passive.get("probability", 0))
    if float(passive.get("mental_ratio_max", 1)) < 1 or float(passive.get("mental_ratio_min", 0)) > 0:
        chance *= .5
    expected = sum(min(count, k) * math.comb(n, k) * chance ** k * (1 - chance) ** (n - k) for k in range(n + 1))
    return float(passive.get("buff", 0)) * expected / turns


def passive_buffs(passives, attrs: tuple[str, ...]) -> dict[str, float]:
    """Expected per-turn appeal buff of the learned passives for each build attribute."""
    return {a: sum(passive_turn_buff(x) for x in passives if x.get("attribute") == a) for a in attrs}


def _traversable(card: CardFeatures, p: Panel | None, completed_events: tuple[str, ...]) -> bool:
    """The node can be learned (owned 凸, event done). Its own value may still be unmodelled: it is a path step."""
    return (p is not None and p.kind != "unknown" and p.required_lb <= card.ownership.limit_break
            and (not p.event or f"{card.card.card_id}:{p.event}" in completed_events))


def _modeled(p: Panel) -> bool:
    """The node's value is modelled. 2026-10-14 (live W.I.N.G. + user): a Live skill whose side effects are not parsed
    (興味無視, link / duet buffs, 注目度 ...) still counts its parsed appeal multiplier -- dropping it removed every gold
    「+」 skill and, through a P root like 「黒百合前で待ち合わせ」, the whole P panel from the plan."""
    if p.kind == "live":
        return bool(p.effects.get("components") or p.effects.get("excellent"))
    return (not p.unsupported and not p.effects.get("unsupported")
            and (p.kind != "passive" or _passive_supported(p.effects)))


def _adjacent_panel_path(card: CardFeatures, panel_id: str, completed_events: tuple[str, ...],
                         purchased: tuple[str, ...], mastered_skills=(),
                         stage: frozenset[str] = frozenset()) -> tuple[Panel, ...] | None:
    """Minimum incremental SP witness through explicit, reviewed undirected edges."""
    topology = card.card.panel_topology
    panels = {p.id: p for p in card.panels}
    def usable(key: str) -> bool:
        return _traversable(card, panels.get(key), completed_events)
    if not usable(panel_id) or not _target(panels[panel_id], stage):
        return None
    neighbors = {k: set() for k in panels}
    for left, right in topology["edges"]:
        if left in neighbors and right in neighbors:
            neighbors[left].add(right)
            neighbors[right].add(left)
    paid = set(purchased)
    queue, best = [], {}
    def route_state(path):
        history = set(mastered_skills)
        history.update(identity for k in path if (identity := panel_skill_identity(panels[k])) is not None)
        return path[-1], frozenset(history)
    def route_cost(path):
        sequence = ((k, panels[k]) for k in path if k not in paid)
        return sum(price for _, _, price, _, _ in priced_panel_sequence(sequence, mastered_skills))
    for key in sorted(set(topology["roots"]) | paid):
        if usable(key):
            cost = route_cost((key,))
            entry = (cost, (key,))
            best[route_state((key,))] = entry
            heapq.heappush(queue, entry)
    while queue:
        cost, path = heapq.heappop(queue)
        key = path[-1]
        if best.get(route_state(path)) != (cost, path):
            continue
        if key == panel_id:
            return tuple(panels[k] for k in path)
        for nxt in sorted(neighbors[key]):
            if not usable(nxt) or nxt in path:
                continue
            next_path = (*path, nxt)
            entry = (route_cost(next_path), next_path)
            state = route_state(next_path)
            if state not in best or entry < best[state]:
                best[state] = entry
                heapq.heappush(queue, entry)
    return None


def _target(panel: Panel, stage: frozenset[str]) -> bool:
    """Worth a path of its own: a modelled value, or an idol-in-the-Live passive whose idol is on stage."""
    return _modeled(panel) or stage_passive(panel, stage) is not None


def panel_closure(card: CardFeatures, panel_id: str, completed_events: tuple[str, ...] = (),
                  purchased: tuple[str, ...] = (), mastered_skills=(),
                  stage: frozenset[str] = frozenset()) -> tuple[Panel, ...] | None:
    """Use verified adjacency when present; legacy explicit AND witnesses remain valid. ``stage`` (the team's performing
    idols) lets an idol-in-the-Live passive be a target; callers without a team keep the old rule."""
    if card.card.panel_topology is not None:
        return _adjacent_panel_path(card, panel_id, completed_events, purchased, mastered_skills, stage)
    panels = {panel.id: panel for panel in card.panels}
    done: set[str] = set()
    visiting: set[str] = set()
    ordered: list[Panel] = []
    if panel_id not in panels or not _target(panels[panel_id], stage):
        return None
    def visit(key: str) -> bool:
        if key in done:
            return True
        panel = panels.get(key)
        if (panel is None or key in visiting or not panel.prerequisites_known
                or not _traversable(card, panel, completed_events)):
            return False
        visiting.add(key)
        if not all(visit(parent) for parent in panel.prerequisites):
            return False
        visiting.remove(key)
        done.add(key)
        ordered.append(panel)
        return True
    return tuple(ordered) if visit(panel_id) else None


def _live_value(skill: LiveSkill, attrs: tuple[str, ...]) -> float:
    # An Excellent N-times appeal lands on the targeted judge's attribute, so it counts for every strategy attribute
    # (same scale as features.mechanical_vector: max(component, excellent)).
    own = sum(skill.components.get(a, 0) + (skill.buff_amount if skill.buff_attribute == a else 0) for a in attrs)
    return own / len(attrs) + skill.excellent


def _equip(skills: tuple[LiveSkill, ...], skill: LiveSkill, attrs: tuple[str, ...]) -> tuple[LiveSkill, ...]:
    if not (skill.components or skill.excellent) or _signature(skill) in {_signature(s) for s in skills}:
        return skills
    if len(skills) < 4:
        return (*skills, skill)
    weakest = min(range(4), key=lambda i: _live_value(skills[i], attrs))
    if _live_value(skill, attrs) <= _live_value(skills[weakest], attrs):
        return skills
    return tuple(skill if i == weakest else s for i, s in enumerate(skills))


def _plan(cards: tuple[CardFeatures, ...], raw: Mapping[str, float], cfg: GrowthConfig,
          attrs: tuple[str, ...], projected_caps: Mapping[str, float],
          stage: frozenset[str] = frozenset()) -> tuple[tuple[tuple[str, Panel], ...], float]:
    """Marginal path benefit, not a sum of all nominal panel strength."""
    selected: set[str] = set(cfg.starting_panel_ids)
    ordered: list[tuple[str, Panel]] = []
    caps = dict(projected_caps)
    skills = cfg.starting_live_skills
    skill_history = {_signature(s) for s in skills}
    mastered = mastered_identities(cards, cfg.starting_panel_ids, skills)
    while True:
        candidates = []
        for card in cards:
            for panel in card.panels:
                paid = tuple(p.id for p in card.panels if f"{card.card.card_id}:{p.id}" in selected)
                closure = panel_closure(card, panel.id, cfg.events_completed, paid, mastered, stage)
                if closure is None:
                    continue
                extra = [(f"{card.card.card_id}:{p.id}", p) for p in closure
                         if f"{card.card.card_id}:{p.id}" not in selected]
                if not extra:
                    continue
                proposed = skills
                proposed_caps = dict(caps)
                benefit = 0.0
                priced = list(priced_panel_sequence(extra, mastered))
                for _, p, _, duplicate, _ in priced:
                    if p.kind == "live":
                        skill = LiveSkill.from_dict(p.effects)
                        if not duplicate and _signature(skill) not in skill_history:
                            proposed = _equip(proposed, skill, attrs)
                    elif p.kind == "cap":
                        for a, v in p.effects.items():
                            prior = proposed_caps[a]
                            proposed_caps[a] += float(v)
                            weight = 1 if a in attrs else .2 if a == "Me" else 0
                            benefit += weight * max(0, min(float(v), raw[a] - prior)) / 100
                    elif p.kind == "stat":
                        benefit += sum(min(float(p.effects.get(a, 0)), max(0, proposed_caps[a] - raw[a])) / 100 for a in attrs)
                    elif p.kind == "passive":
                        # Conditions are preserved for battle. No generic text or unsupported condition bonus; an
                        # idol-in-the-Live condition is resolved for this team (stage_passive). 2026-10-27: valued like
                        # the stat it multiplies (appeal ~ stat x (1 + buff)), in the cap's units of 100 stat; the old
                        # buff x probability (0.08 for 20% at 40% x2) never beat a cap panel.
                        passive = stage_passive(p, stage) or p.effects
                        a = passive.get("attribute")
                        if (a in attrs and passive.get("max_activations", 0) > 0
                                and not passive.get("unsupported")
                                and not passive.get("condition")):
                            benefit += passive_turn_buff(passive) * min(raw[a], proposed_caps[a]) / 100
                benefit += sum(_live_value(s, attrs) for s in proposed) - sum(_live_value(s, attrs) for s in skills)
                cost = sum(price for _, _, price, _, _ in priced)
                if benefit > 0:
                    candidates.append((benefit / max(1, cost), benefit, panel.id, extra, proposed, proposed_caps))
        if not candidates:
            break
        _, _, _, extra, skills, caps = max(candidates, key=lambda c: (c[0], c[1], c[2]))
        ordered.extend(extra)
        selected.update(key for key, _ in extra)
        skill_history.update(_signature(LiveSkill.from_dict(p.effects)) for _, p in extra if p.kind == "live")
        mastered.update(identity for _, p in extra if (identity := panel_skill_identity(p)) is not None)
    initial = mastered_identities(cards, cfg.starting_panel_ids, cfg.starting_live_skills)
    return tuple(ordered), float(sum(price for _, _, price, _, _ in priced_panel_sequence(ordered, initial)))


def _simulate(p: CardFeatures, supports: tuple[CardFeatures, ...], guest: CardFeatures | None,
              cfg: GrowthConfig, actions: tuple[Mapping[str, Any], ...], attrs: tuple[str, ...],
              purchases: tuple[tuple[str, Panel], ...] = (), factor: float = 1) -> dict[str, Any]:
    training = (*supports, *((guest,) if guest else ()))
    stamina_effects = [effect for card in training for skill, lv in card.support_effects
                       if skill.kind == "stamina_support"
                       and (effect := support_skill_expectation(skill, lv, card.card.rarity)) is not None]
    stamina_multiplier = expected_stamina_multiplier(stamina_effects)
    cards = (p, *training)
    stage = live_context(p, supports, attrs)
    by_id = {c.card.card_id: c for c in cards}
    raw = dict(cfg.starting_stats or dict.fromkeys(STATS, 0.0))
    stats = dict(raw)
    caps = dict(cfg.starting_caps)
    sp, cost, stamina, fans = cfg.starting_sp, 0.0, cfg.starting_stamina, cfg.starting_fans
    skills = cfg.starting_live_skills
    passives: list[Mapping[str, Any]] = list(cfg.starting_passives)
    skill_history = {_signature(s) for s in skills}
    mastered = mastered_identities(cards, cfg.starting_panel_ids, skills)
    learned: list[str] = list(cfg.starting_panel_ids)
    # A support's own bond skill is its starting bond, including the guest.
    bonds = (dict(cfg.starting_bonds) if cfg.starting_bonds is not None else
             {c.card.card_id: min(100, sum(v * 5 for s, v in c.support_effects if s.kind == "bond" and not s.unsupported)) for c in training})
    timeline, season_fans = [], []
    route = cfg.starting_route_feasible
    def buy() -> None:
        nonlocal cost, skills
        if not route:
            return
        for key, panel in purchases:
            if key in learned:
                continue
            # A cheap downstream node must not bypass an unaffordable neighbor.
            prefix = key.rsplit(":", 1)[0]
            feature = by_id[prefix]
            if not _traversable(feature, panel, cfg.events_completed):
                continue
            topology = feature.card.panel_topology
            if topology is not None:
                neighbors = {b if a == panel.id else a for a, b in topology["edges"]
                             if panel.id in (a, b)}
                reachable = (panel.id in topology["roots"] or
                             any(f"{prefix}:{parent}" in learned for parent in neighbors))
            else:
                reachable = (panel.prerequisites_known and
                             all(f"{prefix}:{parent}" in learned for parent in panel.prerequisites))
            if not reachable:
                continue
            identity = panel_skill_identity(panel)
            duplicate = identity is not None and identity in mastered
            price = 0 if duplicate else panel.sp_cost
            if cost + price > sp:
                continue
            cost += price
            learned.append(key)
            if panel.kind == "cap":
                for a, v in panel.effects.items():
                    caps[a] += float(v)
            elif panel.kind == "stat":
                for a, v in panel.effects.items():
                    raw[a] += float(v)
                    stats[a] = min(caps[a], stats[a] + float(v))
            elif panel.kind == "live":
                skill = LiveSkill.from_dict(panel.effects)
                if not duplicate and _signature(skill) not in skill_history:
                    skills = _equip(skills, skill, attrs)
                skill_history.add(_signature(skill))
                if identity is not None:
                    mastered.add(identity)
            elif panel.kind == "passive" and _modeled(panel):   # an unmodelled bridge passive is learned, not applied
                passives.append(dict(panel.effects))
            elif panel.kind == "passive" and (resolved := stage_passive(panel, stage)) is not None:
                passives.append(resolved)
    buy()  # Learning before WING remains possible when no training weeks remain.
    lesson_turn = 0
    for week, planned in enumerate(actions[cfg.start_week:], start=cfg.start_week):
        buy()  # Caps learned before this week's gain; no retroactive restoration.
        action = planned["action"] if route else "blocked"
        if action == "flex":   # ラジオ while behind the メンタル line, else the build's lessons in turn
            action = "radio" if stats["Me"] < flex_me_target(week) else attrs[lesson_turn % len(attrs)]
        spending = 15 if action in ATTRIBUTES else 20 if action in ("radio", "talk") else 5 if action == "magazine" else 0
        if spending and (stamina < cfg.rest_threshold or stamina < spending):
            action = "rest"
        lesson_turn += planned["action"] == "flex" and action in ATTRIBUTES
        gains = dict.fromkeys(STATS, 0.0)
        sp_gain, fan_gain, cap_gain, recovery = 0.0, 0.0, dict.fromkeys(STATS, 0.0), 0.0
        if action == "blocked":
            pass
        elif action == "rest":
            stamina = min(100, stamina + cfg.rest_recovery)
        elif action == "audition":
            if fans < float(planned.get("required_fans", 0)):
                route = False
                action = "blocked"
            else:
                fan_gain = float(planned.get("fans", 0))
                for a in STATS:
                    gains[a] = float(planned.get("stats", {}).get(a, 0))
                for card in training:
                    for skill, lv in card.support_effects:
                        if skill.unsupported:
                            continue
                        if skill.kind == "audition_mastery_sp":
                            sp_gain += lv * cfg.first_place_probability
                        elif skill.kind == "audition_mastery_stat" and skill.attribute in STATS:
                            gains[skill.attribute] += lv * cfg.first_place_probability
        else:
            level = planned.get("facility_level", cfg.facility_levels[week // 8])
            if isinstance(level, bool) or not isinstance(level, int) or not 1 <= level <= 5:
                raise ValueError("facility_level must be 1..5")
            if action in ATTRIBUTES:
                base_attr, base_sp, base_fans = BASE["lesson"][level - 1]
                base = {action: base_attr}
            else:
                main, mental, base_sp, base_fans = BASE[action][level - 1]
                base = {"Vo" if action == "radio" else "Da" if action == "talk" else "Vi": main, "Me": mental}
            trouble_base = 20 if action == "magazine" else 70
            trouble = min(1, max(0, math.ceil(((trouble_base - stamina) / 10) ** 3) / 100))
            success = 1 - trouble
            expected_perfect = success * cfg.perfect_probability
            mastery = dict.fromkeys(STATS, 0.0)
            corr = dict.fromkeys(STATS, 0.0)
            excellent = 0.0
            sp_mastery = 0.0
            for card in training:
                placement = cfg.placement_probability
                bond = bonds[card.card.card_id]
                same_unit = float(card.card.unit is not None and card.card.unit == p.card.unit)
                same_unit += sum(placement for other in training if other.card.card_id != card.card.card_id
                                and card.card.unit is not None and card.card.unit == other.card.unit)
                for a in STATS:
                    corr[a] += placement * .0004 * card.stats.get(a, 0) * (1 + .0001 * bond) * (1 + .1 * week)
                exc = placement * cfg.excellent_probability if bond >= 50 else 0
                boost = sum(lv * .02 for s, lv in card.support_effects if s.kind == "excellent_boost" and not s.unsupported)
                excellent += exc * (1 + boost)
                for skill, lv in card.support_effects:
                    if skill.unsupported or (skill.kind.startswith("mastery_") and skill.room is None):
                        continue
                    if skill.room is not None and skill.room != action:
                        continue
                    multiplier = placement * (same_unit if skill.kind.startswith("unit_mastery_") else 1)
                    if skill.kind in ("mastery_stat", "unit_mastery_stat") and skill.attribute in STATS:
                        mastery[skill.attribute] += lv * multiplier
                    elif skill.kind in ("mastery_sp", "unit_mastery_sp"):
                        sp_mastery += lv * multiplier
                    elif skill.kind in ("mastery_stamina", "unit_mastery_stamina"):
                        recovery += lv * 3 * multiplier * success
                    elif skill.kind in ("mastery_cap", "unit_mastery_cap") and skill.attribute in STATS:
                        cap_gain[skill.attribute] += lv * multiplier * (success + expected_perfect)
                bonds[card.card.card_id] = min(100, bond + 8 * placement * success)
            for a in STATS:
                # Ceil at each success outcome before averaging. Failure penalties are unknown.
                successful_gain = base.get(a, 0) * ((1 + corr[a]) * cfg.tension + .2 * excellent) + mastery[a]
                gains[a] = ((success - expected_perfect) * math.ceil(successful_gain)
                            + expected_perfect * math.ceil(2 * successful_gain)) * factor
            sp_preview = base_sp * (cfg.tension + .2 * excellent) + sp_mastery
            sp_gain = ((success - expected_perfect) * math.ceil(sp_preview)
                       + expected_perfect * math.ceil(2 * sp_preview)) * factor
            fan_gain = base_fans * (success + expected_perfect)  # Support stat correction for fans is zero.
            stamina = min(100, max(0, stamina - spending * stamina_multiplier + recovery))
        # Expected event increments have an explicit week; event-gated panels still need confirmed IDs.
        for card in cards if action != "blocked" else ():
            for index, event in enumerate(card.card.events):
                if f"{card.card.card_id}:{event.get('id', '')}" in cfg.events_completed:
                    continue
                if event.get("unsupported") or event.get("condition"):
                    continue
                if int(event.get("week", min(31, (index + 1) * 6))) != week:
                    continue
                event_gains = event.get("gains", event.get("stats", {}))
                if event.get("options"):
                    option = max(event["options"], key=lambda o: sum(float(o.get("gains", o.get("stats", {})).get(a, 0)) for a in attrs))
                    event_gains = option.get("gains", option.get("stats", {}))
                for a in STATS:
                    gains[a] += float(event_gains.get(a, 0)) * cfg.event_completion_probability * factor
                sp_gain += float(event_gains.get("SP", 0)) * cfg.event_completion_probability * factor
        sp += sp_gain
        fans += fan_gain
        for a in STATS:
            caps[a] += cap_gain[a]
            raw[a] += gains[a]
            stats[a] = min(caps[a], stats[a] + gains[a])
        buy()  # Earned SP opens caps for future growth; discarded growth stays discarded.
        if week % 8 == 7:
            season_fans.append(fans)
            route = route and fans >= (1000, 10000, 50000, 500000)[week // 8]
        timeline.append({"week": week, "planned_action": planned["action"], "action": action,
                         "audition_id": planned.get("audition_id"),
                         "reward_bound": planned.get("reward_bound"),
                         "unbound_attribute_bonus": planned.get("unbound_attribute_bonus", 0),
                         "stats": dict(stats), "raw_stats": dict(raw), "caps": dict(caps),
                         "sp": sp, "spent_sp": cost, "stamina": stamina, "fans": fans,
                         "bonds": dict(bonds), "route_feasible": route,
                         "unlocked_panel_ids": tuple(learned), "learned_skills": tuple(skills),
                         "passives": tuple(passives), "skill_slots": len(skills),
                         "skill_slots_verified": len(skills) == 4})
    return {"raw": raw, "stats": stats, "caps": caps, "sp": sp, "stamina": stamina,
            "fans": tuple(season_fans), "route": route, "timeline": tuple(timeline),
            "final_fans": fans, "bonds": bonds,
            "plan": SkillPlan(skills, tuple(passives), tuple(learned), cost, dict(caps))}


def estimate_team_growth(p: CardFeatures, supports: tuple[CardFeatures, ...], guest: CardFeatures,
                         strategy: str, config: GrowthConfig, *,
                         replay_panel_ids: tuple[str, ...] | None = None) -> GrowthProjection:
    if len(supports) != 4:
        raise ValueError("growth needs exactly four active supports and one guest")
    return _estimate(p, supports, guest, strategy, config, replay_panel_ids=replay_panel_ids)


def estimate_partial_growth(p: CardFeatures, supports: tuple[CardFeatures, ...], guest: CardFeatures | None,
                            strategy: str, config: GrowthConfig) -> GrowthProjection:
    """Beam guide only; empty slots contribute no fabricated cards or READY claim."""
    if len(supports) > 4:
        raise ValueError("partial active team cannot exceed four supports")
    return _estimate(p, supports, guest, strategy, config, partial=True)


def _estimate(p: CardFeatures, supports: tuple[CardFeatures, ...], guest: CardFeatures | None,
              strategy: str, config: GrowthConfig, *, partial: bool = False,
              replay_panel_ids: tuple[str, ...] | None = None) -> GrowthProjection:
    config = bind_p_initial_state(p, config)
    attrs = _attributes(strategy)
    actions = config.actions or _default_actions(attrs)
    cards = (p, *supports, *((guest,) if guest else ()))
    stage = live_context(p, supports, attrs)
    if p.card.kind != "P" or any(c.card.kind != "S" for c in cards[1:]):
        raise ValueError("invalid P/support kind")
    if len({c.card.idol for c in cards}) != len(cards) or len({c.card.card_id for c in cards}) != len(cards):
        raise ValueError("duplicate card or idol")
    known_panels = {f"{c.card.card_id}:{p.id}" for c in cards for p in c.panels}
    if set(config.starting_panel_ids) - known_panels:
        raise ValueError("starting panel does not belong to the owned card profile")
    if config.starting_bonds is not None and set(config.starting_bonds) != {c.card.card_id for c in cards[1:]}:
        raise ValueError("starting_bonds must bind every selected support including Guest")
    if config.start_week and (config.starting_stats is None or config.starting_bonds is None):
        raise ValueError("resume needs observed starting_stats and starting_bonds")
    if config.starting_stats is not None and any(config.starting_stats[a] > config.starting_caps[a] for a in STATS):
        raise ValueError("effective starting stat exceeds its current cap")
    # The Guest never plays its own Live, so only its growth-relevant gaps count (CardFeatures.guest_unknowns).
    unknowns = [f"{c.card.card_id}:{u}" for c in cards
                for u in (*(c.guest_unknowns if c is guest else c.unknowns), *c.unsupported)]
    warnings = []
    if partial:
        unknowns.append("partial beam guide: unselected slots absent; not a complete team recommendation")
    for week, action in enumerate(actions[config.start_week:], start=config.start_week):
        if action["action"] == "audition" and not action.get("source"):
            unknowns.append(f"week {week}: custom audition rewards/unlock lack source; explicit route assumption only")
        if action.get("unbound_attribute_bonus"):
            warnings.append(f"week {week}: {action.get('audition_id')} attribute bonus +{action['unbound_attribute_bonus']} unbound; conservative lower bound, not exact expected reward")
    for c in cards:
        if c.card.panel_topology is not None and not c.card.panel_topology["complete"]:
            unknowns.append(f"{c.card.card_id}:panel adjacency graph is incomplete")
        for s, _ in c.support_effects:
            if s.kind not in SUPPORTED and s.kind not in ZERO_VALUE_SUPPORT and not (s.kind in CONDITIONAL_SUPPORT and
                    support_skill_expectation(s, _, c.card.rarity) is not None):
                unknowns.append(f"{c.card.card_id}:unsupported support {s.id}/{s.kind}")
            if s.kind.startswith("unit_mastery_") and c.card.unit is None:
                unknowns.append(f"{c.card.card_id}:unit unknown")
            if s.kind.startswith("mastery_") and s.room is None:
                unknowns.append(f"{c.card.card_id}:{s.id} mastery room unknown")
        for event in c.card.events:
            if event.get("unsupported") or event.get("condition"):
                unknowns.append(f"{c.card.card_id}:event condition unsupported {event.get('id', '')}")
        for panel in c.card.panels:
            paid = tuple(p.id for p in c.panels if f"{c.card.card_id}:{p.id}" in config.starting_panel_ids)
            if panel_closure(c, panel.id, config.events_completed, paid, stage=stage) is None:
                warnings.append(f"{c.card.card_id}:skipped unavailable/unsupported/unknown panel path {panel.id}")
                if panel.required_lb <= c.ownership.limit_break and (
                        (c.card.panel_topology is None and not panel.prerequisites_known)
                        or ((panel.unsupported or panel.effects.get("unsupported") or panel.kind == "unknown"
                             or (panel.kind == "passive" and not _passive_supported(panel.effects)))
                            and stage_passive(panel, stage) is None)):
                    unknowns.append(f"{c.card.card_id}:unmodeled panel path/effect {panel.id}")
    if config.starting_stats is None:
        unknowns.append("starting_stats missing: zero-origin growth only, not final attribute evidence")
    if len(config.starting_live_skills) != 4:
        unknowns.append("four starting_live_skills missing: incomplete P combat hand")
    forecast = _simulate(p, supports, guest, config, actions, attrs)
    if replay_panel_ids is None:
        purchases, required = _plan(cards, forecast["raw"], config, attrs, forecast["caps"], stage)
    else:
        if (not isinstance(replay_panel_ids, tuple) or
                any(not isinstance(k, str) for k in replay_panel_ids) or
                len(set(replay_panel_ids)) != len(replay_panel_ids)):
            raise ValueError("replay_panel_ids must be an ordered, unique tuple of qualified panel IDs")
        available = {f"{c.card.card_id}:{panel.id}": (c, panel) for c in cards for panel in c.panels}
        if set(replay_panel_ids) & set(config.starting_panel_ids):
            raise ValueError("replay lists only new purchases, not already purchased panels")
        if any(k not in available or panel_closure(available[k][0], available[k][1].id,
                   config.events_completed, tuple(p.id for p in available[k][0].panels
                   if f"{available[k][0].card.card_id}:{p.id}" in config.starting_panel_ids), stage=stage) is None
                   for k in replay_panel_ids):
            raise ValueError("replay contains an unavailable or unverified panel path")
        purchases = tuple((k, available[k][1]) for k in replay_panel_ids)
        mastered = mastered_identities(cards, config.starting_panel_ids, config.starting_live_skills)
        required = float(sum(price for _, _, price, _, _ in priced_panel_sequence(purchases, mastered)))
    result = _simulate(p, supports, guest, config, actions, attrs, purchases)
    if replay_panel_ids is not None and result["plan"].unlocked_panel_ids != (*config.starting_panel_ids, *replay_panel_ids):
        raise ValueError("replay could not fund and unlock the entire ordered plan within remaining weeks")
    low = result if partial else _simulate(p, supports, guest, config, actions, attrs, purchases, 1 - config.sensitivity_fraction)
    high = result if partial else _simulate(p, supports, guest, config, actions, attrs, purchases, 1 + config.sensitivity_fraction)
    assumptions = (
        f"绝对第{config.start_week}周起的{32 - config.start_week}周期望投影；普通试镜按已通过条件记粉丝，不是完整育成抽样或True End概率。",
        "默认行动表是可替换的路线假设：S1前4周杂志凑粉丝，其余非试镜周按メンタル进度线（各季末150/220/290/330）选ラジオ或主属性课程，体力低时休息；S3先4万SPOT LIGHT后5万增刊号；不称最优路线。自定义试镜奖励/门槛仅在其显式输入假设下成立。",
        "普通试镜只奖励Vo/Da/Vi中两项小值、一项大值，不奖励Me；大值属性未绑定时仅计三项小值作为保守下界，遗漏额外属性奖励可见于warnings，不是准确期望且未猜均匀随机。",
        f"同行概率={config.placement_probability:g}；独立均匀1/6只是未校准先验；unit计同组合P及其他支援的期望同行，不含本人。",
        f"设施各季={config.facility_levels}；升级概率未知，以用户轨迹/假设替代。",
        f"成功后的Perfect概率={config.perfect_probability:g}，Excellent概率={config.excellent_probability:g}均为假设；bond≥50才计Excellent。",
        f"事件完成概率={config.event_completion_probability:g}、默认事件周与分支选择为假设；事件锁必须有显式完成记录。",
        f"休息回复={config.rest_recovery:g}，低于{config.rest_threshold:g}替代本周；不额外增加育成周。",
        "体力サポート与おやすみブースト按真实技能Lv×3%及稀有度推算；多卡独立发动是假设，减耗相加并以0为下限，不降低当前失败率。",
        "休息增强按各卡独立发动的回复期望相加，再按体力100裁剪；不是休息结果的完整概率分布。",
        f"试镜第一名概率={config.first_place_probability:g}为显式假设，第二名通过不保证オデマス发动。",
        "SP省略未知全属性平均×0.525修正；普通试镜无SP，未计WING赛后或诞生偶像SP。",
        "失败仅按零成长保守近似；失败属性损失/情绪变化与事件相关性未校准。",
        "敏感性范围按课程/事件增量参数变动，非概率分位数；包含SP路径可负担性变化。",
    )
    return GrowthProjection(result["raw"], result["stats"], result["caps"], result["sp"], required,
                            1.0 if required <= 0 else min(1.0, result["sp"] / required),
                            result["stamina"], result["fans"], result["route"], result["plan"], assumptions,
                            tuple(sorted(set(unknowns))),
                            {"kind": "partial_not_evaluated" if partial else "sensitivity", "fraction": config.sensitivity_fraction,
                             "low": low["stats"], "high": high["stats"]}, result["timeline"],
                            tuple(sorted(set(warnings))), result["final_fans"], dict(result["bonds"]))
