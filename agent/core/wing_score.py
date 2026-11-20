"""W.I.N.G. weekly action scorer: pure, deterministic, no input, no screen reading.

User decisions: score every Produce action and take the highest; a room's value comes
from its supports, events and facility level, not from its stat label; a 約束 is neutral by itself (its kept/broken
effects are ordinary score terms); stay close to but under the stat cap; auditions are gated (S3 50k only after the
40k win lifts the rank).
Every value marked 推测 is a guess to be re-fitted from shadow-mode logs; never tune on a single run.

Unit: PEP (Primary-Equivalent Points), 1 PEP = +1 primary stat below its cap.

Candidates are the buttons available on the observed screen: every lesson / work room, rest, every audition.
Breakdown terms (all PEP, summing to the score):

  stat_gain, cap_overflow   primary gain × (1 − trouble) × (1 + Perfect); the part above "cap + buyable cap" removed
  off_stat, mental          other stats (思い出 appeal only) and Me (valuable below the season survival line)
  sp                        optional reviewed-path marginal value; otherwise SP × w_sp
  bond, events              supports in the room: bond toward the Excellent line (early seasons) and support events
  fans                      work/lesson fans and banked audition fans (value floor + urgency)
  audition_progress         P(win) × wins-saved × independent future-week estimate × growth
  fan_urgency               P(win) × wins-saved × independent future-week estimate × U
  audition_stats            P(win) × win stat reward (primary part, conservative)
  stamina, trouble_risk     stamina value via a trouble-rate look-ahead; failure / catastrophe losses
  promise                   kept: tension +25 and affection +10; broken: tension −10 (W1); no hard rule
  tension                   rest 大成功 tension bump

Unknown inputs are never invented: a term that cannot be computed is 0 and its name is listed in ``unknown``; a
candidate whose core value cannot be computed is moved to ``excluded`` with the reason. Lower bounds (formula without
the unread supports) are labelled ``*_lower_bound``; a team profile (``TeamProfile``) can replace them with full
formula estimates. The audition win probability is a pluggable ``WinModel``; the default uses
``wing_audition.audition_win_estimate`` (P(pass)) and falls back to the logistic when that returns no estimate.
"""
from __future__ import annotations

import dataclasses
import math
from typing import Any, Callable, Mapping

from agent.core import wing_state as ws
from agent.core.true_end.sp_value import SPValueContext, marginal_sp_value

# ---- W1 base tables (仕様考察, player reverse-engineered; wiki marks some cells as guesses) ----------------------------
LESSON_BASE = {1: (19, 4, 30), 2: (22, 4, 120), 3: (26, 5, 270), 4: (30, 6, 480), 5: (34, 7, 960)}  # stat, SP, fans
WORK_BASE = {   # Lv -> ({stat: gain}, SP, fans); radio Vo/Me, talk Da/Me + SP, magazine Vi/Me + fans
    "radio": {1: ({"Vo": 4, "Me": 15}, 4, 150), 2: ({"Vo": 4, "Me": 18}, 4, 300), 3: ({"Vo": 5, "Me": 21}, 5, 600),
              4: ({"Vo": 6, "Me": 24}, 6, 1000), 5: ({"Vo": 7, "Me": 27}, 7, 1500)},
    "talk": {1: ({"Da": 4, "Me": 4}, 15, 150), 2: ({"Da": 4, "Me": 4}, 18, 300), 3: ({"Da": 5, "Me": 5}, 21, 600),
             4: ({"Da": 6, "Me": 6}, 24, 1000), 5: ({"Da": 7, "Me": 7}, 27, 1500)},
    "magazine": {1: ({"Vi": 4, "Me": 4}, 4, 250), 2: ({"Vi": 4, "Me": 4}, 4, 500), 3: ({"Vi": 5, "Me": 5}, 5, 900),
                 4: ({"Vi": 6, "Me": 6}, 6, 1400), 5: ({"Vi": 7, "Me": 7}, 7, 2000)},
}
WORK_MAIN_STAT = {"radio": "Me", "talk": "SP", "magazine": "fans"}
STAMINA_COST = {"lesson": 15, "radio": 20, "talk": 20, "magazine": 5}
TROUBLE_BASE = {"lesson": 70, "radio": 70, "talk": 70, "magazine": 20}
MAX_STAMINA = 100                                   # W1 (not shown in game)
# Win stat rewards (W6, Middle) -> (low, low, high); which stat gets the high part is undocumented, so only the low
# part is credited to the primary (conservative).
AUDITION_WIN_STATS = {(1, 1_000): (2, 2, 4), (2, 10_000): (5, 5, 10), (3, 40_000): (6, 6, 12),
                      (3, 50_000): (7, 7, 14), (4, 50_000): (7, 7, 14), (4, 100_000): (8, 8, 16)}
# 推奨能力値 fallback (2026-10-09, live seg2b: the reader returned {} on every S1 page and every audition became
# p_win_unknown). Used only when the primary's recommended value is unread; the estimate is then marked
# ``table_fallback``. Values are the highest primary / Me 推奨 observed per season on native audition tabs (the shown
# value rises during a season and is shared by the season's pages, so the max is the conservative choice):
#   S1 Vo 70 (demo2 #503/#504, early weeks) -> 100 (#659-#781, all of 1k/2k/3k), Me 70;
#   S2 10k Vo 150/Me 150 (demo1 #418, WING_DEMO_20261005 row 2), Vo 120/Me 150 (demo2 #944);
#   S3 40k Vo 330/Me 250 (demo1 #480), 15k/40k Vo 390/Me 250 (demo2 #1263/#1697), 50k Vo 380/Me 280 (demo1 #525),
#      50k Vo 410/Me 280 (demo2 #1561);
#   S4 15k/25k/50k Vo 350-410/Me 280 (demo2 #1772/#1929/#2351), 100k Vo 450-500/Me 450 (demo1 #711/#763, demo2
#      #1930/#2143; THE LEGEND 推奨 Vo450~480/Me450).
# Unobserved pages (S1 5k, the semifinal/final) use the season row; no season -> no fallback (still unknown).
RECOMMENDED_FALLBACK = {1: (100, 70), 2: (150, 150), 3: (390, 250), 4: (410, 280)}       # season -> (primary, Me)
RECOMMENDED_FALLBACK_BY_REWARD = {(3, 50_000): (410, 280), (4, 100_000): (500, 450)}
# Static W.I.N.G. audition table (2026-10-09, user: 试镜信息需要提前知道而不是每周实时查看). Each season offers the same
# 4 tiers in every week of the season (live seg2b S1 weeks_left 6 and 4 showed the same
# 1k/2k/3k/5k pages; demo 2 S1/S3/S4 pages repeat across weeks). Rank gates (W6 解放 column,
# user-confirmed: S2 15k needs D, S3 50k C, S4 100k B; S1 5k E). Difficulty 「難易度」 from native pages
# (live seg2b S1 1/2/3; demo 1 #418 S2 10k 8, #480 S3 40k 13, #525 S3 50k 15; demo 2 #1697 S3 15k 11, #1560 S3 20k
# 12; S4 16/17/18/19), None where no page was observed. 推奨能力値 is deliberately not in the table: it
# rises during a season, so the scorer uses ``RECOMMENDED_FALLBACK`` (marked ``table_fallback``). Seasons outside
# 1-4 (W.I.N.G. semifinal/final) are not covered -> the navigator falls back to the audition-tab survey.
WING_AUDITION_TABLE = {   # season -> ((fan_reward, required_rank, difficulty), ...)
    1: ((1_000, "F", 1), (2_000, "F", 2), (3_000, "F", 3), (5_000, "E", None)),
    2: ((3_000, "F", None), (5_000, "F", None), (10_000, "F", 8), (15_000, "D", None)),
    3: ((15_000, "F", 11), (20_000, "F", 12), (40_000, "F", 13), (50_000, "C", 15)),
    4: ((15_000, "F", 16), (25_000, "F", 17), (50_000, "F", 18), (100_000, "B", 19)),
}


# 強化週間: while one runs, each season's final week adds a limited audition,
# so that week's audition tab is surveyed instead of taken from the table. Game days (05:00 JST), inclusive.
SPECIAL_AUDITION_WINDOWS = (("2026-10-20", "2026-11-10"),)


def special_window_active(day: str | None = None) -> bool:
    if day is None:
        from agent.core.wing_ledger import game_day
        day = game_day()
    return any(start <= day <= end for start, end in SPECIAL_AUDITION_WINDOWS)


def table_audition_pages(season: Any, weeks_left: Any = None) -> list | None:
    """The season's audition pages from ``WING_AUDITION_TABLE`` in the navigator's survey-page format, or None when
    the table does not cover this season/week (then the audition tab must be surveyed)."""
    if not isinstance(season, int) or isinstance(season, bool) or season not in WING_AUDITION_TABLE:
        return None
    if weeks_left is not None and not (isinstance(weeks_left, int) and 1 <= weeks_left <= ws.WEEKS_PER_SEASON):
        return None
    return [{"reward": reward, "difficulty": difficulty, "recommended": {}, "required_rank": rank, "locked": False,
             "source": "table"} for reward, rank, difficulty in WING_AUDITION_TABLE[season]]


