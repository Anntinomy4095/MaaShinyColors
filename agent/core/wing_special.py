"""強化週間 limited auditions in the W.I.N.G. fan plan: pure, deterministic, no input, no screen reading.

while an event window is open (``wing_score.special_window_active``), each
season's final week adds one limited audition (S1 +40k, S2 +100k, S3 +50k, S4 +250k), passed at top 2.

A limited audition is only a bonus (「容易输」「S4补TE不要加」「如果不行则放弃」): it never lowers a
target and the route never depends on it. Every season's ordinary auditions still reach the season target (gate +
route target, 500k in S4 for TE) by themselves; a win simply leaves later seasons fewer ordinary wins to plan from
the real fans, a loss changes nothing. ``season_plan`` says, per week, whether this season's limited audition is in
the plan (the user's per-season setting ``seasons``, default all):

* weeks before the final week: ``reserve`` (plan the ordinary auditions one week earlier so the final week stays
  free) only when the ordinary route of the static table (``season_wins``) fits in ``weeks_left - 1`` weeks with
  ``slack`` spare weeks for a lost audition (``special_skipped:no_fallback`` otherwise). The entry condition must be
  met or reachable without extra auditions (S1 fans: the ordinary route itself passes 10k); ``prep`` nudges the week
  toward it only with weeks the ordinary route and its spare do not need (S2 kept promises for 親愛度, S3 rooms with
  supports for 絆, S4 Me work/lessons when the Me deficit fits in those free weeks and ``prep_weeks``);
* the final week: the page must be enterable (entry condition met), the win estimate (``wing_audition``, the model
  of the ordinary auditions) known and the primary at least ``min_ratio[live]`` × its 推奨能力値 (own play: 1.0, the
  rule table's ordinary threshold; the game's Auto, which plays S2/S4: 1.3 and Me ≥ the 推奨 Me). ``bonus`` = the
  season target is already met: then the limited audition is entered. Below the target the final week belongs to
  the ordinary audition; the limited one is used only as the last chance when no ordinary audition can reach it.

Entry conditions without a reader (S2 思い出 Lv2 / 親愛度 25, S3 three red bonds) stay ``unknown`` until the final
week's page shows the limited page enterable (met) or under its 出場条件 overlay / without 推奨能力値 (unmet).
"""
from __future__ import annotations

import dataclasses
import math
from typing import Any, Mapping

from agent.core import wing_score as wsc
from agent.core import wing_state as ws


@dataclasses.dataclass(frozen=True)
class Special:
    id: str                 # wing_screen.SPECIAL_AUDITION_NAMES key; the audition id ends with "_<id>"
    season: int
    reward: int
    condition: str          # fans | affection | bonds | Me
    need: int
    live: str               # how its Live is played: policy | trend2 (S3 guide) | game_auto (rule not playable)
    rule: str               # 特殊ルール (research doc)


SPECIALS = {
    1: Special("machikado", 1, 40_000, "fans", 10_000, "policy", "对手得意属性每回合变化（只改对手）"),
    2: Special("ereebest", 2, 100_000, "affection", 25, "game_auto",
               "三位审查员轮流 2 回合属性 −80%、Me 伤害 0（回合未知）"),
    3: Special("mezase", 3, 50_000, "bonds", 3, "trend2", "每 2 回合审查员属性 +100% 2 回合"),
    4: Special("kaeritakunai", 4, 250_000, "Me", 500, "game_auto", "2way，LA 权重大，10 回合以上"),
}
BY_ID = {s.id: s for s in SPECIALS.values()}
# Live play per limited audition (wing_nav): the S2 debuff turns and the S4 2way/LA timing have no Live sample and
# no reader, so the game's Auto plays them; S1's rule changes only the rivals; S3 follows the guide (trend 1 when the
# primary is trend 1, else spear trend 2 for its Top Appeal; live-passed once with the plain trend-1 focus, wing5b).
LIVE_MODES = ("policy", "trend2", "game_auto")
# Entry margin over the page's 推奨能力値 primary per Live mode: own play uses the ordinary threshold of the rule
# table (primary >= 推奨); the game's Auto plays without trend focus or LA timing and its win rate on these rules is
# unknown, so it needs a clear margin (and Me >= 推奨 Me).
DEFAULT_MIN_RATIO = {"policy": 1.0, "trend2": 1.0, "game_auto": 1.3}
SEASONS_ALL = tuple(SPECIALS)

DEFAULT_TARGETS = dict(wsc.ScoreParams().season_fan_targets)
MAX_SEASON_WINS = ws.WEEKS_PER_SEASON