# Rank by cumulative fans. E–B 推测 from two TE runs and W6 entry ranks: the season gates (1k/10k/50k/100k) coincide
# with E/D/C/B — S2 15k (needs D) was skipped at 3,228 fans, S3 50k (needs C) only after the 40k win (13,852 -> 53,852),
# THE LEGEND (needs B) from 100k. A 500k / S 1M: two independent web sources agree (wikiwiki
# 各オーディション個別攻略 + gameranbu ファン人数の増やし方, R1/R10).
# Still a projection: the rank read on the screen always wins (``_rank_max``); an unread rank is never invented.
RANK_FAN_THRESHOLDS = (("S", 1_000_000), ("A", 500_000), ("B", 100_000), ("C", 50_000), ("D", 10_000), ("E", 1_000))
RANK_THRESHOLD_SOURCES = {"S": "wiki_R1+gameranbu_R10", "A": "wiki_R1+gameranbu_R10", "B": "te_runs_inferred",
                          "C": "te_runs_inferred", "D": "te_runs_inferred", "E": "te_runs_inferred"}
# Hard trouble cap (2026-10-08): a stamina-consuming candidate whose own トラブル率 is at or above this is
# excluded (``trouble_hard_cap``); only auditions, rest and low-trouble rooms remain. Never configurable above 20 %.
TROUBLE_HARD_CAP_MAX = 20.0
FALLBACKS = ("empty_room", "rest", "none")      # ScoreParams.fallback
KIND_ORDER = {"audition": 0, "lesson": 1, "work": 2, "rest": 3}
TERMS = ("stat_gain", "cap_overflow", "off_stat", "mental", "sp", "bond", "events", "fans", "audition_progress",
         "fan_urgency", "audition_stats", "stamina", "trouble_risk", "promise", "tension")


@dataclasses.dataclass(frozen=True)
class ScoreParams:
    """All scorer weights in one place."""
    w_off: float = 0.05                     # off-stat PEP per point (only 思い出 all-stat appeal)
    w_me_low: float = 0.8                   # Me PEP per point while Me < season survival line
    w_me_high: float = 0.05
    me_targets: tuple = ((1, 150), (2, 200), (3, 250), (4, 300))   # W4/W7
    w_sp: float = 1.0                       # PEP per SP (推测)
    w_sp_min: float = 0.3
    w_sp_max: float = 2.0
    w_overflow: float = 1.0                 # 1.0 removes exactly the gain above the cap
    # Perfect (大成功, ×2 of the whole gain incl. Excellent). The preview never includes it;
    # it is revealed after the action. ~10 % is conditional on success (W1), so the expected gain is
    # preview × P(success) × (1 + p_perfect_given_success) — counted once, in ``perfect_given_success``.
    perfect_rate: float = 0.10              # base P(Perfect | success) (W1)
    perfect_up: float = 0.0                 # パーフェクト発生率 UP 小 (+0.01–0.02) when the team has it (unread -> 0)
    # Unverified: whether the preview already contains マスタリー and テンション. True = take the preview as
    # the full non-Perfect gain; False = treat it as a lower bound (noted on the candidate). Keep explicit.
    preview_includes_mastery_tension: bool = True
    grad_factor: float = 1.0                # G.R.A.D.編補正 slot of the W1 formula; 1 for W.I.N.G.
    # ---- optimistic room bound (2026-10-10 preview check; ``room_upper_gains``) ----
    # A room without a read preview gets an upper gain: the W1 formula with every present support at this card stat
    # (murmurmaid Lv80 primary 250; 推测 generous ceiling), bond 100, all of them Excellent
    # (unless the room shows no badge) and full tension (k = 4). Unread support count -> preview_upper_supports_unread.
    preview_upper_support_stat: int = 350
    preview_upper_supports_unread: int = 3
    preview_upper_tension_k: int = 4
    # SP: table base + this much per present support (マスタリーSP Lv × 1 per support, W3; native previews show lesson
    # SP +18..+67 against a table base of 4-7, demo1 #325/#460; 推测 ceiling)
    preview_upper_sp_per_support: int = 20
    # ---- supports in the room ----
    bond_beta: float = 0.5                  # β: PEP per bond point below 50, fading to 0 at W = bond_fade_week (推测)
    bond_fade_week: int = 16
    bond_gain: int = 8                      # bond per shared successful action (W1)
    support_event_rate: float = 0.10        # chance a present support triggers its event (推测; W1: all by bond ~75)
    support_event_value: float = 15.0       # PEP per event (推测; e.g. +20 Vo / +10 Me / +10–15 SP, W11)
    # ---- risk / stamina ----
    # 2026-10-08 (推测, to be re-fitted from logs): a failure = this week's gain lost + tension −20 for
    # ~4 weeks + a small stat drop; a catastrophe also empties stamina (1–2 extra rest weeks ≈ 2 × future_week_pep)
    # and drops stats more. Before: 10 / 60 (a 3-support SP room still beat rest at 25 % trouble).
    l_fail: float = 30.0                    # PEP lost by a FAILURE besides the gain (推测)
    l_cat_base: float = 120.0               # PEP lost by a CATASTROPHE besides stamina (推测)
    cat_share_floor: float = 0.10           # share of troubles that are 大失敗, at least (wiki 仕様考察 R6: ~10 %)
    trouble_hard_cap: float = TROUBLE_HARD_CAP_MAX   # % own trouble rate excluding a stamina-consuming action
    stamina_lookahead: float = 0.35         # γ: weight of the trouble rate one lesson later (推测)
    lookahead_cost: int = 15
    rest_expected: int = 50                 # E[rest] of +30/+50/+70 (probabilities unknown; 推测 mean)
    flagged_recovery_stamina: int = 9       # recovery when only the boolean flag is known (推测 ≈ マスタリー体力 Lv3)
    # ---- promise (W1: kept → tension +25, affection +10; broken → tension −10; refused → −5) ----
    tension_horizon_weeks: float = 4.0      # weeks a tension change keeps affecting lessons (推测)
    affection_point_value: float = 0.3      # PEP per affection point (思い出 Lv every 25; 推测)
    b_tension_rest: float = 2.0             # rest 大成功 tension bump (推测, tiny)
    # User setting 「约定」: 1.0 = neutral (an ordinary term, rule 2); > 1 = prefer keeping. Scales
    # both the kept value and the broken cost; never a hard rule.
    promise_weight: float = 1.0                # 中立 by default (2026-10-14 user: a small bonus only); 优先守约 = 4.0
    # ---- fans and auditions ----
    urgency_max: float = 3.0
    urgency_s0: float = 1.0                 # 推测
    urgency_tau: float = 0.6                # 推测
    slack_p_floor: float = 0.5
    fan_value_floor: float = 0.3            # banked fans after the target is met (推测)
    # A needed win displaces one week either now or later; later lesson weeks are worth more (W1: corr grows with
    # W, facility Lv rises), so deferring costs this growth per spare week. 推测 ~3 %/week.
    lesson_growth_per_week: float = 0.03
    # S4 True End route: a needed 100k-type win is worth the best room × (base + growth × season
    # progress) -- later weeks weigh more; a room wins only when its own score is exceptionally high.
    te_s4_base: float = 1.0
    te_s4_growth: float = 1.5
    # S1 fans often come from high-Lv lessons and work alone. A needed win is discounted by the
    # chance that the best room's weekly fans cover the deficit by season end (logistic in the uncovered share).
    passive_fan_tau: float = 0.15          # 推测
    future_week_pep: float = 30.0          # independent usual-week value (uncalibrated PEP prior, not today's best room)
    # TE targets (W7): S3 ≥ 100k lets THE LEGEND start in S4 week 1; S4 needs the 500k True End total.
    season_fan_targets: tuple = ((1, 1_000), (2, 10_000), (3, 100_000), (4, ws.TRUE_END_FANS))
    max_plan_steps: int = 8
    # ---- 強化週間 limited auditions (wing_special.season_plan) ----
    special_promise_weight: float = 2.0     # S2 entry (親愛度 25): a kept promise (+10 affection) weighs this much
    special_bond_beta: float = 0.25         # S3 entry (three red bonds): bond PEP per point while β has faded (推测)
    special_me_pep_max: float = 2.0         # S4 entry (Me 500): Me PEP per point at most (bonus value / deficit)
    # ---- default audition win model (R_spear is not observable yet, so only R_rec terms) ----
    a0: float = 0.5
    a2: float = 3.0
    a3: tuple = ((1, 0.3), (2, 0.6), (3, -1.2))
    a4: float = 0.2
    a5: float = 3.0
    mental_ok_ratio: float = 0.65           # observed pass at Me 310/450 = 0.69; proposed 0.7
    audition_mastery_value: float = 0.0     # needs the team's オーディションマスタリー
    # ---- fallback ----
    floor_ratio: float = 0.6
    # 2026-10-08: 15 -> 10. 2026-10-25 user 「尽量不要让课程失败」 (but not lower: idle weeks waste
    # growth): it also caps the stamina actions (effective hard cap = min(trouble_hard_cap, trouble_rest_rate)).
    trouble_rest_rate: float = 10.0
    # User setting 「都不划算时」(formerly 低分兜底, 2026-10-06/08). Only when the best candidate is a lesson / work
    # scoring below floor_ratio × empty primary room gain: "empty_room" (default: the empty primary lesson if its own
    # trouble ≤ trouble_rest_rate, not capped and not below rest's score, else rest), "rest" (rest directly) or
    # "none" (keep the best-scored action). Never replaces a best rest or audition, never a higher-scoring rest.
    fallback: str = "empty_room"

    @classmethod
    def from_overrides(cls, overrides: Mapping[str, Any] | None) -> "ScoreParams":
        if not overrides:
            return cls()
        names = {f.name for f in dataclasses.fields(cls)}
        unknown = set(overrides) - names
        if unknown:
            raise ValueError(f"未知评分参数：{sorted(unknown)}")
        params = dataclasses.replace(cls(), **dict(overrides))
        if not params.w_sp_min <= params.w_sp <= params.w_sp_max:
            raise ValueError("w_sp 必须在 w_sp_min..w_sp_max 之间")
        if params.fallback not in FALLBACKS:
            raise ValueError(f"fallback 必须为 {FALLBACKS} 之一")
        if (isinstance(params.trouble_hard_cap, bool) or not isinstance(params.trouble_hard_cap, (int, float))
                or not 0 < params.trouble_hard_cap <= TROUBLE_HARD_CAP_MAX):
            raise ValueError(f"trouble_hard_cap 必须在 (0, {TROUBLE_HARD_CAP_MAX:g}] 之内")
        if not 0 <= params.cat_share_floor <= 1:
            raise ValueError("cat_share_floor 必须在 0..1 之间")
        if not 0 <= params.promise_weight <= 5:
            raise ValueError("promise_weight 必须在 0..5 之间")
        if (isinstance(params.future_week_pep, bool) or not isinstance(params.future_week_pep, (int, float))
                or not math.isfinite(params.future_week_pep) or params.future_week_pep <= 0):
            raise ValueError("future_week_pep 必须是大于 0 的有限数值")
        return params


UNIT_MASTERY_ROOM = "unit"      # masteries row of ユニットマスタリー (any room, per other unit member present)


@dataclasses.dataclass(frozen=True)
class SupportProfile:
    """One support of the formed team (from the team screen / card table); every field optional."""
    id: str
    stats: Mapping[str, int] | None = None          # card stats (corr_i uses the room's stat)
    bond0: int | None = None                        # opening bond = アイドルの絆 Lv × 5
    masteries: Mapping[str, Mapping[str, float]] = dataclasses.field(default_factory=dict)
    # room type -> {"stat": Lv, "stat_Vo/Da/Vi/Me": Lv, "sp": Lv, "stamina": Lv}; room type: lesson_Vo/lesson_Da/lesson_Vi/
    # radio/talk/magazine (the icon under the support on the formation screen names the target room, W2)
    unknowns: tuple[str, ...] = ()                  # incomplete card/skill inputs carried from the strict adapter
    unit: str | None = None
    stamina_support: tuple[Mapping[str, float], ...] = ()
    rest_support: tuple[Mapping[str, float], ...] = ()


@dataclasses.dataclass(frozen=True)
class TeamProfile:
    supports: Mapping[str, SupportProfile] = dataclasses.field(default_factory=dict)
    produce_unit: str | None = None


def unit_partners(holder: SupportProfile, present: list[SupportProfile], team: TeamProfile) -> int:
    """Same-unit idols at this action, including P and excluding the skill holder."""
    if not holder.unit:
        return 0
    return sum(s.id != holder.id and s.unit == holder.unit for s in present) + int(team.produce_unit == holder.unit)


def first_place_probability(state: ws.WingState, audition: ws.Audition, primary: str,
                            params: ScoreParams) -> tuple:
    from agent.core import wing_audition as wa
    audition, fallback = with_recommended_fallback(state, audition, primary)
    estimate = wa.audition_win_estimate(wa.context_from_wing(state, audition, primary, need_first=False))
    return estimate.get("p_first"), tuple(fallback) + tuple(f"wa:{u}" for u in estimate.get("unknown", ()))


@dataclasses.dataclass(frozen=True)
class Candidate:
    id: str
    kind: str                       # lesson | work | rest | audition
    attribute: str | None
    score: float | None             # None -> excluded
    terms: Mapping[str, float]
    unknown: tuple = ()
    notes: tuple = ()
    supports: int | None = None
    trouble: float | None = None    # トラブル率 % used (lesson / work)

    def to_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind, "attribute": self.attribute,
                "score": None if self.score is None else round(self.score, 3),
                "terms": {k: round(v, 3) for k, v in self.terms.items()},
                "unknown": list(self.unknown), "notes": list(self.notes), "trouble": self.trouble,
                "supports": self.supports}

    def explain(self) -> str:
        parts = [f"{k}{v:+.1f}" for k, v in sorted(self.terms.items(), key=lambda kv: (-abs(kv[1]), kv[0]))
                 if abs(v) >= 0.05]
        score = "excluded" if self.score is None else f"{self.score:.1f}"
        return f"{self.id} {score} = {' '.join(parts) or '0'}" + (f" unknown={list(self.unknown)}" if self.unknown
                                                                 else "")


@dataclasses.dataclass(frozen=True)
class ScoreResult:
    ranked: tuple
    excluded: tuple
    choice: Candidate | None
    rule: str                       # best | fallback_empty_room | fallback_rest | no_candidate | no_reference_gain
    context: Mapping[str, Any]

    def to_dict(self) -> dict:
        return {"rule": self.rule, "choice": None if self.choice is None else self.choice.id,
                "ranked": [c.to_dict() for c in self.ranked], "excluded": [c.to_dict() for c in self.excluded],
                "context": dict(self.context)}


# Win model interface: (state, audition, primary, params) -> (P(win) or None, unknown-input names)
WinModel = Callable[[ws.WingState, ws.Audition, str, ScoreParams], tuple]


# ---- formulas (W1) ---------------------------------------------------------------------------------------------------
def elapsed_weeks(season: int, week: int) -> int:
    """W in the lesson formula: S1 week 1 = 0 ... S4 week 8 = 31."""
    return (season - 1) * ws.WEEKS_PER_SEASON + (week - 1)


def perfect_given_success(p: "ScoreParams") -> float:
    """P(Perfect | success): base ~10 % + UP 小. Perfect mastery is not modelled (its
    activation odds are unknown); the trouble risk supplies P(success) separately."""
    return min(1.0, max(0.0, p.perfect_rate + p.perfect_up))


def lesson_gain(base: int, *, week_index: int, support_stats=(), bonds=None, tension_k: int = 0,
                n_excellent: int = 0, mastery: int = 0, perfect: bool = False, grad: float = 1.0) -> int:
    """W1: ceil((base × ((1 + Σcorr_i) × tension × G.R.A.D. + 0.2 × n_exc) + mastery) × perfect).

    corr_i = 0.0004 × stat_i × (1 + 0.0001 × bond_i) × (1 + 0.1 × W); tension = 1 + 0.025 k (k 0..4)."""
    bonds = list(bonds) if bonds is not None else [0] * len(support_stats)
    if len(bonds) != len(support_stats):
        raise ValueError("bonds 与 support_stats 数量不一致")
    corr = sum(0.0004 * s * (1 + 0.0001 * b) * (1 + 0.1 * week_index) for s, b in zip(support_stats, bonds))
    value = (base * ((1 + corr) * (1 + 0.025 * tension_k) * grad + 0.2 * n_excellent) + mastery) \
        * (2 if perfect else 1)
    return math.ceil(round(value, 9))


def trouble_rate(stamina: int, base: int = 70) -> int:
    """W1: ceil(((B − stamina) / 10)^3) in %, 0 at stamina ≥ B, at most 99."""
    if stamina >= base:
        return 0
    d = base - stamina
    return min(99, -(-(d ** 3) // 1000))


def stamina_from_trouble(rate: float, base: int = 70, max_stamina: int = MAX_STAMINA) -> tuple | None:
    """Integer stamina interval consistent with a shown trouble rate, or None when no stamina matches."""
    target = int(round(rate))
    hits = [s for s in range(0, max_stamina + 1) if trouble_rate(s, base) == target]
    return (min(hits), max(hits)) if hits else None


def rank_from_fans(fans: int | None) -> str | None:
    """Projected rank letter by cumulative fans (推测, see RANK_FAN_THRESHOLDS); 'F' below 1k."""
    if fans is None:
        return None
    for letter, need in RANK_FAN_THRESHOLDS:
        if fans >= need:
            return letter
    return "F"


def _rank_max(a: str | None, b: str | None) -> str | None:
    if a is None or b is None:
        return a or b
    return a if ws.RANKS.index(a) >= ws.RANKS.index(b) else b


def work_type(option: ws.ScheduleOption) -> str | None:
    text = f"{option.label} {option.id}".lower()
    if "ラジオ" in text or "radio" in text:
        return "radio"
    if "トーク" in text or "talk" in text:
        return "talk"
    if "雑誌" in text or "撮影" in text or "magazine" in text:
        return "magazine"
    return None


def room_type(option: ws.ScheduleOption) -> str | None:
    if option.kind == "lesson":
        return f"lesson_{option.attribute}" if option.attribute else None
    return work_type(option)


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x)) if x >= 0 else math.exp(x) / (1.0 + math.exp(x))


def fallback_recommended(season: Any, fan_reward: int | None) -> dict | None:
    """Table 推奨能力値 (``RECOMMENDED_FALLBACK``) as {"primary": n, "Me": n}; None when the season is not 1-4."""
    if not isinstance(season, int) or isinstance(season, bool):
        return None
    row = RECOMMENDED_FALLBACK_BY_REWARD.get((season, fan_reward)) or RECOMMENDED_FALLBACK.get(season)
    return None if row is None else {"primary": row[0], "Me": row[1]}


def with_recommended_fallback(state: ws.WingState, audition: ws.Audition, primary: str) -> tuple:
    """(audition, notes): the audition unchanged when its primary 推奨 was read; else a copy whose missing primary/Me
    values come from the table, with notes ``("table_fallback",)``. A read value is never overwritten."""
    rec = dict(audition.recommended or {})
    need = rec.get(primary)
    if isinstance(need, (int, float)) and need > 0:
        return audition, ()
    row = fallback_recommended(state.season, audition.fan_reward)
    if row is None:
        return audition, ()
    rec[primary] = row["primary"]
    rec.setdefault("Me", row["Me"])
    return dataclasses.replace(audition, recommended=rec), ("table_fallback",)