def special_of(audition_id: Any) -> Special | None:
    """The limited audition of an audition id (``s2_100k_ereebest`` -> ereebest), None for a regular one."""
    text = str(audition_id or "")
    return next((s for s in SPECIALS.values() if text.endswith("_" + s.id)), None)


def is_special(audition_id: Any) -> bool:
    return special_of(audition_id) is not None


def season_target(season: int, goal: str, targets: Mapping[int, int] | None = None) -> int:
    """Fans the route wants at the end of ``season``: the gate, plus the route target (and 500k in S4) for TE."""
    gate = ws.SEASON_FAN_GATES[season]
    if goal != "true_end":
        return gate
    target = max(gate, dict(targets or DEFAULT_TARGETS).get(season, 0))
    return max(target, ws.TRUE_END_FANS) if season == ws.SEASONS else target


def season_wins(fans: int, season: int, target: int) -> tuple[float, int]:
    """(ordinary wins, fans after them) to reach ``target`` within ``season`` on the static table: the highest tier
    unlocked by the projected rank each step. (inf, fans) when the table cannot reach it within a season."""
    if season not in wsc.WING_AUDITION_TABLE:
        return math.inf, fans
    wins = 0
    rank = wsc.rank_from_fans(fans) or "F"
    while fans < target:
        tiers = [reward for reward, gate, _ in wsc.WING_AUDITION_TABLE[season]
                 if ws.RANKS.index(rank) >= ws.RANKS.index(gate)]
        if not tiers or wins >= MAX_SEASON_WINS:
            return math.inf, fans
        fans += max(tiers)
        wins += 1
        rank = wsc._rank_max(rank, wsc.rank_from_fans(fans))
    return wins, fans


def ordinary_wins(fans: int, first_season: int, goal: str, targets: Mapping[int, int] | None = None) -> float:
    """Ordinary wins from the start of ``first_season`` to the end of S4 on the static table route (no limited
    auditions assumed)."""
    total = 0
    for season in range(first_season, ws.SEASONS + 1):
        wins, fans = season_wins(fans, season, season_target(season, goal, targets))
        if math.isinf(wins):
            return math.inf
        total += wins
    return total


def _saved(before: float, after: float) -> float:
    if math.isinf(before) and not math.isinf(after):
        return float(MAX_SEASON_WINS)
    if math.isinf(after):
        return 0.0
    return max(0.0, float(before - after))


def limited_audition(state: ws.WingState, special: Special) -> ws.Audition | None:
    return next((a for a in state.auditions if a.id.endswith("_" + special.id)), None)


def _best_me_gain(state: ws.WingState) -> int:
    """Best Me gain one week can bring: the largest read preview, else the table radio work of the shown level."""
    gains = [o.preview.get("Me", 0) for o in state.options if o.available and o.kind in ("lesson", "work")]
    best = max(gains, default=0)
    if best:
        return best
    radio = [wsc.WORK_BASE["radio"][o.level][0]["Me"] for o in state.options
             if o.available and o.kind == "work" and wsc.work_type(o) == "radio" and o.level in wsc.WORK_BASE["radio"]]
    return max(radio, default=0)


def win_check(state: ws.WingState, page: ws.Audition, special: Special, primary: str | None,
              min_ratio: Mapping[str, float] | None = None) -> tuple[bool, str, dict]:
    """(ok, reason, detail): the limited page's win estimate is known and the primary clears the margin."""
    from agent.core import wing_audition as wa
    ratios = {**DEFAULT_MIN_RATIO, **dict(min_ratio or {})}
    if primary not in ws.ATTRIBUTES:
        return False, "special_skipped:primary_unknown", {}
    estimate = wa.audition_win_estimate(wa.context_from_wing(state, page, primary, need_first=False))
    stat, need = state.stats.get(primary), page.recommended.get(primary)
    detail = {"p_pass": estimate.get("p_pass"), "primary": stat, "recommended": need,
              "min_ratio": ratios[special.live]}
    if estimate.get("p_pass") is None or not isinstance(stat, int) or not need:
        return False, "special_skipped:win_unknown", detail
    detail["ratio"] = round(stat / need, 3)
    if stat < ratios[special.live] * need:
        return False, "special_skipped:win_chance_low", detail
    if special.live == "game_auto":
        me, me_need = state.stats.get("Me"), page.recommended.get("Me")
        detail.update(me=me, me_recommended=me_need)
        if not isinstance(me, int) or not me_need:
            return False, "special_skipped:win_unknown", detail
        if me < me_need:
            return False, "special_skipped:win_chance_low", detail
    return True, "win_chance_ok", detail