def default_win_probability(state: ws.WingState, audition: ws.Audition, primary: str,
                            params: ScoreParams) -> tuple:
    """Logistic model with the observable terms only (R_rec, trend, 思い出, Me). An unread
    primary 推奨 uses the season table (``table_fallback`` in the unknown list) instead of making p_win unknown."""
    audition, fallback = with_recommended_fallback(state, audition, primary)
    pw, unknown = _default_win_probability(state, audition, primary, params)
    return pw, tuple(fallback) + tuple(unknown)


def _default_win_probability(state: ws.WingState, audition: ws.Audition, primary: str,
                             params: ScoreParams) -> tuple:
    stat, me = state.stats.get(primary), state.stats.get("Me")
    need = audition.recommended.get(primary)
    if need is None or need <= 0 or stat is None:
        return None, ("recommended_primary",)
    unknown = []
    logit = params.a0 + params.a2 * math.log(max(stat, 1) / need)
    if state.trend is not None:
        logit += dict(params.a3).get(state.trend[primary], 0.0)
    else:
        unknown.append("trend")
    if state.memory_level is not None:
        logit += params.a4 * state.memory_level
    else:
        unknown.append("memory_level")
    me_need = audition.recommended.get("Me")
    if me_need is not None and me is not None:
        logit += params.a5 * ((1 if me >= params.mental_ok_ratio * me_need else 0) - 1)
    else:
        unknown.append("recommended_mental")
    return _sigmoid(logit), tuple(unknown)


def wing_audition_win_probability(state: ws.WingState, audition: ws.Audition, primary: str,
                                  params: ScoreParams) -> tuple:
    """Default WinModel: ``wing_audition.audition_win_estimate`` (the audition-play agent's model, P(pass) for a
    regular audition where 2nd also passes). When it cannot estimate, fall back to ``default_win_probability`` and
    list ``wing_audition_estimate`` as unknown."""
    from agent.core import wing_audition as wa          # local import: optional component, no import cycle
    audition, fallback = with_recommended_fallback(state, audition, primary)
    est = wa.audition_win_estimate(wa.context_from_wing(state, audition, primary, need_first=False))
    p_pass = est.get("p_pass") if isinstance(est, Mapping) else None
    if p_pass is not None:
        return float(p_pass), tuple(fallback) + tuple(f"wa:{u}" for u in est.get("unknown", ()))
    pw, unknown = _default_win_probability(state, audition, primary, params)
    return pw, ("wing_audition_estimate",) + tuple(fallback) + tuple(unknown)


# ---- optimistic room bound / preview check ---------------------------------------------------------------
def room_upper_gains(o: ws.ScheduleOption, week_index: int, p: ScoreParams) -> tuple | None:
    """Optimistic ({stat: gain}, SP) for a lesson / work room whose preview is unread (None: preview read / no Lv).

    Lesson: W1 formula with ``supports`` (unread: ``preview_upper_supports_unread``) supports at
    ``preview_upper_support_stat``, bond 100, each Excellent (0 when the room shows no badge, the read count when
    known) and tension k = ``preview_upper_tension_k``. Work: the table gain × the same support factor. SP: a read
    SP stays; else table base + ``preview_upper_sp_per_support`` per support. Supports' stat masteries are not added
    (unknown per room); the generous stat ceiling covers them in practice (推测)."""
    if o.preview or o.level not in LESSON_BASE:
        return None
    gains = _upper_stat_gains(o, week_index, p)
    if gains is None:
        return None
    n = o.supports if o.supports is not None else p.preview_upper_supports_unread
    typ = room_type(o)
    base_sp = LESSON_BASE[o.level][1] if o.kind == "lesson" else WORK_BASE[typ][o.level][1]
    sp = o.sp_gain if o.sp_gain is not None else base_sp + n * p.preview_upper_sp_per_support
    return gains, sp


def _upper_stat_gains(o: ws.ScheduleOption, week_index: int, p: ScoreParams) -> dict | None:
    n = o.supports if o.supports is not None else p.preview_upper_supports_unread
    if o.excellent is not None:
        n_exc = min(o.excellent, n)
    else:
        n_exc = 0 if o.excellent_present is False else n
    kw = dict(week_index=week_index, support_stats=[p.preview_upper_support_stat] * n, bonds=[100] * n,
              tension_k=p.preview_upper_tension_k, n_excellent=n_exc, grad=p.grad_factor)
    if o.kind == "lesson" and o.attribute:
        return {o.attribute: lesson_gain(LESSON_BASE[o.level][0], **kw)}
    typ = room_type(o)
    if typ in WORK_BASE:
        factor = lesson_gain(1000, **kw) / 1000
        return {k: math.ceil(v * factor) for k, v in WORK_BASE[typ][o.level][0].items()}
    return None


def score_week(state: ws.WingState, primary: str, *, goal: str = "true_end",
               params: ScoreParams | Mapping[str, Any] | None = None, extra_cap: int = 0,
               team: TeamProfile | None = None, win_model: WinModel | None = None,
               sp_context: SPValueContext | None = None, preview_bounds: bool = False,
               first_model: WinModel | None = None, special: Mapping[str, Any] | None = None) -> ScoreResult:
    """``_score_week`` plus, with ``preview_bounds``, a per-room bound in ``context["room_bounds"]``.

    Each available lesson / work room gets {"source": "preview" | "estimate", "estimate": its score (None when
    excluded), "upper": its score when its preview were ``room_upper_gains`` (= estimate for a read preview),
    "could_win": that optimistic world chooses this room while the real result does not}. The optimistic world
    changes only that room, so g_ref, stamina and fan terms stay consistent with the decision rule.
    ``context["preview_check"]`` lists the could-win room ids by descending upper score: rooms worth tapping once to
    read the real preview before deciding (the scorer never taps; wing_nav does, bounded per week)."""
    kw = dict(goal=goal, params=params, extra_cap=extra_cap, team=team, win_model=win_model,
              sp_context=sp_context, first_model=first_model, special=special)
    result = _score_week(state, primary, **kw)
    if not preview_bounds or result.choice is None or state.season is None or state.week is None:
        return result
    p = params if isinstance(params, ScoreParams) else ScoreParams.from_overrides(params)
    W = elapsed_weeks(state.season, state.week)
    scores = {c.id: c.score for c in result.ranked}
    bounds = {}
    for o in state.options:
        if not o.available or o.kind not in ("lesson", "work"):
            continue
        estimate = scores.get(o.id)
        entry = {"source": "preview" if o.preview else "estimate",
                 "estimate": None if estimate is None else round(estimate, 3),
                 "upper": None if estimate is None else round(estimate, 3), "could_win": False}
        upper = room_upper_gains(o, W, p) if estimate is not None else None
        if upper is not None:
            options = tuple(dataclasses.replace(x, preview=upper[0], sp_gain=upper[1]) if x.id == o.id else x
                            for x in state.options)
            alt = _score_week(dataclasses.replace(state, options=options), primary, **kw)
            mine = next((c for c in alt.ranked if c.id == o.id), None)
            entry["upper"] = None if mine is None else round(max(mine.score, estimate), 3)
            entry["upper_gains"], entry["upper_sp"] = upper
            entry["could_win"] = (alt.choice is not None and alt.choice.id == o.id
                                  and result.choice.id != o.id)
        bounds[o.id] = entry
    # Rooms with supports first (their SP/support gains are what the formula misses), then unread, then empty rooms.
    # A primary gain clipped by the cap is already low here: the optimistic world applies the same cap_overflow.
    tier = {o.id: 0 if (o.supports or 0) > 0 else 1 if o.supports is None else 2 for o in state.options}
    check = sorted((i for i, b in bounds.items() if b["could_win"]),
                   key=lambda i: (tier.get(i, 1), -bounds[i]["upper"], i))
    return dataclasses.replace(result, context={**result.context, "room_bounds": bounds, "preview_check": check})