def season_plan(state: ws.WingState, goal: str = "true_end", *, window: bool = False,
                targets: Mapping[int, int] | None = None, seasons=SEASONS_ALL, primary: str | None = None,
                min_ratio: Mapping[str, float] | None = None, prep_weeks: int = 2, slack: int = 1) -> dict | None:
    """The current season's limited audition in the plan, or None (no event window / not a week phase).

    ``targets``: the route's season fan targets (the scorer's S3 100k by default); ``seasons``: the seasons the user
    left on (Produce setting 「限定试镜」); ``primary``: the run's primary stat for the final-week win check."""
    if not window or state.phase != "week" or state.season not in SPECIALS or state.weeks_left is None \
            or state.fans is None:
        return None
    sp = SPECIALS[state.season]
    weeks_left, fans, season = state.weeks_left, state.fans, state.season
    final = weeks_left == 1
    soft = season_target(season, goal, targets)
    plan: dict[str, Any] = {"id": sp.id, "season": season, "reward": sp.reward, "condition": sp.condition,
                            "need": sp.need, "live": sp.live, "final_week": final, "planned": False,
                            "reserve": False, "bonus": False, "wins_saved": 0.0, "prep": None, "status": "unknown",
                            "reason": None, "target": soft}

    def skip(reason: str) -> dict:
        plan["reason"] = reason
        return plan

    if season not in tuple(seasons or ()):
        return skip("special_skipped:setting_off")
    have = {"fans": fans, "Me": state.stats.get("Me")}.get(sp.condition)
    plan["have"] = have
    page = limited_audition(state, sp) if final else None
    if final:
        plan["status"] = "unseen" if page is None else "met" if page.available else "unmet"
    elif sp.condition in ("fans", "Me"):
        plan["status"] = "unread" if have is None else "met" if have >= sp.need else "pending"
    if plan["status"] in ("unmet", "unseen", "unread"):
        return skip(f"special_skipped:condition_{plan['status']}")

    # Later seasons plan from the real fans after a win; this only values the limited candidate (logs / ranking).
    nxt = season + 1
    if nxt <= ws.SEASONS:
        end_fans = max(fans, soft)
        plan["wins_saved"] = _saved(ordinary_wins(end_fans, nxt, goal, targets),
                                    ordinary_wins(end_fans + sp.reward, nxt, goal, targets))

    if final:
        ok, reason, detail = win_check(state, page, sp, primary, min_ratio)
        plan["win"] = detail
        if not ok:
            return skip(reason)
        plan.update(planned=True, bonus=fans >= soft,
                    reason="bonus_target_met" if fans >= soft else "last_chance_only")
        return plan

    # Fallback: the ordinary route alone reaches the season target before the final week, with spare weeks.
    n_ord, end_fans = season_wins(fans, season, soft)
    plan["ordinary_wins"] = n_ord
    free = None if math.isinf(n_ord) else (weeks_left - 1) - n_ord - slack
    if free is None or free < 0:
        return skip("special_skipped:no_fallback")
    plan["free_weeks"] = free
    if sp.condition == "fans" and plan["status"] == "pending" and end_fans < sp.need:
        return skip("special_skipped:condition_out_of_reach")      # 10k would need extra auditions
    if sp.condition == "Me" and plan["status"] == "pending":
        deficit = sp.need - have
        cap = (state.stat_caps or {}).get("Me")
        gain = _best_me_gain(state)
        if isinstance(cap, int) and cap < sp.need:
            return skip(f"special_skipped:me_cap_{cap}_below_{sp.need}")
        weeks = math.ceil(deficit / gain) if gain else math.inf
        if weeks > min(prep_weeks, free):
            return skip(f"special_skipped:me_deficit_{deficit}_not_cheap")
        plan["prep"] = {"kind": "Me", "deficit": deficit, "gain_per_week": gain,
                        "value_weeks": max(1.0, plan["wins_saved"])}
    elif sp.condition in ("affection", "bonds") and free >= 1:
        plan["prep"] = {"kind": sp.condition}
    plan.update(planned=True, reserve=True,
                reason="condition_met" if plan["status"] == "met" else f"condition_{plan['status']}_tentative")
    return plan


def summary(plan: Mapping[str, Any] | None) -> dict | None:
    """Log-friendly subset of a season plan."""
    if not plan:
        return None
    return {k: plan.get(k) for k in ("id", "status", "planned", "reserve", "bonus", "wins_saved", "prep", "reason")
            if k in plan}