def _score_week(state: ws.WingState, primary: str, *, goal: str = "true_end",
                params: ScoreParams | Mapping[str, Any] | None = None, extra_cap: int = 0,
                team: TeamProfile | None = None, win_model: WinModel | None = None,
                sp_context: SPValueContext | None = None, first_model: WinModel | None = None,
                special: Mapping[str, Any] | None = None) -> ScoreResult:
    """Rank this week's available actions.

    ``extra_cap``: primary cap the SP planner can buy before acting. ``team``: optional support profiles (needs
    ``ScheduleOption.support_ids``). ``win_model``: audition P(win) provider (default: ``wing_audition_win_probability``).
    ``sp_context``: reviewed panel snapshot and a terminal evaluator in PEP units; replaces only the SP term.
    Purchases funded by this action occur after its gains, so future cap benefit belongs in that evaluator.
    ``special``: the season's limited-audition plan (``wing_special.season_plan``), a bonus only: targets never
    change and limited auditions never enter the ordinary win chains. A planned one reserves the final week
    (ordinary deadlines one week earlier) and nudges free weeks toward its entry condition (``prep``). In the final
    week it is a candidate only once the season target is met (``context["special_bonus"]``: the policy enters it)
    or as the last chance when no ordinary audition can reach the target; otherwise it is excluded."""
    if primary not in ws.ATTRIBUTES:
        raise ValueError("primary 必须是 Vo/Da/Vi")
    p = params if isinstance(params, ScoreParams) else ScoreParams.from_overrides(params)
    win_model = win_model or wing_audition_win_probability
    team = team or TeamProfile()
    first_model = first_model or first_place_probability
    from agent.core.support_effects import expected_stamina_multiplier
    stamina_effects = [effect for support in team.supports.values() for effect in support.stamina_support]
    cost_multiplier = expected_stamina_multiplier(stamina_effects)
    rest_extra = sum(effect["expected_recovery"] for support in team.supports.values() for effect in support.rest_support)
    ctx: dict[str, Any] = {"primary": primary, "goal": goal}
    if state.season is None or state.week is None:
        return ScoreResult((), (), None, "no_candidate", {**ctx, "reason": "season_or_week_unread"})
    W = elapsed_weeks(state.season, state.week)
    weeks_left = state.weeks_left
    max_st = state.max_stamina or MAX_STAMINA
    ctx.update(W=W, weeks_left=weeks_left)
    sp_ready = False
    sp_unknown = ()
    sp_branches = {}
    ctx["sp_valuation"] = {"mode": "estimated_weight", "unit": "PEP", "weight": p.w_sp}
    if sp_context is not None:
        if not isinstance(sp_context, SPValueContext) or sp_context.primary != primary:
            raise ValueError("SP value context does not match primary")
        if extra_cap:
            raise ValueError("SP path context cannot combine with an unpurchased extra_cap")
        for observed, snapshot in ((state.stats, sp_context.state.stats),
                                   (state.stat_caps or {}, sp_context.state.caps)):
            if any(observed.get(a) is not None and observed[a] != snapshot[a] for a in ws.STATS):
                raise ValueError("SP value snapshot does not match observed state")
        sp_unknown = tuple(name for missing, name in (
            (state.sp is None, "sp_unread"),
            (any(state.stats.get(a) is None for a in ws.STATS), "sp_value_stats_unread"),
            (any((state.stat_caps or {}).get(a) is None for a in ws.STATS), "sp_value_caps_unread")) if missing)
        sp_ready = not sp_unknown
        ctx["sp_valuation"] = {"mode": "reviewed_path_marginal", "unit": "PEP",
                               "unknowns": list(sp_unknown), "rooms": {}, "auditions": {}}

    if state.stamina is not None:
        stamina, st_src = state.stamina, "observed"
    elif state.trouble_rate is not None and stamina_from_trouble(state.trouble_rate) is not None:
        stamina, st_src = stamina_from_trouble(state.trouble_rate)[0], "from_trouble_rate_lower_bound"
    else:
        stamina, st_src = None, "unknown"
    ctx.update(stamina=stamina, stamina_source=st_src)

    stat = state.stats.get(primary)
    cap = state.stat_caps.get(primary) if state.stat_caps else None
    cap_eff = None if cap is None else cap + extra_cap
    me = state.stats.get("Me")
    me_target = dict(p.me_targets).get(state.season)
    w_me = p.w_me_low if (me is not None and me_target is not None and me < me_target) else p.w_me_high
    ctx.update(stat=stat, cap_eff=cap_eff, w_me=w_me)

    def phi(s: float) -> float:
        return trouble_rate(int(s)) / 100 + p.stamina_lookahead * trouble_rate(int(s) - p.lookahead_cost) / 100

    # ---- per-room estimates ----
    def room_support_profiles(o: ws.ScheduleOption) -> list[SupportProfile] | None:
        if not o.support_ids:
            return None
        if o.supports is not None and len(o.support_ids) != o.supports:
            return None
        return [team.supports[i] for i in o.support_ids if i in team.supports] \
            if all(i in team.supports for i in o.support_ids) else None

    def room_gains(o: ws.ScheduleOption):
        """({stat: gain}, sp, fans, room type, unknown, notes): preview first, else W1 formula/table."""
        unknown, notes = [], []
        typ = room_type(o)
        profs = room_support_profiles(o)
        if profs is not None:
            unknown.extend(f"support.{s.id}.{gap}" for s in profs for gap in s.unknowns)
        elif o.support_ids:
            unknown.append("support_profiles_incomplete")
        elif o.supports:
            unknown.append("support_ids")
        gains = dict(o.preview)
        source = "preview" if gains else None
        if not gains and o.level is not None:
            if o.kind == "lesson" and o.attribute:
                base = LESSON_BASE[o.level][0]
                excellent = o.excellent
                if excellent is None:
                    excellent = 1 if o.excellent_present is True else 0
                    if o.excellent_present is True:
                        unknown.append("excellent_count")
                        notes.append("excellent_count_lower_bound")
                mastery = sum((s.masteries.get(typ) or {}).get("stat", 0) for s in profs) if profs is not None else 0
                if profs is not None and all(s.stats and o.attribute in s.stats for s in profs):
                    stats_ = [s.stats[o.attribute] for s in profs]
                    bonds = [s.bond0 or 0 for s in profs]
                    gains = {o.attribute: lesson_gain(base, week_index=W, support_stats=stats_, bonds=bonds,
                                                      n_excellent=excellent, mastery=mastery,
                                                      grad=p.grad_factor)}
                    source = "formula_team_profile"
                else:
                    gains = {o.attribute: lesson_gain(base, week_index=W, n_excellent=excellent,
                                                      mastery=mastery, grad=p.grad_factor)}
                    source = "formula_lower_bound"
            elif typ in WORK_BASE:
                gains = dict(WORK_BASE[typ][o.level][0])
                source = "table_lower_bound"
        if source is None:
            unknown.append("stat_gain")
        elif source != "preview":
            notes.append(f"gain_{source}")
        elif not p.preview_includes_mastery_tension:
            notes.append("gain_preview_lower_bound")
        if profs is not None:
            for holder in profs:
                if holder.masteries.get(UNIT_MASTERY_ROOM) and not holder.unit:
                    unknown.append(f"support.{holder.id}.unit_unknown")
                elif holder.masteries.get(UNIT_MASTERY_ROOM) and (team.produce_unit is None or
                                                               any(s.unit is None for s in profs if s.id != holder.id)):
                    unknown.append("unit_member_identity_incomplete")
        if gains and source != "preview" and profs is not None:
            for attribute in ws.STATS:
                mastery_gain = sum((s.masteries.get(typ) or {}).get(f"stat_{attribute}", 0) for s in profs)
                unit_gain = sum(unit_partners(s, profs, team) *
                                (s.masteries.get(UNIT_MASTERY_ROOM) or {}).get(f"stat_{attribute}", 0) for s in profs)
                if unit_gain:
                    mastery_gain += unit_gain
                    notes.append(f"unit_mastery_{attribute}_{unit_gain:g}")
                if mastery_gain:
                    gains[attribute] = gains.get(attribute, 0) + mastery_gain
                    notes.append(f"mastery_{attribute}_{mastery_gain:g}")
        sp = o.sp_gain
        if sp is None and o.level is not None:
            if o.kind == "lesson":
                sp = LESSON_BASE[o.level][1]
            elif typ in WORK_BASE:
                sp = WORK_BASE[typ][o.level][1]
            if sp is not None:
                if profs is not None:
                    sp += sum((s.masteries.get(typ) or {}).get("sp", 0) for s in profs)
                    unit_sp = sum(unit_partners(s, profs, team) *
                                  (s.masteries.get(UNIT_MASTERY_ROOM) or {}).get("sp", 0) for s in profs)
                    if unit_sp:
                        sp += unit_sp
                        notes.append(f"unit_mastery_sp_{unit_sp:g}")
                notes.append("sp_table_lower_bound")
        if sp is None:
            unknown.append("sp")
        fans = o.fan_gain
        if fans is None and o.level is not None:
            if o.kind == "lesson":
                fans = LESSON_BASE[o.level][2]
            elif typ in WORK_BASE:
                fans = WORK_BASE[typ][o.level][2]
        return gains, sp, fans, typ, profs, unknown, notes

    rooms = [o for o in state.options if o.available and o.kind in ("lesson", "work")]
    room_data = {o.id: room_gains(o) for o in rooms}
    primary_lessons = [o for o in rooms if o.kind == "lesson" and o.attribute == primary
                       and primary in room_data[o.id][0]]
    if not primary_lessons:
        return ScoreResult((), (), None, "no_reference_gain", {**ctx, "reason": "primary_lesson_gain_unknown"})
    g_ref = max(room_data[o.id][0][primary] for o in primary_lessons) * (1 + perfect_given_success(p))
    empties = [o for o in primary_lessons if o.supports == 0]
    g_empty = (min(room_data[o.id][0][primary] for o in empties) * (1 + perfect_given_success(p))) \
        if empties else None
    ctx.update(g_ref=round(g_ref, 3), g_empty=None if g_empty is None else round(g_empty, 3))

    # ---- auditions: eligibility and win probability ----
    fans = state.fans
    targets = dict(p.season_fan_targets)
    gate = state.season_gate or 0
    soft_target = max(gate, targets.get(state.season, 0)) if goal == "true_end" else gate
    hard_target = max(gate, ws.TRUE_END_FANS) if (goal == "true_end" and state.season == 4) else gate
    te_done = goal == "true_end" and fans is not None and fans >= ws.TRUE_END_FANS
    audition_list_read = bool(state.auditions)
    from agent.core import wing_special as wsp
    limited = {a.id for a in state.auditions if wsp.is_special(a.id)}
    sp_plan = special if isinstance(special, Mapping) and special.get("planned") else None
    if isinstance(special, Mapping):
        ctx["special"] = wsp.summary(special)
    reserve = 1 if sp_plan and sp_plan.get("reserve") and weeks_left is not None and weeks_left > 1 else 0
    ord_weeks = None if weeks_left is None else weeks_left - reserve

    pwin: dict[str, tuple] = {}
    for a in state.auditions:
        pw, unk = win_model(state, a, primary, p)
        pwin[a.id] = (pw, tuple(unk))

    def enterable(a: ws.Audition, rank: str | None, f: int | None) -> bool:
        """Entry is possible (availability, fans, rank). An unknown p_win does not make an audition unenterable:
        for reachability it "needs an estimate" (2026-10-09: an unread 推奨 must not turn into fan_plan_unreachable);
        it is still excluded from scoring below."""
        if not a.available:
            return False
        if a.min_fans is not None and (f is None or a.min_fans > f):
            return False
        if a.required_rank is None:
            return True
        return rank is not None and ws.RANKS.index(rank) >= ws.RANKS.index(a.required_rank)

    def wins_needed(f: int, rank: str | None, target: int, ordinary: bool = True) -> tuple[float, list[str]]:
        """Greedy chain of wins (highest enterable reward each step, rank re-projected after every win); limited
        auditions only when ``ordinary`` is False."""
        chain = []
        for _ in range(p.max_plan_steps):
            if f >= target:
                return len(chain), chain
            options = [a for a in state.auditions if enterable(a, rank, f) and not (ordinary and a.id in limited)]
            if not options:
                return math.inf, chain
            best = max(options, key=lambda a: (a.fan_reward, pwin[a.id][0] or 0.0, a.id))
            chain.append(best.id)
            f += best.fan_reward
            rank = _rank_max(rank, rank_from_fans(f))
        return (len(chain) if f >= target else math.inf), chain

    excluded = []
    eligible = []
    for a in sorted(state.auditions, key=lambda a: a.id):
        gate_ok = state.rank_at_least(a.required_rank)
        reason = None
        if not a.available:
            reason = "not_available"
        elif gate_ok is None:
            reason = "rank_unread"
        elif gate_ok is False:
            reason = "rank_locked"
        elif a.min_fans is not None and (fans is None or a.min_fans > fans):
            reason = "fans_below_entry"
        elif pwin[a.id][0] is None:
            reason = "p_win_unknown"
        if reason:
            excluded.append(Candidate(a.id, "audition", primary, None, {k: 0.0 for k in TERMS}, pwin[a.id][1],
                                      (reason,)))
        else:
            eligible.append(a)

    rank_now = state.rank
    forced = None
    if fans is not None:
        n_need, chain = wins_needed(fans, rank_now, soft_target)
        hard_need, _ = wins_needed(fans, rank_now, hard_target)
        if limited and sp_plan and weeks_left is not None and (math.isinf(hard_need) or hard_need > weeks_left):
            # No ordinary route is left: the limited audition this week is the last chance.
            any_need, any_chain = wins_needed(fans, rank_now, hard_target, ordinary=False)
            if any_need < hard_need:
                hard_need, forced = any_need, (any_chain[0] if any_chain else None)
                ctx["limited_last_chance"] = forced
    else:
        n_need, chain, hard_need = math.inf, [], math.inf
    deficit = None if fans is None else max(0, soft_target - fans)
    hard_deficit = None if fans is None else max(0, hard_target - fans)
    first = next((a for a in state.auditions if chain and a.id == chain[0]), None)
    if fans is None or deficit == 0 or te_done:
        urgency = 0.0
    elif math.isinf(n_need):
        urgency = p.urgency_max if audition_list_read else 0.0
        if not audition_list_read:
            ctx["urgency_unknown"] = True
    else:
        pbar = max(pwin[first.id][0] or 0.0, p.slack_p_floor) if first else 1.0
        slack = ord_weeks - n_need / pbar
        urgency = p.urgency_max * _sigmoid(-(slack - p.urgency_s0) / p.urgency_tau)
        ctx["slack"] = round(slack, 3)
    fan_floor = 0.0 if te_done else p.fan_value_floor
    best_reward = max((a.fan_reward for a in eligible), default=None)
    ref_fans = best_reward or max(deficit or 0, 1_000)
    w_fan = g_ref / (ref_fans / 1000)
    audition_w_fan = p.future_week_pep / (ref_fans / 1000)
    work_fans = [room_data[o.id][2] for o in rooms if o.kind == "work" and room_data[o.id][2]]
    if hard_deficit and math.isinf(hard_need):
        unreachable = audition_list_read and (not work_fans or max(work_fans) * weeks_left < hard_deficit)
        if not audition_list_read:
            ctx["audition_list_unread"] = True
    else:
        unreachable = bool(hard_deficit) and hard_need > weeks_left
    ctx.update(soft_target=soft_target, hard_target=hard_target, deficit=deficit, n_need=n_need, plan_chain=chain,
               hard_need=hard_need, urgency=round(urgency, 4), fan_value_floor=fan_floor, w_fan=round(w_fan, 4),
               best_audition=forced or (first.id if first else None), rank=rank_now,
               hard_plan_unreachable=unreachable,
               tight=bool(hard_deficit) and not math.isinf(hard_need) and hard_need >= ord_weeks)
    if reserve:
        ctx["special_reserved_week"] = True
    # A planned limited audition of the final week: entered once both targets are met, the last chance when no
    # ordinary audition can reach them, otherwise excluded (the route never depends on it).
    bonus_id = next((a.id for a in state.auditions if a.id in limited and sp_plan and sp_plan.get("final_week")
                     and sp_plan.get("bonus") and not deficit and not hard_deficit), None)
    if bonus_id:
        ctx["special_bonus"] = bonus_id
    plan_unknown_p = sorted({x for x in chain if pwin[x][0] is None})
    if plan_unknown_p:
        ctx["plan_p_win_unknown"] = plan_unknown_p          # reachable only if these can be estimated / won
    ctx.update(future_week_pep=p.future_week_pep, future_week_pep_source="configured_uncalibrated_prior",
               audition_w_fan=round(audition_w_fan, 4))

    # ---- promise (W1 tension / affection effects; no hard rule) ----
    promised = state.promise_due()
    promise_keepable = promised is not None and any(o.kind == "lesson" and o.attribute == promised for o in rooms)
    tension_point = g_ref * 0.025 / 20 * p.tension_horizon_weeks       # 20 tension points = one colour step
    # 2026-10-14 user: promises are accepted, the promised lesson only gets a small bonus and every other room scores
    # as usual (no break cost) at the default 中立. 优先守约 (weight 4) also charges the break cost; when the fan plan
    # is tight a needed audition still wins (the weight falls back to neutral).
    weight = min(1.0, p.promise_weight) if ctx.get("tight") else p.promise_weight
    prep = (sp_plan or {}).get("prep") or {}
    keep_weight = weight
    if prep.get("kind") == "affection" and not ctx.get("tight"):
        keep_weight = max(weight, p.special_promise_weight)      # 親愛度 for the S2 limited audition; no break cost
    keep_value = (25 * tension_point + 10 * p.affection_point_value) * keep_weight
    break_cost = 10 * tension_point * weight if weight > 1.0 else 0.0
    ctx.update(promise=promised, promise_keep_value=round(keep_value, 3), promise_break_cost=round(break_cost, 3))

    def promise_term(kind: str, attribute: str | None) -> float:
        if not promise_keepable:
            return 0.0
        return keep_value if (kind == "lesson" and attribute == promised) else -break_cost

    def fan_value(n: int | None) -> float:
        return 0.0 if not n else n / 1000 * w_fan * (fan_floor + urgency)

    bond_beta = p.bond_beta * max(0.0, 1 - W / p.bond_fade_week)
    if prep.get("kind") == "bonds":
        bond_beta = max(bond_beta, p.special_bond_beta)          # S3 limited audition: three red bonds
    me_prep = 0.0
    if prep.get("kind") == "Me" and prep.get("deficit"):
        me_prep = min(p.special_me_pep_max, float(prep.get("value_weeks") or 0) * p.future_week_pep / prep["deficit"])
        ctx["special_me_pep"] = round(me_prep, 3)

    ranked = []
    # ---- lessons and work ----
    for o in rooms:
        gains, sp, fan_gain, typ, profs, unknown, notes = room_data[o.id]
        unknown, notes = list(unknown), list(notes)
        terms = {k: 0.0 for k in TERMS}
        if not gains:
            excluded.append(Candidate(o.id, o.kind, o.attribute, None, terms, tuple(unknown), ("gain_unknown",),
                                      o.supports))
            continue
        if o.trouble_rate is not None:
            pt = o.trouble_rate
        elif state.trouble_rate is not None and (o.kind == "lesson" or typ in ("radio", "talk")):
            pt = state.trouble_rate                    # the shown rate uses the lesson base B = 70
        elif stamina is not None:
            if typ not in TROUBLE_BASE and o.kind != "lesson":
                notes.append("trouble_base_assumed_lesson")
            pt = trouble_rate(stamina, TROUBLE_BASE.get("lesson" if o.kind == "lesson" else typ, 70))
        else:
            excluded.append(Candidate(o.id, o.kind, o.attribute, None, terms, tuple(unknown + ["trouble_rate"]),
                                      ("trouble_unknown",), o.supports))
            continue
        hard_cap = min(p.trouble_hard_cap, p.trouble_rest_rate)
        if pt >= hard_cap:          # every lesson / work room consumes stamina (magazine: B = 20)
            excluded.append(Candidate(o.id, o.kind, o.attribute, None, terms, tuple(unknown),
                                      tuple(notes) + ("trouble_hard_cap", f"trouble_{pt:g}_ge_{hard_cap:g}"),
                                      o.supports, round(float(pt), 3)))
            continue
        pt = pt / 100
        ok = 1 - pt
        perfect = perfect_given_success(p)
        e_ok = ok * (1 + perfect)     # Perfect counted once, only on success
        for key, gain in gains.items():
            if key == primary:
                terms["stat_gain"] += e_ok * gain
                if stat is not None and cap_eff is not None:
                    headroom = max(0, cap_eff - stat)
                    overflow = max(0, gain - headroom)
                    perfect_overflow = max(0, 2 * gain - headroom)
                    terms["cap_overflow"] -= p.w_overflow * ok * ((1 - perfect) * overflow + perfect * perfect_overflow)
                    if overflow:
                        notes.append(f"overflow_{overflow}")
                    elif perfect_overflow:
                        notes.append(f"perfect_overflow_{perfect_overflow}")
                elif "cap" not in unknown:
                    unknown.append("cap")
            elif key == "Me":
                terms["mental"] += w_me * e_ok * gain
                if me_prep and gain > 0:
                    terms["mental"] += me_prep * e_ok * min(gain, prep["deficit"])
                    notes.append(f"special_me_prep_{min(gain, prep['deficit'])}")
            else:
                terms["off_stat"] += p.w_off * e_ok * gain
        if sp is not None:
            if sp_context is None:
                terms["sp"] = p.w_sp * e_ok * sp
            elif not sp_ready:
                unknown.extend(sp_unknown)
            else:
                perfect = perfect_given_success(p)
                reports = {}
                for branch, multiplier, probability in (("normal", 1, ok * (1 - perfect)),
                                                         ("perfect", 2, ok * perfect)):
                    if probability <= 0:
                        continue
                    gain = multiplier * sp
                    if gain not in sp_branches:
                        sp_branches[gain] = marginal_sp_value(sp_context, state.sp, gain)
                    report = sp_branches[gain]
                    reports[branch] = {"probability": probability, **report}
                    terms["sp"] += probability * report["marginal_pep"]
                    unknown.extend(report["unknowns"])
                    if not report["search_complete"]:
                        unknown.append("sp_value_search_truncated")
                ctx["sp_valuation"]["rooms"][o.id] = reports
                notes.append("sp_reviewed_path_marginal")
        terms["fans"] = ok * fan_value(fan_gain)
        # supports present: bond toward Excellent (early), support events (any time)
        n_sup = o.supports
        if n_sup is None:
            unknown.append("supports")
        elif n_sup:
            if profs is not None and all(s.bond0 is not None for s in profs):
                bond_points = sum(min(p.bond_gain, max(0, 50 - s.bond0)) for s in profs)
                notes.append("bond_from_profile_opening_value")
            else:
                bond_points = n_sup * p.bond_gain        # bond unread: assume below 50 while β > 0 (推测)
                if bond_beta > 0:
                    unknown.append("bond")
            terms["bond"] = ok * bond_beta * bond_points
            terms["events"] = ok * n_sup * p.support_event_rate * p.support_event_value
        if o.excellent:
            notes.append(f"excellent_{o.excellent}")
        # stamina and risk
        if o.stamina_cost is not None:
            cost = o.stamina_cost
        elif o.kind == "lesson":
            cost = STAMINA_COST["lesson"]
        elif typ in STAMINA_COST:
            cost = STAMINA_COST[typ]
        else:
            cost = max(STAMINA_COST.values())
            notes.append("cost_upper_bound")
        # An explicit observed cost already includes the screen's current modifiers.
        if o.stamina_cost is None and stamina_effects:
            cost *= cost_multiplier
            notes.append("stamina_support_independent_expectation")
        if o.stamina_gain is not None:
            rec = o.stamina_gain
        elif profs is not None and any((s.masteries.get(typ) or {}).get("stamina") for s in profs):
            rec = 3 * sum((s.masteries.get(typ) or {}).get("stamina", 0) for s in profs)     # W3: Lv × 3
            notes.append("recovery_from_profile")
        elif o.stamina_recovery:
            rec = p.flagged_recovery_stamina
            notes.append("recovery_amount_assumed")
        else:
            rec = 0
        if o.stamina_gain is None and profs is not None:
            unit_rec = 3 * sum(unit_partners(s, profs, team) *
                               (s.masteries.get(UNIT_MASTERY_ROOM) or {}).get("stamina", 0) for s in profs)
            rec += unit_rec
            if unit_rec:
                notes.append(f"unit_mastery_stamina_{unit_rec:g}")
        # 推测: 16 % already shows 大失敗 reports; R6: about 10 % of troubles are 大失敗 even at low rates
        p_cat = pt * min(1.0, max(p.cat_share_floor, (pt * 100 - 10) / 90))
        if stamina is not None:
            after = min(max_st, max(0.0, stamina - cost + ok * rec))
            terms["stamina"] = g_ref * (phi(stamina) - phi(after))
            l_cat = p.l_cat_base + g_ref * (phi(0) - phi(after))
        else:
            unknown.append("stamina")
            l_cat = p.l_cat_base
        terms["trouble_risk"] = -(pt * p.l_fail + p_cat * l_cat)
        terms["promise"] = promise_term(o.kind, o.attribute)
        ranked.append(Candidate(o.id, o.kind, o.attribute, sum(terms.values()), terms, tuple(unknown),
                                tuple(notes), o.supports, round(pt * 100, 3)))

    # ---- rest ----
    for o in (o for o in state.options if o.available and o.kind == "rest"):
        terms = {k: 0.0 for k in TERMS}
        if stamina is None:
            excluded.append(Candidate(o.id, "rest", None, None, terms, ("stamina",), ("stamina_unknown",)))
            continue
        recovery = o.stamina_gain if o.stamina_gain is not None else p.rest_expected + rest_extra
        after = min(max_st, stamina + recovery)
        terms["stamina"] = g_ref * (phi(stamina) - phi(after))
        terms["tension"] = p.b_tension_rest
        terms["promise"] = promise_term("rest", None)
        notes = (f"stamina_{stamina}_to_{after}",)
        if rest_extra and o.stamina_gain is None:
            notes += ("rest_support_expectation",)
        ranked.append(Candidate(o.id, "rest", None, sum(terms.values()), terms, (), notes))

    # ---- auditions: a needed win displaces one week now or later (later weeks are worth more) ----
    best_other = max((c.score for c in ranked if c.kind in ("lesson", "work")), default=g_ref)
    best_other = max(best_other, 0.0)
    ctx["best_room_score"] = round(best_other, 3)
    best_room = max((c for c in ranked if c.kind in ("lesson", "work")), key=lambda c: c.score, default=None)
    passive = room_data[best_room.id][2] if best_room is not None else None
    p_needed = 1.0
    if (passive and deficit and weeks_left is not None and not (goal == "true_end" and state.season == 4)):
        cover = passive * weeks_left / deficit
        p_needed = _sigmoid((1.0 - cover) / p.passive_fan_tau)
        ctx.update(passive_fans_week=passive, passive_cover=round(cover, 3), p_win_needed=round(p_needed, 3))
    rewards = {a.id: a.fan_reward for a in state.auditions}
    for a in eligible:
        if a.id in limited and a.id not in (bonus_id, forced):
            excluded.append(Candidate(a.id, "audition", primary, None, {k: 0.0 for k in TERMS}, pwin[a.id][1],
                                      ("limited_bonus_only",)))
            continue
        pw, unk = pwin[a.id]
        terms = {k: 0.0 for k in TERMS}
        notes = [f"p_win_{round(pw, 3)}", f"reward_{a.fan_reward}"]
        unknown = list(unk) + ["future_week_value_estimated"]
        progress = buffer = 0.0
        if fans is not None and not math.isinf(n_need) and n_need > 0:
            after_fans = fans + a.fan_reward
            after_rank = _rank_max(rank_now, rank_from_fans(after_fans))
            n_after, after_chain = wins_needed(after_fans, after_rank, soft_target)
            progress = 0.0 if math.isinf(n_after) else max(0.0, float(n_need - n_after))
            if progress > 0 and 0 < n_after < math.inf:
                # 2026-10-25 user (S4, 161,667 fans: 50k picked over 100k, both left 3 wins): fans above the target
                # after the planned wins are a buffer against a later loss; banked, at most this audition's reward.
                final = after_fans + sum(rewards[x] for x in after_chain)
                buffer = min(a.fan_reward, max(0, final - soft_target))
            newly = sorted(x.id for x in state.auditions if x.required_rank and not enterable(x, rank_now, fans)
                           and enterable(x, after_rank, after_fans))
            if newly:
                notes.append("unlocks:" + ",".join(newly))
        elif fans is not None and math.isinf(n_need) and deficit:
            progress = 1.0 if a.fan_reward >= (deficit or 0) else 0.0
        notes.append(f"wins_saved_{progress:g}")
        spare = max(0.0, (weeks_left - n_need)) if not math.isinf(n_need) else 0.0
        terms["audition_progress"] = (pw * progress * p_needed * p.future_week_pep
                                      * (1 + p.lesson_growth_per_week * spare))
        later = 0.0
        if a.id in limited and isinstance(special, Mapping) and a.id.endswith("_" + str(special.get("id"))):
            later = float(special.get("wins_saved") or 0)
            if later:
                terms["audition_progress"] += pw * later * p.future_week_pep
                notes.append(f"limited_later_wins_saved_{later:g}")
        terms["fan_urgency"] = pw * progress * p.future_week_pep * urgency
        if goal == "true_end" and state.season == 4 and progress > 0 and not te_done:
            done = (ws.WEEKS_PER_SEASON - weeks_left) / ws.WEEKS_PER_SEASON if weeks_left is not None else 1.0
            terms["fan_urgency"] += pw * progress * best_other * (p.te_s4_base + p.te_s4_growth * done)
            notes.append(f"te_s4_weight_{round(p.te_s4_base + p.te_s4_growth * done, 2)}")
        if progress == 0 and not later:                    # surplus fans only bank; needed fans count as progress
            terms["fans"] = pw * a.fan_reward / 1000 * audition_w_fan * fan_floor
        elif buffer:
            terms["fans"] = pw * buffer / 1000 * audition_w_fan * fan_floor
            notes.append(f"fan_buffer_{buffer:g}")
        win_stats = AUDITION_WIN_STATS.get((state.season, a.fan_reward))
        if win_stats is None:
            unknown.append("win_stats")
        terms["audition_stats"] = pw * ((min(win_stats) if win_stats else 0) + p.audition_mastery_value)
        mastery = {}
        for support in team.supports.values():
            for key, amount in (support.masteries.get("audition") or {}).items():
                mastery[key] = mastery.get(key, 0) + amount
        if mastery:
            pf, first_unknown = first_model(state, a, primary, p)
            unknown.extend(first_unknown)
            if pf is None:
                unknown.append("audition_first_probability")
            else:
                pf = min(pw, max(0., float(pf)))
                notes.append(f"audition_mastery_p_first_{pf:g}")
                for attribute in ws.STATS:
                    gain = mastery.get(f"stat_{attribute}", 0)
                    if attribute == primary:
                        reward = min(win_stats) if win_stats else 0
                        gain = min(gain, max(0, cap_eff - stat - reward)) if stat is not None and cap_eff is not None else gain
                        terms["audition_stats"] += pf * gain
                    elif attribute == "Me":
                        terms["mental"] += pf * w_me * gain
                    else:
                        terms["off_stat"] += pf * p.w_off * gain
                sp_gain = mastery.get("sp", 0)
                if sp_gain:
                    if sp_context is None:
                        terms["sp"] += pf * p.w_sp * sp_gain
                    elif sp_ready:
                        report = marginal_sp_value(sp_context, state.sp, sp_gain)
                        terms["sp"] += pf * report["marginal_pep"]
                        ctx["sp_valuation"]["auditions"][a.id] = {"probability": pf, **report}
                        unknown.extend(report["unknowns"])
                        if not report["search_complete"]:
                            unknown.append("sp_value_search_truncated")
                    else:
                        unknown.extend(sp_unknown)
        terms["promise"] = promise_term("audition", None)
        ranked.append(Candidate(a.id, "audition", primary, sum(terms.values()), terms, tuple(unknown),
                                tuple(notes)))

    ranked.sort(key=lambda c: (-round(c.score, 9), KIND_ORDER.get(c.kind, 9), -(c.supports or 0), c.id))
    alternative = next((c for c in ranked if c.kind != "audition"), None)
    ctx.update(best_non_audition_id=alternative.id if alternative else None,
               best_non_audition_score=round(alternative.score, 3) if alternative else None,
               audition_margins={c.id: round(c.score - alternative.score, 3) for c in ranked
                                 if c.kind == "audition"} if alternative else {})
    excluded.sort(key=lambda c: (KIND_ORDER.get(c.kind, 9), c.id))
    if not ranked:
        return ScoreResult((), tuple(excluded), None, "no_candidate", ctx)

    best, rule = ranked[0], "best"
    floor = None if g_empty is None else p.floor_ratio * g_empty
    ctx["floor"] = None if floor is None else round(floor, 3)
    # 「都不划算时」: only a weak lesson / work best is replaced; a best rest or audition is already the safe choice.
    if floor is not None and best.kind in ("lesson", "work") and best.score < floor and p.fallback != "none":
        rest = next((c for c in ranked if c.kind == "rest"), None)
        empty = next((c for c in ranked if c.kind == "lesson" and c.attribute == primary and c.supports == 0), None)
        at_cap = empty is not None and empty.terms["cap_overflow"] < 0 and             abs(empty.terms["cap_overflow"]) >= empty.terms["stat_gain"] - 1e-9
        if p.fallback == "empty_room" and empty is not None and empty.trouble is not None                 and empty.trouble <= p.trouble_rest_rate and not at_cap                 and (rest is None or empty.score >= rest.score):
            best, rule = empty, "fallback_empty_room"
        elif rest is not None:
            best, rule = rest, "fallback_rest"
        ctx["fallback_reason"] = (f"best_{ranked[0].id}_{ranked[0].score:.1f}_below_floor_{floor:.1f}"
                                  f"_to_{best.id}")
    return ScoreResult(tuple(ranked), tuple(excluded), best, rule, ctx)


# ---- explicit score card (2026-10-08; read-only display / log payload, no decision) ----------------------
# Six display groups (+ promise/tension) over TERMS; ui/components/wing-score-card.js mirrors this table
# (tests/test_wing_score_card.py checks the two stay equal).
TERM_GROUPS = (
    ("primary", "主属性成长", ("stat_gain", "cap_overflow")),
    ("other", "其他属性/Me", ("off_stat", "mental")),
    ("sp", "SP", ("sp",)),
    ("support", "支援（絆+事件）", ("bond", "events")),
    ("fans", "粉丝/试镜进度", ("fans", "audition_progress", "fan_urgency", "audition_stats")),
    ("risk", "体力与失败风险", ("stamina", "trouble_risk")),
    ("misc", "约定/テンション", ("promise", "tension")),
)
EXCLUDE_REASON_TEXT = {
    "trouble_hard_cap": "トラブル率达到硬上限，耗体力行动不选",
    "gain_unknown": "预览增量未读",
    "trouble_unknown": "トラブル率与体力都未读",
    "stamina_unknown": "体力未读",
}
_KIND_TEXT = {"lesson": "课程", "work": "工作", "rest": "休息", "audition": "试镜"}


def _row_label(c: Mapping[str, Any]) -> str:
    kind, attr, n = c.get("kind"), c.get("attribute"), c.get("supports")
    if kind == "audition":
        return "试镜"
    text = _KIND_TEXT.get(kind, str(kind)) + (f" {attr}" if attr and kind == "lesson" else "")
    if kind in ("lesson", "work") and isinstance(n, int):
        text += f"（支援 {n} 人）" if n else "（空房）"
    return text


def group_terms(terms: Mapping[str, float]) -> dict:
    """Sum the score terms into the display groups (unknown term names land in ``misc``)."""
    out = {key: 0.0 for key, _, _ in TERM_GROUPS}
    owner = {t: key for key, _, names in TERM_GROUPS for t in names}
    for name, value in (terms or {}).items():
        out[owner.get(name, "misc")] += float(value)
    return {k: round(v, 3) for k, v in out.items()}


def score_card(plan: Mapping[str, Any], *, chosen: str | None = None, rule: str | None = None) -> dict:
    """Display payload from ``WeeklyDecision.plan`` (``scores`` / ``excluded`` / ``context`` / ``score_rule``) or a
    ``ScoreResult.to_dict()`` (``ranked``). Pure and read-only: it never re-scores or changes a decision."""
    rows_in = plan.get("scores", plan.get("ranked")) or []
    chosen = chosen if chosen is not None else plan.get("choice")
    rule = rule if rule is not None else plan.get("score_rule", plan.get("rule"))
    ctx = plan.get("context") or {}
    rows = []
    for c in rows_in:
        rows.append({"id": c["id"], "kind": c["kind"], "attribute": c.get("attribute"), "label": _row_label(c),
                     "supports": c.get("supports"), "score": c.get("score"),
                     "groups": group_terms(c.get("terms") or {}), "trouble": c.get("trouble"),
                     "unknown": list(c.get("unknown") or []), "chosen": c["id"] == chosen})
    excluded = []
    for c in plan.get("excluded") or []:
        notes = list(c.get("notes") or [])
        reason = next((n for n in notes if n in EXCLUDE_REASON_TEXT), notes[0] if notes else "unknown")
        excluded.append({"id": c["id"], "kind": c["kind"], "attribute": c.get("attribute"), "label": _row_label(c),
                         "reason": reason,
                         "reason_text": EXCLUDE_REASON_TEXT.get(reason, reason), "trouble": c.get("trouble"),
                         "unknown": list(c.get("unknown") or [])})
    best = next((r for r in rows if r["chosen"]), None)
    summary = None
    if best is not None:
        top = sorted(((g, v) for g, v in best["groups"].items() if abs(v) >= 0.05), key=lambda kv: -abs(kv[1]))[:3]
        labels = {k: label for k, label, _ in TERM_GROUPS}
        summary = f"选 {best['label']}（{best['id']}，{best['score']:.1f} 分）：" + \
            "，".join(f"{labels[g]} {v:+.1f}" for g, v in top)
        if rule and rule.endswith(("fallback_empty_room", "fallback_rest")) and ctx.get("fallback_reason"):
            summary += f"；都不划算时规则：{ctx['fallback_reason']}"
    return {"version": 1, "groups": [{"key": k, "label": label, "terms": list(names)} for k, label, names in TERM_GROUPS],
            "rule": rule, "chosen": chosen, "summary": summary, "rows": rows, "excluded": excluded,
            "context": {k: ctx.get(k) for k in ("primary", "goal", "W", "weeks_left", "stamina", "stamina_source",
                                                "floor", "fallback_reason") if k in ctx}}
